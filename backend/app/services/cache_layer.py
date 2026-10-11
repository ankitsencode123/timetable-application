"""
app/services/cache_layer.py
===========================================================================
Complete, zero-cost caching layer for the Timetable API
(Render free web service + Supabase Postgres + Vercel frontend + Groq free tier)

TARGET LOAD: ~120 students at the same moment reading the timetable and
asking the public AI chatbot.

WHAT THIS FILE GIVES YOU
------------------------
1. Published-timetable SNAPSHOT cache (in-process, built once, shared by all)
   * All /public/timetable* and /public/meta responses are pre-serialised JSON
     bytes + pre-gzipped bytes + a content ETag. A request costs microseconds
     and ZERO database queries.
   * Stale-while-revalidate: every ~20s one background probe (2 tiny queries)
     checks for changes; students never wait on the DB.
   * Stale-if-error: if Supabase hiccups, the last good snapshot keeps being
     served instead of a 500.
   * Request coalescing (single-flight): 120 simultaneous cold requests =
     ONE database load.
   * Automatic, exact invalidation: SQLAlchemy session events notice any
     commit that touched TimetableEntry / TimetableVersion (publish, unpublish,
     edit, create_draft, bulk update/delete ...) and rebuild immediately.
     No need to touch the rest of your code.
   * Safety nets for out-of-band edits (e.g. SQL editor in Supabase):
     fingerprint probe + hard TTL.

2. HTTP caching for browsers / Vercel edge
   * ETag + If-None-Match -> 304 Not Modified (almost no bandwidth)
   * Cache-Control: public, max-age, stale-while-revalidate
   * Pre-compressed gzip (no per-request CPU burn on Render's tiny CPU)

3. Public AI chat cache (protects the Groq free-tier quota)
   * Question normalisation -> same question phrased trivially differently
     hits the same cache entry.
   * Cache key = question + timetable content digest + faculty/room data +
     today's date (IST), so answers can never be stale after a publish and
     "today"/"tomorrow" questions never leak across days.
   * Single-flight: 120 students asking the same popular question = ONE
     Groq call.
   * Global Groq rate budget (sliding window RPM), bounded concurrency,
     per-client / per-IP throttle (cache hits are free and never throttled),
     circuit breaker on repeated failures.
   * Graceful degradation: if Groq is busy / rate-limited / down, a
     deterministic lookup against the cached timetable answers day / teacher /
     program / subject questions instead of an error.
   * Calendar answers (try_handle_calendar_question) cached with a short TTL.

4. Teacher chat: cached live-schedule context (build_schedule_context_cached),
   auto-invalidated on any timetable commit.

5. Admin endpoints: GET /api/admin/cache/stats, POST /api/admin/cache/purge

INTEGRATION (3 small edits, nothing else changes)
-------------------------------------------------
A) Save this file as  app/services/cache_layer.py

B) app/main.py  -> right after `app = FastAPI(...)` and BEFORE the
   `app.include_router(...)` lines:

       from app.services.cache_layer import install_cache
       install_cache(app)

   install_cache() registers cached versions of GET /api/public/timetable,
   /timetable/program/{p}/{s}, /timetable/teacher/{code}, /meta and
   POST /api/public/chat *before* your existing ones, so they take priority.
   (You may delete the old duplicates in public.py / chat.py later.)

C) app/services/chat_service.py (teacher chat) -> replace
       schedule_ctx = _build_schedule_context(db) if db else ""
   with
       from app.services.cache_layer import build_schedule_context_cached
       schedule_ctx = build_schedule_context_cached(db) if db else ""

RENDER SETTINGS (important)
---------------------------
* Start command MUST use a single worker so every request shares one cache:
      uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
* Free instances sleep after ~15 min idle (cache is rebuilt on wake-up by the
  startup warm-up). Point a free UptimeRobot/cron-job.org monitor at
      https://<your-app>.onrender.com/api/public/meta   (every 5 min)
  It keeps Render awake AND touches Supabase so the free project is not paused
  for inactivity.
* Use Supabase's pooled connection string (port 6543) in DATABASE_URL.

FRONTEND (Vercel) - optional but recommended
--------------------------------------------
* Send a stable random id with chat requests so one student cannot starve the
  rest of a shared campus Wi-Fi IP:
      let id = localStorage.getItem("cid");
      if (!id) { id = crypto.randomUUID(); localStorage.setItem("cid", id); }
      fetch(`${API}/api/public/chat`, { method: "POST",
        headers: {"Content-Type": "application/json", "X-Client-Id": id},
        body: JSON.stringify({ message }) });
* Use fixed "suggested question" chips: identical questions share one cache
  entry, so thousands of taps cost one Groq call.
* Poll /api/public/meta (cheap) every 30-60 s and refetch the timetable only
  when version_id changes. The browser's ETag handling does the rest.

TUNING (all optional environment variables; defaults are production-safe)
-------------------------------------------------------------------------
  TT_SOFT_TTL=20            seconds before a background change-probe is kicked
  TT_HARD_TTL=300           seconds before a full rebuild is forced
  TT_HTTP_MAX_AGE=15        Cache-Control max-age for browsers / edge
  TT_HTTP_SWR=120           Cache-Control stale-while-revalidate
  CHAT_TTL=21600            seconds an AI answer stays cached (6 h)
  CHAT_CALENDAR_TTL=60      seconds a calendar answer stays cached
  CHAT_FALLBACK_TTL=20      seconds a degraded (non-AI) answer stays cached
  CHAT_MAX_ENTRIES=3000     LRU capacity of the answer cache
  CHAT_MAX_CHARS=500        max question length
  LLM_CONCURRENCY=3         simultaneous Groq calls
  LLM_RPM=20                total Groq requests per minute this app may spend
                            (set below your account's free-tier limit; keys
                            from the same Groq org share one quota)
  LLM_QUEUE_WAIT=12         max seconds a request may queue for an RPM slot
  CHAT_REQUEST_TIMEOUT=40   max seconds a student waits for an answer
  CHAT_FLIGHT_TIMEOUT=60    hard cap for one Groq flight
  CLIENT_MISS_PER_MIN=8     uncached questions / minute / X-Client-Id
  IP_MISS_PER_MIN=150       uncached questions / minute / IP (campus NAT-safe)
  BREAKER_THRESHOLD=3       consecutive LLM failures before the breaker opens
  BREAKER_COOLDOWN=30       seconds the breaker stays open
===========================================================================
"""
import asyncio
import gzip
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from collections import Counter, OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from loguru import logger
from sqlalchemy import func

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.timetable import TimetableVersion, VersionStatus
from app.models.timetable_entry import TimetableEntry
from app.models.user import RoleEnum, User
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.timetable import ScheduleEntry

__all__ = [
    "install_cache",
    "invalidate_timetable_data",
    "purge_all_caches",
    "build_schedule_context_cached",
    "normalize_question",
    "CFG",
]

# India has no DST, so a fixed offset is exact and needs no tz database.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def _now_ist() -> datetime:
    return datetime.now(IST)


# ============================================================================
# 1. CONFIGURATION
# ============================================================================
def _env_num(name: str, default, cast=float):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logger.warning(f"[cache] invalid value for {name}={raw!r}; using {default}")
        return default


@dataclass(frozen=True)
class CacheConfig:
    snapshot_soft_ttl: float = _env_num("TT_SOFT_TTL", 20.0)
    snapshot_hard_ttl: float = _env_num("TT_HARD_TTL", 300.0)
    http_max_age: int = _env_num("TT_HTTP_MAX_AGE", 15, int)
    http_swr: int = _env_num("TT_HTTP_SWR", 120, int)

    chat_ttl: float = _env_num("CHAT_TTL", 6 * 3600.0)
    chat_calendar_ttl: float = _env_num("CHAT_CALENDAR_TTL", 60.0)
    chat_fallback_ttl: float = _env_num("CHAT_FALLBACK_TTL", 20.0)
    chat_max_entries: int = _env_num("CHAT_MAX_ENTRIES", 3000, int)
    chat_max_chars: int = _env_num("CHAT_MAX_CHARS", 500, int)

    llm_concurrency: int = _env_num("LLM_CONCURRENCY", 3, int)
    llm_rpm: int = _env_num("LLM_RPM", 20, int)
    llm_queue_wait: float = _env_num("LLM_QUEUE_WAIT", 12.0)
    request_timeout: float = _env_num("CHAT_REQUEST_TIMEOUT", 40.0)
    flight_timeout: float = _env_num("CHAT_FLIGHT_TIMEOUT", 60.0)

    client_miss_per_min: int = _env_num("CLIENT_MISS_PER_MIN", 8, int)
    ip_miss_per_min: int = _env_num("IP_MISS_PER_MIN", 150, int)

    breaker_threshold: int = _env_num("BREAKER_THRESHOLD", 3, int)
    breaker_cooldown: float = _env_num("BREAKER_COOLDOWN", 30.0)


CFG = CacheConfig()

# Dedicated executors so cache work can never starve (or be starved by) the
# framework's shared 40-thread pool.
_DB_EXEC = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cache-db")
_CHAT_EXEC = ThreadPoolExecutor(
    max_workers=max(2, CFG.llm_concurrency) + 3, thread_name_prefix="cache-chat"
)


# ============================================================================
# 2. SMALL UTILITIES (metrics, TTL cache, throttles, breaker)
# ============================================================================
class _Metrics:
    def __init__(self) -> None:
        self._c: Counter = Counter()
        self._lock = threading.Lock()
        self.started_at = time.time()

    def inc(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._c[key] += n

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._c)


METRICS = _Metrics()


class TTLCache:
    """Thread-safe LRU cache with per-entry TTL (monotonic clock)."""

    def __init__(self, maxsize: int, name: str = "cache") -> None:
        self.name = name
        self.maxsize = max(1, maxsize)
        self._d: "OrderedDict[Any, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self._sets = 0

    def get(self, key: Any, count: bool = True) -> Any:
        now = time.monotonic()
        with self._lock:
            item = self._d.get(key)
            if item is None:
                if count:
                    self.misses += 1
                return None
            exp, val = item
            if exp <= now:
                del self._d[key]
                if count:
                    self.misses += 1
                return None
            self._d.move_to_end(key)
            if count:
                self.hits += 1
            return val

    def set(self, key: Any, value: Any, ttl: float) -> None:
        now = time.monotonic()
        with self._lock:
            self._d[key] = (now + ttl, value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)
                self.evictions += 1
            self._sets += 1
            if self._sets % 256 == 0:  # opportunistic sweep of expired items
                for k in [k for k, (exp, _) in self._d.items() if exp <= now]:
                    del self._d[k]

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._d),
                "maxsize": self.maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_ratio": round(self.hits / total, 4) if total else None,
            }


class KeyedThrottle:
    """Sliding-window limiter per key (event-loop use only). Memory bounded."""

    def __init__(self, max_keys: int = 20000) -> None:
        self._d: "OrderedDict[str, deque]" = OrderedDict()
        self._max_keys = max_keys

    def allow(self, key: str, limit: int, window: float = 60.0) -> Tuple[bool, int]:
        now = time.monotonic()
        dq = self._d.get(key)
        if dq is None:
            dq = deque()
            self._d[key] = dq
            while len(self._d) > self._max_keys:
                self._d.popitem(last=False)
        else:
            self._d.move_to_end(key)
        while dq and now - dq[0] >= window:
            dq.popleft()
        if len(dq) >= limit:
            return False, int(window - (now - dq[0])) + 1
        dq.append(now)
        return True, 0


class AsyncSlidingWindow:
    """Global budget (e.g. Groq requests per minute). Waits for a slot up to max_wait."""

    def __init__(self, max_calls: int, window: float = 60.0) -> None:
        self.max_calls = max(1, max_calls)
        self.window = window
        self._ts: deque = deque()

    def used(self) -> int:
        now = time.monotonic()
        return sum(1 for t in self._ts if now - t < self.window)

    async def acquire(self, max_wait: float) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            now = time.monotonic()
            while self._ts and now - self._ts[0] >= self.window:
                self._ts.popleft()
            if len(self._ts) < self.max_calls:
                self._ts.append(now)  # check-and-take is atomic (no await in between)
                return True
            wait = self.window - (now - self._ts[0]) + 0.05
            if now + wait > deadline:
                return False
            await asyncio.sleep(wait)


class CircuitBreaker:
    """Opens after N consecutive failures (or immediately on a rate-limit error)."""

    def __init__(self, threshold: int, cooldown: float) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._fails = 0
        self._open_until = 0.0

    def allow(self) -> bool:
        return time.monotonic() >= self._open_until

    def success(self) -> None:
        self._fails = 0
        self._open_until = 0.0

    def failure(self, hard: bool = False) -> None:
        self._fails += 1
        if hard or self._fails >= self.threshold:
            self._open_until = time.monotonic() + self.cooldown
            self._fails = 0

    def state(self) -> Dict[str, Any]:
        remaining = max(0.0, self._open_until - time.monotonic())
        return {"open": remaining > 0, "seconds_remaining": round(remaining, 1)}


@contextmanager
def _db_session():
    """Open a session through the app's own get_db() dependency (respecting overrides), close it reliably."""
    db_getter = get_db
    is_override = False
    try:
        from app.main import app
        if app and get_db in app.dependency_overrides:
            db_getter = app.dependency_overrides[get_db]
            is_override = True
    except Exception:
        pass

    gen = db_getter()
    db = next(gen)
    try:
        yield db
    finally:
        try:
            gen.close()
        except Exception:
            pass
        if not is_override:
            try:
                db.close()
            except Exception:
                pass


# ============================================================================
# 3. PUBLISHED-TIMETABLE SNAPSHOT
# ============================================================================
@dataclass(frozen=True)
class Blob:
    json: bytes
    gz: bytes
    etag: str


def _make_blob(obj: Any) -> Blob:
    raw = json.dumps(jsonable_encoder(obj), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    etag = '"' + hashlib.sha1(raw).hexdigest()[:32] + '"'
    gz = gzip.compress(raw, compresslevel=6, mtime=0) if len(raw) >= 400 else b""
    return Blob(raw, gz, etag)


@dataclass(frozen=True)
class Snapshot:
    exists: bool
    version_id: Optional[int]
    fingerprint: tuple
    built_at: float
    entry_count: int
    all: Blob
    meta: Blob
    empty: Blob
    by_program: Dict[Tuple[str, str], Blob]
    by_teacher: Dict[str, Blob]
    rows: Tuple[dict, ...]      # lightweight rows for the deterministic fallback
    prompt_table: str           # exact LLM table (same format as before)
    prompt_digest: str          # changes iff the LLM-visible timetable changes


class SnapshotUnavailable(Exception):
    pass


def _probe(db) -> tuple:
    """Cheap change-fingerprint of the published timetable (2 tiny queries)."""
    pub = (
        db.query(TimetableVersion.id, TimetableVersion.published_at)
        .filter(TimetableVersion.status == VersionStatus.PUBLISHED)
        .order_by(TimetableVersion.id.desc())
        .first()
    )
    if pub is None:
        return ("none",)
    cols = [func.count(TimetableEntry.id), func.max(TimetableEntry.id)]
    upd = getattr(TimetableEntry, "updated_at", None)
    if upd is not None:
        cols.append(func.max(upd))
    row = db.query(*cols).filter(TimetableEntry.version_id == pub.id).one()
    return (pub.id, str(pub.published_at)) + tuple(str(x) for x in row)


_PROMPT_HEADER = (
    "LIVE PUBLISHED TIMETABLE:\n"
    "| Day | Program | Semester | Time | Subject/Teacher/Room |\n"
    "|---|---|---|---|---|\n"
)


def _build_snapshot(db, fingerprint: tuple) -> Snapshot:
    pub = (
        db.query(TimetableVersion)
        .filter(TimetableVersion.status == VersionStatus.PUBLISHED)
        .order_by(TimetableVersion.id.desc())
        .first()
    )
    empty_blob = _make_blob([])
    if pub is None:
        meta = _make_blob(None)
        return Snapshot(
            exists=False, version_id=None, fingerprint=fingerprint, built_at=time.monotonic(),
            entry_count=0, all=empty_blob, meta=meta, empty=empty_blob,
            by_program={}, by_teacher={}, rows=(), prompt_table="",
            prompt_digest=hashlib.sha1(b"").hexdigest()[:16],
        )

    entries = (
        db.query(TimetableEntry)
        .filter(TimetableEntry.version_id == pub.id)
        .order_by(TimetableEntry.id)
        .all()
    )
    dicts: List[dict] = []
    lite: List[dict] = []
    prog_idx: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    teach_idx: Dict[str, List[dict]] = defaultdict(list)
    table_lines: List[str] = []

    for e in entries:
        d = e.to_dict()
        dicts.append(d)
        program, sem, teacher = e.program or "", e.semester or "", e.teacher or ""
        # Same normalisation the original endpoints applied per request.
        prog_idx[(program.lower().replace(".", ""), sem.lower())].append(d)
        teach_idx[teacher.lower()].append(d)
        lite.append({
            "day": e.day or "", "program": program, "semester": sem,
            "start_time": e.start_time or "", "end_time": e.end_time or "",
            "teacher": teacher, "subject_name": e.subject_name or "",
            "subject_code": e.subject_code or "", "room": e.room or "",
            "entry_type": e.entry_type or "",
        })
        table_lines.append(
            f"| {e.day} | {e.program} | {e.semester} | "
            f"{e.start_time}-{e.end_time} | {e.teacher}({e.subject_name}):{e.room} |"
        )

    prompt_table = (_PROMPT_HEADER + "\n".join(table_lines)) if table_lines else ""
    meta = _make_blob({
        "version_id": pub.id,
        "published_at": pub.published_at.isoformat() if pub.published_at else None,
        "change_summary": pub.change_summary,
    })
    return Snapshot(
        exists=True, version_id=pub.id, fingerprint=fingerprint, built_at=time.monotonic(),
        entry_count=len(dicts), all=_make_blob(dicts), meta=meta, empty=empty_blob,
        by_program={k: _make_blob(v) for k, v in prog_idx.items()},
        by_teacher={k: _make_blob(v) for k, v in teach_idx.items()},
        rows=tuple(lite), prompt_table=prompt_table,
        prompt_digest=hashlib.sha1(prompt_table.encode("utf-8")).hexdigest()[:16],
    )


class SnapshotStore:
    """
    Serves the published timetable from memory.

      fresh  (< soft TTL)         -> return instantly
      soft   (soft..hard TTL)     -> return instantly + background change-probe
      hard / invalidated / empty  -> single shared reload (coalesced)
      reload fails                -> serve last good snapshot (stale-if-error)
    """

    def __init__(self, cfg: CacheConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()          # guards the fields below (threads + loop)
        self._snap: Optional[Snapshot] = None
        self._checked_at = 0.0
        self._gen = 0
        self._dirty = False
        self._fail_until = 0.0
        self._load_task: Optional["asyncio.Task"] = None   # event-loop only
        self._bg_task: Optional["asyncio.Task"] = None     # event-loop only

    # ---- thread-safe helpers (called from SQLAlchemy hooks in worker threads)
    @property
    def generation(self) -> int:
        with self._lock:
            return self._gen

    def peek(self) -> Optional[Snapshot]:
        with self._lock:
            return self._snap

    def invalidate(self) -> None:
        with self._lock:
            self._gen += 1
            self._dirty = True

    # ---- synchronous worker (runs in _DB_EXEC)
    def _sync_refresh(self) -> Snapshot:
        last: Optional[Snapshot] = None
        for _ in range(3):
            with self._lock:
                gen, cur, force = self._gen, self._snap, self._dirty
            with _db_session() as db:
                fp = _probe(db)
                METRICS.inc("snapshot_probes")
                reusable = (
                    cur is not None
                    and not force
                    and cur.fingerprint == fp
                    and (time.monotonic() - cur.built_at) < self.cfg.snapshot_hard_ttl
                )
                if reusable:
                    snap = cur
                else:
                    snap = _build_snapshot(db, fp)
                    METRICS.inc("snapshot_builds")
            with self._lock:
                if self._gen == gen:           # nothing invalidated while we were reading
                    self._snap = snap
                    self._checked_at = time.monotonic()
                    self._dirty = False
                    self._fail_until = 0.0
                    return snap
            last = snap                        # data may be older than a commit -> retry
        with self._lock:                       # keep dirty=True: next request reloads again
            self._snap = last
            self._checked_at = time.monotonic()
        return last  # type: ignore[return-value]

    # ---- async side
    async def _refresh_shared(self) -> Snapshot:
        task = self._load_task
        if task is None or task.done():
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._run_refresh())
            task.add_done_callback(lambda t: (not t.cancelled()) and t.exception())
            self._load_task = task
        return await asyncio.shield(task)

    async def _run_refresh(self) -> Snapshot:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_DB_EXEC, self._sync_refresh)

    def _kick_background(self) -> None:
        if self._bg_task is not None and not self._bg_task.done():
            return
        self._bg_task = asyncio.get_running_loop().create_task(self._run_bg())

    async def _run_bg(self) -> None:
        try:
            await self._refresh_shared()
        except Exception as exc:
            logger.warning(f"[cache] background revalidation failed: {exc}")
            with self._lock:  # retry in ~10s rather than hammering a sick DB
                self._checked_at = time.monotonic() - self.cfg.snapshot_soft_ttl + 10.0

    async def get(self) -> Snapshot:
        with self._lock:
            snap, dirty, checked, fail_until = (
                self._snap, self._dirty, self._checked_at, self._fail_until
            )
        now = time.monotonic()
        if snap is not None and not dirty:
            age = now - checked
            if age < self.cfg.snapshot_soft_ttl:
                return snap
            if age < self.cfg.snapshot_hard_ttl:
                self._kick_background()
                return snap
        if now < fail_until:
            if snap is not None:
                METRICS.inc("snapshot_stale_served_on_error")
                return snap
            raise SnapshotUnavailable()
        try:
            return await self._refresh_shared()
        except Exception as exc:
            logger.error(f"[cache] snapshot refresh failed: {exc}")
            with self._lock:
                self._fail_until = time.monotonic() + 5.0
            if snap is not None:
                METRICS.inc("snapshot_stale_served_on_error")
                return snap
            raise SnapshotUnavailable() from exc


STORE = SnapshotStore(CFG)

# Teacher-chat schedule-context cache (tiny, auto-invalidated).
_CTX_CACHE = TTLCache(64, "schedule_context")
_CTX_TTL = 60.0


def invalidate_timetable_data(reason: str = "manual") -> None:
    """Drop snapshot + schedule-context caches. Safe to call from any thread."""
    STORE.invalidate()
    _CTX_CACHE.clear()
    METRICS.inc("invalidations")
    logger.debug(f"[cache] timetable caches invalidated ({reason})")


# ----------------------------------------------------------------------------
# Automatic invalidation: SQLAlchemy session events
# ----------------------------------------------------------------------------
_HOOKS_INSTALLED = False
_DIRTY_KEY = "_tt_cache_dirty"


def _install_sqlalchemy_hooks() -> None:
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    from sqlalchemy import event
    from sqlalchemy.orm import Session as SASession

    watched = (TimetableVersion, TimetableEntry)

    def _touches(objs) -> bool:
        return any(isinstance(o, watched) for o in objs)

    @event.listens_for(SASession, "after_flush")
    def _after_flush(session, flush_context):
        try:
            if _touches(session.new) or _touches(session.dirty) or _touches(session.deleted):
                session.info[_DIRTY_KEY] = True
        except Exception:
            session.info[_DIRTY_KEY] = True    # be conservative

    def _bulk(ctx):
        try:
            cls = getattr(getattr(ctx, "mapper", None), "class_", None)
            if cls is None or issubclass(cls, watched):
                ctx.session.info[_DIRTY_KEY] = True
        except Exception:
            pass

    event.listen(SASession, "after_bulk_update", _bulk)
    event.listen(SASession, "after_bulk_delete", _bulk)

    @event.listens_for(SASession, "after_commit")
    def _after_commit(session):
        if session.info.pop(_DIRTY_KEY, False):
            invalidate_timetable_data("db-commit")

    @event.listens_for(SASession, "after_rollback")
    def _after_rollback(session):
        session.info.pop(_DIRTY_KEY, None)

    _HOOKS_INSTALLED = True
    logger.info("[cache] SQLAlchemy auto-invalidation hooks installed")


# ============================================================================
# 4. HTTP RESPONSES (ETag / 304 / gzip / Cache-Control)
# ============================================================================
_GZIP_OK_RE = re.compile(r"\bgzip\b(?!\s*;\s*q=0(?:\.0+)?\s*(?:,|$))", re.I)


def _etag_matches(header: str, etag: str) -> bool:
    header = header.strip()
    if header == "*":
        return True
    want = etag.strip('"')
    for part in header.split(","):
        p = part.strip()
        if p.startswith("W/"):
            p = p[2:]
        if p.strip('"') == want:
            return True
    return False


def _blob_response(request: Request, blob: Blob, snap: Snapshot) -> Response:
    headers = {
        "ETag": blob.etag,
        "Cache-Control": (
            f"public, max-age={CFG.http_max_age}, "
            f"s-maxage={CFG.http_max_age}, stale-while-revalidate={CFG.http_swr}"
        ),
        "Vary": "Accept-Encoding",
        "X-Timetable-Version": str(snap.version_id) if snap.version_id is not None else "none",
    }
    inm = request.headers.get("if-none-match")
    if inm and _etag_matches(inm, blob.etag):
        METRICS.inc("http_304")
        return Response(status_code=304, headers=headers)
    if blob.gz and _GZIP_OK_RE.search(request.headers.get("accept-encoding", "")):
        headers["Content-Encoding"] = "gzip"
        return Response(content=blob.gz, media_type="application/json", headers=headers)
    return Response(content=blob.json, media_type="application/json", headers=headers)


async def _snapshot_or_http_error() -> Snapshot:
    try:
        snap = await STORE.get()
    except SnapshotUnavailable:
        raise HTTPException(status_code=503, detail="Timetable temporarily unavailable. Please retry shortly.")
    if not snap.exists:
        raise HTTPException(status_code=404, detail="No published timetable available")
    return snap


# ============================================================================
# 5. PUBLIC CHAT: normalisation, deterministic fallback, engine
# ============================================================================
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "please", "pls", "plz", "kindly",
    "tell", "me", "us", "give", "show", "can", "could", "would", "you", "i", "my", "we",
    "our", "want", "wanna", "know", "hey", "hi", "hii", "hello", "ok", "okay", "thanks",
    "thank", "u",
})
_DOT_IN_WORD_RE = re.compile(r"(?<=[^\W\d_])\.(?=[^\W\d_])")   # b.tech -> btech
_PUNCT_RE = re.compile(r"[^\w\s:\-/+&]")
_WS_RE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """Canonical form used for cache keys (order of content words is preserved)."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = _DOT_IN_WORD_RE.sub("", t)
    t = _PUNCT_RE.sub(" ", t)
    tokens = [w for w in _WS_RE.split(t.strip()) if w]
    content = [w for w in tokens if w not in _STOPWORDS]
    return " ".join(content or tokens)


def _payload(message: str) -> Dict[str, Any]:
    return {"message": message, "schedule_updated": False, "response_data": {}, "parsed_actions": []}


def _to_payload_dict(r: Any) -> Dict[str, Any]:
    if isinstance(r, dict):
        out = dict(r)
    elif hasattr(r, "model_dump"):
        out = r.model_dump()
    elif hasattr(r, "dict"):
        out = r.dict()
    else:
        out = {"message": str(r)}
    out.setdefault("message", "")
    out.setdefault("schedule_updated", False)
    out.setdefault("response_data", {})
    out.setdefault("parsed_actions", [])
    return out


def _looks_rate_limited(exc: BaseException) -> bool:
    s = str(exc).lower()
    return any(t in s for t in ("429", "rate limit", "rate_limit", "too many requests", "quota"))


_FALLBACK_GENERIC = (
    "I couldn't answer that right now. "
    "Please try again in a minute, or check the timetable grid directly."
)

_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_PATTERNS = [
    ("monday", r"\bmon(?:day)?\b"), ("tuesday", r"\btue(?:s|sday)?\b"),
    ("wednesday", r"\bwed(?:nesday)?\b"), ("thursday", r"\bthu(?:r|rs|rsday)?\b"),
    ("friday", r"\bfri(?:day)?\b"), ("saturday", r"\bsat(?:urday)?\b"),
    ("sunday", r"\bsun(?:day)?\b"),
]
_DAY_ORDER = {d[:3]: i for i, d in enumerate(_DAY_NAMES)}


def _has_term(text: str, term: str) -> bool:
    if not term:
        return False
    return re.search(r"(?<![\w:])" + re.escape(term) + r"(?![\w:])", text) is not None


def _fallback_answer(snap: Optional[Snapshot], msg: str, now: datetime) -> Optional[str]:
    """
    Deterministic, LLM-free lookup used when Groq is busy / limited / down.
    Returns None when the question is not specific enough to answer safely.
    """
    if snap is None or not snap.exists or not snap.rows:
        return None
    low = unicodedata.normalize("NFKC", msg).casefold()
    nodot = low.replace(".", "")

    days = {name for name, pat in _DAY_PATTERNS if re.search(pat, low)}
    if re.search(r"\btoday\b", low):
        days.add(_DAY_NAMES[now.weekday()])
    if re.search(r"\btomorrow\b", low):
        days.add(_DAY_NAMES[(now.weekday() + 1) % 7])

    rows = snap.rows
    teachers = {r["teacher"].lower() for r in rows if r["teacher"]}
    programs = {r["program"].lower().replace(".", "") for r in rows if r["program"]}
    sems = {r["semester"].lower() for r in rows if r["semester"]}
    m_teachers = {t for t in teachers if _has_term(low, t)}
    m_programs = {p for p in programs if _has_term(nodot, p)}
    m_sems = {s for s in sems if _has_term(low, s)}
    subj_terms = set()
    for r in rows:
        n, c = r["subject_name"].lower(), r["subject_code"].lower()
        if n and _has_term(low, n):
            subj_terms.add(n)
        if len(c) >= 2 and _has_term(low, c):
            subj_terms.add(c)

    # A bare semester number alone is too weak a signal to answer from.
    if not (days or m_teachers or m_programs or subj_terms):
        return None

    if days:
        d3 = {d[:3] for d in days}
        rows = tuple(r for r in rows if r["day"].strip().lower()[:3] in d3)
    if m_teachers:
        rows = tuple(r for r in rows if r["teacher"].lower() in m_teachers)
    if m_programs:
        rows = tuple(r for r in rows if r["program"].lower().replace(".", "") in m_programs)
    if m_sems:
        rows = tuple(r for r in rows if r["semester"].lower() in m_sems)
    if subj_terms:
        rows = tuple(r for r in rows if r["subject_name"].lower() in subj_terms
                     or r["subject_code"].lower() in subj_terms)

    if not rows:
        return "I couldn't find any classes matching that in the published timetable."

    ordered = sorted(rows, key=lambda r: (_DAY_ORDER.get(r["day"].strip().lower()[:3], 9), r["start_time"]))
    limit = 40
    lines = [
        "The AI assistant is busy right now, so here are the matching entries "
        "straight from the published timetable:",
        "",
        "| Day | Time | Program / Sem | Subject | Teacher | Room |",
        "|---|---|---|---|---|---|",
    ]
    for r in ordered[:limit]:
        lines.append(
            f"| {r['day']} | {r['start_time']}\u2013{r['end_time']} | {r['program']} {r['semester']} | "
            f"{r['subject_name']} | {r['teacher']} | {r['room']} |"
        )
    if len(ordered) > limit:
        lines.append(f"\n(Showing the first {limit} of {len(ordered)} matches \u2014 narrow the question for the rest.)")
    return "\n".join(lines)


def _catalog_signature() -> tuple:
    """Cheap fingerprint of the static prompt inputs (faculty, rooms, internal teachers)."""
    try:
        from app.scheduler import constraints as C
        from app.scheduler.data import EXISTING_TIMETABLE_MD, FACULTY_MASTER_MD, ROOM_EXAMPLE_MD
        return (
            hash(FACULTY_MASTER_MD), hash(ROOM_EXAMPLE_MD), hash(EXISTING_TIMETABLE_MD),
            hash(frozenset(C.INTERNAL_TEACHERS)),
        )
    except Exception:
        return ("nosig",)


def _preload_scheduler_modules() -> None:
    for name in ("app.scheduler.data", "app.scheduler.constraints", "app.scheduler.llm", "app.calendar_chat"):
        try:
            __import__(name)
        except Exception as exc:
            logger.warning(f"[cache] warm-up import failed for {name}: {exc}")


def _build_system_prompt(snap: Optional[Snapshot]) -> str:
    from app.scheduler import constraints as C
    from app.scheduler.data import EXISTING_TIMETABLE_MD, FACULTY_MASTER_MD, ROOM_EXAMPLE_MD

    internal_teachers_str = ", ".join(sorted(C.INTERNAL_TEACHERS))
    live = snap.prompt_table if (snap is not None and snap.exists and snap.prompt_table) else ""
    timetable_context = live or EXISTING_TIMETABLE_MD
    current_date = _now_ist().strftime("%A, %B %d, %Y")
    return f"""You are a helpful timetable assistant for a university.
Answer ONLY from the timetable data provided below. Be concise and precise.
If you cannot find the information, say so clearly.

CURRENT DATE:
Today is {current_date}. Use this to understand questions about "today" or "tomorrow".

TIMETABLE:
{timetable_context}

FACULTY:
{FACULTY_MASTER_MD}
Note: The following short names correspond to INTERNAL teachers: {internal_teachers_str}. Any others are visiting/external.


ROOMS:
{ROOM_EXAMPLE_MD}

Rules:
- Only answer questions about the timetable (schedules, rooms, teachers, subjects, programs, semesters, days, times).
- Do NOT make up any information not in the data.
- When referencing teacher short names, use the faculty master to resolve full names if helpful.
- Format answers clearly, use bullet points or tables when listing multiple classes.
"""


def _calendar_sync(message: str) -> Optional[Dict[str, Any]]:
    from app.calendar_chat import try_handle_calendar_question
    with _db_session() as db:
        r = try_handle_calendar_question(message, db)
    return None if r is None else _to_payload_dict(r)


def _llm_sync(snap: Optional[Snapshot], message: str) -> str:
    from app.scheduler.llm import call_groq
    messages = [
        {"role": "system", "content": _build_system_prompt(snap)},
        {"role": "user", "content": message},
    ]
    content, _ = call_groq(messages, expect_json=False)
    return content


class ChatEngine:
    def __init__(self, cfg: CacheConfig, store: SnapshotStore, cache: TTLCache) -> None:
        self.cfg = cfg
        self.store = store
        self.cache = cache
        self.inflight: Dict[str, "asyncio.Task"] = {}
        self.breaker = CircuitBreaker(cfg.breaker_threshold, cfg.breaker_cooldown)
        self.rpm = AsyncSlidingWindow(cfg.llm_rpm, 60.0)
        self.throttle = KeyedThrottle()
        self._sem: Optional[asyncio.Semaphore] = None   # created lazily inside the running loop

    # ---- helpers
    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.cfg.llm_concurrency))
        return self._sem

    async def _safe_snapshot(self) -> Optional[Snapshot]:
        try:
            return await self.store.get()
        except SnapshotUnavailable:
            return None

    @staticmethod
    def _make_key(norm: str, snap: Optional[Snapshot]) -> str:
        digest = snap.prompt_digest if snap is not None else "nosnap"
        raw = "\x1f".join((norm, digest, repr(_catalog_signature()), _now_ist().strftime("%Y-%m-%d")))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _check_throttle(self, client_id: Optional[str], ip: str) -> Tuple[bool, int]:
        ok, retry = self.throttle.allow("ip:" + ip, self.cfg.ip_miss_per_min)
        if not ok:
            return False, retry
        if client_id:
            ok, retry = self.throttle.allow("cid:" + client_id, self.cfg.client_miss_per_min)
            if not ok:
                return False, retry
        return True, 0

    def _degraded(self, key: str, snap: Optional[Snapshot], msg: str, why: str) -> Tuple[Dict[str, Any], str]:
        METRICS.inc("chat_fallbacks")
        METRICS.inc(f"chat_fallback_{why}")
        text = _fallback_answer(snap, msg, _now_ist()) or _FALLBACK_GENERIC
        payload = _payload(text)
        self.cache.set(key, payload, self.cfg.chat_fallback_ttl)
        return payload, "FALLBACK"

    # ---- the one flight per distinct question
    async def _flight(self, key: str, msg: str, snap: Optional[Snapshot]) -> Tuple[Dict[str, Any], str]:
        try:
            return await asyncio.wait_for(self._flight_inner(key, msg, snap), self.cfg.flight_timeout)
        except asyncio.TimeoutError:
            logger.warning("[chat] flight timed out")
            self.breaker.failure()
            return self._degraded(key, snap, msg, "timeout")
        except Exception as exc:
            logger.exception(f"[chat] flight crashed: {exc}")
            return self._degraded(key, snap, msg, "error")

    async def _flight_inner(self, key: str, msg: str, snap: Optional[Snapshot]) -> Tuple[Dict[str, Any], str]:
        cfg = self.cfg
        loop = asyncio.get_running_loop()
        cached = self.cache.get(key, count=False)
        if cached is not None:
            return cached, "HIT"

        # 1) Calendar questions (DB-backed, no Groq). Short TTL: calendar can change independently.
        try:
            cal = await loop.run_in_executor(_CHAT_EXEC, _calendar_sync, msg)
        except Exception as exc:
            logger.warning(f"[chat] calendar handler failed, continuing to LLM: {exc}")
            cal = None
        if cal is not None:
            METRICS.inc("chat_calendar")
            self.cache.set(key, cal, cfg.chat_calendar_ttl)
            return cal, "CALENDAR"

        # 2) LLM, guarded by breaker -> concurrency -> RPM budget
        if not self.breaker.allow():
            return self._degraded(key, snap, msg, "breaker_open")
        async with self._semaphore():
            if not await self.rpm.acquire(cfg.llm_queue_wait):
                return self._degraded(key, snap, msg, "rpm_budget")
            METRICS.inc("llm_calls")
            try:
                content = await loop.run_in_executor(_CHAT_EXEC, _llm_sync, snap, msg)
            except Exception as exc:
                METRICS.inc("llm_failures")
                logger.warning(f"[chat] Groq call failed: {exc}")
                self.breaker.failure(hard=_looks_rate_limited(exc))
                return self._degraded(key, snap, msg, "llm_error")

        content = (content or "").strip()
        if not content:
            METRICS.inc("llm_failures")
            self.breaker.failure()
            return self._degraded(key, snap, msg, "empty")
        self.breaker.success()
        payload = _payload(content)
        self.cache.set(key, payload, cfg.chat_ttl)
        return payload, "MISS"

    def _flight_done(self, key: str, task: "asyncio.Task") -> None:
        if self.inflight.get(key) is task:
            self.inflight.pop(key, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error(f"[chat] flight error: {task.exception()}")

    # ---- public entry point
    async def answer(self, message: str, client_id: Optional[str], ip: str) -> Tuple[Dict[str, Any], str]:
        METRICS.inc("chat_requests")
        msg = (message or "").strip()
        if not msg:
            return _payload("Please type a question about the timetable."), "SKIP"
        if len(msg) > self.cfg.chat_max_chars:
            return _payload(f"Please keep your question under {self.cfg.chat_max_chars} characters."), "SKIP"

        snap = await self._safe_snapshot()
        key = self._make_key(normalize_question(msg), snap)

        hit = self.cache.get(key)
        if hit is not None:
            METRICS.inc("chat_hits")
            return hit, "HIT"

        task = self.inflight.get(key)
        if task is None:
            ok, retry = self._check_throttle(client_id, ip)
            if not ok:
                METRICS.inc("chat_rate_limited")
                return _payload(
                    f"You're sending questions too quickly. Please wait about {retry} seconds and try again."
                ), "RATE_LIMITED"
            task = asyncio.get_running_loop().create_task(self._flight(key, msg, snap))
            self.inflight[key] = task
            task.add_done_callback(lambda t, k=key: self._flight_done(k, t))
            METRICS.inc("chat_misses")
            leader = True
        else:
            METRICS.inc("chat_coalesced")
            leader = False

        try:
            payload, status = await asyncio.wait_for(asyncio.shield(task), self.cfg.request_timeout)
        except asyncio.TimeoutError:
            METRICS.inc("chat_timeouts")
            return _payload("The assistant is taking longer than usual. Please try again in a moment."), "TIMEOUT"
        except asyncio.CancelledError:
            raise
        except Exception:
            return _payload(_FALLBACK_GENERIC), "ERROR"
        return payload, (status if leader else ("COALESCED" if status in ("MISS", "CALENDAR") else status))

    def stats(self) -> Dict[str, Any]:
        return {
            "inflight": len(self.inflight),
            "breaker": self.breaker.state(),
            "llm_rpm_used_last_minute": self.rpm.used(),
            "llm_rpm_budget": self.cfg.llm_rpm,
            "answer_cache": self.cache.stats(),
        }


CHAT_CACHE = TTLCache(CFG.chat_max_entries, "chat_answers")
CHAT = ChatEngine(CFG, STORE, CHAT_CACHE)


# ============================================================================
# 6. TEACHER CHAT: cached schedule context (drop-in for _build_schedule_context)
# ============================================================================
def _compute_schedule_context(db, version_id: Optional[int]) -> str:
    from app.services import timetable_service
    if version_id:
        version = timetable_service.get_version(db, version_id)
    else:
        version = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
    if not version or not version.entries:
        return ""
    rows = [
        f"| {e.day} | {e.program} | {e.semester} | "
        f"{e.start_time} | {e.end_time} | {e.subject_code} | "
        f"{e.subject_name} | {e.teacher} | {e.room} | {e.entry_type} |"
        for e in version.entries
    ]
    header = (
        "| Day | Program | Semester | Start Time | End Time | Code | Subject | Teacher | Room | Type |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    return header + "\n".join(rows)


def build_schedule_context_cached(db, version_id: Optional[int] = None) -> str:
    """
    Same output as chat_service._build_schedule_context, but computed once and
    shared until any timetable commit happens (auto-invalidated by the hooks).
    """
    key = ("ctx", version_id or "latest")
    hit = _CTX_CACHE.get(key)
    if hit is not None:
        return hit
    gen = STORE.generation
    try:
        value = _compute_schedule_context(db, version_id)
    except Exception as exc:
        logger.warning(f"[cache] schedule context build failed: {exc}")
        return ""
    if value and STORE.generation == gen:      # never cache data read across an invalidation
        _CTX_CACHE.set(key, value, _CTX_TTL)
    return value


# ============================================================================
# 7. ROUTERS
# ============================================================================
public_router = APIRouter()
admin_router = APIRouter()


def _client_ident(request: Request) -> Tuple[Optional[str], str]:
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
    raw = request.headers.get("x-client-id", "")
    cid = re.sub(r"[^A-Za-z0-9_\-]", "", raw)[:64] or None
    return cid, ip or "unknown"


@public_router.get("/timetable", response_model=List[ScheduleEntry])
async def cached_public_timetable(request: Request):
    snap = await _snapshot_or_http_error()
    return _blob_response(request, snap.all, snap)


@public_router.get("/timetable/program/{program}/{semester}", response_model=List[ScheduleEntry])
async def cached_public_timetable_by_program(program: str, semester: str, request: Request):
    snap = await _snapshot_or_http_error()
    key = (program.lower().replace(".", ""), semester.lower())
    return _blob_response(request, snap.by_program.get(key, snap.empty), snap)


@public_router.get("/timetable/teacher/{code}", response_model=List[ScheduleEntry])
async def cached_public_timetable_by_teacher(code: str, request: Request):
    snap = await _snapshot_or_http_error()
    return _blob_response(request, snap.by_teacher.get(code.lower(), snap.empty), snap)


@public_router.get("/meta")
async def cached_public_meta(request: Request):
    snap = await _snapshot_or_http_error()
    return _blob_response(request, snap.meta, snap)


@public_router.post("/chat", response_model=ChatResponse)
async def cached_public_chat(req: ChatRequest, request: Request):
    cid, ip = _client_ident(request)
    payload, status = await CHAT.answer(req.message, cid, ip)
    return JSONResponse(
        content=jsonable_encoder(payload),
        headers={"X-Cache": status, "Cache-Control": "no-store"},
    )


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def collect_stats() -> Dict[str, Any]:
    snap = STORE.peek()
    return {
        "uptime_seconds": int(time.time() - METRICS.started_at),
        "metrics": METRICS.snapshot(),
        "snapshot": None if snap is None else {
            "published_exists": snap.exists,
            "version_id": snap.version_id,
            "entries": snap.entry_count,
            "age_seconds": round(time.monotonic() - snap.built_at, 1),
            "all_bytes": len(snap.all.json),
            "all_gzip_bytes": len(snap.all.gz),
            "programs_indexed": len(snap.by_program),
            "teachers_indexed": len(snap.by_teacher),
        },
        "chat": CHAT.stats(),
        "schedule_context_cache": _CTX_CACHE.stats(),
        "config": asdict(CFG),
    }


@admin_router.get("/stats")
async def cache_stats(_: User = Depends(_require_admin)):
    return collect_stats()


@admin_router.post("/purge")
async def cache_purge(_: User = Depends(_require_admin)):
    purge_all_caches()
    return {"purged": True}


def purge_all_caches() -> None:
    """Admin / emergency: clear AI answers and force a snapshot rebuild."""
    CHAT_CACHE.clear()
    invalidate_timetable_data("purge")


# ============================================================================
# 8. INSTALLER
# ============================================================================
async def warmup() -> None:
    """Pre-build the snapshot and pre-import heavy modules so the first student is fast."""
    loop = asyncio.get_running_loop()
    try:
        await STORE.get()
        logger.info("[cache] timetable snapshot warmed")
    except Exception as exc:
        logger.warning(f"[cache] snapshot warm-up failed (will retry on demand): {exc}")
    try:
        await loop.run_in_executor(_CHAT_EXEC, _preload_scheduler_modules)
    except Exception as exc:
        logger.warning(f"[cache] module warm-up failed: {exc}")


def install_cache(app: FastAPI) -> None:
    """
    Call once, right after `app = FastAPI(...)` and BEFORE including api_router,
    so the cached routes are matched before the originals.
    """
    _install_sqlalchemy_hooks()
    app.include_router(public_router, prefix="/api/public", tags=["public (cached)"])
    app.include_router(admin_router, prefix="/api/admin/cache", tags=["cache-admin"])

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan(a):
        async with original_lifespan(a) as state:   # your table creation / seeding runs first
            await warmup()
            try:
                yield state
            finally:
                _DB_EXEC.shutdown(wait=False)
                _CHAT_EXEC.shutdown(wait=False)

    app.router.lifespan_context = _lifespan
    logger.info("[cache] cache layer installed")
