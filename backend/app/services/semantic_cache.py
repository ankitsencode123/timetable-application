"""
app/services/semantic_cache.py
===========================================================================
SEMANTIC cache tier for the public timetable chatbot
(Render free web service + Supabase Postgres + Vercel frontend + Groq).

HOW THIS COMBINES WITH app/services/cache_layer.py
--------------------------------------------------
cache_layer stays the foundation and keeps doing everything it did before:
published-timetable snapshot + HTTP/ETag caching (/timetable*, /meta), the
teacher-chat schedule-context cache, SQLAlchemy auto-invalidation hooks, the
Groq guards and its /api/admin/cache endpoints. This module sits ON TOP of it
and owns POST /api/public/chat:

    question
      -> L1  exact-text alias      (cache_layer CHAT_CACHE + its key recipe:
                                    snapshot digest + catalog sig + IST date)
      -> L2a exact canonical hit   (slot-analysed form, this module)
      -> L2b semantic hit          (slot guard + residual guard + lexical cosine)
      -> L2c dense hit (optional)
      -> join an identical / equivalent in-flight call
      -> calendar handler -> Groq (guarded) -> store in L2 + alias in L1
      -> answer
    (if this engine ever raises, the request is answered by cache_layer's
     original ChatEngine instead of returning a 500)

What is SHARED with cache_layer (one copy, not two):
  * Timetable data: L2 derives its data epoch / lexicon / fallback rows from
    cache_layer.STORE's snapshot, so there is ONE database load, ONE change
    probe and ONE set of SQLAlchemy hooks for the whole app.
  * Groq protection: the same CircuitBreaker, requests-per-minute window,
    concurrency semaphore and per-client/IP throttle objects as cache_layer's
    ChatEngine, so LLM_RPM is a true app-wide budget.
  * Utilities (IST clock, DB session helper, payload helpers) are imported.
  * Admin: POST /api/admin/cache/purge also clears the semantic store, and
    GET /api/admin/cache/stats includes a "semantic" section.

L1 aliases only point at L2 entry ids and are validated on every hit, so an
epoch change, TTL expiry, admin eviction or downvote eviction in L2 can never
be bypassed through L1. L1 keys are case-preserving on purpose: cache_layer's
normalize_question() casefolds, which would merge e.g. "who teaches IT"
(subject code) with "who teaches it" (pronoun) -- the analyser treats those
differently, so L1 must not merge them either.

Kill switch: CHAT_ENGINE=legacy makes cache_layer's original public chat engine
serve /api/public/chat exactly as before (this module then installs nothing).

THE L2 PIPELINE IN DETAIL
-------------------------
    question -> analyse -> exact hit? -> semantic hit? -> join in-flight call?
             -> calendar handler -> Groq (guarded) -> store -> answer

WHY A NAIVE "EMBEDDING + COSINE" CACHE IS WRONG FOR A TIMETABLE BOT
-------------------------------------------------------------------
    "Who teaches CN on Monday?"   vs   "Who teaches CN on Tuesday?"

An embedding model scores these ~0.98 similar, yet the answers differ. The same
goes for teacher codes, rooms, semesters, programs, "free" vs "busy", "not",
"before" vs "after", and so on. A plain similarity threshold therefore serves
confidently WRONG answers. This design makes that impossible by construction:

  1. SLOT GUARD (hard gate). Every question is parsed into typed slots:
     day, program, semester, year, teacher, subject, room, time, number,
     intent (who/when/where/count/list/subjects) and operators (free, busy,
     not, all, before, after, practical, ...). Relative words are resolved
     ("today"/"tomorrow" -> real weekday in IST). Two questions can only match
     if their HARD slots are IDENTICAL. Lookup is bucketed by that signature,
     so a "Tuesday" question can never even see a "Monday" entry.
  2. RESIDUAL GUARD. Any word the analyser does not understand (a subject it
     has never seen, a name, a code) stays as a residual token. For a lexical
     match the residual sets must be equal. Unknown words therefore make the
     cache MISS rather than guess. Precision first; a miss only costs one
     Groq call.
  3. SIMILARITY. Inside a bucket, a hashed lexical vector over the canonical
     tokens (cosine >= SEM_T_LEX) must also agree. This ranks candidates and
     is a second safety net.
  4. OPTIONAL DENSE EMBEDDINGS (SEM_EMBED_URL, any OpenAI-compatible
     /embeddings endpoint). Only used inside the same hard-slot bucket, only on
     near misses, only with a very high threshold (SEM_T_DENSE). It may relax
     the residual guard for paraphrase words the lexicon has never seen. It
     never relaxes the slot guard. If the endpoint is slow or down, it is
     skipped.

Correctness scoping:
  * Every entry is tagged with a data EPOCH = sha256 of exactly what the LLM
    sees (published timetable table + faculty + rooms + internal teachers).
    A publish/edit changes the epoch, so stale answers can never be served;
    old-epoch entries are purged.
  * Questions using today/tomorrow/yesterday are scoped to the IST date.
  * "now / next / upcoming / ongoing" questions are volatile: cached only
    for ~45 s, never semantically.
  * Holiday / exam / event / calendar / date questions go to your
    calendar handler and are never semantically cached.
  * Prompt-injection-looking questions are answered but never cached.
  * "Not found" style answers are cached briefly and only by exact match.
  * Degraded (non-LLM) answers are cached for seconds only, in memory only.

Survives Render's sleep: cache entries are persisted to Postgres (table
`semantic_cache_entries`, created automatically, RLS enabled on Postgres) by a
write-behind thread and reloaded at boot for the current epoch. Persistence is
best effort: if the DB is unhappy the cache simply runs memory-only.

Protects Groq's free tier: single-flight (including paraphrases), global
requests-per-minute budget, bounded concurrency, circuit breaker, per-client /
per-IP throttle on uncached questions only, deterministic fallback answer built
from the cached timetable when Groq is busy or down.

Self-healing: GET header X-Cache-Entry + POST /api/public/chat/feedback lets
the UI report a bad answer; enough distinct downvotes evict it.

INTEGRATION (already wired in app/main.py)
------------------------------------------
    from app.services.semantic_cache import install_semantic_cache
    install_semantic_cache(app)          # FIRST: owns POST /api/public/chat
    from app.services.cache_layer import install_cache
    install_cache(app)                   # snapshot, /timetable*, /meta, hooks

   FastAPI matches the FIRST registered route, so install_semantic_cache must
   run before install_cache (a warning is logged if the order is wrong).
   It registers POST /api/public/chat (+ /chat/feedback) and admin routes under
   /api/admin/semcache. cache_layer's own /chat route stays registered behind
   it and serves again if CHAT_ENGINE=legacy.

   Frontend (Vercel): send a stable id header and expose nothing else:
       headers: {"Content-Type": "application/json", "X-Client-Id": id}
   Response headers (exposed via CORS): X-Cache, X-Cache-Entry, X-Cache-Score.
   X-Cache values: HIT-L1 | HIT-EXACT | HIT-SEMANTIC | HIT-DENSE | MISS |
   COALESCED | CALENDAR | FALLBACK | RATE_LIMITED | TIMEOUT | SKIP | ERROR |
   LEGACY-<status> (answered by cache_layer's engine after an internal error).

RENDER
------
  * ONE worker:  uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
  * Ping /api/public/meta (or any cheap route) every 5 min with UptimeRobot.
  * Use the Supabase pooled connection string.

ENV VARS (all optional, defaults are production-safe)
-----------------------------------------------------
  CHAT_ENGINE=semantic     "legacy" = serve public chat with cache_layer only
  SEM_ENABLED=1            kill switch (0 = no cache reads/writes, still coalesces)
  SEM_L1=1                 use cache_layer's CHAT_CACHE as an exact-text alias tier
  SEM_LEGACY_FALLBACK=1    answer via cache_layer's ChatEngine if this engine raises
  SEM_T_LEX=0.80           lexical cosine threshold inside a bucket
  SEM_T_DENSE=0.92         dense cosine threshold (only if SEM_EMBED_URL set)
  SEM_DENSE_FLOOR=0.35     min lexical cosine for a candidate to get a dense check
  SEM_TTL=86400            LLM answer TTL (s)
  SEM_NEG_TTL=600          "not found" answer TTL (s)
  SEM_VOLATILE_TTL=45      now/next questions (s)
  SEM_CAL_TTL=60           calendar answers (s)
  SEM_DEGRADED_TTL=20      non-LLM fallback answers (s)
  SEM_MAX_ENTRIES=4000     LRU capacity
  SEM_BUCKET_CAP=64        max entries per hard-slot bucket
  SEM_SCAN_LIMIT=200       max candidates scored per lookup
  SEM_MAX_CHARS=400        max question length
  SEM_PERSIST=1            persist entries to the DB
  SEM_PERSIST_FLUSH=5      seconds between write-behind flushes
  SEM_LOAD_LIMIT=4000      max entries reloaded at boot
  SEM_TABLE=semantic_cache_entries
  SEM_LLM_QUEUE_WAIT       max seconds a call may wait for an RPM slot (default LLM_QUEUE_WAIT)
  SEM_REQUEST_TIMEOUT      max seconds a student waits (default CHAT_REQUEST_TIMEOUT)
  SEM_FLIGHT_TIMEOUT       hard cap for one Groq flight (default CHAT_FLIGHT_TIMEOUT)
  SEM_CLIENT_MISS_PER_MIN  uncached questions/min per X-Client-Id (default CLIENT_MISS_PER_MIN)
  SEM_IP_MISS_PER_MIN      uncached questions/min per IP (default IP_MISS_PER_MIN)
  SEM_EMBED_URL=           e.g. https://generativelanguage.googleapis.com/v1beta/openai
  SEM_EMBED_KEY=           bearer key for that endpoint
  SEM_EMBED_MODEL=         e.g. text-embedding-004
  SEM_EMBED_TIMEOUT=3      seconds
  SEM_WARM=0               pre-answer likely questions after a publish
  SEM_WARM_MAX=40          max warm-up LLM calls per boot
  SEM_DOWNVOTE_EVICT=2     distinct downvotes that evict an entry

  Shared with cache_layer (configure them there, they apply to both tiers):
  LLM_RPM, LLM_CONCURRENCY, BREAKER_THRESHOLD, BREAKER_COOLDOWN (one Groq
  budget/breaker for the whole app) and TT_SOFT_TTL / TT_HARD_TTL (timetable
  change-probe / forced-rebuild intervals of the shared snapshot).
===========================================================================
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import threading
import time
import unicodedata
import zlib
from array import array
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from difflib import get_close_matches
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import Column, Float, Integer, LargeBinary, MetaData, String, Table, Text

from app.api.auth import get_current_user
from app.models.user import RoleEnum, User
from app.schemas.chat import ChatRequest, ChatResponse

# ---- the foundation layer: everything below is shared, not duplicated ----
from app.services import cache_layer as _cl
from app.services.cache_layer import (  # noqa: F401  (IST re-exported for callers)
    CFG as LEGACY_CFG,
    CHAT as LEGACY_CHAT,
    CHAT_CACHE as L1_CACHE,
    IST,
    STORE as SNAPSHOT_STORE,
    AsyncSlidingWindow,
    ChatEngine as LegacyChatEngine,
    CircuitBreaker,
    KeyedThrottle,
    Snapshot,
    SnapshotUnavailable,
    _catalog_signature,
    _db_session,
    _FALLBACK_GENERIC,
    _looks_rate_limited,
    _now_ist,
    _payload,
    _to_payload_dict as _to_payload,
    invalidate_timetable_data,
    purge_all_caches as legacy_purge_all_caches,
)

try:  # optional accelerator, never required (ortools already pulls numpy in)
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

__all__ = [
    "install_semantic_cache",
    "set_llm_function",
    "SemanticChatEngine",
    "ENGINE",
    "CFG",
    "ANALYZER",
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
        logger.warning(f"[semcache] invalid {name}={raw!r}; using {default}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class SemConfig:
    enabled: bool = _env_bool("SEM_ENABLED", True)
    l1_enabled: bool = _env_bool("SEM_L1", True)
    legacy_fallback: bool = _env_bool("SEM_LEGACY_FALLBACK", True)
    chat_engine: str = _env_str("CHAT_ENGINE", "semantic").lower()

    t_lex: float = _env_num("SEM_T_LEX", 0.80)
    t_dense: float = _env_num("SEM_T_DENSE", 0.92)
    dense_floor: float = _env_num("SEM_DENSE_FLOOR", 0.35)

    ttl: float = _env_num("SEM_TTL", 86400.0)
    neg_ttl: float = _env_num("SEM_NEG_TTL", 600.0)
    volatile_ttl: float = _env_num("SEM_VOLATILE_TTL", 45.0)
    calendar_ttl: float = _env_num("SEM_CAL_TTL", LEGACY_CFG.chat_calendar_ttl)
    degraded_ttl: float = _env_num("SEM_DEGRADED_TTL", LEGACY_CFG.chat_fallback_ttl)

    max_entries: int = _env_num("SEM_MAX_ENTRIES", 4000, int)
    bucket_cap: int = _env_num("SEM_BUCKET_CAP", 64, int)
    scan_limit: int = _env_num("SEM_SCAN_LIMIT", 200, int)
    max_chars: int = _env_num("SEM_MAX_CHARS", 400, int)

    persist: bool = _env_bool("SEM_PERSIST", True)
    persist_flush: float = _env_num("SEM_PERSIST_FLUSH", 5.0)
    load_limit: int = _env_num("SEM_LOAD_LIMIT", 4000, int)
    table: str = _env_str("SEM_TABLE", "semantic_cache_entries")

    data_soft_ttl: float = _env_num("SEM_DATA_SOFT_TTL", LEGACY_CFG.snapshot_soft_ttl)
    data_hard_ttl: float = _env_num("SEM_DATA_HARD_TTL", LEGACY_CFG.snapshot_hard_ttl)

    llm_rpm: int = _env_num("SEM_LLM_RPM", LEGACY_CFG.llm_rpm, int)
    llm_concurrency: int = _env_num("LLM_CONCURRENCY", LEGACY_CFG.llm_concurrency, int)
    llm_queue_wait: float = _env_num("SEM_LLM_QUEUE_WAIT", LEGACY_CFG.llm_queue_wait)
    request_timeout: float = _env_num("SEM_REQUEST_TIMEOUT", LEGACY_CFG.request_timeout)
    flight_timeout: float = _env_num("SEM_FLIGHT_TIMEOUT", LEGACY_CFG.flight_timeout)

    client_miss_per_min: int = _env_num("SEM_CLIENT_MISS_PER_MIN", LEGACY_CFG.client_miss_per_min, int)
    ip_miss_per_min: int = _env_num("SEM_IP_MISS_PER_MIN", LEGACY_CFG.ip_miss_per_min, int)
    breaker_threshold: int = _env_num("SEM_BREAKER_THRESHOLD", LEGACY_CFG.breaker_threshold, int)
    breaker_cooldown: float = _env_num("SEM_BREAKER_COOLDOWN", LEGACY_CFG.breaker_cooldown)

    embed_url: str = _env_str("SEM_EMBED_URL")
    embed_key: str = _env_str("SEM_EMBED_KEY")
    embed_model: str = _env_str("SEM_EMBED_MODEL")
    embed_timeout: float = _env_num("SEM_EMBED_TIMEOUT", 3.0)

    warm: bool = _env_bool("SEM_WARM", False)
    warm_max: int = _env_num("SEM_WARM_MAX", 40, int)
    downvote_evict: int = _env_num("SEM_DOWNVOTE_EVICT", 2, int)


CFG = SemConfig()

# Dedicated executors: never compete with the framework's shared thread pool.
_DB_EXEC = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sem-db")
_LLM_EXEC = ThreadPoolExecutor(max_workers=max(2, CFG.llm_concurrency) + 1, thread_name_prefix="sem-llm")
_AUX_EXEC = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sem-aux")


# ============================================================================
# 2. SMALL UTILITIES (KeyedThrottle, AsyncSlidingWindow, CircuitBreaker,
#    _looks_rate_limited, _db_session are imported from app.services.cache_layer)
# ============================================================================
class _Metrics:
    def __init__(self) -> None:
        self._c: Counter = Counter()
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.lookup_us_ewma = 0.0

    def inc(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._c[key] += n

    def observe_lookup(self, seconds: float) -> None:
        us = seconds * 1e6
        with self._lock:
            self.lookup_us_ewma = us if self.lookup_us_ewma == 0 else 0.95 * self.lookup_us_ewma + 0.05 * us

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            d = dict(self._c)
        hits = d.get("hit_exact", 0) + d.get("hit_semantic", 0) + d.get("hit_dense", 0)
        served_free = hits + d.get("coalesced", 0)
        total = d.get("requests", 0)
        d["llm_calls_saved"] = served_free
        d["free_ratio"] = round(served_free / total, 4) if total else None
        d["lookup_us_ewma"] = round(self.lookup_us_ewma, 1)
        return d


METRICS = _Metrics()


def _l1_key(text: str, epoch: str, now: datetime) -> str:
    """
    Case-safe L1 alias key built on cache_layer's normalization recipe +
    active data epoch + IST date. Preserves uppercase tokens (e.g. 'IT' vs 'it')
    so L1 never merges an acronym with a stopword.
    """
    norm_low = _cl.normalize_question(text)
    upper_tokens = " ".join(w for w in re.findall(r"[A-Za-z0-9]+", text or "") if any(c.isupper() for c in w))
    raw = "\x1f".join(("sem-l1", epoch, now.strftime("%Y-%m-%d"), norm_low, upper_tokens))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sha(*parts: str, n: int = 40) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:n]


def _sha256(*parts: str, n: int = 24) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:n]


# ============================================================================
# 3. QUERY ANALYSIS: lexicon, slot extraction, canonical form
# ============================================================================
_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_RES = [
    ("monday", re.compile(r"\bmon(?:day)?s?\b")),
    ("tuesday", re.compile(r"\btue(?:s|sday)?s?\b")),
    ("wednesday", re.compile(r"\bwed(?:nesday)?s?\b")),
    ("thursday", re.compile(r"\bthu(?:r|rs|rsday)?s?\b")),
    ("friday", re.compile(r"\bfri(?:day)?s?\b")),
    ("saturday", re.compile(r"\bsat(?:urday)?s?\b")),
    ("sunday", re.compile(r"\bsun(?:day)?s?\b")),
]
_REL_DAY_RES = [
    (re.compile(r"\bday\s+after\s+(?:tomorrow|tomorow|tommorow|tmrw|tmr)\b"), 2),
    (re.compile(r"\bday\s+before\s+yesterday\b"), -2),
    (re.compile(r"\b(?:tomorrow|tomorow|tommorow|tmrw|tmr)\b"), 1),
    (re.compile(r"\b(?:today|todays|2day|aaj|tonight)\b"), 0),
    (re.compile(r"\byesterday\b"), -1),
]

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_DOT_IN_WORD_RE = re.compile(r"(?<=[^\W\d_])\.(?=[^\W\d_])")  # b.tech -> btech
_PUNCT_RE = re.compile(r"[^\w\s#:\-]")
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-_][^\W_]+)*")

_ORD_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}
_ORD_ALT = "|".join(_ORD_WORDS)
_ROMAN_ALT = "|".join(sorted(_ROMAN, key=len, reverse=True))
_ROMAN_ALT_E = "|".join(r for r in sorted(_ROMAN, key=len, reverse=True) if r != "i")

_SEM_PATTERNS = [
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*-?\s*sem(?:ester)?s?\b"), lambda m: int(m.group(1))),
    (re.compile(r"\bsem(?:ester)?s?\s*[-:#]?\s*(\d{1,2})(?:st|nd|rd|th)?\b"), lambda m: int(m.group(1))),
    (re.compile(rf"\b({_ORD_ALT})\s+sem(?:ester)?s?\b"), lambda m: _ORD_WORDS[m.group(1)]),
    (re.compile(rf"\bsem(?:ester)?s?\s*[-:#]?\s*({_ROMAN_ALT})\b"), lambda m: _ROMAN[m.group(1)]),
    (re.compile(rf"\b({_ROMAN_ALT_E})\s*-?\s*sem(?:ester)?s?\b"), lambda m: _ROMAN[m.group(1)]),
]
_YEAR_PATTERNS = [
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*-?\s*year\b"),
    re.compile(r"\byear\s*[-:#]?\s*(\d{1,2})\b"),
]
_BARE_ORDINAL_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")

_TIME_HM_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b")
_TIME_H_RE = re.compile(r"\b(\d{1,2})\s*(am|pm)\b")
_TIME_OCLOCK_RE = re.compile(r"\b(\d{1,2})\s*oclock\b")

_ROOM_RE = re.compile(r"\b(?:room|rm|r)\s*[#:\-]?\s*(\d{2,3}[a-z]?)\b")
_ROOM_HASH_RE = re.compile(r"(?<![\w])#\s*(\d{2,3}[a-z]?)\b")

_STATIC_PROGRAMS = ("btech", "mtech", "msc", "bsc", "mca", "bca", "mba", "bba", "phd")

_INTENT_PHRASES = [
    (re.compile(r"\btime\s*-?\s*table\b"), "C:timetable"),
    (re.compile(r"\b(?:which|what)\s+(?:teacher|teachers|faculty|professor|prof|lecturer|instructor|sir|madam|mam)\b"), "INT:WHO"),
    (re.compile(r"\b(?:which|what)\s+(?:room|rooms|hall|classroom|venue|location|lab)\b"), "INT:WHERE"),
    (re.compile(r"\b(?:room|hall)\s*(?:number|no|num)\b"), "INT:WHERE"),
    (re.compile(r"\b(?:what|which)\s+(?:time|timing|timings|slot|period)s?\b"), "INT:WHEN"),
    (re.compile(r"\bat\s+what\s+time\b"), "INT:WHEN"),
    (re.compile(r"\b(?:what|which)\s+days?\b"), "INT:WHEN"),
    (re.compile(r"\bhow\s+many\b|\bnumber\s+of\b|\bno\s+of\b|\bcount\s+of\b"), "INT:COUNT"),
    (re.compile(r"\bfree\s+(?:slot|slots|period|periods|hour|hours|time)\b"), "OP:free"),
    (re.compile(r"\b(?:per|each|every)\s+day\b|\bdaily\b"), "OP:daily"),
]

# Word map. "" = filler (dropped). INT:/OP: are HARD (must match), C: is SOFT.
_WM: Dict[str, str] = {}


def _reg(slot: str, words: str) -> None:
    for w in words.split():
        _WM[w] = slot


_reg("", """a an the is are was were be been being am pm do does did doing done have has had having if so then than
    please pls plz kindly tell me us give show can could would will shall should may might must you your yours ur
    i im ive my mine we our he she they them his her their it its this that these those and or also too as of to in
    on at by for from with about into over under per any some there here how what which whats get gets getting got
    let see check find know look view open need want wanna like hey hi hii hello hlo ok okay thanks thank thx u
    list display provide share mention fetch say ask answer question info information detail details regarding
    related sure just right go going table kya hai hain h ka ki ke ko se par pe mein mai hoga hogi wala wali batao
    bata btao bta dikhao dikha de dena na aur plus will""")
_reg("INT:WHO", "who whom whose whos kaun kon")
_reg("INT:WHEN", "when whens kab timing timings")
_reg("INT:WHERE", "where wheres whereabouts venue location kahan kaha kidhar")
_reg("INT:COUNT", "count total kitne kitni kitna")
_reg("C:class", "class classes lecture lectures lec lecs session sessions period periods slot slots lesson lessons")
_reg("C:timetable", "timetable timetables time-table schedule schedules routine routines tt")
_reg("C:teacher", """teacher teachers faculty faculties professor professors prof profs lecturer lecturers instructor
    instructors sir madam mam maam staff incharge in-charge""")
_reg("C:teach", """teach teaches teaching taught take takes taking taken handle handles handling handled conduct
    conducts conducting conducted engage engages engaging""")
_reg("C:room", "room rooms classroom classrooms hall halls")
_reg("C:subject", "subject subjects course courses paper papers")
_reg("C:day", "day days")
_reg("C:week", "week weeks weekly")
_reg("C:sem", "sem sems semester semesters")
_reg("OP:practical", "practical practicals lab labs laboratory laboratories")
_reg("OP:theory", "theory theoretical")
_reg("OP:free", "free vacant empty unoccupied available availability")
_reg("OP:busy", "busy occupied engaged booked")
_reg("OP:clash", "clash clashes clashing conflict conflicts overlap overlaps overlapping collide")
_reg("OP:before", "before earlier prior")
_reg("OP:after", "after later post")
_reg("OP:until", "until till")
_reg("OP:between", "between")
_reg("OP:morning", "morning forenoon")
_reg("OP:afternoon", "afternoon")
_reg("OP:evening", "evening")
_reg("OP:lunch", "lunch recess")
_reg("OP:first", "first earliest")
_reg("OP:last", "last latest final")
_reg("OP:all", "all every each entire whole full complete everything")
_reg("OP:neg", "not no never none nobody except excluding exclude without isnt arent dont doesnt cant cannot wont didnt")
_reg("OP:only", "only")
_reg("OP:other", "other others another else")
_reg("OP:same", "same common both")
_reg("OP:diff", "different differ")
_reg("OP:most", "most maximum max busiest")
_reg("OP:least", "least minimum min")
_reg("OP:weekend", "weekend weekends")
_reg("OP:weekday", "weekday weekdays")
for _w in ("second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"):
    _WM[_w] = "OP:" + _w

_COMMON: FrozenSet[str] = frozenset(_WM)
_WM_LONG: Tuple[str, ...] = tuple(sorted(w for w, v in _WM.items() if v and len(w) >= 6 and w.isalpha()))

_HARD_PREFIXES = ("DAY:", "PROG:", "SEM:", "YEAR:", "TEA:", "SUB:", "ROOM:", "TIME:", "NUM:", "CODE:", "OP:", "INT:")
_HONORIFICS = frozenset({"dr", "prof", "mr", "mrs", "ms", "shri", "smt", "sir", "madam", "professor"})

_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|above|prior|earlier|instructions)"
    r"|disregard|system\s*prompt|you\s+are\s+now|act\s+as\b|pretend|jailbreak|developer\s+mode"
    r"|reveal\s+.{0,20}(?:prompt|instructions)|override|</?\s*system|\[\s*/?inst"
    r"|forget\s+(?:everything|all|your)|new\s+instructions"
)
_VOLATILE_RE = re.compile(
    r"\b(?:now|right\s+now|currently|ongoing|at\s+the\s+moment|next|upcoming|later|soon|tonight|remaining|"
    r"just\s+now|current(?:ly)?\s+(?:class|lecture|period|session|slot)|going\s+on|happening)\b"
)
_MONTHS = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_CALENDAR_RE = re.compile(
    r"\b(?:holiday|holidays|vacation|vacations|exam|exams|examination|examinations|event|events|fest|festival|"
    r"leave|calendar|academic|deadline|deadlines|result|results|datesheet|date\s*sheet|notice|notices|"
    r"cancel(?:l?ed)?|break|breaks|semester\s+end)\b"
    rf"|\b(?:{_MONTHS})\b|\bmay\s+\d|\b\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?\b"
)


def _norm_phrase(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold().replace("'", "").replace("\u2019", "")
    s = _DOT_IN_WORD_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def _stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _room_id(s: str) -> Optional[str]:
    m = re.search(r"(\d{2,3}[a-z]?)", (s or "").lower())
    return m.group(1) if m else None


def _sem_digits(s: str) -> Optional[str]:
    m = re.search(r"\d+", s or "")
    return str(int(m.group(0))) if m else None


class Lexicon:
    """Entities of the CURRENT published timetable (teachers, subjects, programs, rooms)."""

    def __init__(self) -> None:
        self.teachers: Dict[str, str] = {}          # lower -> slot id
        self.teacher_canon: Dict[str, str] = {}     # lower -> canonical-case text
        self.teacher_words: Dict[str, str] = {}     # unique alias word -> slot id
        self.subject_codes: Dict[str, str] = {}     # lower code -> slot id
        self.subject_words: Dict[str, str] = {}     # unique name word -> slot id
        self.phrase_rx: Optional["re.Pattern[str]"] = None
        self.phrase_slot: Dict[str, str] = {}       # normalised phrase -> "SUB:x"/"TEA:x"
        self.program_rxs: List[Tuple["re.Pattern[str]", str]] = []
        self.rooms: Set[str] = set()
        self.fuzzy_pool: Tuple[str, ...] = _WM_LONG
        self.signature = "empty"

    @staticmethod
    def build(rows: Sequence[dict], faculty_md: str, internal_teachers: Iterable[str]) -> "Lexicon":
        lex = Lexicon()
        vocab: Set[str] = set()

        # ---- teachers
        for t in internal_teachers or ():
            t = (t or "").strip()
            if t:
                lex.teachers[t.lower()] = t.lower()
                lex.teacher_canon[t.lower()] = t
        for r in rows:
            for part in re.split(r"\s*(?:[,/&;+]|\band\b)\s*", r.get("teacher") or ""):
                part = part.strip()
                if part:
                    lex.teachers.setdefault(part.lower(), part.lower())
                    lex.teacher_canon.setdefault(part.lower(), part)

        # ---- faculty aliases from the faculty master markdown (best effort)
        phrase_slot: Dict[str, str] = {}
        t_word_owner: Dict[str, Set[str]] = defaultdict(set)
        try:
            for line in (faculty_md or "").splitlines():
                if "|" not in line:
                    continue
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                short = next((c for c in cells if c.lower() in lex.teachers), None)
                if not short:
                    continue
                sid = lex.teachers[short.lower()]
                for c in cells:
                    if not c or c.lower() == short.lower() or "@" in c:
                        continue
                    p = _norm_phrase(c)
                    if re.search(r"\d", p) or not (4 <= len(p) <= 60):
                        continue
                    words = [w for w in p.split() if w not in _HONORIFICS]
                    if not words:
                        continue
                    if len(words) >= 2:
                        phrase_slot[" ".join(words)] = "TEA:" + sid
                    for w in words:
                        if len(w) >= 4:
                            t_word_owner[w].add(sid)
                            vocab.add(w)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[semcache] faculty alias parse failed: {exc}")
        for w, owners in t_word_owner.items():
            if len(owners) == 1 and w not in _COMMON:
                lex.teacher_words[w] = next(iter(owners))

        # ---- subjects
        s_word_owner: Dict[str, Set[str]] = defaultdict(set)
        programs: Set[str] = set(_STATIC_PROGRAMS)
        for r in rows:
            code = (r.get("subject_code") or "").strip().lower()
            name = _norm_phrase(r.get("subject_name") or "")
            if code:
                lex.subject_codes[code] = code
            if name and code:
                if len(name) >= 4 and name not in _COMMON:
                    phrase_slot.setdefault(name, "SUB:" + code)
                for w in name.split():
                    sw = _stem(w)
                    if len(sw) >= 4 and sw not in _COMMON:
                        s_word_owner[sw].add(code)
                        vocab.add(sw)
            p = _alnum(r.get("program") or "")
            if len(p) >= 3:
                programs.add(p)
            rid = _room_id(r.get("room") or "")
            if rid:
                lex.rooms.add(rid)
        for w, owners in s_word_owner.items():
            if len(owners) == 1:
                lex.subject_words[w] = "SUB:" + next(iter(owners))

        # ---- compiled phrase matcher (longest first)
        if phrase_slot:
            ordered = sorted(phrase_slot, key=len, reverse=True)
            lex.phrase_slot = phrase_slot
            lex.phrase_rx = re.compile(
                r"\b(?:" + "|".join(r"\s+".join(re.escape(w) for w in p.split()) for p in ordered) + r")\b"
            )

        # ---- programs (letters may be separated by spaces: "m tech")
        for p in sorted(programs, key=len, reverse=True):
            lex.program_rxs.append((re.compile(r"\b" + r"\s*".join(re.escape(c) for c in p) + r"\b"), p))

        lex.fuzzy_pool = tuple(sorted(set(_WM_LONG) | {v for v in vocab if len(v) >= 6}))[:1500]
        lex.signature = _sha256(
            repr(sorted(lex.teachers)), repr(sorted(lex.subject_codes)), repr(sorted(phrase_slot)),
            repr(sorted(programs)), repr(sorted(lex.rooms)),
        )
        return lex


EMPTY_LEXICON = Lexicon()


@dataclass(frozen=True)
class Profile:
    raw: str
    norm: str                       # canonical text (exact-cache key text)
    canon: Tuple[str, ...]          # canonical tokens (embedding input)
    hard: FrozenSet[str]            # slots that must be IDENTICAL
    hard_sig: str
    residual: FrozenSet[str]        # tokens the analyser could not interpret
    rel: bool                       # uses today/tomorrow/yesterday (date scoped)
    volatile: bool                  # now/next/upcoming (very short TTL)
    calendar_hint: bool             # holiday/exam/date: owned by calendar handler
    injection: bool
    empty: bool
    filters: Dict[str, FrozenSet[str]]
    dense_text: str

    @property
    def cacheable(self) -> bool:
        return not (self.injection or self.empty)

    @property
    def semantic_ok(self) -> bool:
        return self.cacheable and not (self.volatile or self.calendar_hint) and len(self.canon) <= 30


class QueryAnalyzer:
    """Free text -> Profile. Pure function of (text, now, lexicon)."""

    def analyze(self, text: str, now: datetime, lex: Lexicon = EMPTY_LEXICON) -> Profile:
        raw = _CTRL_RE.sub(" ", unicodedata.normalize("NFKC", text or ""))
        raw = _WS_RE.sub(" ", raw).strip()
        low = raw.casefold().replace("'", "").replace("\u2019", "")
        low = _DOT_IN_WORD_RE.sub("", low)
        low = _PUNCT_RE.sub(" ", low)
        low = " " + _WS_RE.sub(" ", low) + " "

        injection = bool(_INJECTION_RE.search(low))
        volatile = bool(_VOLATILE_RE.search(low))
        calendar_hint = bool(_CALENDAR_RE.search(low))
        rel = False

        hard: Set[str] = set()
        soft: List[str] = []
        residual: List[str] = []

        def take(rx: "re.Pattern[str]", s: str, make: Callable[["re.Match[str]"], Optional[str]]) -> str:
            def repl(m: "re.Match[str]") -> str:
                slot = make(m)
                if slot is None:
                    return m.group(0)
                hard.add(slot)
                return " "
            return rx.sub(repl, s)

        # ---- relative days -> real weekday (IST), then explicit days
        wd = now.weekday()
        for rx, off in _REL_DAY_RES:
            if rx.search(low):
                rel = True
            low = take(rx, low, lambda m, off=off: "DAY:" + _DAY_NAMES[(wd + off) % 7])
        for name, rx in _DAY_RES:
            low = take(rx, low, lambda m, name=name: "DAY:" + name)

        # ---- times
        def _tm(h: int, mi: int, ap: Optional[str]) -> Optional[str]:
            if ap == "pm" and h < 12:
                h += 12
            elif ap == "am" and h == 12:
                h = 0
            return f"TIME:{h:02d}:{mi:02d}" if h <= 23 and mi <= 59 else None

        low = take(_TIME_HM_RE, low, lambda m: _tm(int(m.group(1)), int(m.group(2)), m.group(3)))
        low = take(_TIME_H_RE, low, lambda m: _tm(int(m.group(1)), 0, m.group(2)))
        low = take(_TIME_OCLOCK_RE, low, lambda m: _tm(int(m.group(1)), 0, None))

        # ---- rooms
        low = take(_ROOM_RE, low, lambda m: "ROOM:" + m.group(1))
        low = take(_ROOM_HASH_RE, low, lambda m: "ROOM:" + m.group(1))

        # ---- semesters / years
        for rx, fn in _SEM_PATTERNS:
            low = take(rx, low, lambda m, fn=fn: f"SEM:{fn(m)}")
        for rx in _YEAR_PATTERNS:
            low = take(rx, low, lambda m: f"YEAR:{int(m.group(1))}")
        low = take(_BARE_ORDINAL_RE, low, lambda m: f"SEM:{int(m.group(1))}")

        # ---- programs
        for rx, p in lex.program_rxs or [(re.compile(r"\b" + r"\s*".join(map(re.escape, p)) + r"\b"), p)
                                         for p in _STATIC_PROGRAMS]:
            low = take(rx, low, lambda m, p=p: "PROG:" + p)

        # ---- multi-word entities (subject names, faculty full names)
        if lex.phrase_rx is not None:
            def _ph(m: "re.Match[str]") -> Optional[str]:
                return lex.phrase_slot.get(_WS_RE.sub(" ", m.group(0)))
            low = take(lex.phrase_rx, low, _ph)

        # ---- intent / operator phrases
        for rx, slot in _INTENT_PHRASES:
            if rx.search(low):
                low = rx.sub(" ", low)
                if slot.startswith("C:"):
                    soft.append(slot)
                else:
                    hard.add(slot)

        # ---- word level
        def raw_exact(canon: str) -> bool:
            return re.search(r"(?<![A-Za-z0-9])" + re.escape(canon) + r"(?![A-Za-z0-9])", raw) is not None

        def raw_upper(tok: str) -> bool:
            return re.search(r"(?<![A-Za-z0-9])" + re.escape(tok.upper()) + r"(?![A-Za-z0-9])", raw) is not None

        def classify(tok: str) -> bool:
            tid = lex.teachers.get(tok)
            if tid is not None:
                if tok in _COMMON or len(tok) <= 2:
                    if not raw_exact(lex.teacher_canon.get(tok, tok)):
                        residual.append("X:" + tok)
                        return True
                hard.add("TEA:" + tid)
                return True
            sid = lex.subject_codes.get(tok)
            if sid is not None:
                if tok in _COMMON and not raw_upper(tok):
                    residual.append("X:" + tok)
                    return True
                hard.add("SUB:" + sid)
                return True
            val = _WM.get(tok)
            if val is None:
                val = _WM.get(_stem(tok))
            if val is not None:
                if val:
                    (hard.add(val) if val.startswith(_HARD_PREFIXES) else soft.append(val))
                return True
            st = _stem(tok)
            slot = lex.subject_words.get(st)
            if slot:
                hard.add(slot)
                return True
            tw = lex.teacher_words.get(tok)
            if tw:
                hard.add("TEA:" + tw)
                return True
            return False

        for tok in _TOKEN_RE.findall(low):
            if tok.isdigit():
                if len(tok) <= 6:
                    hard.add("NUM:" + str(int(tok)))
                else:
                    hard.add("CODE:" + tok)
                continue
            if classify(tok):
                continue
            if len(tok) >= 6 and not any(c.isdigit() for c in tok):
                m = get_close_matches(_stem(tok), lex.fuzzy_pool, n=1, cutoff=0.84)
                if m and classify(m[0]):
                    continue
            st = _stem(tok)
            if any(c.isdigit() for c in st):
                hard.add("CODE:" + st)
            else:
                residual.append("X:" + st)

        # ---- canonical post-processing (collapse paraphrase variants)
        if "C:teacher" in soft:
            soft = [s for s in soft if s != "C:teacher"]
            hard.add("INT:WHO")
        if "INT:WHO" in hard:
            soft = [s for s in soft if s != "C:teach"]
        if "C:room" in soft:
            soft = [s for s in soft if s != "C:room"]
            hard.add("INT:WHERE")
        if "C:subject" in soft:
            soft = [s for s in soft if s != "C:subject"]
            hard.add("INT:SUBJECTS")
        if not any(s.startswith("INT:") for s in hard):
            hard.add("INT:LIST")

        soft_u = list(dict.fromkeys(soft))
        res_u = list(dict.fromkeys(residual))
        hard_sorted = sorted(hard)
        canon = tuple(hard_sorted + soft_u + res_u)
        substantive = [h for h in hard_sorted if h != "INT:LIST"]
        empty = not (substantive or soft_u or res_u)

        filters = {
            "days": frozenset(h[4:] for h in hard if h.startswith("DAY:")),
            "progs": frozenset(h[5:] for h in hard if h.startswith("PROG:")),
            "sems": frozenset(h[4:] for h in hard if h.startswith("SEM:")),
            "teachers": frozenset(h[4:] for h in hard if h.startswith("TEA:")),
            "subjects": frozenset(h[4:] for h in hard if h.startswith("SUB:")),
            "rooms": frozenset(h[5:] for h in hard if h.startswith("ROOM:")),
        }
        return Profile(
            raw=raw, norm=" ".join(canon), canon=canon, hard=frozenset(hard),
            hard_sig="|".join(hard_sorted), residual=frozenset(res_u), rel=rel, volatile=volatile,
            calendar_hint=calendar_hint, injection=injection, empty=empty, filters=filters,
            dense_text=low.strip()[:300],
        )


ANALYZER = QueryAnalyzer()


# ============================================================================
# 4. EMBEDDERS
# ============================================================================
SparseVec = Tuple["array[int]", "array[float]"]


class LexicalEmbedder:
    """Hashed sparse vector over canonical tokens: hard slots > residual > soft
    concepts, plus bigrams and char trigrams of residual words (typo tolerance).
    Pure Python, ~50 us per question, zero model download."""

    W_HARD, W_SOFT, W_RES, W_BI, W_CH = 2.0, 0.6, 1.0, 0.5, 0.25
    MASK = 0x3FFFFF

    def _h(self, s: str) -> int:
        return zlib.crc32(s.encode("utf-8")) & self.MASK

    def query_dict(self, canon: Sequence[str]) -> Dict[int, float]:
        feats: Dict[int, float] = defaultdict(float)
        prev = None
        for tok in canon:
            if tok.startswith(_HARD_PREFIXES):
                w = self.W_HARD
            elif tok.startswith("X:"):
                w = self.W_RES
            else:
                w = self.W_SOFT
            feats[self._h("u|" + tok)] += w
            if prev is not None:
                feats[self._h("b|" + prev + "|" + tok)] += self.W_BI
            prev = tok
            if tok.startswith("X:") and len(tok) >= 7:
                word = "#" + tok[2:] + "#"
                for i in range(len(word) - 2):
                    feats[self._h("c|" + word[i:i + 3])] += self.W_CH
        norm = sum(v * v for v in feats.values()) ** 0.5
        if norm == 0:
            return {}
        return {k: v / norm for k, v in feats.items()}

    @staticmethod
    def pack(d: Dict[int, float]) -> SparseVec:
        items = sorted(d.items())
        return array("I", [k for k, _ in items]), array("f", [v for _, v in items])


EMBED = LexicalEmbedder()


def _sparse_dot(qd: Dict[int, float], vec: Optional[SparseVec]) -> float:
    if not qd or vec is None:
        return 0.0
    idx, val = vec
    g = qd.get
    return float(sum(g(i, 0.0) * v for i, v in zip(idx, val)))


def _dict_dot(a: Optional[Dict[int, float]], b: Optional[Dict[int, float]]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return float(sum(v * b.get(k, 0.0) for k, v in a.items()))


def _dense_cos(a: "array[float]", b: "array[float]") -> float:
    if len(a) != len(b) or not len(a):
        return 0.0
    if np is not None:
        return float(np.dot(np.frombuffer(a, dtype=np.float32), np.frombuffer(b, dtype=np.float32)))
    return float(sum(x * y for x, y in zip(a, b)))


class DenseEmbedder:
    """Optional OpenAI-compatible /embeddings client. Always fails soft."""

    def __init__(self, cfg: SemConfig) -> None:
        self.url = cfg.embed_url.rstrip("/")
        self.key = cfg.embed_key
        self.model = cfg.embed_model
        self.timeout = cfg.embed_timeout
        self.breaker = CircuitBreaker(3, 60.0)
        self.calls = 0
        self.failures = 0

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.model)

    @property
    def tag(self) -> str:
        return f"{self.model}"

    def embed_sync(self, text: str) -> Optional["array[float]"]:
        if not self.enabled or not self.breaker.allow():
            return None
        try:
            import requests
            self.calls += 1
            r = requests.post(
                self.url + "/embeddings",
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
                json={"model": self.model, "input": text},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                raise RuntimeError(f"http {r.status_code}")
            vec = r.json()["data"][0]["embedding"]
            norm = sum(x * x for x in vec) ** 0.5
            if not norm:
                raise RuntimeError("zero vector")
            out = array("f", [x / norm for x in vec])
            self.breaker.success()
            return out
        except Exception as exc:
            self.failures += 1
            self.breaker.failure(hard=_looks_rate_limited(exc))
            logger.debug(f"[semcache] dense embed failed: {exc}")
            return None


# ============================================================================
# 5. ENTRY, STORE, PERSISTENCE
# ============================================================================
@dataclass
class Entry:
    id: str
    epoch: str
    scope: str
    bucket: str
    norm: str
    question: str
    residual: FrozenSet[str]
    vec: Optional[SparseVec]
    answer: Dict[str, Any]
    semantic: bool
    persist: bool
    created: float
    expires: float
    hits: int = 0
    last_hit: float = 0.0
    dense: Optional["array[float]"] = None
    dense_tag: str = ""


class SemanticStore:
    """Thread-safe LRU of entries + per-bucket index for semantic lookup."""

    def __init__(self, cfg: SemConfig, on_remove: Optional[Callable[[List[str]], None]] = None) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._entries: "OrderedDict[str, Entry]" = OrderedDict()
        self._buckets: Dict[str, "OrderedDict[str, None]"] = {}
        self._on_remove = on_remove
        self._ops = 0
        self.evictions = 0
        self.expired = 0

    # ---- internals (lock held)
    def _drop(self, eid: str) -> Optional[Entry]:
        e = self._entries.pop(eid, None)
        if e is not None:
            b = self._buckets.get(e.bucket)
            if b is not None:
                b.pop(eid, None)
                if not b:
                    self._buckets.pop(e.bucket, None)
        return e

    def _notify(self, ids: List[str]) -> None:
        if ids and self._on_remove:
            try:
                self._on_remove(ids)
            except Exception:  # pragma: no cover
                pass

    # ---- API
    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_exact(self, eid: str, now: float) -> Optional[Entry]:
        with self._lock:
            e = self._entries.get(eid)
            if e is None:
                return None
            if e.expires <= now:
                self._drop(eid)
                self.expired += 1
                return None
            return e

    def touch(self, e: Entry, now: float) -> None:
        with self._lock:
            if e.id in self._entries:
                self._entries.move_to_end(e.id)
            e.hits += 1
            e.last_hit = now

    def search(self, bucket: str, qd: Dict[int, float], now: float, top: int = 5) -> List[Tuple[float, Entry]]:
        with self._lock:
            ids = self._buckets.get(bucket)
            if not ids:
                return []
            out: List[Tuple[float, Entry]] = []
            scanned = 0
            for eid in reversed(ids):
                e = self._entries.get(eid)
                if e is None or e.expires <= now:
                    continue
                scanned += 1
                if scanned > self.cfg.scan_limit:
                    break
                out.append((_sparse_dot(qd, e.vec), e))
        out.sort(key=lambda x: -x[0])
        return out[:top]

    def put(self, e: Entry) -> None:
        removed: List[str] = []
        with self._lock:
            self._drop(e.id)
            self._entries[e.id] = e
            self._entries.move_to_end(e.id)
            if e.semantic:
                b = self._buckets.setdefault(e.bucket, OrderedDict())
                b[e.id] = None
                while len(b) > self.cfg.bucket_cap:
                    oldest = next(iter(b))
                    self._drop(oldest)
                    removed.append(oldest)
                    self.evictions += 1
            while len(self._entries) > self.cfg.max_entries:
                oldest = next(iter(self._entries))
                self._drop(oldest)
                removed.append(oldest)
                self.evictions += 1
            self._ops += 1
            sweep = self._ops % 200 == 0
        if sweep:
            removed += self.sweep(time.time(), notify=False)
        self._notify(removed)

    def sweep(self, now: float, notify: bool = True) -> List[str]:
        with self._lock:
            dead = [k for k, e in self._entries.items() if e.expires <= now]
            for k in dead:
                self._drop(k)
            self.expired += len(dead)
        if notify:
            self._notify(dead)
        return dead

    def remove(self, ids: Iterable[str]) -> int:
        n, gone = 0, []
        with self._lock:
            for i in ids:
                if self._drop(i) is not None:
                    n += 1
                    gone.append(i)
        self._notify(gone)
        return n

    def bucket_ids(self, bucket: str) -> List[str]:
        with self._lock:
            return list(self._buckets.get(bucket, ()))

    def purge_other_epochs(self, epoch: str) -> int:
        with self._lock:
            ids = [k for k, e in self._entries.items() if e.epoch != epoch]
            for k in ids:
                self._drop(k)
        return len(ids)

    def clear(self) -> int:
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
            self._buckets.clear()
        return n

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            sizes = [len(b) for b in self._buckets.values()]
            return {
                "entries": len(self._entries), "max_entries": self.cfg.max_entries,
                "buckets": len(self._buckets), "largest_bucket": max(sizes) if sizes else 0,
                "evictions": self.evictions, "expired": self.expired,
            }


_meta = MetaData()
_table = Table(
    CFG.table, _meta,
    Column("id", String(40), primary_key=True),
    Column("epoch", String(40), nullable=False, index=True),
    Column("scope", String(16), nullable=False, default=""),
    Column("bucket", String(40), nullable=False),
    Column("norm", Text, nullable=False),
    Column("question", Text, nullable=False, default=""),
    Column("residual", Text, nullable=False, default=""),
    Column("answer", Text, nullable=False),
    Column("dense", LargeBinary, nullable=True),
    Column("dense_tag", String(96), nullable=True),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("hits", Integer, nullable=False, default=0),
    Column("last_hit", Float, nullable=False, default=0.0),
)

_ENGINE_CACHE: Dict[str, Any] = {}


def _get_engine():
    if "e" in _ENGINE_CACHE:
        return _ENGINE_CACHE["e"]
    eng = None
    try:
        from app.core.database import engine as app_engine  # type: ignore
        eng = app_engine
    except Exception:
        pass
    if eng is None:
        from sqlalchemy import create_engine
        from app.core.config import get_settings
        url = get_settings().DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        kw: Dict[str, Any] = {"pool_pre_ping": True}
        if not url.startswith("sqlite"):
            kw.update(pool_size=1, max_overflow=0)
        eng = create_engine(url, **kw)
    _ENGINE_CACHE["e"] = eng
    return eng


def _entry_row(e: Entry) -> Dict[str, Any]:
    return {
        "id": e.id, "epoch": e.epoch, "scope": e.scope, "bucket": e.bucket, "norm": e.norm,
        "question": e.question[:400], "residual": " ".join(sorted(e.residual)),
        "answer": json.dumps(e.answer, ensure_ascii=False, separators=(",", ":")),
        "dense": e.dense.tobytes() if e.dense is not None else None,
        "dense_tag": e.dense_tag or None,
        "created_at": e.created, "expires_at": e.expires, "hits": e.hits, "last_hit": e.last_hit,
    }


class Persister:
    """Write-behind persistence. NEVER blocks or fails a request."""

    def __init__(self, cfg: SemConfig) -> None:
        self.cfg = cfg
        self.enabled = cfg.persist
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=5000)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._schema_ok = False
        self._fail_streak = 0
        self._backoff_until = 0.0
        self.written = 0
        self.dropped = 0
        self.errors = 0
        self.last_error = ""

    # ---- lifecycle
    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="sem-persist", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def ensure_schema(self) -> bool:
        if self._schema_ok:
            return True
        try:
            eng = _get_engine()
            _meta.create_all(eng, checkfirst=True)
            if eng.dialect.name == "postgresql":
                try:  # keep Supabase's public REST API away from this table
                    with eng.begin() as conn:
                        from sqlalchemy import text
                        conn.execute(text(f'ALTER TABLE "{CFG.table}" ENABLE ROW LEVEL SECURITY'))
                except Exception as exc:
                    logger.debug(f"[semcache] RLS enable skipped: {exc}")
            self._schema_ok = True
            return True
        except Exception as exc:
            self.errors += 1
            self.last_error = f"schema: {exc}"
            logger.warning(f"[semcache] persistence schema unavailable: {exc}")
            return False

    # ---- producers (called from any thread)
    def _put(self, item: tuple) -> None:
        if not self.enabled:
            return
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def submit_upsert(self, e: Entry) -> None:
        if e.persist:
            self._put(("up", _entry_row(e)))

    def submit_delete(self, ids: List[str]) -> None:
        self._put(("del", list(ids)))

    def submit_purge_other(self, epoch: str) -> None:
        self._put(("purge_other", epoch))

    def submit_purge_all(self) -> None:
        self._put(("purge_all", None))

    def submit_purge_expired(self) -> None:
        self._put(("purge_expired", None))

    # ---- consumer
    def _run(self) -> None:
        pending: List[tuple] = []
        last_flush = time.monotonic()
        while not self._stop.is_set():
            try:
                pending.append(self._q.get(timeout=1.0))
            except queue.Empty:
                pass
            due = time.monotonic() - last_flush >= self.cfg.persist_flush
            if pending and (due or len(pending) >= 100):
                if time.monotonic() >= self._backoff_until:
                    if self._flush(pending):
                        pending = []
                        self._fail_streak = 0
                    else:
                        self._fail_streak += 1
                        self._backoff_until = time.monotonic() + min(300.0, 5.0 * (2 ** self._fail_streak))
                        if self._fail_streak >= 3:
                            self.dropped += len(pending)
                            pending = []
                last_flush = time.monotonic()
        if pending:  # best-effort final flush
            self._flush(pending)

    def _flush(self, ops: List[tuple]) -> bool:
        if not self.ensure_schema():
            return False
        try:
            eng = _get_engine()
            ups: Dict[str, Dict[str, Any]] = {}
            with eng.begin() as conn:
                def flush_ups() -> None:
                    if ups:
                        conn.execute(_table.delete().where(_table.c.id.in_(list(ups))))
                        conn.execute(_table.insert(), list(ups.values()))
                        self.written += len(ups)
                        ups.clear()
                for kind, arg in ops:
                    if kind == "up":
                        ups[arg["id"]] = arg
                    elif kind == "del":
                        for k in arg:
                            ups.pop(k, None)
                        for i in range(0, len(arg), 500):
                            conn.execute(_table.delete().where(_table.c.id.in_(arg[i:i + 500])))
                    else:
                        flush_ups()
                        if kind == "purge_other":
                            conn.execute(_table.delete().where(_table.c.epoch != arg))
                        elif kind == "purge_all":
                            conn.execute(_table.delete())
                        elif kind == "purge_expired":
                            conn.execute(_table.delete().where(_table.c.expires_at < time.time()))
                flush_ups()
            return True
        except Exception as exc:
            self.errors += 1
            self.last_error = str(exc)[:300]
            logger.warning(f"[semcache] persistence flush failed: {exc}")
            self._schema_ok = False
            return False

    def load(self, epoch: str, limit: int) -> List[Dict[str, Any]]:
        """Blocking read (run in an executor)."""
        if not self.enabled or not self.ensure_schema():
            return []
        try:
            from sqlalchemy import select
            eng = _get_engine()
            now = time.time()
            stmt = (
                select(_table)
                .where(_table.c.epoch == epoch, _table.c.expires_at > now)
                .order_by(_table.c.last_hit.desc(), _table.c.created_at.desc())
                .limit(limit)
            )
            with eng.connect() as conn:
                return [dict(r._mapping) for r in conn.execute(stmt)]
        except Exception as exc:
            self.errors += 1
            self.last_error = f"load: {exc}"[:300]
            logger.warning(f"[semcache] persisted cache load failed: {exc}")
            return []

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled, "schema_ok": self._schema_ok, "queued": self._q.qsize(),
            "written": self.written, "dropped": self.dropped, "errors": self.errors,
            "last_error": self.last_error, "backing_off": time.monotonic() < self._backoff_until,
        }


# ============================================================================
# 6. DATA EPOCH (derived from cache_layer.STORE's Snapshot — zero extra DB load)
# ============================================================================
class DataUnavailable(Exception):
    pass


@dataclass(frozen=True)
class DataState:
    epoch: str
    version_id: Optional[int]
    fingerprint: tuple
    built_at: float
    rows: Tuple[dict, ...]
    prompt_table: str
    faculty_md: str
    room_md: str
    existing_md: str
    internal_teachers: str
    lexicon: Lexicon
    snapshot: Optional[Snapshot] = None


def _static_catalog() -> Tuple[str, str, str, str]:
    try:
        from app.scheduler.constraints import INTERNAL_TEACHERS
        from app.scheduler.data import EXISTING_TIMETABLE_MD, FACULTY_MASTER_MD, ROOM_EXAMPLE_MD
        return FACULTY_MASTER_MD, ROOM_EXAMPLE_MD, EXISTING_TIMETABLE_MD, ", ".join(sorted(INTERNAL_TEACHERS))
    except Exception as exc:
        logger.warning(f"[semcache] static catalog unavailable: {exc}")
        return "", "", "", ""


def _prep_dict_row(r: dict) -> dict:
    teacher = r.get("teacher") or ""
    tset = {p.strip().lower() for p in re.split(r"\s*(?:[,/&;+]|\band\b)\s*", teacher) if p.strip()}
    day = r.get("day") or ""
    prog = r.get("program") or ""
    sem = r.get("semester") or ""
    code = r.get("subject_code") or ""
    room = r.get("room") or ""
    return {
        "day": day, "program": prog, "semester": sem,
        "start_time": r.get("start_time") or "", "end_time": r.get("end_time") or "", "teacher": teacher,
        "subject_name": r.get("subject_name") or "", "subject_code": code,
        "room": room, "entry_type": r.get("entry_type") or "",
        "_d3": day.strip().lower()[:3], "_prog": _alnum(prog),
        "_sem": _sem_digits(sem), "_tset": tset,
        "_code": code.strip().lower(), "_room": _room_id(room),
    }


def _prep_row(e) -> dict:
    return _prep_dict_row({
        "day": e.day or "", "program": e.program or "", "semester": e.semester or "",
        "start_time": e.start_time or "", "end_time": e.end_time or "", "teacher": e.teacher or "",
        "subject_name": e.subject_name or "", "subject_code": e.subject_code or "",
        "room": e.room or "", "entry_type": getattr(e, "entry_type", "") or "",
    })


def _state_from_snapshot(snap: Snapshot) -> DataState:
    faculty_md, room_md, existing_md, internal = _static_catalog()
    rows = [_prep_dict_row(r) for r in snap.rows]
    prompt_table = snap.prompt_table if snap.exists else ""
    internal_names = [t.strip() for t in internal.split(",") if t.strip()]
    lexicon = Lexicon.build(rows, faculty_md, internal_names)
    # Epoch = exactly what the LLM can see (+ lexicon, which changes slot semantics).
    epoch = _sha256(prompt_table or existing_md, faculty_md, room_md, internal, lexicon.signature)
    return DataState(
        epoch=epoch, version_id=snap.version_id, fingerprint=snap.fingerprint, built_at=snap.built_at,
        rows=tuple(rows), prompt_table=prompt_table, faculty_md=faculty_md, room_md=room_md,
        existing_md=existing_md, internal_teachers=internal, lexicon=lexicon, snapshot=snap,
    )


class DataProvider:
    """
    Derives and caches DataState on top of cache_layer.STORE (SnapshotStore).
    Because both layers share ONE SnapshotStore:
      * A database load or background probe happens ONCE for both /timetable* and /chat.
      * SQLAlchemy auto-invalidation in cache_layer invalidates both layers at once.
    """

    def __init__(self, cfg: SemConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._state: Optional[DataState] = None
        self._source_key: Optional[tuple] = None
        self._gen = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._gen + SNAPSHOT_STORE.generation

    def peek(self) -> Optional[DataState]:
        with self._lock:
            return self._state

    def _on_shared_invalidate(self, _reason: str = "") -> None:
        with self._lock:
            self._gen += 1
            self._source_key = None

    def invalidate(self) -> None:  # thread-safe
        self._on_shared_invalidate("semcache")
        SNAPSHOT_STORE.invalidate()

    async def get(self) -> DataState:
        try:
            snap = await SNAPSHOT_STORE.get()
        except SnapshotUnavailable as exc:
            with self._lock:
                st = self._state
            if st is not None:
                METRICS.inc("data_stale_served")
                return st
            raise DataUnavailable() from exc

        skey = (id(snap), snap.fingerprint, _catalog_signature())
        with self._lock:
            if self._state is not None and self._source_key == skey:
                return self._state

        st = _state_from_snapshot(snap)
        METRICS.inc("data_builds")
        with self._lock:
            self._state = st
            self._source_key = skey
        return st


def _install_sqlalchemy_hooks(provider: DataProvider) -> None:
    """Delegate SQLAlchemy session hooks to cache_layer so only one listener set is active."""
    _cl._install_sqlalchemy_hooks()
    _cl.register_cache_hooks(on_invalidate=provider._on_shared_invalidate)


# ============================================================================
# 7. LLM BRIDGE + DETERMINISTIC FALLBACK
# ============================================================================
def _build_system_prompt(state: Optional[DataState], now: datetime) -> str:
    if state is None:
        faculty, rooms, existing, internal = _static_catalog()
        live = ""
    else:
        faculty, rooms, existing, internal = state.faculty_md, state.room_md, state.existing_md, state.internal_teachers
        live = state.prompt_table
    timetable_context = live or existing
    current_date = now.strftime("%A, %B %d, %Y")
    return f"""You are a helpful timetable assistant for a university.
Answer ONLY from the timetable data provided below. Be concise and precise.
If you cannot find the information, say so clearly.

CURRENT DATE:
Today is {current_date}. Use this to understand questions about "today" or "tomorrow".

TIMETABLE:
{timetable_context}

FACULTY:
{faculty}
Note: The following short names correspond to INTERNAL teachers: {internal}. Any others are visiting/external.


ROOMS:
{rooms}

Rules:
- Only answer questions about the timetable (schedules, rooms, teachers, subjects, programs, semesters, days, times).
- Do NOT make up any information not in the data.
- When referencing teacher short names, use the faculty master to resolve full names if helpful.
- Format answers clearly, use bullet points or tables when listing multiple classes.
"""


def default_llm_function(state: Optional[DataState], message: str, now: datetime) -> str:
    """Blocking Groq call (runs in an executor). Swap with set_llm_function()."""
    from app.scheduler.llm import call_groq
    messages = [
        {"role": "system", "content": _build_system_prompt(state, now)},
        {"role": "user", "content": message},
    ]
    content, _ = call_groq(messages, expect_json=False)
    return content


_DAY_ORDER = {d[:3]: i for i, d in enumerate(_DAY_NAMES)}


def _deterministic_answer(state: Optional[DataState], prof: Profile, raw_msg: str = "", now: Optional[datetime] = None) -> Optional[str]:
    """
    Combined LLM-free lookup:
      1) Slot-based filter from QueryAnalyzer (days, programs, semesters, teachers, subject codes, rooms)
      2) Falls back to cache_layer._fallback_answer (which also matches full subject_name substrings)
    Returns None if the question is too vague for both.
    """
    if state is not None and state.rows:
        f = prof.filters
        if any(f.values()):
            rows = state.rows
            if f["days"]:
                d3 = {d[:3] for d in f["days"]}
                rows = tuple(r for r in rows if r["_d3"] in d3)
            if f["progs"]:
                rows = tuple(r for r in rows if r["_prog"] in f["progs"])
            if f["sems"]:
                rows = tuple(r for r in rows if r["_sem"] in f["sems"])
            if f["teachers"]:
                rows = tuple(r for r in rows if r["_tset"] & f["teachers"])
            if f["subjects"]:
                rows = tuple(r for r in rows if r["_code"] in f["subjects"])
            if f["rooms"]:
                rows = tuple(r for r in rows if r["_room"] in f["rooms"])
            if not rows:
                return "I couldn't find any classes matching that in the published timetable."
            ordered = sorted(rows, key=lambda r: (_DAY_ORDER.get(r["_d3"], 9), r["start_time"]))
            limit = 40
            out = [
                "The AI assistant is busy right now, so here are the matching entries straight from the published timetable:",
                "", "| Day | Time | Program / Sem | Subject | Teacher | Room |", "|---|---|---|---|---|---|",
            ]
            for r in ordered[:limit]:
                out.append(
                    f"| {r['day']} | {r['start_time']}\u2013{r['end_time']} | {r['program']} {r['semester']} | "
                    f"{r['subject_name']} | {r['teacher']} | {r['room']} |"
                )
            if len(ordered) > limit:
                out.append(f"\n(Showing the first {limit} of {len(ordered)} matches \u2014 narrow the question for the rest.)")
            return "\n".join(out)

    if state is not None and state.snapshot is not None and raw_msg:
        return _cl._fallback_answer(state.snapshot, raw_msg, now or _now_ist())
    return None


def _calendar_sync(message: str) -> Optional[Dict[str, Any]]:
    try:
        from app.calendar_chat import try_handle_calendar_question  # type: ignore
    except Exception:
        return None
    with _db_session() as db:
        r = try_handle_calendar_question(message, db)
    return None if r is None else _to_payload(r)


_NEGATIVE_RE = re.compile(
    r"(?:couldn'?t|could not|can'?t|cannot|unable to|don'?t|do not)\s+(?:find|have|see)|no (?:information|data|classes|such)"
    r"|not (?:found|available|in the (?:data|timetable))|isn'?t (?:in|available)|no matching"
)
_ERRORISH_RE = re.compile(r"couldn'?t answer that right now|rate limit|internal error|try again in a (?:minute|moment)")


# ============================================================================
# 8. THE ENGINE
# ============================================================================
@dataclass
class ChatResult:
    payload: Dict[str, Any]
    status: str
    entry_id: Optional[str] = None
    score: Optional[float] = None


@dataclass
class _Ctx:
    msg: str
    prof: Profile
    state: Optional[DataState]
    epoch: str
    scope: str
    bucket: str
    eid: str
    now: datetime
    qd: Optional[Dict[int, float]]
    internal: bool = False
    qdense: Optional["array[float]"] = None


@dataclass
class _Flight:
    key: str
    bucket: str
    residual: FrozenSet[str]
    qd: Optional[Dict[int, float]]
    semantic_ok: bool
    task: "asyncio.Task" = None  # type: ignore[assignment]


class SemanticChatEngine:
    def __init__(self, cfg: SemConfig) -> None:
        self.cfg = cfg
        self.data = DataProvider(cfg)
        self.persister = Persister(cfg)
        self.store = SemanticStore(cfg, on_remove=self.persister.submit_delete if cfg.persist else None)
        self.dense = DenseEmbedder(cfg)
        # Share the app-wide Groq guards with cache_layer when using the shared config
        if (
            cfg.breaker_threshold == LEGACY_CFG.breaker_threshold
            and cfg.breaker_cooldown == LEGACY_CFG.breaker_cooldown
        ):
            self.breaker = LEGACY_CHAT.breaker
        else:
            self.breaker = CircuitBreaker(cfg.breaker_threshold, cfg.breaker_cooldown)
        if cfg.llm_rpm == LEGACY_CFG.llm_rpm:
            self.rpm = LEGACY_CHAT.rpm
        else:
            self.rpm = AsyncSlidingWindow(cfg.llm_rpm, 60.0)
        self.throttle = LEGACY_CHAT.throttle if cfg is CFG else KeyedThrottle()
        self.llm_fn: Callable[[Optional[DataState], str, datetime], str] = default_llm_function
        self._sem: Optional[asyncio.Semaphore] = None
        self._flights: Dict[str, _Flight] = {}
        self._by_bucket: Dict[str, List[_Flight]] = defaultdict(list)
        self._active_epoch: Optional[str] = None
        self._load_task: Optional["asyncio.Task"] = None
        self._bg: Set["asyncio.Task"] = set()
        self._downvotes: "OrderedDict[str, Set[str]]" = OrderedDict()
        self._fb_throttle = KeyedThrottle(5000)

    # ------------------------------------------------------------------ helpers
    def _semaphore(self) -> asyncio.Semaphore:
        if self.cfg.llm_concurrency == LEGACY_CFG.llm_concurrency:
            return LEGACY_CHAT._semaphore()
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.cfg.llm_concurrency))
        return self._sem

    def _spawn(self, coro) -> "asyncio.Task":
        t = asyncio.get_running_loop().create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    async def _state(self) -> Optional[DataState]:
        try:
            st = await self.data.get()
        except DataUnavailable:
            return None
        if st.epoch != self._active_epoch:
            self._activate(st.epoch)
        return st

    def _activate(self, epoch: str) -> None:
        """Called (event loop) when the data epoch changes or at boot."""
        first = self._active_epoch is None
        self._active_epoch = epoch
        removed = self.store.purge_other_epochs(epoch)
        if not first and self.cfg.l1_enabled:
            L1_CACHE.clear()
        if removed:
            METRICS.inc("epoch_purged", removed)
        if self.cfg.persist:
            self.persister.submit_purge_other(epoch)
            self._load_task = self._spawn(self._load_persisted(epoch))
        logger.info(f"[semcache] epoch {'initialised' if first else 'changed'} -> {epoch} (purged {removed})")

    async def _load_persisted(self, epoch: str) -> None:
        try:
            rows = await asyncio.get_running_loop().run_in_executor(
                _DB_EXEC, self.persister.load, epoch, self.cfg.load_limit
            )
        except Exception as exc:
            logger.warning(f"[semcache] load failed: {exc}")
            return
        today = _now_ist().strftime("%Y-%m-%d")
        now, n = time.time(), 0
        for r in rows:
            try:
                if r["epoch"] != epoch or r["expires_at"] <= now:
                    continue
                scope = r.get("scope") or ""
                if len(scope) == 16 or (len(scope) == 10 and scope != today):
                    continue  # volatile / stale-date entries are never reusable
                if self._active_epoch != epoch:
                    return  # epoch moved on while we were loading
                canon = r["norm"].split()
                dense = None
                if r.get("dense"):
                    dense = array("f")
                    dense.frombytes(bytes(r["dense"]))
                e = Entry(
                    id=r["id"], epoch=epoch, scope=scope, bucket=r["bucket"], norm=r["norm"],
                    question=r.get("question") or "",
                    residual=frozenset(x for x in (r.get("residual") or "").split() if x),
                    vec=EMBED.pack(EMBED.query_dict(canon)), answer=json.loads(r["answer"]),
                    semantic=True, persist=True, created=r["created_at"], expires=r["expires_at"],
                    hits=int(r.get("hits") or 0), last_hit=float(r.get("last_hit") or 0.0),
                    dense=dense, dense_tag=r.get("dense_tag") or "",
                )
                if self.store.get_exact(e.id, now) is None:
                    self.store.put(e)
                    n += 1
            except Exception as exc:
                logger.debug(f"[semcache] skipped corrupt persisted row: {exc}")
        METRICS.inc("persisted_loaded", n)
        logger.info(f"[semcache] reloaded {n} persisted entries for epoch {epoch}")

    def _check_throttle(self, client_id: Optional[str], ip: str) -> Tuple[bool, int]:
        ok, retry = self.throttle.allow("ip:" + ip, self.cfg.ip_miss_per_min)
        if not ok:
            return False, retry
        if client_id:
            ok, retry = self.throttle.allow("cid:" + client_id, self.cfg.client_miss_per_min)
            if not ok:
                return False, retry
        return True, 0

    @staticmethod
    def _scope(prof: Profile, now: datetime) -> str:
        if prof.volatile:
            return now.strftime("%Y-%m-%dT%H:%M")
        if prof.rel:
            return now.strftime("%Y-%m-%d")
        return ""

    def _ttl(self, kind: str, scope: str, now: datetime) -> float:
        base = {
            "llm": self.cfg.ttl, "neg": self.cfg.neg_ttl, "calendar": self.cfg.calendar_ttl,
            "degraded": self.cfg.degraded_ttl,
        }[kind]
        if kind == "llm" and scope and len(scope) == 16:
            base = self.cfg.volatile_ttl
        elif scope and len(scope) == 10:  # date-scoped: never outlive IST midnight
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            base = min(base, max(30.0, (midnight - now).total_seconds()))
        return base

    def _bind_l1(self, msg: str, epoch: str, now: datetime, e: Entry, status: str, score: float) -> None:
        """Bind the normalized question in cache_layer's L1 TTLCache to the L2 Entry id."""
        if not (self.cfg.enabled and self.cfg.l1_enabled):
            return
        ttl = max(1.0, e.expires - time.time())
        L1_CACHE.set(_l1_key(msg, epoch, now), ("sem_ref", e.id, status, round(score, 4)), ttl)

    # ------------------------------------------------------------------ storing
    def _store(self, ctx: _Ctx, payload: Dict[str, Any], kind: str, semantic: bool) -> Optional[Entry]:
        if not self.cfg.enabled or not ctx.prof.cacheable:
            return None
        if self._active_epoch is not None and ctx.epoch != self._active_epoch:
            METRICS.inc("store_skipped_epoch_moved")
            return None  # computed from data that is no longer current
        semantic = semantic and ctx.prof.semantic_ok and kind == "llm"
        now = time.time()
        e = Entry(
            id=ctx.eid, epoch=ctx.epoch, scope=ctx.scope, bucket=ctx.bucket, norm=ctx.prof.norm,
            question=ctx.prof.raw[:400], residual=ctx.prof.residual,
            vec=EMBED.pack(ctx.qd) if (semantic and ctx.qd) else None, answer=payload,
            semantic=semantic, persist=semantic and self.cfg.persist and ctx.epoch != "nostate",
            created=now, expires=now + self._ttl(kind, ctx.scope, ctx.now), last_hit=now,
            dense=ctx.qdense, dense_tag=self.dense.tag if ctx.qdense is not None else "",
        )
        self.store.put(e)
        self._bind_l1(ctx.msg, ctx.epoch, ctx.now, e, "HIT-EXACT", 1.0)
        if e.persist:
            self.persister.submit_upsert(e)
        if semantic and self.dense.enabled and e.dense is None:
            self._spawn(self._attach_dense(e, ctx.prof.dense_text))
        return e

    async def _attach_dense(self, e: Entry, text: str) -> None:
        try:
            vec = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(_AUX_EXEC, self.dense.embed_sync, text),
                self.cfg.embed_timeout + 1.0,
            )
        except Exception:
            return
        if vec is not None and self.store.get_exact(e.id, time.time()) is e:
            e.dense, e.dense_tag = vec, self.dense.tag
            self.persister.submit_upsert(e)

    def _degrade(self, ctx: _Ctx, why: str) -> ChatResult:
        METRICS.inc("fallbacks")
        METRICS.inc(f"fallback_{why}")
        text = _deterministic_answer(ctx.state, ctx.prof, ctx.msg, ctx.now) or _FALLBACK_GENERIC
        payload = _payload(text)
        e = self._store(ctx, payload, "degraded", semantic=False)
        return ChatResult(payload, "FALLBACK", e.id if e else None)

    # ------------------------------------------------------------------ flight
    async def _flight(self, ctx: _Ctx) -> ChatResult:
        try:
            return await asyncio.wait_for(self._flight_inner(ctx), self.cfg.flight_timeout)
        except asyncio.TimeoutError:
            logger.warning("[semcache] flight timed out")
            self.breaker.failure()
            return self._degrade(ctx, "timeout")
        except Exception as exc:
            logger.exception(f"[semcache] flight crashed: {exc}")
            return self._degrade(ctx, "error")

    async def _flight_inner(self, ctx: _Ctx) -> ChatResult:
        loop = asyncio.get_running_loop()
        # 1) calendar handler (DB-backed, no Groq). Exact-cached briefly, never semantic.
        try:
            cal = await loop.run_in_executor(_AUX_EXEC, _calendar_sync, ctx.msg)
        except Exception as exc:
            logger.warning(f"[semcache] calendar handler failed, continuing to LLM: {exc}")
            cal = None
        if cal is not None:
            METRICS.inc("calendar_answers")
            e = self._store(ctx, cal, "calendar", semantic=False)
            return ChatResult(cal, "CALENDAR", e.id if e else None)

        # 2) LLM behind breaker -> concurrency -> RPM budget
        if not self.breaker.allow():
            return self._degrade(ctx, "breaker_open")
        async with self._semaphore():
            reserve = max(1, int(self.cfg.llm_rpm * 0.4)) if ctx.internal else 0
            if not await self.rpm.acquire(self.cfg.llm_queue_wait, reserve):
                return self._degrade(ctx, "rpm_budget")
            METRICS.inc("llm_calls")
            _cl.METRICS.inc("llm_calls")
            try:
                content = await loop.run_in_executor(_LLM_EXEC, self.llm_fn, ctx.state, ctx.msg, ctx.now)
            except Exception as exc:
                METRICS.inc("llm_failures")
                _cl.METRICS.inc("llm_failures")
                logger.warning(f"[semcache] LLM call failed: {exc}")
                self.breaker.failure(hard=_looks_rate_limited(exc))
                return self._degrade(ctx, "llm_error")

        content = (content or "").strip()
        if not content or _ERRORISH_RE.search(content.lower()[:200]):
            METRICS.inc("llm_failures")
            _cl.METRICS.inc("llm_failures")
            self.breaker.failure()
            return self._degrade(ctx, "empty_or_error")
        self.breaker.success()
        payload = _payload(content)
        negative = bool(_NEGATIVE_RE.search(content.lower()[:300]))
        e = self._store(ctx, payload, "neg" if negative else "llm", semantic=not negative)
        return ChatResult(payload, "MISS", e.id if e else None)

    def _flight_done(self, fl: _Flight, task: "asyncio.Task") -> None:
        if self._flights.get(fl.key) is fl:
            self._flights.pop(fl.key, None)
        lst = self._by_bucket.get(fl.bucket)
        if lst is not None:
            try:
                lst.remove(fl)
            except ValueError:
                pass
            if not lst:
                self._by_bucket.pop(fl.bucket, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error(f"[semcache] flight error: {task.exception()}")

    def _find_flight(self, ctx: _Ctx) -> Optional[_Flight]:
        fl = self._flights.get(ctx.eid)
        if fl is not None:
            return fl
        if ctx.prof.semantic_ok and ctx.qd:
            for cand in self._by_bucket.get(ctx.bucket, ()):
                if cand.semantic_ok and cand.residual == ctx.prof.residual \
                        and _dict_dot(ctx.qd, cand.qd) >= self.cfg.t_lex:
                    return cand
        return None

    # ------------------------------------------------------------------ lookup
    def _hit(self, e: Entry, status: str, score: float) -> ChatResult:
        self.store.touch(e, time.time())
        METRICS.inc({"HIT-EXACT": "hit_exact", "HIT-SEMANTIC": "hit_semantic", "HIT-DENSE": "hit_dense"}[status])
        return ChatResult(e.answer, status, e.id, round(score, 4))

    async def _dense_lookup(self, ctx: _Ctx, cands: List[Tuple[float, Entry]]) -> Optional[ChatResult]:
        eligible = [(s, e) for s, e in cands
                    if e.dense is not None and s >= self.cfg.dense_floor and e.dense_tag == self.dense.tag]
        if not eligible:
            return None
        try:
            qv = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(_AUX_EXEC, self.dense.embed_sync, ctx.prof.dense_text),
                self.cfg.embed_timeout + 1.0,
            )
        except Exception:
            qv = None
        if qv is None:
            return None
        ctx.qdense = qv
        best: Optional[Tuple[float, Entry]] = None
        for _, e in eligible:
            c = _dense_cos(qv, e.dense)  # type: ignore[arg-type]
            if c >= self.cfg.t_dense and (best is None or c > best[0]):
                best = (c, e)
        if best is not None:
            self._bind_l1(ctx.msg, ctx.epoch, ctx.now, best[1], "HIT-DENSE", best[0])
            return self._hit(best[1], "HIT-DENSE", best[0])
        return None

    def lookup_sync(self, prof: Profile, epoch: str, scope: str, qd: Optional[Dict[int, float]]):
        """Pure lookup used by /inspect: returns (exact_entry, candidates)."""
        now = time.time()
        eid, bucket = _sha(epoch, scope, prof.norm), _sha(epoch, scope, prof.hard_sig, n=24)
        exact = self.store.get_exact(eid, now)
        cands = self.store.search(bucket, qd, now) if (qd and prof.semantic_ok) else []
        return exact, cands

    # ------------------------------------------------------------------ public API
    async def answer(self, message: str, client_id: Optional[str], ip: str, internal: bool = False) -> ChatResult:
        METRICS.inc("requests")
        _cl.METRICS.inc("chat_requests")
        msg = _WS_RE.sub(" ", _CTRL_RE.sub(" ", message or "")).strip()
        if not msg:
            return ChatResult(_payload("Please type a question about the timetable."), "SKIP")
        if len(msg) > self.cfg.max_chars:
            return ChatResult(_payload(f"Please keep your question under {self.cfg.max_chars} characters."), "SKIP")

        try:
            return await self._answer_inner(msg, client_id, ip, internal)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"[semcache] unexpected engine error, falling back to cache_layer: {exc}")
            METRICS.inc("legacy_fallback")
            if self.cfg.legacy_fallback:
                payload, status = await LEGACY_CHAT.answer(msg, client_id, ip)
                return ChatResult(payload, f"LEGACY-{status}")
            return ChatResult(_payload(_FALLBACK_GENERIC), "ERROR")

    async def _answer_inner(self, msg: str, client_id: Optional[str], ip: str, internal: bool) -> ChatResult:
        t0 = time.perf_counter()
        state = await self._state()
        now = _now_ist()
        epoch = state.epoch if state else "nostate"

        # ---- L1 fast path (cache_layer.CHAT_CACHE alias -> validated L2 Entry) ----
        if self.cfg.enabled and self.cfg.l1_enabled:
            l1_val = L1_CACHE.get(_l1_key(msg, epoch, now))
            if isinstance(l1_val, tuple) and len(l1_val) == 4 and l1_val[0] == "sem_ref":
                _, ref_id, ref_status, ref_score = l1_val
                e = self.store.get_exact(ref_id, time.time())
                if e is not None and e.epoch == epoch:
                    METRICS.inc("hit_l1")
                    _cl.METRICS.inc("chat_hits")
                    METRICS.observe_lookup(time.perf_counter() - t0)
                    return self._hit(e, ref_status, ref_score)

        # ---- L2 slot analysis + exact canonical + semantic + optional dense ----
        prof = ANALYZER.analyze(msg, now, state.lexicon if state else EMPTY_LEXICON)
        scope = self._scope(prof, now)
        qd = EMBED.query_dict(prof.canon) if prof.semantic_ok else None
        ctx = _Ctx(
            msg=msg, prof=prof, state=state, epoch=epoch, scope=scope,
            bucket=_sha(epoch, scope, prof.hard_sig, n=24), eid=_sha(epoch, scope, prof.norm),
            now=now, qd=qd, internal=internal,
        )
        if prof.injection:
            METRICS.inc("injection_uncached")

        cands: List[Tuple[float, Entry]] = []
        if self.cfg.enabled and prof.cacheable:
            wall = time.time()
            e = self.store.get_exact(ctx.eid, wall)
            if e is not None:
                self._bind_l1(msg, epoch, now, e, "HIT-EXACT", 1.0)
                _cl.METRICS.inc("chat_hits")
                METRICS.observe_lookup(time.perf_counter() - t0)
                return self._hit(e, "HIT-EXACT", 1.0)
            if prof.semantic_ok and qd:
                cands = self.store.search(ctx.bucket, qd, wall)
                for score, cand in cands:
                    if cand.residual == prof.residual and score >= self.cfg.t_lex:
                        self._bind_l1(msg, epoch, now, cand, "HIT-SEMANTIC", score)
                        _cl.METRICS.inc("chat_hits")
                        METRICS.observe_lookup(time.perf_counter() - t0)
                        return self._hit(cand, "HIT-SEMANTIC", score)
                if self.dense.enabled and cands:
                    res = await self._dense_lookup(ctx, cands)
                    if res is not None:
                        _cl.METRICS.inc("chat_hits")
                        METRICS.observe_lookup(time.perf_counter() - t0)
                        return res
        METRICS.observe_lookup(time.perf_counter() - t0)

        # ---- miss: join an identical / equivalent in-flight call, or lead one
        fl = self._find_flight(ctx)
        leader = fl is None
        if fl is None:
            if not internal:
                ok, retry = self._check_throttle(client_id, ip)
                if not ok:
                    METRICS.inc("rate_limited")
                    _cl.METRICS.inc("chat_rate_limited")
                    return ChatResult(
                        _payload(f"You're sending questions too quickly. Please wait about {retry} seconds and try again."),
                        "RATE_LIMITED",
                    )
            fl = _Flight(ctx.eid, ctx.bucket, prof.residual, qd, prof.semantic_ok)
            fl.task = asyncio.get_running_loop().create_task(self._flight(ctx))
            self._flights[fl.key] = fl
            self._by_bucket[fl.bucket].append(fl)
            fl.task.add_done_callback(lambda t, f=fl: self._flight_done(f, t))
            METRICS.inc("misses")
            _cl.METRICS.inc("chat_misses")
        else:
            METRICS.inc("coalesced")
            _cl.METRICS.inc("chat_coalesced")

        try:
            res: ChatResult = await asyncio.wait_for(asyncio.shield(fl.task), self.cfg.request_timeout)
        except asyncio.TimeoutError:
            METRICS.inc("timeouts")
            _cl.METRICS.inc("chat_timeouts")
            return ChatResult(_payload("The assistant is taking longer than usual. Please try again in a moment."), "TIMEOUT")
        except asyncio.CancelledError:
            raise
        except Exception:
            return ChatResult(_payload(_FALLBACK_GENERIC), "ERROR")
        if not leader and res.status in ("MISS", "CALENDAR"):
            return ChatResult(res.payload, "COALESCED", res.entry_id, res.score)
        return res

    # ------------------------------------------------------------------ feedback / admin
    def feedback(self, entry_id: str, helpful: bool, who: str) -> Dict[str, Any]:
        if helpful:
            return {"ok": True, "evicted": False}
        votes = self._downvotes.setdefault(entry_id, set())
        votes.add(who)
        self._downvotes.move_to_end(entry_id)
        while len(self._downvotes) > 2000:
            self._downvotes.popitem(last=False)
        METRICS.inc("downvotes")
        if len(votes) >= self.cfg.downvote_evict:
            n = self.store.remove([entry_id])
            self._downvotes.pop(entry_id, None)
            if n:
                L1_CACHE.clear()
                METRICS.inc("downvote_evictions")
            return {"ok": True, "evicted": bool(n)}
        return {"ok": True, "evicted": False, "votes": len(votes)}

    async def evict_matching(self, question: str) -> Dict[str, Any]:
        """Admin: drop the exact entry AND everything in the same hard-slot bucket (+ L1 aliases)."""
        state = await self._state()
        now = _now_ist()
        prof = ANALYZER.analyze(question, now, state.lexicon if state else EMPTY_LEXICON)
        epoch = state.epoch if state else "nostate"
        scope = self._scope(prof, now)
        ids = {_sha(epoch, scope, prof.norm)} | set(self.store.bucket_ids(_sha(epoch, scope, prof.hard_sig, n=24)))
        evicted = self.store.remove(ids)
        if evicted:
            L1_CACHE.clear()
        return {"evicted": evicted, "hard_signature": prof.hard_sig}

    async def inspect(self, question: str) -> Dict[str, Any]:
        state = await self._state()
        now = _now_ist()
        prof = ANALYZER.analyze(question, now, state.lexicon if state else EMPTY_LEXICON)
        epoch = state.epoch if state else "nostate"
        scope = self._scope(prof, now)
        qd = EMBED.query_dict(prof.canon) if prof.semantic_ok else None
        exact, cands = self.lookup_sync(prof, epoch, scope, qd)
        decision = "MISS"
        if exact is not None:
            decision = "HIT-EXACT"
        elif any(e.residual == prof.residual and s >= self.cfg.t_lex for s, e in cands):
            decision = "HIT-SEMANTIC"
        return {
            "decision": decision, "epoch": epoch, "scope": scope,
            "hard_signature": prof.hard_sig, "residual": sorted(prof.residual), "canonical": prof.norm,
            "flags": {"rel": prof.rel, "volatile": prof.volatile, "calendar_hint": prof.calendar_hint,
                      "injection": prof.injection, "empty": prof.empty, "semantic_ok": prof.semantic_ok},
            "candidates": [
                {"score": round(s, 4), "question": e.question, "residual_equal": e.residual == prof.residual,
                 "hits": e.hits, "id": e.id} for s, e in cands
            ],
        }

    def _purge_local(self) -> int:
        n = self.store.clear()
        self.persister.submit_purge_all()
        self.data._on_shared_invalidate("purge")
        return n

    def purge(self) -> Dict[str, Any]:
        n = self._purge_local()
        legacy_purge_all_caches()
        return {"purged": n}

    def stats(self) -> Dict[str, Any]:
        st = self.data.peek()
        return {
            "uptime_seconds": int(time.time() - METRICS.started_at),
            "metrics": METRICS.snapshot(),
            "l1_cache": L1_CACHE.stats(),
            "store": self.store.stats(),
            "persistence": self.persister.stats(),
            "inflight": len(self._flights),
            "breaker": self.breaker.state(),
            "llm_rpm_used_last_minute": self.rpm.used(),
            "llm_rpm_budget": self.cfg.llm_rpm,
            "dense": {"enabled": self.dense.enabled, "calls": self.dense.calls, "failures": self.dense.failures,
                      "breaker": self.dense.breaker.state()},
            "active_epoch": self._active_epoch,
            "data": None if st is None else {
                "version_id": st.version_id, "rows": len(st.rows), "epoch": st.epoch,
                "age_seconds": round(time.monotonic() - st.built_at, 1),
                "teachers": len(st.lexicon.teachers), "subject_codes": len(st.lexicon.subject_codes),
            },
            "config": {k: v for k, v in asdict(self.cfg).items() if k != "embed_key"},
        }

    # ------------------------------------------------------------------ warm-up
    def _warm_questions(self, st: DataState) -> List[str]:
        qs: List[str] = []
        combos = sorted({(r["program"], r["semester"]) for r in st.rows if r["program"] and r["semester"]})
        for p, s in combos:
            qs.append(f"Show the timetable for {p} {s} semester")
        for d in _DAY_NAMES[:6]:
            qs.append(f"What classes are there on {d.title()}?")
        for t in sorted({t.strip() for r in st.rows for t in re.split(r"\s*[,/&;+]\s*", r["teacher"]) if t.strip()}):
            qs.append(f"What classes does {t} teach?")
        return qs[: self.cfg.warm_max]

    async def warm(self) -> Dict[str, int]:
        st = await self._state()
        if st is None or not st.rows:
            return {"asked": 0, "llm": 0}
        asked = llm = 0
        for q in self._warm_questions(st):
            before = METRICS.snapshot().get("llm_calls", 0)
            await self.answer(q, None, "warmup", internal=True)
            asked += 1
            llm += METRICS.snapshot().get("llm_calls", 0) - before
            await asyncio.sleep(0.2)
        logger.info(f"[semcache] warm-up done: asked={asked} llm_calls={llm}")
        return {"asked": asked, "llm": llm}

    # ------------------------------------------------------------------ lifecycle
    async def startup(self) -> None:
        _install_sqlalchemy_hooks(self.data)
        self.persister.start()
        try:
            await self._state()  # builds data + activates epoch + kicks persisted load
            if self._load_task is not None:
                await asyncio.wait_for(asyncio.shield(self._load_task), 15.0)
        except Exception as exc:
            logger.warning(f"[semcache] startup warm-up incomplete (will recover on demand): {exc}")
        self._spawn(self._janitor())
        if self.cfg.warm:
            self._spawn(self.warm())

    async def _janitor(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(60.0)
            try:
                self.store.sweep(time.time())
                n += 1
                if n % 60 == 0:
                    self.persister.submit_purge_expired()
                await self._state()  # keeps the epoch fresh even with no traffic
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.debug(f"[semcache] janitor error: {exc}")

    async def shutdown(self) -> None:
        for t in list(self._bg):
            t.cancel()
        self.persister.stop()


ENGINE = SemanticChatEngine(CFG)

# Register companion hooks with cache_layer so invalidations, purges, and stats are unified.
_cl.register_cache_hooks(
    on_invalidate=ENGINE.data._on_shared_invalidate,
    on_purge=ENGINE._purge_local,
    stats_name="semantic",
    stats_fn=ENGINE.stats,
)


def set_llm_function(fn: Callable[[Optional[DataState], str, datetime], str]) -> None:
    """Replace the blocking LLM callable: fn(state, message, now_ist) -> str."""
    ENGINE.llm_fn = fn


# ============================================================================
# 9. ROUTES
# ============================================================================
public_router = APIRouter()
admin_router = APIRouter()


class FeedbackRequest(BaseModel):
    entry_id: str
    helpful: bool


def _client_ident(request: Request) -> Tuple[Optional[str], str]:
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
    cid = re.sub(r"[^A-Za-z0-9_\-]", "", request.headers.get("x-client-id", ""))[:64] or None
    return cid, ip or "unknown"


@public_router.post("/chat", response_model=ChatResponse)
async def semantic_public_chat(req: ChatRequest, request: Request):
    cid, ip = _client_ident(request)
    res = await ENGINE.answer(req.message, cid, ip)
    headers = {
        "X-Cache": res.status,
        "Cache-Control": "no-store",
        "Access-Control-Expose-Headers": "X-Cache, X-Cache-Entry, X-Cache-Score",
    }
    if res.entry_id:
        headers["X-Cache-Entry"] = res.entry_id
    if res.score is not None:
        headers["X-Cache-Score"] = str(res.score)
    return JSONResponse(content=jsonable_encoder(res.payload), headers=headers)


@public_router.post("/chat/feedback")
async def semantic_chat_feedback(body: FeedbackRequest, request: Request):
    cid, ip = _client_ident(request)
    ok, retry = ENGINE._fb_throttle.allow("fb:" + ip, 20)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Too many feedback requests. Retry in {retry}s.")
    if not re.fullmatch(r"[0-9a-f]{40}", body.entry_id or ""):
        raise HTTPException(status_code=422, detail="Invalid entry id")
    return ENGINE.feedback(body.entry_id, body.helpful, cid or ip)


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@admin_router.get("/stats")
async def semcache_stats(_: User = Depends(_require_admin)):
    return ENGINE.stats()


@admin_router.post("/purge")
async def semcache_purge(_: User = Depends(_require_admin)):
    return ENGINE.purge()


@admin_router.get("/inspect")
async def semcache_inspect(q: str, _: User = Depends(_require_admin)):
    """Dry run: how would this question be parsed and matched? Never calls the LLM."""
    return await ENGINE.inspect(q[: CFG.max_chars])


@admin_router.post("/evict")
async def semcache_evict(body: ChatRequest, _: User = Depends(_require_admin)):
    return await ENGINE.evict_matching(body.message[: CFG.max_chars])


@admin_router.post("/warm")
async def semcache_warm(_: User = Depends(_require_admin)):
    return await ENGINE.warm()


# ============================================================================
# 10. INSTALLER
# ============================================================================
def install_semantic_cache(app: FastAPI) -> None:
    """
    Call once, right after `app = FastAPI(...)` and BEFORE any include_router
    (and before cache_layer.install_cache) so POST /api/public/chat is ours.
    If CHAT_ENGINE=legacy, cache_layer's /api/public/chat remains primary.
    """
    _install_sqlalchemy_hooks(ENGINE.data)
    if CFG.chat_engine == "legacy":
        logger.info("[semcache] CHAT_ENGINE=legacy; cache_layer owns POST /api/public/chat")
        app.include_router(admin_router, prefix="/api/admin/semcache", tags=["semcache-admin"])
        return

    prior_chat_idx = next(
        (
            i for i, r in enumerate(app.router.routes)
            if getattr(r, "path", None) == "/api/public/chat" and "POST" in (getattr(r, "methods", None) or ())
        ),
        None,
    )
    app.include_router(public_router, prefix="/api/public", tags=["public (semantic cache)"])
    app.include_router(admin_router, prefix="/api/admin/semcache", tags=["semcache-admin"])

    if prior_chat_idx is not None:
        logger.warning(
            "[semcache] install_semantic_cache() was called after another /api/public/chat route; "
            "reordering so semantic_public_chat takes precedence"
        )
        sem_idx = next(
            (
                i for i, r in enumerate(app.router.routes)
                if getattr(r, "path", None) == "/api/public/chat"
                and getattr(r, "endpoint", None) is semantic_public_chat
            ),
            None,
        )
        if sem_idx is not None and sem_idx > prior_chat_idx:
            route = app.router.routes.pop(sem_idx)
            app.router.routes.insert(prior_chat_idx, route)

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan(a):
        async with original_lifespan(a) as state:  # your table creation / seeding runs first
            await ENGINE.startup()
            try:
                yield state
            finally:
                await ENGINE.shutdown()
                for ex in (_DB_EXEC, _LLM_EXEC, _AUX_EXEC):
                    ex.shutdown(wait=False)

    app.router.lifespan_context = _lifespan
    logger.info("[semcache] semantic cache installed (L1=cache_layer + L2=semantic)")
