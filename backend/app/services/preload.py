r"""
app/services/preload.py
===========================================================================
Optimistic preloading for the Timetable admin / teacher dashboard
(Render free web service + Supabase Postgres + Vercel frontend)

IDEA
----
While the admin is still typing their e-mail / password on the login page,
the frontend fires a tiny, UNAUTHENTICATED "warm" request. The backend uses
that head start to:

  1. WAKE UP the Render free instance (cold start is the biggest delay),
  2. Warm the DB connection pool, the crypto stack (bcrypt / JWT) and the
     lazily imported modules, so the real login request is fast,
  3. Load the WHOLE dashboard data set into memory: version history, latest
     draft, published version and the full entry lists of the most recent
     versions. No account lookup is done: any well-formed e-mail triggers the
     load (the load is shared, so it runs at most once per cache lifetime).

Right after login the dashboard reads everything from RAM:

     GET /api/preload/bundle      one authenticated request, ETag/304, gzip
     (optional) shadow routes     your existing /versions, /versions/{id} and
                                  /timetable/draft endpoints served from RAM

SECURITY MODEL (read this)
--------------------------
* The password is NEVER sent or needed. Do not send it. The warm endpoint
  ignores unknown fields and never logs them.
* The warm endpoint returns the same body for every request
  (202 {"status": "accepted"}) and never returns timetable data. It does not
  look the e-mail up, so it cannot be used to discover which accounts exist.
  The e-mail is only a "someone is typing a login" signal: it is never stored,
  logged or used for anything else.
* Data is only ever returned by AUTHENTICATED endpoints (require_teacher_or_admin)
  with "Cache-Control: private" so no CDN / Vercel edge can store it.
* Abuse protection: per-IP throttle and a cap on pending warm tasks. Because
  the bundle is ONE shared cache entry (single-flight, soft TTL), an anonymous
  caller can trigger at most one rebuild per soft TTL, no matter how many
  requests or different e-mails are sent.
* Idle protection: nothing is refreshed in the background unless a warm
  request or an authenticated call happened recently, so an idle free-tier DB
  is left alone.

CORRECTNESS
-----------
* Invalidation is automatic: it plugs into cache_layer's SQLAlchemy commit
  hooks (publish / unpublish / edit / draft creation / bulk updates...).
  After a commit the next read waits for a fresh rebuild (never stale after
  a write); if someone was recently active the rebuild starts immediately
  (debounced) so it is usually finished before the next click.
* Out-of-band edits (Supabase SQL editor) are caught by a cheap fingerprint
  probe + a hard TTL.
* Stale-while-revalidate + stale-if-error + single-flight (coalesced) loads.
* Generation counter: data read across an invalidation is never published.

INTEGRATION (3 small edits)
---------------------------
A) Save this file as  app/services/preload.py
   (it builds on app/services/cache_layer.py, which you already have).

B) app/main.py, right after install_cache(app) and BEFORE include_router(...):

       from app.services.cache_layer import install_cache
       from app.services.preload import install_preload

       install_cache(app)
       install_preload(
           app,
           # OPTIONAL: serve your existing authenticated GETs from RAM too.
           # Use the FULL paths as mounted in your app (leave None to skip).
           versions_prefix="/api/versions",        # GET "", GET "/{id}"
           draft_path="/api/timetable/draft",      # GET draft
       )

   The shadow routes are registered before your routers, so they win; the
   original handlers stay in place as dead code and you can delete them later.
   If you are unsure about the prefixes, omit both arguments and use
   GET /api/preload/bundle only (frontend example below).

C) OPTIONAL, app/api/auth.py, inside login(), right after store_refresh_token:

       from app.services.preload import kick_preload
       kick_preload("post-login")     # belt and braces: bundle is hot at login

Render: nothing new. Keep `--workers 1` (one shared in-process cache).
CORS: the warm call is a normal JSON POST without credentials; the CORS
middleware you already have for Vercel is enough.

FRONTEND (Vercel) - login page
------------------------------
    const API = import.meta.env.VITE_API_URL;
    const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;
    let timer = null, lastEmail = "", lastAt = 0;

    export function warmBackend(email) {          // never include the password
      const e = (email || "").trim().toLowerCase();
      const valid = EMAIL_RE.test(e);
      if (valid && e === lastEmail && Date.now() - lastAt < 15000) return;
      if (valid) { lastEmail = e; lastAt = Date.now(); }
      fetch(`${API}/api/preload/warm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(valid ? { email: e } : {}),
        credentials: "omit",
        keepalive: true,
      }).catch(() => {});                          // best effort, ignore errors
    }

    // 1) page load: wakes Render + warms DB pool and crypto (no e-mail needed)
    useEffect(() => { warmBackend(""); }, []);

    // 2) while typing the e-mail (debounced), 3) when the password gets focus
    onEmailInput    = (v) => { clearTimeout(timer); timer = setTimeout(() => warmBackend(v), 500); };
    onPasswordFocus = ()  => warmBackend(emailValue);

    // 4) right after a successful login: ONE request, served from RAM
    export async function loadDashboard(accessToken) {
      const res = await fetch(`${API}/api/preload/bundle`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        credentials: "include",
      });
      if (!res.ok) throw new Error("bundle failed");
      return res.json();
      // { latest_version_id, published_version_id, published_same_as_latest,
      //   versions: [...], latest: {...VersionDetail}|null,
      //   published: {...VersionDetail}|null }
    }

TUNING (all optional environment variables; defaults are production-safe)
-------------------------------------------------------------------------
  PRELOAD_ENABLED=1               kill switch (0 = install_preload is a no-op)
  PRELOAD_SOFT_TTL=45             seconds before a background change-probe
  PRELOAD_HARD_TTL=300            seconds before a full rebuild is forced
  PRELOAD_DETAIL_COUNT=2          most recent versions whose full entry list
                                  is preloaded (latest + published always)
  PRELOAD_MAX_BUNDLE_BYTES=6000000  memory guard for the optional extra details
  PRELOAD_EXTRA_DETAIL_ENTRIES=8  LRU of other versions loaded on demand
  PRELOAD_EAGER_REBUILD=1         rebuild right after a commit if users are active
  PRELOAD_EAGER_DEBOUNCE=0.75     seconds to coalesce bursts of commits
  PRELOAD_ACTIVE_WINDOW=900       seconds a user counts as "recently active"
  PRELOAD_WARM_IP_PER_MIN=20      warm requests / minute / IP
  PRELOAD_MAX_PENDING=8           max concurrent background warm tasks
  PRELOAD_LIGHT_INTERVAL=30       min seconds between e-mail-less light warms
  PRELOAD_CRYPTO_AT_STARTUP=1     warm bcrypt / JWT in the background at boot
===========================================================================
"""
import asyncio
import gzip
import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from concurrent.futures import ThreadPoolExecutor

from app.api.auth import require_teacher_or_admin
from app.models.timetable import TimetableVersion
from app.models.timetable_entry import TimetableEntry
from app.models.user import User
from app.schemas.timetable import VersionDetail, VersionOut
from app.services import timetable_service

# Building blocks shared with the existing cache layer (same process, same
# metrics, same invalidation hooks, same /api/admin/cache/stats endpoint).
from app.services.cache_layer import (
    METRICS,
    STORE as PUBLIC_SNAPSHOT_STORE,
    KeyedThrottle,
    TTLCache,
    _db_session,
    _install_sqlalchemy_hooks,
    register_cache_hooks,
)

__all__ = [
    "install_preload",
    "kick_preload",
    "invalidate_preload",
    "PCFG",
]


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
        logger.warning(f"[preload] invalid value for {name}={raw!r}; using {default}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class PreloadConfig:
    enabled: bool = _env_bool("PRELOAD_ENABLED", True)

    soft_ttl: float = _env_num("PRELOAD_SOFT_TTL", 45.0)
    hard_ttl: float = _env_num("PRELOAD_HARD_TTL", 300.0)
    detail_count: int = _env_num("PRELOAD_DETAIL_COUNT", 2, int)
    max_bundle_bytes: int = _env_num("PRELOAD_MAX_BUNDLE_BYTES", 6_000_000, int)
    extra_detail_entries: int = _env_num("PRELOAD_EXTRA_DETAIL_ENTRIES", 8, int)

    eager_rebuild: bool = _env_bool("PRELOAD_EAGER_REBUILD", True)
    eager_debounce: float = _env_num("PRELOAD_EAGER_DEBOUNCE", 0.75)
    active_window: float = _env_num("PRELOAD_ACTIVE_WINDOW", 900.0)

    warm_ip_per_min: int = _env_num("PRELOAD_WARM_IP_PER_MIN", 20, int)
    max_pending: int = _env_num("PRELOAD_MAX_PENDING", 8, int)
    light_interval: float = _env_num("PRELOAD_LIGHT_INTERVAL", 30.0)
    crypto_at_startup: bool = _env_bool("PRELOAD_CRYPTO_AT_STARTUP", True)


PCFG = PreloadConfig()

# Own small pool: preload work can neither starve nor be starved by the
# framework's shared thread pool or cache_layer's executors.
_EXEC = ThreadPoolExecutor(max_workers=3, thread_name_prefix="preload")
_EXEC_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    """Return the preload pool, recreating it if a previous app lifespan shut
    it down (e.g. several TestClient(app) contexts in one process)."""
    global _EXEC
    if getattr(_EXEC, "_shutdown", False):
        with _EXEC_LOCK:
            if getattr(_EXEC, "_shutdown", False):
                _EXEC = ThreadPoolExecutor(max_workers=3, thread_name_prefix="preload")
    return _EXEC


def _task_usable(task: Optional["asyncio.Task"]) -> bool:
    """A pending task that belongs to the CURRENT event loop (never await a task
    created on a loop that has since been closed / replaced)."""
    if task is None or task.done():
        return False
    try:
        return task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        return False


# ============================================================================
# 2. SERIALISATION HELPERS (pre-serialised JSON + gzip + ETag)
# ============================================================================
@dataclass(frozen=True)
class PBlob:
    raw: bytes
    gz: bytes
    etag: str


def _blob_from_raw(raw: bytes) -> PBlob:
    etag = '"' + hashlib.sha1(raw).hexdigest()[:32] + '"'
    gz = gzip.compress(raw, compresslevel=6, mtime=0) if len(raw) >= 400 else b""
    return PBlob(raw, gz, etag)


def _blob_from_obj(obj: Any) -> PBlob:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _blob_from_raw(raw)


def _jb(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _dump(schema: Any, data: Any, from_attributes: bool = False) -> Any:
    """Validate through the response schema (exactly what FastAPI would do) and
    return JSON-safe primitives. Works with pydantic v1 and v2."""
    if hasattr(schema, "model_validate"):                       # pydantic v2
        obj = schema.model_validate(data, from_attributes=True) if from_attributes else schema.model_validate(data)
    else:                                                       # pydantic v1
        obj = schema.from_orm(data) if from_attributes else schema.parse_obj(data)
    return jsonable_encoder(obj)


def _safe_json(raw: Optional[str]) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("[preload] corrupt JSON column ignored")
        return {}


def _version_detail_dict(v: Any) -> Dict[str, Any]:
    """Same shape the GET /versions/{id} handler builds."""
    return {
        "id": v.id,
        "created_by": v.created_by,
        "parent_version_id": v.parent_version_id,
        "status": v.status,
        "change_summary": v.change_summary,
        "created_at": v.created_at,
        "published_at": v.published_at,
        "archived_at": v.archived_at,
        "entries": [e.to_dict() for e in v.entries],
        "validation_result": _safe_json(v.validation_result_json),
        "llm_output": _safe_json(v.llm_output_json),
    }


# ============================================================================
# 3. THE DASHBOARD BUNDLE
# ============================================================================
@dataclass(frozen=True)
class Bundle:
    fingerprint: tuple
    built_at: float                 # monotonic
    built_wall: str                 # ISO, informational (header only)
    latest_id: Optional[int]
    published_id: Optional[int]
    versions_count: int
    versions: PBlob                 # == GET versions list (VersionOut[])
    draft: Optional[PBlob]          # == GET draft (VersionOut of newest version)
    details: Dict[int, PBlob]       # == GET versions/{id} for preloaded ids
    combined: PBlob                 # == GET /api/preload/bundle
    total_bytes: int


def _probe(db) -> tuple:
    """Cheap change-fingerprint of everything the dashboard shows (3 tiny queries)."""
    cols = [func.count(TimetableVersion.id), func.max(TimetableVersion.id)]
    for name in ("published_at", "archived_at", "updated_at"):
        col = getattr(TimetableVersion, name, None)
        if col is not None:
            cols.append(func.max(col))
    head = db.query(*cols).one()
    latest_id = head[1]

    statuses = (
        db.query(TimetableVersion.status, func.count(TimetableVersion.id))
        .group_by(TimetableVersion.status)
        .all()
    )
    status_sig = tuple(sorted((str(getattr(s, "value", s)), int(n)) for s, n in statuses))

    entry_sig: tuple = ()
    if latest_id is not None:
        ecols = [func.count(TimetableEntry.id), func.max(TimetableEntry.id)]
        upd = getattr(TimetableEntry, "updated_at", None)
        if upd is not None:
            ecols.append(func.max(upd))
        row = db.query(*ecols).filter(TimetableEntry.version_id == latest_id).one()
        entry_sig = tuple(str(x) for x in row)

    return (tuple(str(x) for x in head), status_sig, entry_sig)


def _build_bundle(db, fingerprint: tuple, cfg: PreloadConfig) -> Bundle:
    t0 = time.monotonic()

    versions = list(timetable_service.list_versions(db))
    versions_blob = _blob_from_obj([_dump(VersionOut, v, from_attributes=True) for v in versions])

    latest = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
    published = timetable_service.get_published_version(db)
    latest_id = latest.id if latest is not None else None
    published_id = published.id if published is not None else None

    draft_blob = _blob_from_obj(_dump(VersionOut, latest, from_attributes=True)) if latest is not None else None

    # Which versions get their full entry list preloaded (latest + published
    # always; a few more recent ones while the memory budget allows).
    wanted: List[int] = []
    for vid in (latest_id, published_id):
        if vid is not None and vid not in wanted:
            wanted.append(vid)
    mandatory = len(wanted)
    for v in sorted(versions, key=lambda x: x.id, reverse=True)[: max(0, cfg.detail_count)]:
        if v.id not in wanted:
            wanted.append(v.id)

    details: Dict[int, PBlob] = {}
    total = len(versions_blob.raw) + (len(draft_blob.raw) if draft_blob else 0)
    for idx, vid in enumerate(wanted):
        if idx >= mandatory and total >= cfg.max_bundle_bytes:
            break
        if latest is not None and vid == latest_id:
            obj = latest
        elif published is not None and vid == published_id:
            obj = published
        else:
            obj = timetable_service.get_version(db, vid)
        if obj is None:
            continue
        blob = _blob_from_obj(_dump(VersionDetail, _version_detail_dict(obj)))
        if idx >= mandatory and total + len(blob.raw) > cfg.max_bundle_bytes:
            continue
        details[vid] = blob
        total += len(blob.raw)

    same = latest_id is not None and latest_id == published_id
    latest_raw = details[latest_id].raw if latest_id in details else b"null"
    pub_raw = (
        details[published_id].raw
        if (published_id is not None and not same and published_id in details)
        else b"null"
    )
    combined = _blob_from_raw(b"".join([
        b'{"latest_version_id":', _jb(latest_id),
        b',"published_version_id":', _jb(published_id),
        b',"published_same_as_latest":', _jb(bool(same)),
        b',"versions":', versions_blob.raw,
        b',"latest":', latest_raw,
        b',"published":', pub_raw,
        b"}",
    ]))

    logger.info(
        f"[preload] bundle built in {(time.monotonic() - t0) * 1000:.0f} ms: "
        f"{len(versions)} versions, {len(details)} detailed, "
        f"{(total + len(combined.raw)) / 1024:.0f} KB in RAM"
    )
    return Bundle(
        fingerprint=fingerprint,
        built_at=time.monotonic(),
        built_wall=datetime.now(timezone.utc).isoformat(),
        latest_id=latest_id,
        published_id=published_id,
        versions_count=len(versions),
        versions=versions_blob,
        draft=draft_blob,
        details=details,
        combined=combined,
        total_bytes=total + len(combined.raw),
    )


def _load_version_blob_sync(version_id: int) -> Optional[PBlob]:
    with _db_session() as db:
        v = timetable_service.get_version(db, version_id)
        if v is None:
            return None
        return _blob_from_obj(_dump(VersionDetail, _version_detail_dict(v)))


class BundleUnavailable(Exception):
    pass


# ============================================================================
# 4. BUNDLE STORE (stale-while-revalidate, single-flight, auto-invalidated)
# ============================================================================
class BundleStore:
    """
      fresh  (< soft TTL)        -> return instantly (zero DB queries)
      soft   (soft..hard TTL)    -> return instantly + background change-probe
      dirty / hard / empty       -> one shared reload (coalesced); callers wait
      reload fails               -> serve last good bundle (stale-if-error)
    """

    def __init__(self, cfg: PreloadConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()            # guards the fields below
        self._bundle: Optional[Bundle] = None
        self._checked_at = 0.0
        self._gen = 0
        self._dirty = False
        self._fail_until = 0.0
        self._last_activity = 0.0
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        # event-loop-only state
        self._load_task: Optional["asyncio.Task"] = None
        self._bg_task: Optional["asyncio.Task"] = None
        self._eager_handle: Optional[asyncio.TimerHandle] = None
        self._detail_flights: Dict[int, "asyncio.Task"] = {}
        self._extra = TTLCache(cfg.extra_detail_entries, "preload_detail_extra")

    # ---- thread-safe helpers ------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    @property
    def generation(self) -> int:
        with self._lock:
            return self._gen

    def peek(self) -> Optional[Bundle]:
        with self._lock:
            return self._bundle

    def is_dirty(self) -> bool:
        with self._lock:
            return self._dirty

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    def recently_active(self) -> bool:
        la = self._last_activity
        return la > 0 and (time.monotonic() - la) < self.cfg.active_window

    def invalidate(self, reason: str = "manual") -> None:
        """Called from SQLAlchemy commit hooks in worker threads."""
        with self._lock:
            self._gen += 1
            self._dirty = True
        self._extra.clear()
        METRICS.inc("preload_invalidations")
        loop = self.loop
        if self.cfg.eager_rebuild and loop is not None and self.recently_active():
            try:
                loop.call_soon_threadsafe(self._arm_eager)
            except RuntimeError:        # loop already closed (shutdown)
                pass

    def purge(self) -> None:
        self.invalidate("purge")

    # ---- eager rebuild (only while users are active) --------------------------
    def _arm_eager(self) -> None:
        if self._eager_handle is not None:
            return                      # a rebuild is already scheduled; coalesce
        self._eager_handle = asyncio.get_running_loop().call_later(
            self.cfg.eager_debounce, self._fire_eager
        )

    def _fire_eager(self) -> None:
        self._eager_handle = None
        if self.is_dirty() and self.recently_active():
            METRICS.inc("preload_eager_rebuilds")
            self._kick_background()

    def cancel_timers(self) -> None:
        if self._eager_handle is not None:
            self._eager_handle.cancel()
            self._eager_handle = None

    # ---- synchronous worker (runs in _EXEC) ---------------------------------
    def _sync_refresh(self) -> Bundle:
        last: Optional[Bundle] = None
        for _ in range(3):
            with self._lock:
                gen, cur, force = self._gen, self._bundle, self._dirty
            with _db_session() as db:
                fp = _probe(db)
                METRICS.inc("preload_probes")
                reusable = (
                    cur is not None
                    and not force
                    and cur.fingerprint == fp
                    and (time.monotonic() - cur.built_at) < self.cfg.hard_ttl
                )
                if reusable:
                    bundle = cur
                else:
                    bundle = _build_bundle(db, fp, self.cfg)
                    METRICS.inc("preload_builds")
            with self._lock:
                if self._gen == gen:     # nothing was committed while we were reading
                    self._bundle = bundle
                    self._checked_at = time.monotonic()
                    self._dirty = False
                    self._fail_until = 0.0
                    return bundle
            last = bundle                # a commit raced us -> read again
        with self._lock:                 # keep dirty=True: the next request reloads
            self._bundle = last
            self._checked_at = time.monotonic()
        return last  # type: ignore[return-value]

    # ---- async side -----------------------------------------------------------
    async def _refresh_shared(self) -> Bundle:
        task = self._load_task
        if not _task_usable(task):
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._run_refresh())
            task.add_done_callback(lambda t: (not t.cancelled()) and t.exception())
            self._load_task = task
        return await asyncio.shield(task)

    async def _run_refresh(self) -> Bundle:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor(), self._sync_refresh)

    def _kick_background(self) -> None:
        if _task_usable(self._bg_task):
            return
        self._bg_task = asyncio.get_running_loop().create_task(self._run_bg())

    async def _run_bg(self) -> None:
        try:
            await self._refresh_shared()
        except Exception as exc:
            logger.warning(f"[preload] background revalidation failed: {exc}")
            with self._lock:             # retry in ~10 s instead of hammering a sick DB
                self._checked_at = time.monotonic() - self.cfg.soft_ttl + 10.0

    async def get(self) -> Bundle:
        with self._lock:
            bundle, dirty, checked, fail_until = (
                self._bundle, self._dirty, self._checked_at, self._fail_until
            )
        now = time.monotonic()
        if bundle is not None and not dirty:
            age = now - checked
            if age < self.cfg.soft_ttl:
                METRICS.inc("preload_hits")
                return bundle
            if age < self.cfg.hard_ttl:
                METRICS.inc("preload_hits")
                self._kick_background()
                return bundle
        if now < fail_until:
            if bundle is not None:
                METRICS.inc("preload_stale_served_on_error")
                return bundle
            raise BundleUnavailable()
        METRICS.inc("preload_loads")
        try:
            return await self._refresh_shared()
        except Exception as exc:
            logger.error(f"[preload] bundle refresh failed: {exc}")
            with self._lock:
                self._fail_until = time.monotonic() + 5.0
            if bundle is not None:
                METRICS.inc("preload_stale_served_on_error")
                return bundle
            raise BundleUnavailable() from exc

    # ---- single version detail (read-through for ids outside the bundle) ---------
    async def detail(self, bundle: Bundle, version_id: int) -> Optional[PBlob]:
        blob = bundle.details.get(version_id)
        if blob is not None:
            return blob
        blob = self._extra.get(version_id)
        if blob is not None:
            return blob
        task = self._detail_flights.get(version_id)
        if task is not None and not task.done() and not _task_usable(task):
            task = None                                 # stale flight from an old event loop
        if task is None:
            gen = self.generation

            async def _load() -> Optional[PBlob]:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(_executor(), _load_version_blob_sync, version_id)
                if result is not None and self.generation == gen:   # never cache across a commit
                    self._extra.set(version_id, result, self.cfg.hard_ttl)
                return result

            task = asyncio.get_running_loop().create_task(_load())
            task.add_done_callback(lambda t: (not t.cancelled()) and t.exception())
            self._detail_flights[version_id] = task
            task.add_done_callback(lambda _t, v=version_id: self._detail_flights.pop(v, None))
        try:
            return await asyncio.shield(task)
        except Exception as exc:
            logger.error(f"[preload] version {version_id} load failed: {exc}")
            raise BundleUnavailable() from exc


BUNDLES = BundleStore(PCFG)


# ============================================================================
# 5. PRE-AUTH WARM COORDINATOR (abuse-safe, enumeration-safe)
# ============================================================================
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


def _normalize_email(raw: Optional[str]) -> Optional[str]:
    """Same normalisation the login endpoint applies (strip + lower)."""
    if not raw or not isinstance(raw, str):
        return None
    e = raw.strip().lower()
    if len(e) > 254 or not _EMAIL_RE.match(e):
        return None
    return e


def _select_one_sync() -> None:
    with _db_session() as db:
        db.execute(text("SELECT 1"))


def _warm_crypto_sync() -> None:
    """Pay the one-time lazy-init cost of bcrypt / passlib / jose now rather than
    during the first real login (this is slow on Render's tiny free CPU)."""
    t0 = time.monotonic()
    from app.core.security import (  # local import: same modules the auth router uses
        create_access_token, create_refresh_token, decode_token, hash_password,
    )
    hash_password(secrets.token_urlsafe(8))
    token = create_access_token("0", "warmup")
    decode_token(token)
    create_refresh_token("0")
    logger.info(f"[preload] crypto stack warmed in {(time.monotonic() - t0) * 1000:.0f} ms")


class WarmCoordinator:
    def __init__(self, cfg: PreloadConfig, store: BundleStore) -> None:
        self.cfg = cfg
        self.store = store
        self.ip_throttle = KeyedThrottle(5000)
        self._tasks: Set["asyncio.Task"] = set()
        self._crypto_started = False
        self._last_light = 0.0

    # ---- task plumbing --------------------------------------------------------
    def _spawn(self, coro: Awaitable[Any]) -> "asyncio.Task":
        task = asyncio.get_running_loop().create_task(coro)   # type: ignore[arg-type]
        self._tasks.add(task)

        def _done(t: "asyncio.Task") -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error(f"[preload] background task failed: {t.exception()!r}")

        task.add_done_callback(_done)
        return task

    async def shutdown(self) -> None:
        self.store.cancel_timers()
        for t in tuple(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- warm steps -------------------------------------------------------------
    async def _ensure_crypto(self) -> None:
        if self._crypto_started:
            return
        self._crypto_started = True          # set first: concurrent warms do not repeat it
        try:
            await asyncio.get_running_loop().run_in_executor(_executor(), _warm_crypto_sync)
        except Exception as exc:
            logger.warning(f"[preload] crypto warm-up skipped: {exc}")

    async def _light_warm(self) -> None:
        """No e-mail known yet (page just loaded): wake the instance, warm the DB
        pool and the crypto stack. Loads no data and needs no account lookup."""
        now = time.monotonic()
        if now - self._last_light < self.cfg.light_interval:
            METRICS.inc("preload_light_deduped")
            return
        self._last_light = now
        loop = asyncio.get_running_loop()
        await self._ensure_crypto()
        try:
            await loop.run_in_executor(_executor(), _select_one_sync)
            METRICS.inc("preload_light_warms")
        except Exception as exc:
            logger.warning(f"[preload] light warm: DB not reachable yet: {exc}")

    async def _warm_bundle(self) -> None:
        """Load the dashboard bundle. Shared + single-flight + TTL-guarded: if it
        is already fresh this returns instantly without touching the DB."""
        t0 = time.monotonic()
        self.store.touch()
        await self._ensure_crypto()
        try:
            await self.store.get()
            METRICS.inc("preload_warm_ok")
        except BundleUnavailable:
            METRICS.inc("preload_warm_failed")
            return
        try:                                                    # public snapshot, nearly free
            await PUBLIC_SNAPSHOT_STORE.get()
        except Exception:
            pass
        logger.info(f"[preload] warm finished in {(time.monotonic() - t0) * 1000:.0f} ms")

    async def _load_bundle_quiet(self, reason: str) -> None:
        try:
            await self.store.get()
            METRICS.inc("preload_kicks_ok")
        except BundleUnavailable:
            METRICS.inc("preload_kicks_failed")
        except Exception as exc:
            logger.warning(f"[preload] kick ({reason}) failed: {exc}")

    # ---- entry points -------------------------------------------------------------
    async def handle(self, email: Optional[str], ip: str) -> Tuple[int, int]:
        """Never awaits DB work: returns immediately. -> (http_status, retry_after)."""
        METRICS.inc("preload_warm_requests")
        ok, retry = self.ip_throttle.allow(ip, self.cfg.warm_ip_per_min)
        if not ok:
            METRICS.inc("preload_warm_throttled")
            return 429, retry

        if _normalize_email(email) is None:       # nothing typed yet: wake + light warm only
            self._spawn(self._light_warm())
            return 202, 0

        if len(self._tasks) >= self.cfg.max_pending:
            METRICS.inc("preload_warm_dropped")
            return 202, 0
        self._spawn(self._warm_bundle())          # e-mail is NOT stored, logged or looked up
        return 202, 0

    def kick_bundle(self, reason: str) -> None:
        """Event-loop only. Used by kick_preload() (e.g. right after a login)."""
        self.store.touch()
        self._spawn(self._load_bundle_quiet(reason))

    def spawn_startup_crypto(self) -> None:
        self._spawn(self._ensure_crypto())



WARM = WarmCoordinator(PCFG, BUNDLES)


def kick_preload(reason: str = "manual") -> None:
    """Thread-safe: start loading the dashboard bundle in the background.
    Call it from sync code, e.g. in the login handler after a successful login."""
    loop = BUNDLES.loop
    if loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(WARM.kick_bundle, reason)
    except RuntimeError:
        pass


def invalidate_preload(reason: str = "manual") -> None:
    """Manual invalidation (normally automatic through cache_layer's hooks)."""
    BUNDLES.invalidate(reason)


# ============================================================================
# 6. HTTP HELPERS
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


def _private_response(request: Request, blob: PBlob, bundle: Bundle) -> Response:
    """Authenticated data: browser may keep it but must revalidate (ETag -> 304);
    shared caches / CDNs / the Vercel edge must never store it."""
    headers = {
        "ETag": blob.etag,
        "Cache-Control": "private, no-cache",
        "Vary": "Authorization, Cookie, Accept-Encoding",
        "X-Preload-Generated-At": bundle.built_wall,
    }
    inm = request.headers.get("if-none-match")
    if inm and _etag_matches(inm, blob.etag):
        METRICS.inc("preload_http_304")
        return Response(status_code=304, headers=headers)
    if blob.gz and _GZIP_OK_RE.search(request.headers.get("accept-encoding", "")):
        headers["Content-Encoding"] = "gzip"
        return Response(content=blob.gz, media_type="application/json", headers=headers)
    return Response(content=blob.raw, media_type="application/json", headers=headers)


async def _bundle_or_503() -> Bundle:
    try:
        return await BUNDLES.get()
    except BundleUnavailable:
        raise HTTPException(status_code=503, detail="Timetable data temporarily unavailable. Please retry shortly.")


def _client_ip(request: Request) -> str:
    # Render terminates TLS in front of the app; the first X-Forwarded-For hop is
    # the client. A spoofed value cannot bypass the pending-task cap and the
    # single shared bundle (one rebuild per soft TTL), which protect the database.
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
    return ip or "unknown"


# ============================================================================
# 7. ROUTES
# ============================================================================
router = APIRouter()


class WarmRequest(BaseModel):
    email: Optional[str] = Field(default=None, max_length=320)
    # Unknown fields (e.g. a password sent by mistake) are ignored and never read.


@router.post("/warm", status_code=202)
async def preload_warm(request: Request, body: Optional[WarmRequest] = None):
    """PUBLIC. Fire-and-forget hint: "someone is about to log in".
    Identical answer for every e-mail; never returns data."""
    status, retry = await WARM.handle(body.email if body else None, _client_ip(request))
    if status == 429:
        return JSONResponse(
            status_code=429,
            content={"status": "throttled"},
            headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
        )
    return JSONResponse(
        status_code=202,
        content={"status": "accepted"},
        headers={"Cache-Control": "no-store"},
    )


@router.api_route("/ping", methods=["GET", "HEAD"], status_code=204)
async def preload_ping():
    """PUBLIC. Costs nothing: wakes the Render instance (also usable by uptime monitors)."""
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/bundle")
async def preload_bundle(request: Request, user: User = Depends(require_teacher_or_admin)):
    """AUTHENTICATED. Everything the dashboard needs after login, in one request."""
    BUNDLES.touch()
    bundle = await _bundle_or_503()
    return _private_response(request, bundle.combined, bundle)


def _register_shadow_routes(app: FastAPI, versions_prefix: Optional[str], draft_path: Optional[str]) -> None:
    """Serve the EXISTING authenticated GET endpoints from RAM (same JSON shape).
    Registered before your routers so they take priority."""

    if versions_prefix:
        base = "/" + versions_prefix.strip("/")

        async def versions_list_cached(request: Request, user: User = Depends(require_teacher_or_admin)):
            BUNDLES.touch()
            bundle = await _bundle_or_503()
            return _private_response(request, bundle.versions, bundle)

        async def version_detail_cached(
            version_id: int, request: Request, user: User = Depends(require_teacher_or_admin)
        ):
            BUNDLES.touch()
            bundle = await _bundle_or_503()
            try:
                blob = await BUNDLES.detail(bundle, version_id)
            except BundleUnavailable:
                raise HTTPException(status_code=503, detail="Timetable data temporarily unavailable. Please retry shortly.")
            if blob is None:
                raise HTTPException(status_code=404, detail="Version not found")
            return _private_response(request, blob, bundle)

        app.add_api_route(base, versions_list_cached, methods=["GET"], include_in_schema=False)
        app.add_api_route(base + "/{version_id:int}", version_detail_cached, methods=["GET"], include_in_schema=False)
        logger.info(f"[preload] shadow routes active: GET {base} and GET {base}/{{id}}")

    if draft_path:
        path = "/" + draft_path.strip("/")

        async def draft_cached(request: Request, user: User = Depends(require_teacher_or_admin)):
            BUNDLES.touch()
            bundle = await _bundle_or_503()
            if bundle.draft is None:
                raise HTTPException(status_code=404, detail="No drafts exist")
            return _private_response(request, bundle.draft, bundle)

        app.add_api_route(path, draft_cached, methods=["GET"], include_in_schema=False)
        logger.info(f"[preload] shadow route active: GET {path}")


# ============================================================================
# 8. STATS + INSTALLER
# ============================================================================
def collect_preload_stats() -> Dict[str, Any]:
    b = BUNDLES.peek()
    return {
        "bundle": None if b is None else {
            "version_id_latest": b.latest_id,
            "version_id_published": b.published_id,
            "versions": b.versions_count,
            "detailed_versions": sorted(b.details.keys()),
            "age_seconds": round(time.monotonic() - b.built_at, 1),
            "bytes_in_ram": b.total_bytes,
            "combined_gzip_bytes": len(b.combined.gz),
        },
        "dirty": BUNDLES.is_dirty(),
        "recently_active": BUNDLES.recently_active(),
        "pending_warm_tasks": len(WARM._tasks),
        "extra_detail_cache": BUNDLES._extra.stats(),
        "config": asdict(PCFG),
    }


def install_preload(
    app: FastAPI,
    *,
    versions_prefix: Optional[str] = None,
    draft_path: Optional[str] = None,
) -> None:
    """
    Call once, after install_cache(app) and BEFORE including your routers.

    versions_prefix: full mount path of the versions router (e.g. "/api/versions")
                     -> GET <prefix> and GET <prefix>/{id} are served from RAM.
    draft_path:      full path of the "current draft" endpoint
                     (e.g. "/api/timetable/draft") -> served from RAM.
    Both are optional; GET /api/preload/bundle works without them.
    """
    if not PCFG.enabled:
        logger.info("[preload] disabled via PRELOAD_ENABLED=0")
        return

    _install_sqlalchemy_hooks()                     # idempotent; auto-invalidation on commits
    register_cache_hooks(
        on_invalidate=BUNDLES.invalidate,
        on_purge=BUNDLES.purge,
        stats_name="preload",
        stats_fn=collect_preload_stats,
    )

    app.include_router(router, prefix="/api/preload", tags=["preload"])
    if versions_prefix or draft_path:
        _register_shadow_routes(app, versions_prefix, draft_path)

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan(a):
        async with original_lifespan(a) as state:   # your table creation / seeding / cache warm-up first
            BUNDLES.bind_loop(asyncio.get_running_loop())
            if PCFG.crypto_at_startup:
                WARM.spawn_startup_crypto()         # background: does not delay startup
            try:
                yield state
            finally:
                await WARM.shutdown()
                _EXEC.shutdown(wait=False)

    app.router.lifespan_context = _lifespan
    logger.info("[preload] optimistic preloading installed")
