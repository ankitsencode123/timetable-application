"""
Advanced NLP Chat Engine  -  single-file drop-in for the Timetable app
=======================================================================

Place this file at:   app/actions/advanced_chat.py
Wire it in (one line, no existing file changes):

    # app/main.py
    from app.actions.advanced_chat import router as advanced_chat_router
    app.include_router(advanced_chat_router, prefix="/actions", tags=["advanced-chat"])

New endpoints
    POST   /actions/advanced-chat           plan -> preflight -> (confirm) -> execute
    GET    /actions/advanced-chat/status    LLM quota / cache / breaker telemetry
    DELETE /actions/advanced-chat/session   forget this session's rules + memory

DESIGN  ("LLM proposes, backend disposes")
-----------------------------------------
  1. LLM is used ONCE per message (plus at most a few bounded repair /
     continuation calls) to turn free text into a structured *plan*:
        decision (execute | clarify | infeasible | reject), issues, questions,
        rules (persistent / per-request constraints) and the action list.
  2. Everything the LLM says is untrusted. A deterministic PREFLIGHT then
        - grounds every teacher / subject / room / program / semester / day / time
          against the live catalog (DB + constraints module), no hardcoded values,
        - resolves every target against the live schedule (0 matches -> not found,
          >1 -> deterministic clarification, never a guess),
        - drops no-ops / duplicates (idempotency),
        - enforces user rules (forbid / protect) on the real before/after diff,
        - simulates each action with the engine's own executor and runs H1-H11,
          rejecting only NEW violations (same logic as ActionEngine).
  3. Execution always goes through SafeActionEngine (locking, stale detection,
     idempotency, audit). Risky plans need explicit confirm=true. After commit the
     stored version is re-read and compared with the predicted schedule.

FREE-TIER FRIENDLY
  * relevance-filtered compact context (only rows/entities the request touches)
  * sliding-window RPM limiter, optional daily cap, per-user limiter
  * 429/5xx aware retries (Retry-After, jittered backoff), model fallback chain,
    optional Groq key-pool failover, circuit breaker
  * plan cache (same text + same head version = 0 LLM calls)
  * preview -> confirm reuses the stored plan (0 extra LLM calls)
  * local fast-paths with no LLM: undo, validate, rules, help
  * truncated-output salvage + continuation, long inputs chunked at clause boundaries

OPTIONAL ENV VARS (all have safe defaults)
  ADV_CHAT_LLM_RPM=12  ADV_CHAT_LLM_DAILY_CAP=0  ADV_CHAT_USER_RPM=6
  ADV_CHAT_MAX_LLM_CALLS=4  ADV_CHAT_MAX_OUTPUT_TOKENS=3500
  ADV_CHAT_FALLBACK_MODELS="modelA,modelB"  ADV_CHAT_USE_GROQ=0
  ADV_CHAT_CONFIRM_THRESHOLD=6  ADV_CHAT_TEACHER_FORBIDDEN="GENERATE_TIMETABLE,ADD_PROGRAM"
NOTE: set these as real environment variables (Render dashboard); .env values that are
not declared in Settings are not exported to os.environ.
NOTE: caches/limiters/sessions are in-process. With N Render workers the effective
LLM rate is N x ADV_CHAT_LLM_RPM, so divide accordingly.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import random
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from loguru import logger
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.api.auth import require_teacher_or_admin
from app.models.user import User
from app.models.timetable import TimetableVersion
from app.services import timetable_service
from app.schemas.actions import ActionExecuteResponse, ActionResultSchema
from app.actions.types import parse_action
from app.actions.concurrency import (
    SafeActionEngine,
    if_match_header,
    idempotency_key_header,
    resolve_expected_version,
    current_head,
)
from app.services.email_service import notify_teachers_of_changes
from app.scheduler import constraints as C
from app.scheduler.validator import validate_schedule, validate_schema

# Engine internals are reused for faithful simulation; degrade gracefully if absent.
try:
    from app.actions import engine as _engine_mod  # type: ignore
except Exception:  # pragma: no cover
    try:
        from app.actions import action_engine as _engine_mod  # type: ignore
    except Exception:
        _engine_mod = None
_simulate_one = getattr(_engine_mod, "_simulate_one", None)
_engine_violation_message = getattr(_engine_mod, "_violation_message", None)

try:
    from app.actions.suggestions import suggest_alternatives
except Exception:  # pragma: no cover
    suggest_alternatives = None

router = APIRouter()


# =====================================================================
# 1. CONFIG
# =====================================================================
def _env(name: str, default, cast=str):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw.strip())
    except Exception:
        return default


class Cfg:
    LLM_RPM = _env("ADV_CHAT_LLM_RPM", 12, int)
    LLM_DAILY_CAP = _env("ADV_CHAT_LLM_DAILY_CAP", 0, int)          # 0 = off
    LLM_TIMEOUT = _env("ADV_CHAT_LLM_TIMEOUT", 75, int)
    LLM_RETRIES = _env("ADV_CHAT_LLM_RETRIES", 2, int)
    LIMITER_MAX_WAIT = _env("ADV_CHAT_LIMITER_MAX_WAIT", 15, int)
    MAX_OUTPUT_TOKENS = _env("ADV_CHAT_MAX_OUTPUT_TOKENS", 3500, int)
    MAX_LLM_CALLS = _env("ADV_CHAT_MAX_LLM_CALLS", 4, int)
    REPAIR_ROUNDS = _env("ADV_CHAT_REPAIR_ROUNDS", 1, int)
    FALLBACK_MODELS = [m.strip() for m in _env("ADV_CHAT_FALLBACK_MODELS", "", str).split(",") if m.strip()]
    USE_GROQ = _env("ADV_CHAT_USE_GROQ", 0, int)
    REFERER = _env("ADV_CHAT_REFERER", "https://timetable-app.onrender.com", str)

    USER_RPM = _env("ADV_CHAT_USER_RPM", 6, int)
    MAX_INPUT_CHARS = _env("ADV_CHAT_MAX_INPUT_CHARS", 12000, int)
    SINGLE_CALL_CHARS = _env("ADV_CHAT_SINGLE_CALL_CHARS", 5000, int)
    CHUNK_CHARS = _env("ADV_CHAT_CHUNK_CHARS", 3500, int)
    MAX_CHUNKS = _env("ADV_CHAT_MAX_CHUNKS", 5, int)

    CONTEXT_ROWS = _env("ADV_CHAT_CONTEXT_ROWS", 70, int)
    CONTEXT_CHARS = _env("ADV_CHAT_CONTEXT_CHARS", 9000, int)
    HISTORY_MSGS = _env("ADV_CHAT_HISTORY_MSGS", 6, int)
    HISTORY_CHARS = _env("ADV_CHAT_HISTORY_CHARS", 500, int)

    CACHE_TTL = _env("ADV_CHAT_CACHE_TTL", 600, int)
    CACHE_MAX = _env("ADV_CHAT_CACHE_MAX", 256, int)
    PLAN_TTL = _env("ADV_CHAT_PLAN_TTL", 1200, int)
    SESSION_TTL = _env("ADV_CHAT_SESSION_TTL", 21600, int)

    MAX_ACTIONS = _env("ADV_CHAT_MAX_ACTIONS", 60, int)
    TEACHER_MAX_ACTIONS = _env("ADV_CHAT_TEACHER_MAX_ACTIONS", 25, int)
    CONFIRM_THRESHOLD = _env("ADV_CHAT_CONFIRM_THRESHOLD", 6, int)
    TEACHER_FORBIDDEN = {
        a.strip().upper()
        for a in _env("ADV_CHAT_TEACHER_FORBIDDEN", "GENERATE_TIMETABLE,ADD_PROGRAM", str).split(",")
        if a.strip()
    }


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# =====================================================================
# 2. SMALL UTILITIES
# =====================================================================
def _n(s: Any) -> str:
    """Compact comparison key: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _tokens(s: Any) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(s or "").lower()) if t]


_TIME_RE = re.compile(r"^\s*(\d{1,2})(?:[:.h]?(\d{2}))?\s*([ap])?\.?m?\.?\s*$", re.I)


def norm_time(v: Any) -> Optional[str]:
    if v is None:
        return None
    m = _TIME_RE.match(str(v).strip().lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap:
        if h < 1 or h > 12:
            return None
        h = (h % 12) + (12 if ap == "p" else 0)
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _mins(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def norm_day(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower().rstrip(".")
    if len(s) < 3:
        return None
    for d in _WEEK:
        dl = d.lower()
        if dl == s or dl.startswith(s) or (len(s) <= 5 and s[:3] == dl[:3]):
            return d
    return None


_ORD_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
              "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}


def sem_num(v: Any) -> Optional[int]:
    s = str(v or "").strip().lower()
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    for w, n in _ORD_WORDS.items():
        if w in s:
            return n
    return None


def sem_label(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _norm_type(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s.startswith(("prac", "lab")) or s in ("p", "pr"):
        return "Practical"
    if s.startswith(("theo", "lec")) or s in ("t", "th"):
        return "Theory"
    if s.startswith("both"):
        return "Both"
    return str(v or "").strip()


def _clip(s: Any, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


class _TTLStore:
    """Thread-safe LRU + TTL map."""

    def __init__(self, max_items: int, ttl: float):
        self._d: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self.max_items, self.ttl = max_items, ttl

    def get(self, key: str):
        with self._lock:
            it = self._d.get(key)
            if not it:
                return None
            ts, val = it
            if time.time() - ts > self.ttl:
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            return val

    def set(self, key: str, val: Any):
        with self._lock:
            self._d[key] = (time.time(), val)
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def pop(self, key: str):
        with self._lock:
            it = self._d.pop(key, None)
            return it[1] if it else None

    def __len__(self):
        return len(self._d)


# =====================================================================
# 3. RATE LIMITING, BREAKER, LLM CLIENT
# =====================================================================
class LLMError(RuntimeError):
    pass


class LLMBusy(LLMError):
    def __init__(self, retry_after: float, msg: str = "AI planner is rate-limited"):
        super().__init__(msg)
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    pass


class _SlidingWindow:
    def __init__(self, max_calls: int, period: float = 60.0):
        self.max, self.period = max(1, max_calls), period
        self.calls: Deque[float] = deque()
        self.lock = threading.Lock()

    def try_acquire(self) -> float:
        now = time.monotonic()
        with self.lock:
            while self.calls and now - self.calls[0] >= self.period:
                self.calls.popleft()
            if len(self.calls) < self.max:
                self.calls.append(now)
                return 0.0
            return self.period - (now - self.calls[0])

    def acquire(self, max_wait: float):
        deadline = time.monotonic() + max_wait
        while True:
            w = self.try_acquire()
            if w <= 0:
                return
            if time.monotonic() + w > deadline:
                raise LLMBusy(w)
            time.sleep(min(w, 1.0) + random.random() * 0.1)

    def used(self) -> int:
        now = time.monotonic()
        with self.lock:
            return sum(1 for c in self.calls if now - c < self.period)


class _DailyCap:
    def __init__(self, cap: int):
        self.cap, self.day, self.count = cap, "", 0
        self.lock = threading.Lock()

    def hit(self):
        if self.cap <= 0:
            return
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self.lock:
            if today != self.day:
                self.day, self.count = today, 0
            if self.count >= self.cap:
                raise LLMBusy(3600, "Daily AI request quota reached; try again later")
            self.count += 1


class _Breaker:
    def __init__(self, threshold: int = 4, cooldown: float = 30.0):
        self.threshold, self.cooldown = threshold, cooldown
        self.fails, self.open_until = 0, 0.0
        self.lock = threading.Lock()

    def is_open(self) -> bool:
        return time.time() < self.open_until

    def success(self):
        with self.lock:
            self.fails = 0

    def failure(self):
        with self.lock:
            self.fails += 1
            if self.fails >= self.threshold:
                self.open_until = time.time() + self.cooldown
                self.fails = 0


_LLM_LIMITER = _SlidingWindow(Cfg.LLM_RPM)
_DAILY = _DailyCap(Cfg.LLM_DAILY_CAP)
_BREAKER = _Breaker()
_USER_LIMITERS: Dict[Any, _SlidingWindow] = {}
_USER_LIM_LOCK = threading.Lock()
_GROQ_RR = {"i": 0}


@dataclass
class LLMResult:
    text: str
    model: str
    finish_reason: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


def _providers() -> List[dict]:
    s = get_settings()
    out: List[dict] = []
    if s.openrouter_key:
        models = [s.OPENROUTER_MODEL] + [m for m in Cfg.FALLBACK_MODELS if m != s.OPENROUTER_MODEL]
        for m in models:
            out.append({"name": "openrouter", "url": OPENROUTER_URL, "key": s.openrouter_key, "model": m,
                        "headers": {"HTTP-Referer": Cfg.REFERER, "X-Title": "Timetable Application"}})
    if Cfg.USE_GROQ and s.groq_key_list:
        keys = s.groq_key_list
        start = _GROQ_RR["i"] % len(keys)
        _GROQ_RR["i"] += 1
        for k in keys[start:] + keys[:start]:
            out.append({"name": "groq", "url": GROQ_URL, "key": k, "model": s.GROQ_MODEL, "headers": {}})
    return out


def _backoff(attempt: int) -> float:
    return min(1.5 * (2 ** attempt), 12.0) + random.random() * 0.5


def _retry_after(r) -> float:
    try:
        return float(r.headers.get("Retry-After", "0"))
    except Exception:
        return 0.0


_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def llm_complete(messages: List[dict], max_tokens: Optional[int] = None) -> LLMResult:
    if _BREAKER.is_open():
        raise LLMUnavailable("AI planner is cooling down after repeated failures; retry in ~30s.")
    provs = _providers()
    if not provs:
        raise LLMError("No LLM API key configured (set OPENROUTER_API_KEY).")
    last = ""
    for pi, p in enumerate(provs):
        has_next = pi < len(provs) - 1
        for attempt in range(Cfg.LLM_RETRIES + 1):
            _LLM_LIMITER.acquire(Cfg.LIMITER_MAX_WAIT)
            _DAILY.hit()
            payload = {"model": p["model"], "messages": messages, "temperature": 0.1,
                       "max_tokens": max_tokens or Cfg.MAX_OUTPUT_TOKENS}
            if p["name"] == "openrouter":
                payload["include_reasoning"] = False
            headers = {"Authorization": "Bearer " + p["key"], "Content-Type": "application/json", **p["headers"]}
            try:
                r = requests.post(p["url"], headers=headers, json=payload, timeout=(10, Cfg.LLM_TIMEOUT))
            except requests.RequestException as e:
                last = f"{p['model']}: {e}"
                time.sleep(_backoff(attempt))
                continue
            sc = r.status_code
            if sc == 200:
                try:
                    data = r.json()
                    ch = data["choices"][0]
                    content = (ch.get("message") or {}).get("content") or ""
                except Exception as e:
                    last = f"{p['model']}: bad response ({e})"
                    continue
                content = _THINK_RE.sub("", content).strip()
                if not content:
                    last = f"{p['model']}: empty completion"
                    continue
                _BREAKER.success()
                return LLMResult(content, p["model"], str(ch.get("finish_reason") or ""), data.get("usage") or {})
            if sc == 429:
                last = f"{p['model']}: rate limited (429)"
                if has_next:
                    break
                if attempt < Cfg.LLM_RETRIES:
                    time.sleep(min(max(_retry_after(r), _backoff(attempt)), Cfg.LIMITER_MAX_WAIT))
                    continue
                break
            if sc in (408, 409, 425) or sc >= 500:
                last = f"{p['model']}: HTTP {sc}"
                time.sleep(_backoff(attempt))
                continue
            last = f"{p['model']}: HTTP {sc} {_clip(r.text, 160)}"
            break  # non-retryable for this provider
    _BREAKER.failure()
    raise LLMUnavailable(f"All LLM providers failed ({last})")


# =====================================================================
# 4. JSON EXTRACTION (robust to fences, prose, trailing commas, truncation)
# =====================================================================
_DEC = json.JSONDecoder()


def _strip_fences(t: str) -> str:
    t = t.strip()
    t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t).strip()
    if t.endswith("```"):
        t = t[:-3].strip()
    return t


def _try_decode_at(t: str, i: int):
    try:
        obj, _ = _DEC.raw_decode(t[i:])
        return obj
    except json.JSONDecodeError:
        pass
    try:
        fixed = re.sub(r",\s*([}\]])", r"\1", t[i:])
        obj, _ = _DEC.raw_decode(fixed)
        return obj
    except json.JSONDecodeError:
        return None


def parse_llm_output(text: str) -> Tuple[Optional[dict], bool]:
    """Returns (plan_object | None, salvaged_from_truncation)."""
    t = _strip_fences(text)
    # 1) well-formed object containing plan keys
    i = t.find("{")
    while i != -1:
        obj = _try_decode_at(t, i)
        if isinstance(obj, dict) and ("decision" in obj or "actions" in obj or "action" in obj):
            if "action" in obj and "actions" not in obj and "decision" not in obj:
                return {"decision": "execute", "actions": [obj]}, False
            return obj, False
        i = t.find("{", i + 1)
    # 2) bare array of actions
    j = t.find("[")
    if j != -1:
        arr = _try_decode_at(t, j)
        if isinstance(arr, list) and arr and all(isinstance(x, dict) for x in arr):
            return {"decision": "execute", "actions": arr}, False
    # 3) salvage truncated plan: pull complete action objects out of "actions":[ ...
    m = re.search(r'"actions"\s*:\s*\[', t)
    if m:
        pos, acts = m.end(), []
        while pos < len(t):
            while pos < len(t) and t[pos] in " \r\n\t,":
                pos += 1
            if pos >= len(t) or t[pos] != "{":
                break
            try:
                obj, used = _DEC.raw_decode(t[pos:])
            except json.JSONDecodeError:
                break
            if isinstance(obj, dict):
                acts.append(obj)
            pos += used
        head: Dict[str, Any] = {"actions": acts}
        dm = re.search(r'"decision"\s*:\s*"(\w+)"', t)
        sm = re.search(r'"summary"\s*:\s*"([^"]{0,400})"', t)
        head["decision"] = dm.group(1) if dm else "execute"
        if sm:
            head["summary"] = sm.group(1)
        return head, True
    return None, False


_VALID_DECISIONS = {"execute", "clarify", "infeasible", "reject"}


def normalize_plan(obj: dict) -> dict:
    plan: Dict[str, Any] = {
        "decision": str(obj.get("decision", "")).lower().strip(),
        "summary": _clip(obj.get("summary", ""), 400),
        "issues": [], "questions": [], "assumptions": [], "rules": [], "drop_rules": [], "actions": [],
    }
    for it in obj.get("issues") or []:
        if isinstance(it, dict):
            plan["issues"].append({"type": str(it.get("type", "note")).lower(),
                                   "blocking": bool(it.get("blocking", False)),
                                   "detail": _clip(it.get("detail", ""), 400)})
        elif isinstance(it, str):
            plan["issues"].append({"type": "note", "blocking": False, "detail": _clip(it, 400)})
    for q in obj.get("questions") or []:
        if isinstance(q, str) and q.strip():
            plan["questions"].append(_clip(q.strip(), 300))
    for a in obj.get("assumptions") or []:
        if isinstance(a, str) and a.strip():
            plan["assumptions"].append(_clip(a.strip(), 250))
    plan["rules"] = [r for r in (obj.get("rules") or []) if isinstance(r, dict)][:10]
    plan["drop_rules"] = [str(x) for x in (obj.get("drop_rules") or [])][:10]
    for a in obj.get("actions") or []:
        if not isinstance(a, dict):
            continue
        if str(a.get("action", "")).upper() == "CLARIFY":  # legacy format
            if a.get("question"):
                plan["questions"].append(_clip(a["question"], 300))
            continue
        plan["actions"].append(a)
    plan["questions"] = plan["questions"][:3]
    plan["actions"] = plan["actions"][: Cfg.MAX_ACTIONS]

    dec = plan["decision"]
    if dec not in _VALID_DECISIONS:
        dec = "clarify" if plan["questions"] and not plan["actions"] else ("execute" if plan["actions"] else "clarify")
    blocking = [i for i in plan["issues"] if i["blocking"]]
    if dec == "execute" and blocking:
        dec = "infeasible" if any(i["type"] in ("contradiction", "infeasible") for i in blocking) else "clarify"
    if dec == "execute" and not plan["actions"] and not plan["rules"] and not plan["drop_rules"]:
        dec = "clarify"
    if dec == "clarify" and not plan["questions"]:
        plan["questions"] = ["Could you say exactly which class(es) should change, and how?"]
    plan["decision"] = dec
    return plan


# =====================================================================
# 5. LIVE CATALOG (DB + constraints; nothing hardcoded)
# =====================================================================
_HONORIFICS = {"mr", "mrs", "ms", "dr", "prof", "professor", "sir", "madam", "miss", "mx"}


def _safe_query(db: Session, fn):
    try:
        return fn()
    except Exception as e:
        logger.debug(f"catalog query skipped: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return []


def _room_is_lab(fac: Any) -> bool:
    try:
        if isinstance(fac, dict):
            for k, v in fac.items():
                if "lab" in str(k).lower() and v:
                    return True
                if isinstance(v, (str, list, set, tuple)) and "lab" in str(v).lower():
                    return True
            return False
        return "lab" in str(fac).lower()
    except Exception:
        return False


@dataclass
class Catalog:
    teachers: Dict[str, str] = field(default_factory=dict)        # short -> full
    subjects: Dict[str, dict] = field(default_factory=dict)       # code -> info
    programs: List[str] = field(default_factory=list)
    days: List[str] = field(default_factory=list)
    rooms: Dict[str, bool] = field(default_factory=dict)          # room -> is_lab
    semesters: List[str] = field(default_factory=list)
    busy: List[dict] = field(default_factory=list)

    # ------------------------------------------------------------ load
    @classmethod
    def load(cls, db: Session, entries: List[dict]) -> "Catalog":
        cat = cls()
        try:
            from app.models.teacher import Teacher
            for t in _safe_query(db, lambda: db.query(Teacher).all()):
                cat.teachers[str(t.short_name)] = str(t.full_name or t.short_name)
        except Exception:
            pass
        for t in list(getattr(C, "INTERNAL_TEACHERS", []) or []):
            cat.teachers.setdefault(str(t), str(t))
        for e in entries:
            if e.get("teacher"):
                cat.teachers.setdefault(str(e["teacher"]), str(e["teacher"]))

        try:
            from app.models.subject import Subject
            for s in _safe_query(db, lambda: db.query(Subject).all()):
                cat.subjects[str(s.code).lower()] = {
                    "name": str(s.name), "program": str(s.program), "semester": str(s.semester),
                    "entry_type": str(s.entry_type), "weekly_hours": s.weekly_hours}
        except Exception:
            pass
        for e in entries:
            code = str(e.get("subject_code") or "").lower()
            if code and code not in cat.subjects:
                cat.subjects[code] = {"name": str(e.get("subject_name") or code), "program": str(e.get("program") or ""),
                                      "semester": str(e.get("semester") or ""), "entry_type": str(e.get("type") or ""),
                                      "weekly_hours": None}

        progs = []
        try:
            from app.models.program import Program
            progs += [str(p.name) for p in _safe_query(db, lambda: db.query(Program).all())]
        except Exception:
            pass
        progs += [str(p) for p in (getattr(C, "VALID_PROGRAMS", []) or [])]
        progs += [str(e.get("program")) for e in entries if e.get("program")]
        seen = set()
        for p in progs:
            if _n(p) not in seen:
                seen.add(_n(p))
                cat.programs.append(p)

        days = list(getattr(C, "VALID_DAYS", []) or []) or sorted({e.get("day") for e in entries if e.get("day")})
        days = [norm_day(d) or str(d) for d in days]
        cat.days = sorted(set(days), key=lambda d: _WEEK.index(d) if d in _WEEK else 99) or _WEEK[:6]

        fac = getattr(C, "ROOM_FACILITIES", {}) or {}
        for r in fac:
            cat.rooms[str(r)] = _room_is_lab(fac[r]) if isinstance(fac, dict) else False
        for e in entries:
            if e.get("room"):
                cat.rooms.setdefault(str(e["room"]), False)

        sems = {str(s["semester"]) for s in cat.subjects.values() if s.get("semester")}
        sems |= {str(e.get("semester")) for e in entries if e.get("semester")}
        cat.semesters = sorted(sems, key=lambda s: sem_num(s) or 99)

        try:
            from app.models.busy_slot import TeacherBusySlot
            for b in _safe_query(db, lambda: db.query(TeacherBusySlot).all()):
                cat.busy.append({"teacher": str(b.teacher_short_name), "scope": str(b.scope),
                                 "day": str(b.day_of_week or ""), "date": str(b.specific_date or ""),
                                 "reason": str(b.reason or "")})
        except Exception:
            pass
        return cat

    # ------------------------------------------------------------ resolvers
    def resolve_teacher(self, name: Any) -> Tuple[Optional[str], List[str]]:
        raw = str(name or "").strip()
        if not raw:
            return None, []
        low = raw.lower()
        for s in self.teachers:
            if s == raw or s.lower() == low:
                return s, []
        nk = _n(raw)
        for s, f in self.teachers.items():
            if _n(s) == nk or _n(f) == nk:
                return s, []
        toks = [t for t in _tokens(raw) if t not in _HONORIFICS]
        hits = []
        if toks:
            for s, f in self.teachers.items():
                ft = set(_tokens(f)) - _HONORIFICS
                if set(toks) <= ft or _n("".join(toks)) == _n(s):
                    hits.append(s)
        if len(hits) == 1:
            return hits[0], []
        if len(hits) > 1:
            return None, hits[:5]
        pool = {}
        for s, f in self.teachers.items():
            pool[s.lower()] = s
            pool[f.lower()] = s
        close = difflib.get_close_matches(low, list(pool), n=3, cutoff=0.75)
        shorts = list(dict.fromkeys(pool[c] for c in close))
        if len(shorts) == 1 and difflib.SequenceMatcher(None, low, close[0]).ratio() >= 0.86:
            return shorts[0], []
        return None, shorts

    def resolve_subject(self, val: Any, program: Any = None, semester: Any = None) -> List[str]:
        raw = str(val or "").strip()
        if not raw:
            return []
        low, nk = raw.lower(), _n(raw)
        if low in self.subjects:
            return [low]
        by_name = [c for c, s in self.subjects.items() if _n(s["name"]) == nk]
        if not by_name:
            by_name = [c for c in self.subjects if _n(c) == nk]
        if not by_name:
            by_name = [c for c, s in self.subjects.items()
                       if len(nk) >= 4 and (_n(s["name"]).startswith(nk) or nk.startswith(_n(s["name"])))]
        if not by_name:
            names = {s["name"].lower(): c for c, s in self.subjects.items()}
            close = difflib.get_close_matches(low, list(names), n=2, cutoff=0.82)
            by_name = list(dict.fromkeys(names[c] for c in close))
        if len(by_name) > 1 and (program or semester):
            f = [c for c in by_name
                 if (not program or _n(self.subjects[c]["program"]) == _n(program))
                 and (not semester or sem_num(self.subjects[c]["semester"]) == sem_num(semester))]
            if f:
                by_name = f
        return by_name

    def resolve_room(self, val: Any) -> Optional[str]:
        nk = _n(val)
        if not nk:
            return None
        table = {_n(r): r for r in self.rooms}
        if nk in table:
            return table[nk]
        core = re.sub(r"^(room|r)", "", nk)
        if core:
            for k, r in table.items():
                if re.sub(r"^(room|r)", "", k) == core:
                    return r
        return None

    def resolve_program(self, val: Any) -> Optional[str]:
        nk = _n(val)
        if not nk:
            return None
        for p in self.programs:
            if _n(p) == nk:
                return p
        close = difflib.get_close_matches(nk, [_n(p) for p in self.programs], n=1, cutoff=0.85)
        if close:
            for p in self.programs:
                if _n(p) == close[0]:
                    return p
        return None

    def resolve_semester(self, val: Any) -> Optional[str]:
        n = sem_num(val)
        if n is None:
            return None
        for s in self.semesters:
            if sem_num(s) == n:
                return s
        if not 1 <= n <= 12:
            return None
        if self.semesters and all(re.fullmatch(r"\d+", s) for s in self.semesters):
            return str(n)
        return sem_label(n)


# =====================================================================
# 6. GROUNDING (LLM output -> canonical values) & TARGET RESOLUTION
# =====================================================================
@dataclass
class Issue:
    kind: str
    message: str
    clarify: bool = False         # answerable only by the user -> deterministic question
    repairable: bool = False      # LLM may fix by re-planning
    candidates: List[str] = field(default_factory=list)


@dataclass
class Pending:
    subjects: Set[str] = field(default_factory=set)
    teachers: Set[str] = field(default_factory=set)
    programs: Set[str] = field(default_factory=set)


_TARGET_KEYS = ("target", "target_a", "target_b")
_SPEC_KEYS = ("spec", "new_spec")
_DAY_KEYS = {"day", "new_day", "day_of_week"}
_TIME_KEYS = {"start_time", "end_time", "new_start_time", "new_end_time"}
_TEACHER_KEYS = {"teacher", "new_teacher", "teacher_short_name"}
_ROOM_KEYS = {"room", "new_room"}


def _ground(d: dict, cat: Catalog, pend: Pending, issues: List[Issue], strict: bool = True) -> dict:
    out: Dict[str, Any] = {}
    ctx_prog = d.get("program")
    ctx_sem = d.get("semester")
    for k, v in d.items():
        if isinstance(v, dict):
            if k in _TARGET_KEYS:
                out[k] = _ground(v, cat, pend, issues, strict=False)
            elif k in _SPEC_KEYS:
                out[k] = _ground(v, cat, pend, issues, strict=True)
            else:
                out[k] = v
            continue
        if v is None or isinstance(v, (bool, int, float, list)):
            out[k] = v
            continue
        s = str(v).strip()
        if s == "":
            out[k] = None if not strict else s
            continue
        if k in _DAY_KEYS:
            dd = norm_day(s)
            if dd is None or dd not in cat.days:
                issues.append(Issue("invalid_day", f"“{s}” is not a valid teaching day (valid: {', '.join(cat.days)}).", repairable=True))
                out[k] = s
            else:
                out[k] = dd
        elif k in _TIME_KEYS:
            tt = norm_time(s)
            if tt is None:
                issues.append(Issue("invalid_time", f"“{s}” is not a valid time (use HH:MM).", repairable=True))
                out[k] = s
            else:
                out[k] = tt
        elif k in _TEACHER_KEYS:
            short, cands = cat.resolve_teacher(s)
            if short:
                out[k] = short
            elif s.lower() in pend.teachers:
                out[k] = s
            elif strict:
                msg = f"I couldn't find a teacher called “{s}”."
                if cands:
                    msg += f" Did you mean: {', '.join(cands)}?"
                issues.append(Issue("unknown_teacher", msg, clarify=True, candidates=cands))
                out[k] = s
            else:
                out[k] = s
        elif k in _ROOM_KEYS:
            r = cat.resolve_room(s)
            if r:
                out[k] = r
            elif strict:
                issues.append(Issue("unknown_room", f"Room “{s}” does not exist (rooms: {', '.join(sorted(cat.rooms))}).", clarify=True,
                                    candidates=sorted(cat.rooms)))
                out[k] = s
            else:
                out[k] = s
        elif k == "subject_code":
            codes = cat.resolve_subject(s, ctx_prog, ctx_sem)
            if len(codes) == 1:
                out[k] = codes[0]
            elif s.lower() in pend.subjects:
                out[k] = s.lower()
            elif strict:
                if len(codes) > 1:
                    issues.append(Issue("ambiguous_subject", f"“{s}” matches several subjects ({', '.join(codes)}). Which one?",
                                        clarify=True, candidates=codes))
                else:
                    issues.append(Issue("unknown_subject", f"I couldn't find a subject “{s}”.", clarify=True))
                out[k] = s.lower()
            else:
                out[k] = s
        elif k == "program" and not (strict and d.get("action") == "ADD_PROGRAM"):
            p = cat.resolve_program(s)
            if p:
                out[k] = p
            elif s.lower() in pend.programs:
                out[k] = s
            elif strict:
                issues.append(Issue("invalid_program", f"Unknown program “{s}” (known: {', '.join(cat.programs)}).", clarify=True,
                                    candidates=list(cat.programs)))
                out[k] = s
            else:
                out[k] = s
        elif k == "semester":
            sm = cat.resolve_semester(s)
            out[k] = sm or s
        elif k == "entry_type":
            out[k] = _norm_type(s)
        elif k == "code":
            out[k] = s.lower()
        else:
            out[k] = v
    # spec: fill canonical subject name from catalog
    if strict and out.get("subject_code") and str(out["subject_code"]).lower() in cat.subjects and "subject_name" in out:
        out["subject_name"] = cat.subjects[str(out["subject_code"]).lower()]["name"]
    return out


def _entry_matches(e: dict, t: dict, cat: Catalog) -> bool:
    if t.get("day") and str(e.get("day", "")).lower() != str(t["day"]).lower():
        return False
    if t.get("program") and _n(e.get("program")) != _n(t["program"]):
        return False
    if t.get("semester"):
        a, b = sem_num(e.get("semester")), sem_num(t["semester"])
        if (a is not None and b is not None and a != b) or ((a is None or b is None) and _n(e.get("semester")) != _n(t["semester"])):
            return False
    if t.get("start_time") and norm_time(e.get("start")) != norm_time(t["start_time"]):
        return False
    if t.get("end_time") and norm_time(e.get("end")) != norm_time(t["end_time"]):
        return False
    subj = t.get("subject_code") or t.get("subject_name")
    if subj:
        codes = cat.resolve_subject(subj, t.get("program"), t.get("semester"))
        if codes:
            if str(e.get("subject_code", "")).lower() not in codes:
                return False
        else:
            sn = _n(e.get("subject_name"))
            if _n(subj) not in (sn, _n(e.get("subject_code"))) and _n(subj) not in sn:
                return False
    if t.get("teacher"):
        parts = [_n(x) for x in re.split(r"[/,+&]", str(e.get("teacher", "")))] + [_n(e.get("teacher"))]
        if _n(t["teacher"]) not in parts:
            return False
    if t.get("room") and _n(e.get("room")) != _n(t["room"]):
        return False
    if t.get("entry_type") and _norm_type(e.get("type")) != _norm_type(t["entry_type"]):
        return False
    return True


def _row_text(e: dict) -> str:
    return (f"{e.get('subject_code')} {e.get('day')} {e.get('start')}-{e.get('end')} "
            f"({e.get('teacher')}) {e.get('program')} {e.get('semester')} {e.get('room')}")


def _resolve_targets(g: dict, schedule: List[dict], cat: Catalog, issues: List[Issue]) -> Dict[str, dict]:
    """Complete each target from the live schedule. Returns {key: matched entry}."""
    found: Dict[str, dict] = {}
    for key in _TARGET_KEYS:
        if not isinstance(g.get(key), dict):
            continue
        t = g[key]
        if not any(v for v in t.values()):
            issues.append(Issue("target_missing", f"{key} is empty – which lesson do you mean?", clarify=True))
            continue
        matches = [e for e in schedule if _entry_matches(e, t, cat)]
        if not matches:
            near = [e for e in schedule if _entry_matches(e, {k: v for k, v in t.items() if k in ("subject_code", "subject_name", "teacher")}, cat)][:4]
            hint = ("; nearest: " + " | ".join(_row_text(e) for e in near)) if near else ""
            issues.append(Issue("target_not_found",
                                f"No lesson matches {_fmt_target(t)} in the current timetable{hint}.", repairable=True,
                                candidates=[_row_text(e) for e in near]))
        elif len(matches) > 1:
            cands = [_row_text(e) for e in matches[:6]]
            issues.append(Issue("target_ambiguous",
                                f"{len(matches)} lessons match {_fmt_target(t)}: " + " | ".join(cands) + " – which one?",
                                clarify=True, candidates=cands))
        else:
            e = matches[0]
            nt = dict(t)
            for src, dst in (("program", "program"), ("semester", "semester"), ("day", "day"), ("start", "start_time"),
                             ("end", "end_time"), ("subject_code", "subject_code"), ("teacher", "teacher")):
                if e.get(src):
                    nt[dst] = e[src]
            g[key] = nt
            found[key] = e
    if "target_a" in found and "target_b" in found and found["target_a"] is found["target_b"]:
        issues.append(Issue("target_ambiguous", "Both targets refer to the same lesson – which two lessons should be swapped?", clarify=True))
    return found


def _ek(e: dict) -> tuple:
    return (str(e.get("day", "")).lower(), _n(e.get("program")), sem_num(e.get("semester")),
            norm_time(e.get("start")), norm_time(e.get("end")), str(e.get("subject_code", "")).lower(),
            _n(e.get("teacher")), _n(e.get("room")), _norm_type(e.get("type")).lower()[:3])


def _is_noop(g: dict, found: Dict[str, dict], schedule: List[dict]) -> Optional[str]:
    at = g.get("action")
    e = found.get("target")

    def same(a, b):
        return str(a or "").strip().lower() == str(b or "").strip().lower()

    if at == "ADD_CLASS" and isinstance(g.get("spec"), dict):
        s = g["spec"]
        cand = {"day": s.get("day"), "program": s.get("program"), "semester": s.get("semester"), "start": s.get("start_time"),
                "end": s.get("end_time"), "subject_code": s.get("subject_code"), "teacher": s.get("teacher"),
                "room": s.get("room"), "type": s.get("entry_type")}
        if any(_ek(x) == _ek(cand) for x in schedule):
            return "an identical lesson already exists"
    if not e:
        return None
    if at == "CHANGE_DAY" and same(g.get("new_day"), e.get("day")):
        return "lesson is already on that day"
    if at == "CHANGE_TIME" and norm_time(g.get("new_start_time")) == norm_time(e.get("start")) and norm_time(g.get("new_end_time")) == norm_time(e.get("end")):
        return "lesson already has that time"
    if at == "CHANGE_ROOM" and _n(g.get("new_room")) == _n(e.get("room")):
        return "lesson is already in that room"
    if at == "CHANGE_TEACHER" and _n(g.get("new_teacher") or g.get("teacher")) == _n(e.get("teacher")):
        return "lesson already has that teacher"
    if at in ("EXTEND_CLASS", "SHORTEN_CLASS") and norm_time(g.get("new_end_time")) == norm_time(e.get("end")):
        return "lesson already ends at that time"
    if at == "MOVE_CLASS":
        checks = []
        if g.get("new_day"):
            checks.append(same(g["new_day"], e.get("day")))
        if g.get("new_start_time"):
            checks.append(norm_time(g["new_start_time"]) == norm_time(e.get("start")))
        if g.get("new_end_time"):
            checks.append(norm_time(g["new_end_time"]) == norm_time(e.get("end")))
        if g.get("new_room"):
            checks.append(_n(g["new_room"]) == _n(e.get("room")))
        if not checks or all(checks):
            return "lesson is already in the requested position"
    return None


def _fmt_target(t: dict) -> str:
    parts = []
    subj = t.get("subject_code") or t.get("subject_name")
    if subj:
        parts.append(str(subj))
    if t.get("day"):
        tm = f" {t['start_time']}-{t.get('end_time', '?')}" if t.get("start_time") else ""
        parts.append(f"{t['day']}{tm}")
    elif t.get("start_time"):
        parts.append(f"{t['start_time']}-{t.get('end_time', '?')}")
    if t.get("teacher"):
        parts.append(f"({t['teacher']})")
    if t.get("program"):
        parts.append(f"{t['program']} {t.get('semester', '')}".strip())
    if t.get("room"):
        parts.append(str(t["room"]))
    return " ".join(parts) or "?"


def describe_action(a: dict) -> str:
    at = str(a.get("action", "?"))
    left = [_fmt_target(a[k]) for k in ("target", "target_a", "target_b", "spec") if isinstance(a.get(k), dict)]
    s = at + (": " + " ⇄ ".join(left) if left else "")
    dest = []
    if isinstance(a.get("new_spec"), dict):
        dest.append(_fmt_target(a["new_spec"]))
    for k, v in a.items():
        if k.startswith("new_") and k != "new_spec" and v:
            dest.append(f"{k[4:]}={v}")
    extras = [f"{k}={_clip(v, 40)}" for k, v in a.items()
              if k not in ("action", "target", "target_a", "target_b", "spec", "new_spec") and not k.startswith("new_")
              and v not in (None, "", [], {})]
    if dest:
        s += " → " + ", ".join(dest)
    if extras and not left:
        s += ": " + ", ".join(extras[:6])
    return s


# =====================================================================
# 7. RULES (session + per-request) enforced on the real before/after diff
# =====================================================================
_RULE_KEYS = ("subject_code", "teacher", "program", "semester", "day", "room", "entry_type")
_RULE_FIELDS = {"teacher", "room", "day", "start", "end", "subject_code", "*"}


def _ground_rule_value(key: str, x: str, cat: Catalog) -> str:
    if key == "teacher":
        return cat.resolve_teacher(x)[0] or x
    if key == "subject_code":
        c = cat.resolve_subject(x)
        return c[0] if len(c) == 1 else x.lower()
    if key == "program":
        return cat.resolve_program(x) or x
    if key == "semester":
        return cat.resolve_semester(x) or x
    if key == "day":
        return norm_day(x) or x
    if key == "room":
        return cat.resolve_room(x) or x
    if key == "entry_type":
        return _norm_type(x)
    return x


def clean_rule(r: dict, cat: Catalog) -> Optional[dict]:
    kind = str(r.get("kind", "note")).lower()
    if kind not in ("forbid", "protect", "note"):
        kind = "note"
    raw_m = r.get("match") if isinstance(r.get("match"), dict) else {}
    match: Dict[str, List[str]] = {}
    for k in _RULE_KEYS:
        v = raw_m.get(k)
        if v in (None, "", []):
            continue
        vals = v if isinstance(v, list) else [v]
        g = []
        for x in vals:
            x = str(x).strip()
            neg = x.startswith("!")
            x = x.lstrip("!").strip()
            if x:
                g.append(("!" if neg else "") + _ground_rule_value(k, x, cat))
        if g:
            match[k] = g
    if kind in ("forbid", "protect") and not match:
        kind = "note"  # too broad to enforce safely
    window = None
    w = r.get("window")
    if isinstance(w, dict) and kind == "forbid":
        f, t = norm_time(w.get("from")), norm_time(w.get("to"))
        if f or t:
            window = {"from": f or "00:00", "to": t or "23:59"}
    raw_f = r.get("fields") if isinstance(r.get("fields"), list) else ["*"]
    fields = [f for f in raw_f if f in _RULE_FIELDS] or ["*"]
    return {"kind": kind, "match": match, "window": window, "fields": fields,
            "scope": "request" if str(r.get("scope", "session")).lower() == "request" else "session",
            "text": _clip(r.get("text", ""), 200)}


def _rule_match(e: dict, match: Dict[str, List[str]]) -> bool:
    for key, vals in match.items():
        ev = {"subject_code": e.get("subject_code"), "teacher": e.get("teacher"), "program": e.get("program"),
              "semester": e.get("semester"), "day": e.get("day"), "room": e.get("room"), "entry_type": e.get("type")}[key]

        def eq(w: str) -> bool:
            if key == "semester":
                return sem_num(ev) == sem_num(w) if sem_num(w) is not None else _n(ev) == _n(w)
            if key == "entry_type":
                return _norm_type(ev) == _norm_type(w)
            if key == "day":
                return str(ev).lower() == str(w).lower()
            return _n(ev) == _n(w)

        pos = [v for v in vals if not v.startswith("!")]
        neg = [v[1:] for v in vals if v.startswith("!")]
        if pos and not any(eq(v) for v in pos):
            return False
        if any(eq(v) for v in neg):
            return False
    return True


def check_rules(rules: List[dict], before: List[dict], after: List[dict]) -> List[str]:
    cb, ca = Counter(_ek(e) for e in before), Counter(_ek(e) for e in after)
    by_key = {}
    for e in before + after:
        by_key.setdefault(_ek(e), e)
    removed = [by_key[k] for k, c in (cb - ca).items() for _ in range(c)]
    added = [by_key[k] for k, c in (ca - cb).items() for _ in range(c)]
    out: List[str] = []
    for r in rules:
        rid = r.get("id", "rule")
        why = r.get("text") or json.dumps(r.get("match"))
        if r["kind"] == "forbid":
            for e in added:
                if not _rule_match(e, r["match"]):
                    continue
                if r.get("window"):
                    s, en = norm_time(e.get("start")), norm_time(e.get("end"))
                    if not s or not en or not (_mins(s) < _mins(r["window"]["to"]) and _mins(en) > _mins(r["window"]["from"])):
                        continue
                out.append(f"Rule {rid} (“{_clip(why, 90)}”) forbids placing {_row_text(e)}.")
        elif r["kind"] == "protect":
            for e in removed:
                if not _rule_match(e, r["match"]):
                    continue
                fr = r.get("fields") or ["*"]
                twins = [a for a in added if (_n(a.get("program")), sem_num(a.get("semester")), str(a.get("subject_code", "")).lower(), _norm_type(a.get("type")))
                         == (_n(e.get("program")), sem_num(e.get("semester")), str(e.get("subject_code", "")).lower(), _norm_type(e.get("type")))]
                if "*" in fr or not twins:
                    out.append(f"Rule {rid} (“{_clip(why, 90)}”) protects {_row_text(e)}, which this change would alter.")
                else:
                    attr = {"teacher": "teacher", "room": "room", "day": "day", "start": "start", "end": "end", "subject_code": "subject_code"}
                    if any(_n(a.get(attr[f])) != _n(e.get(attr[f])) for a in twins for f in fr if f in attr):
                        out.append(f"Rule {rid} (“{_clip(why, 90)}”) freezes {', '.join(fr)} of {_row_text(e)}.")
    return list(dict.fromkeys(out))


# =====================================================================
# 8. SESSION MEMORY
# =====================================================================
@dataclass
class _Session:
    rules: List[dict] = field(default_factory=list)
    changes: Deque[str] = field(default_factory=lambda: deque(maxlen=8))
    turns: Deque[Tuple[str, str]] = field(default_factory=lambda: deque(maxlen=8))
    first_base: Optional[int] = None
    counter: int = 0


_SESSIONS = _TTLStore(500, Cfg.SESSION_TTL)
_PLANS = _TTLStore(500, Cfg.PLAN_TTL)
_PLAN_CACHE = _TTLStore(Cfg.CACHE_MAX, Cfg.CACHE_TTL)


def _get_session(key: str) -> _Session:
    s = _SESSIONS.get(key)
    if s is None:
        s = _Session()
    _SESSIONS.set(key, s)
    return s


def _add_session_rule(sess: _Session, rule: dict):
    sig = json.dumps({k: rule[k] for k in ("kind", "match", "window", "fields")}, sort_keys=True)
    for r in sess.rules:
        if json.dumps({k: r[k] for k in ("kind", "match", "window", "fields")}, sort_keys=True) == sig:
            return
    sess.counter += 1
    rule = {**rule, "id": f"R{sess.counter}"}
    sess.rules.append(rule)
    del sess.rules[:-20]


# =====================================================================
# 9. CONTEXT BUILDER (relevance-filtered, token-lean)
# =====================================================================
@dataclass
class Mentions:
    teachers: Set[str] = field(default_factory=set)
    subjects: Set[str] = field(default_factory=set)
    programs: Set[str] = field(default_factory=set)
    sems: Set[int] = field(default_factory=set)
    days: Set[str] = field(default_factory=set)
    rooms: Set[str] = field(default_factory=set)

    @property
    def strong(self) -> bool:
        return bool(self.teachers or self.subjects or self.programs or self.sems or self.rooms)


def _word_in(text_l: str, needle: str) -> bool:
    n = needle.strip().lower()
    return len(n) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", text_l) is not None


def extract_mentions(text: str, cat: Catalog) -> Mentions:
    t = text.lower()
    tn = _n(text)
    M = Mentions()
    for s, f in cat.teachers.items():
        if _word_in(t, s) or _word_in(t, f) or any(len(w) >= 4 and w not in _HONORIFICS and _word_in(t, w) for w in _tokens(f)):
            M.teachers.add(s.lower())
    for c, info in cat.subjects.items():
        name = info["name"].lower()
        if _word_in(t, c) or _word_in(t, name) or any(len(w) >= 5 and _word_in(t, w) for w in _tokens(name)):
            M.subjects.add(c)
    for p in cat.programs:
        if len(_n(p)) >= 2 and _n(p) in tn:
            M.programs.add(_n(p))
    for m in re.finditer(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*[- ]?\s*sem(?:ester)?|sem(?:ester)?\s*[-:#]?\s*(\d{1,2})", t):
        M.sems.add(int(m.group(1) or m.group(2)))
    for w, n in _ORD_WORDS.items():
        if re.search(rf"\b{w}\s+sem", t):
            M.sems.add(n)
    for d in cat.days:
        if _word_in(t, d.lower()) or _word_in(t, d[:3].lower()) or _word_in(t, d[:4].lower()):
            M.days.add(d)
    for r in cat.rooms:
        if _n(r) in tn or _word_in(t, r):
            M.rooms.add(r)
    for m in re.finditer(r"\broom\s*#?\s*(\d+[a-z]?)", t):
        r = cat.resolve_room(m.group(1))
        if r:
            M.rooms.add(r)
    return M


def _row_score(e: dict, M: Mentions) -> int:
    s = 0
    if str(e.get("teacher", "")).lower() in M.teachers:
        s += 3
    if str(e.get("subject_code", "")).lower() in M.subjects:
        s += 3
    if e.get("room") in M.rooms:
        s += 2
    pm = _n(e.get("program")) in M.programs
    sm = sem_num(e.get("semester")) in M.sems
    if pm and sm:
        s += 2
    elif pm or sm:
        s += 1
    if e.get("day") in M.days:
        s += 1
    return s


def _period_grid(entries: List[dict]) -> List[Tuple[str, str]]:
    ends: Dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        s, en = norm_time(e.get("start")), norm_time(e.get("end"))
        if s and en:
            ends[s][en] += 1
    return [(s, ends[s].most_common(1)[0][0]) for s in sorted(ends)][:14]


def _free_periods(entries, grid, days, pred, blocked_days: Set[str]) -> Dict[str, List[int]]:
    busy: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for e in entries:
        if pred(e):
            s, en = norm_time(e.get("start")), norm_time(e.get("end"))
            if s and en:
                busy[e.get("day")].append((_mins(s), _mins(en)))
    free = {}
    for d in days:
        if d in blocked_days:
            free[d] = []
            continue
        free[d] = [i + 1 for i, (s, en) in enumerate(grid)
                   if not any(bs < _mins(en) and be > _mins(s) for bs, be in busy.get(d, []))]
    return free


def _fmt_free(label: str, free: Dict[str, List[int]]) -> str:
    return f"{label}: " + "; ".join(f"{d[:3]} " + (",".join(f"P{p}" for p in ps) or "none") for d, ps in free.items())


def build_context(text: str, entries: List[dict], cat: Catalog, sess: _Session, rules: List[dict],
                  extra_mention_text: str = "") -> str:
    M = extract_mentions(text + " " + extra_mention_text, cat)
    grid = _period_grid(entries)
    cap = Cfg.CONTEXT_ROWS

    def assemble(cap_rows: int) -> str:
        scored = [(_row_score(e, M), i, e) for i, e in enumerate(entries)]
        if M.strong:
            sel = [x for x in scored if x[0] > 0]
        elif M.days:
            sel = [x for x in scored if x[2].get("day") in M.days]
        else:
            sel = scored if len(scored) <= cap_rows else []
        sel.sort(key=lambda x: (-x[0], x[1]))
        shown = sel[:cap_rows]
        lines = [f"{str(e.get('day'))[:3]}|{e.get('program')}|{e.get('semester')}|{e.get('start')}-{e.get('end')}|"
                 f"{e.get('subject_code')}|{e.get('teacher')}|{e.get('room')}|{_norm_type(e.get('type'))[:1]}" for _, _, e in shown]
        used_codes = {str(e.get("subject_code", "")).lower() for _, _, e in shown} | M.subjects
        used_teachers = {str(e.get("teacher", "")).lower() for _, _, e in shown} | M.teachers

        out = []
        out.append("Programs: " + ", ".join(cat.programs) + " | Semesters: " + ", ".join(cat.semesters))
        out.append("Days: " + ", ".join(cat.days))
        out.append("Rooms: " + ", ".join(f"{r}{'(lab)' if lab else ''}" for r, lab in sorted(cat.rooms.items())))
        out.append("PERIOD GRID: " + " ".join(f"P{i + 1}={s}-{e}" for i, (s, e) in enumerate(grid)))
        tl = [f"{s}={f}" for s, f in cat.teachers.items() if s.lower() in used_teachers or len(cat.teachers) <= 40]
        if tl:
            out.append("Teachers: " + "; ".join(tl[:80]))
        sl = [f"{c}={cat.subjects[c]['name']}[{cat.subjects[c]['program']} {cat.subjects[c]['semester']},{_norm_type(cat.subjects[c]['entry_type'])[:1]}]"
              for c in cat.subjects if c in used_codes or len(cat.subjects) <= 40]
        if sl:
            out.append("Subjects: " + "; ".join(sl[:80]))
        if rules:
            out.append("ACTIVE RULES: " + " || ".join(f"{r['id']}:{r['kind']} {json.dumps(r['match'])}"
                                                    f"{' '+json.dumps(r['window']) if r.get('window') else ''} \"{r.get('text','')}\"" for r in rules))
        if sess.changes:
            out.append("RECENT CHANGES THIS SESSION (oldest first): " + " || ".join(sess.changes))
        busy = [b for b in cat.busy if b["teacher"].lower() in used_teachers]
        if busy:
            out.append("TEACHER BUSY: " + "; ".join(f"{b['teacher']} {b['scope']} {b['day'] or b['date']}" for b in busy[:20]))

        # free-slot hints computed deterministically so the LLM never has to guess
        hints = []
        for tshort in list(M.teachers)[:3]:
            real = next((s for s in cat.teachers if s.lower() == tshort), tshort)
            blocked = {b["day"] for b in cat.busy if b["teacher"].lower() == tshort and b["scope"] == "permanent" and b["day"]}
            hints.append(_fmt_free(f"FREE {real}", _free_periods(entries, grid, cat.days, lambda e, t=tshort: str(e.get("teacher", "")).lower() == t, blocked)))
        groups = list(dict.fromkeys((e.get("program"), e.get("semester")) for _, _, e in shown))
        if 0 < len(groups) <= 2:
            for pg, sg in groups:
                hints.append(_fmt_free(f"FREE {pg} {sg}", _free_periods(entries, grid, cat.days, lambda e, a=pg, b=sg: e.get("program") == a and e.get("semester") == b, set())))
        if hints:
            out.append("FREE SLOTS (no lesson overlap): " + " | ".join(hints))

        if lines:
            more = f" [showing {len(lines)} of {len(sel)} relevant rows; ask if another row is needed]" if len(sel) > len(lines) else ""
            out.append(f"SCHEDULE rows Day|Program|Sem|Start-End|Code|Teacher|Room|Type{more}\n" + "\n".join(lines))
        else:
            counts = Counter((e.get("program"), e.get("semester")) for e in entries)
            out.append(f"SCHEDULE: {len(entries)} lessons total, none matched a named teacher/subject/class. Per class: " +
                       "; ".join(f"{p} {s}={n}" for (p, s), n in counts.most_common(15)) +
                       ". If the request needs specific lessons, ask the user to name the class/teacher/subject.")
        return "\n".join(out)

    ctx = assemble(cap)
    while len(ctx) > Cfg.CONTEXT_CHARS and cap > 15:
        cap = int(cap * 0.7)
        ctx = assemble(cap)
    return ctx


# =====================================================================
# 10. PROMPTS + PLANNER
# =====================================================================
SYSTEM_PROMPT = """\
You are the planning brain of a college timetable assistant. Convert the user's request into ONE JSON object. You do not schedule by guesswork: the backend grounds, simulates, validates and executes everything you emit, and rejects whatever is wrong. Output ONLY the JSON object - no markdown, no prose.

OUTPUT SCHEMA
{"decision":"execute|clarify|infeasible|reject",
 "summary":"<=40 words: what you understood",
 "issues":[{"type":"contradiction|ambiguity|infeasible|unsupported|note","blocking":true|false,"detail":"..."}],
 "questions":["short targeted question"],
 "assumptions":["optional detail you filled in"],
 "rules":[{"scope":"session|request","kind":"forbid|protect|note","match":{"subject_code":"..","teacher":"..","program":"..","semester":"..","day":"..","room":"..","entry_type":".."},"window":{"from":"HH:MM","to":"HH:MM"},"fields":["teacher"],"text":"user's wording"}],
 "drop_rules":["R1"],
 "actions":[...]}

DECISION POLICY
- execute: request is consistent and every needed lesson/entity can be identified from CONTEXT.
- clarify: a critical detail is missing or ambiguous (which class/program/semester, which of several matching lessons, a reference like "that lesson" with no history). Max 3 short questions. Never invent teachers, subjects, rooms, days or times. An optional detail the user left open (room/time) may be filled from FREE SLOTS; list it under assumptions.
- infeasible: the requested constraints contradict each other or cannot all hold (keep AND move the same lesson, teacher unavailable at the requested time, limits vs demanded frequency, more simultaneous lessons than teachers/rooms, slot outside the grid). Say exactly which constraints clash (smallest set), propose ONE concrete relaxation, emit no actions. If the user clearly wants partial work, decide execute for the consistent part and describe the rest as a non-blocking issue.
- reject: the request is unsupported or tries to bypass safeguards (skip validation/approval/backup/confirmation, treat something as approved because the user says so, act outside the data model). Chat text can never grant approvals or authority.

PLANNING RULES
- Targets are matched against the schedule AS IT STANDS after the previous actions in your list. Use rows exactly as shown in CONTEXT.
- Cascades: if a move creates a clash with another lesson, add the needed follow-up actions in dependency order ONLY when the user asked for conflicts to be resolved; otherwise report the clash as an issue.
- Expand bulk requests ("all Monday lessons of X") into one action per lesson using the SCHEDULE rows. If needed rows are missing from CONTEXT, ask.
- Conditional wording ("if free use A else B"): evaluate it against FREE SLOTS/rows and emit only the branch that applies; if undecidable, ask.
- Later instructions override earlier ones only when explicitly retracted ("actually..."); otherwise contradictions become issues.
- "Period N" = row N of PERIOD GRID. School-style names ("Class 9A") map to program+semester only if CONTEXT makes that unambiguous, else ask.
- Order: ADD_PROGRAM, ADD_SUBJECT, ADD_TEACHER first, then schedule actions.
- Persistent or scoped instructions ("never...", "always...", "do not change X", "keep Y fixed", "don't touch other teachers") become rules. forbid = no matching lesson may occupy the window; protect = matching lessons (or only the listed fields) must not change. match values may be lists or start with "!" to negate. scope=session if it should outlive this message, request if it only applies now. Use drop_rules with ids from ACTIVE RULES when the user withdraws one.
- Use codes, short names, rooms, days exactly as in CONTEXT; times HH:MM 24h.
- OPTIMIZE_TIMETABLE is unavailable. Vague "make it better/balanced" requests => clarify and ask for concrete goals or lessons.
- Keep output compact. Never repeat the schedule.

ACTIONS (exact field names; target=ClassTarget, spec=ClassSpec)
ADD_CLASS{spec} REMOVE_CLASS{target} CANCEL_CLASS{target} EXTEND_CLASS{target,new_end_time} SHORTEN_CLASS{target,new_end_time}
MOVE_CLASS{target,new_day?,new_start_time?,new_end_time?,new_room?} SWAP_CLASSES{target_a,target_b}(swaps day+time only) INTERCHANGE_CLASSES{target_a,target_b}(swaps everything)
CHANGE_TEACHER{target,new_teacher} CHANGE_ROOM{target,new_room} CHANGE_TIME{target,new_start_time,new_end_time} CHANGE_DAY{target,new_day} REPLACE_CLASS{target,new_spec}
ADD_TEACHER{short_name,full_name,subjects_csv,is_internal,email?} ADD_SUBJECT{code,name,program,semester,entry_type:Theory|Practical|Both,weekly_hours}
ADD_PROGRAM{name,semesters_count,description} ADD_TEACHER_BUSY{teacher_short_name,scope:permanent|temporary,day_of_week?,specific_date?:YYYY-MM-DD,reason}
VALIDATE_TIMETABLE{} RESTORE_VERSION{version_id} GENERATE_TIMETABLE{extra_notes?,subject_teacher_allocation?,teacher_preferences?,room_information?}
ClassTarget{program,semester,day,start_time,end_time,subject_code,teacher,room,entry_type} - give every field you know; the backend completes it.
ClassSpec{program,semester,day,start_time,end_time,subject_code,subject_name,teacher,entry_type,room} - all required.

EXAMPLE
{"decision":"execute","summary":"Move <subject> on <day> to <new day> <time>.","issues":[],"questions":[],"assumptions":[],"rules":[],"drop_rules":[],"actions":[{"action":"MOVE_CLASS","target":{"subject_code":"<code from SCHEDULE>","day":"<day>","start_time":"HH:MM","teacher":"<short name>"},"new_day":"<day>","new_start_time":"HH:MM","new_end_time":"HH:MM"}]}
"""


class _Budget:
    def __init__(self, limit: int):
        self.limit, self.used, self.model = limit, 0, ""

    @property
    def left(self) -> int:
        return self.limit - self.used

    def take(self):
        if self.used >= self.limit:
            raise LLMError("LLM call budget for this request exhausted")
        self.used += 1


def _history_messages(req_history: Optional[List[Dict[str, str]]], sess: _Session) -> List[dict]:
    src: List[Tuple[str, str]] = []
    if req_history:
        for m in req_history:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
                src.append((m["role"], str(m["content"])))
    else:
        src = list(sess.turns)
    return [{"role": r, "content": _clip(c, Cfg.HISTORY_CHARS)} for r, c in src[-Cfg.HISTORY_MSGS:]]


def _llm_plan(messages: List[dict], budget: _Budget) -> dict:
    """One planning round with JSON-retry and truncation continuation (all budgeted)."""
    budget.take()
    res = llm_complete(messages)
    budget.model = res.model
    obj, salvaged = parse_llm_output(res.text)
    if obj is None and budget.left > 0:
        budget.take()
        res = llm_complete(messages + [
            {"role": "assistant", "content": _clip(res.text, 800)},
            {"role": "user", "content": "That was not a valid JSON object. Return ONLY the JSON object described in the schema."}])
        obj, salvaged = parse_llm_output(res.text)
    if obj is None:
        raise LLMError("The AI planner returned an unparseable answer")
    truncated = salvaged or res.finish_reason == "length"
    rounds = 0
    while truncated and budget.left > 0 and rounds < 2:
        rounds += 1
        done = obj.get("actions") or []
        budget.take()
        cont = llm_complete(messages + [
            {"role": "assistant", "content": json.dumps({"actions": done}, separators=(",", ":"))[:6000]},
            {"role": "user", "content": f"Your output was cut off after {len(done)} actions. Return ONLY {{\"actions\":[...]}} with the REMAINING actions (do not repeat the ones above). If none remain return {{\"actions\":[]}}."}])
        more, salv2 = parse_llm_output(cont.text)
        extra = (more or {}).get("actions") or []
        obj["actions"] = done + [a for a in extra if a not in done]
        truncated = bool(extra) and (salv2 or cont.finish_reason == "length")
        if not extra:
            break
    return normalize_plan(obj)


def _split_text(text: str, size: int) -> List[str]:
    parts = re.split(r"\n{2,}|(?<=[.!?])\s+(?=[A-Z0-9])|\s+(?=(?:\d+[.)]\s))", text)
    chunks, cur = [], ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        if cur and len(cur) + len(p) + 1 > size:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        chunks.append(cur)
    return chunks


def _cache_key(text: str, head: Optional[int], sess: _Session, history: List[dict]) -> str:
    h = hashlib.sha256()
    h.update(re.sub(r"\s+", " ", text.strip().lower()).encode())
    h.update(str(head).encode())
    h.update(json.dumps([r.get("id") for r in sess.rules] + list(sess.changes)[-3:]).encode())
    h.update(json.dumps(history[-4:], sort_keys=True).encode())
    return h.hexdigest()


# =====================================================================
# 11. PREFLIGHT  (ground -> resolve -> noop -> rules -> simulate -> H1-H11)
# =====================================================================
_META = {"ADD_TEACHER", "ADD_SUBJECT", "ADD_PROGRAM"}
_QUANTITY_RULES = {"H8_wrong_weekly_hours", "H9_wrong_practical_duration"}


def _fingerprint(err: dict) -> str:
    rule = err.get("rule", "")
    if rule in _QUANTITY_RULES:
        return json.dumps({"rule": rule, "subject_code": err.get("subject_code") or err.get("subject", ""),
                           "program": err.get("program", ""), "semester": err.get("semester", "")}, sort_keys=True)
    return json.dumps(err, sort_keys=True, default=str)


def _new_errors(base: List[dict], cur: List[dict]) -> List[dict]:
    reprs = [_fingerprint(x) for x in base]
    new = []
    for it in cur:
        fp = _fingerprint(it)
        if fp in reprs:
            reprs.remove(fp)
        else:
            new.append(it)
    return new


def _involves_pending(v: dict, pend: Pending) -> bool:
    for src in (v, v.get("entry"), v.get("a"), v.get("b")):
        if isinstance(src, dict):
            if str(src.get("subject_code", "")).lower() in pend.subjects:
                return True
            if str(src.get("teacher", "")).lower() in pend.teachers:
                return True
    return False


def _viol_text(v: dict) -> str:
    if _engine_violation_message:
        try:
            return _engine_violation_message(v).split("\n\n", 1)[-1].replace("**", "")
        except Exception:
            pass
    return _clip(v.get("note") or v.get("rule") or "constraint violated", 200)


def _compact_suggestions(sugg: Any) -> Optional[List[dict]]:
    try:
        rich = (sugg or {}).get("rich_suggestions") or []
        return [{"action": s.get("action"), "label": s.get("label") or s.get("description")} for s in rich[:3]] or None
    except Exception:
        return None


@dataclass
class Outcome:
    index: int
    action: str
    status: str                 # ok | noop | blocked | deferred | skipped
    detail: str = ""
    kind: str = ""
    change_log: str = ""
    suggestions: Optional[List[dict]] = None


@dataclass
class Preflight:
    outcomes: List[Outcome] = field(default_factory=list)
    grounded: List[dict] = field(default_factory=list)
    final_schedule: List[dict] = field(default_factory=list)
    blocked: bool = False
    clarify: List[str] = field(default_factory=list)
    repairable: bool = False
    forbidden: bool = False
    risk: List[str] = field(default_factory=list)
    has_busy: bool = False
    has_catalog: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for o in self.outcomes if o.status in ("ok", "deferred"))


def run_preflight(actions: List[dict], base_schedule: List[dict], cat: Catalog, rules: List[dict], db: Session,
                  partial_ok: bool, is_admin: bool) -> Preflight:
    pf = Preflight()
    cur = copy.deepcopy(base_schedule)
    base_schema = validate_schema(base_schedule)
    base_viol = validate_schedule(base_schedule)
    pend = Pending()
    cap = Cfg.MAX_ACTIONS if is_admin else Cfg.TEACHER_MAX_ACTIONS
    if len(actions) > cap:
        pf.blocked = pf.forbidden = True
        pf.outcomes.append(Outcome(0, "PLAN", "blocked", f"Plan has {len(actions)} actions; your limit per request is {cap}. Split it into smaller requests.", "too_many"))
        return pf
    if _simulate_one is None:
        pf.warnings.append("Engine simulation unavailable; relying on engine validation at commit time.")

    for i, raw in enumerate(actions):
        raw = dict(raw)
        at = str(raw.get("action", "")).upper().strip()
        raw["action"] = at
        if pf.blocked and not partial_ok:
            pf.outcomes.append(Outcome(i, at, "skipped", "not evaluated because an earlier action was blocked"))
            continue

        def block(kind: str, msg: str, repairable=False, clarify=False, forbidden=False, sugg=None):
            pf.blocked = True
            pf.repairable = pf.repairable or repairable
            pf.forbidden = pf.forbidden or forbidden
            if clarify:
                pf.clarify.append(msg)
            pf.outcomes.append(Outcome(i, at, "blocked", msg, kind, suggestions=sugg))

        if at in Cfg.TEACHER_FORBIDDEN and not is_admin:
            block("forbidden", f"{at} is restricted to administrators.", forbidden=True)
            continue
        if at == "OPTIMIZE_TIMETABLE":
            block("unsupported", "Automatic optimisation is not available yet; name concrete lessons or goals instead.", clarify=True)
            continue

        issues: List[Issue] = []
        g = _ground(raw, cat, pend, issues)
        found: Dict[str, dict] = {}
        if not issues:
            found = _resolve_targets(g, cur, cat, issues)
        if issues:
            iss = issues[0]
            block(iss.kind, iss.message, repairable=iss.repairable and not iss.clarify, clarify=iss.clarify)
            continue
        try:
            act = parse_action(g)
        except Exception as e:
            block("invalid_payload", f"Malformed action: {_clip(e, 160)}", repairable=True)
            continue

        # ---------------- catalog actions -----------------
        if at == "ADD_TEACHER":
            sh = str(g.get("short_name", "")).strip()
            if not sh or not str(g.get("full_name", "")).strip():
                block("missing_field", "ADD_TEACHER needs both a short name and a full name.", clarify=True)
                continue
            if cat.resolve_teacher(sh)[0] and cat.resolve_teacher(sh)[0].lower() == sh.lower():
                pf.outcomes.append(Outcome(i, at, "noop", f"teacher {sh} already exists"))
                continue
            pend.teachers.add(sh.lower())
            pf.has_catalog = True
            pf.grounded.append(g)
            pf.outcomes.append(Outcome(i, at, "deferred", f"will create teacher {sh} ({g.get('full_name')}) and a login account"))
            pf.risk.append("creates a user account")
            continue
        if at == "ADD_SUBJECT":
            code = str(g.get("code", "")).lower()
            if not code or not g.get("name"):
                block("missing_field", "ADD_SUBJECT needs a code and a name.", clarify=True)
                continue
            prog_ok = cat.resolve_program(g.get("program")) or str(g.get("program", "")).lower() in pend.programs
            if not prog_ok:
                block("invalid_program", f"Unknown program “{g.get('program')}” for the new subject (known: {', '.join(cat.programs)}).", clarify=True)
                continue
            if code in cat.subjects:
                ex = cat.subjects[code]
                if _n(ex["name"]) == _n(g.get("name")) and _n(ex["program"]) == _n(g.get("program")) and sem_num(ex["semester"]) == sem_num(g.get("semester")):
                    pf.outcomes.append(Outcome(i, at, "noop", f"subject {code} already exists"))
                else:
                    block("duplicate", f"Subject code “{code}” is already used by “{ex['name']}” ({ex['program']} {ex['semester']}). Pick another code.", clarify=True)
                continue
            if not (isinstance(g.get("weekly_hours"), int) and 0 < g["weekly_hours"] <= 40):
                block("invalid_payload", "weekly_hours must be a positive whole number.", repairable=True)
                continue
            pend.subjects.add(code)
            pf.has_catalog = True
            pf.grounded.append(g)
            pf.outcomes.append(Outcome(i, at, "deferred", f"will create subject {code} “{g.get('name')}” for {g.get('program')} {g.get('semester')}"))
            continue
        if at == "ADD_PROGRAM":
            if cat.resolve_program(g.get("name")):
                pf.outcomes.append(Outcome(i, at, "noop", f"program {g.get('name')} already exists"))
                continue
            pend.programs.add(str(g.get("name", "")).lower())
            pf.has_catalog = True
            pf.grounded.append(g)
            pf.outcomes.append(Outcome(i, at, "deferred", f"will create program {g.get('name')}"))
            pf.risk.append("creates a program")
            continue

        # ---------------- meta / non-simulated -----------------
        if at == "VALIDATE_TIMETABLE":
            pf.grounded.append(g)
            pf.outcomes.append(Outcome(i, at, "ok", "read-only validation"))
            continue
        if at == "GENERATE_TIMETABLE":
            pf.grounded.append(g)
            pf.risk.append("regenerates a timetable (slow, uses AI quota)")
            pf.outcomes.append(Outcome(i, at, "ok", "will run the generator"))
            continue
        if at == "ADD_TEACHER_BUSY":
            scope = str(g.get("scope", "permanent")).lower()
            if scope == "permanent" and not g.get("day_of_week"):
                block("missing_field", "Which weekday is the teacher busy on?", clarify=True)
                continue
            if scope == "temporary":
                try:
                    datetime.strptime(str(g.get("specific_date", "")), "%Y-%m-%d")
                except Exception:
                    block("missing_field", "For a one-off absence please give the exact date (YYYY-MM-DD).", clarify=True)
                    continue
            tshort = g.get("teacher_short_name")
            day = g.get("day_of_week")
            affected = [e for e in cur if _n(e.get("teacher")) == _n(tshort) and (not day or e.get("day") == day)]
            pf.has_busy = True
            pf.grounded.append(g)
            pf.risk.append(f"auto-reschedules or cancels {len(affected)} lesson(s)" if affected else "")
            pf.outcomes.append(Outcome(i, at, "ok", f"{tshort} marked busy; {len(affected)} lesson(s) will be auto-moved or cancelled: " +
                                       "; ".join(_row_text(e) for e in affected[:6])))
            continue
        if at == "RESTORE_VERSION":
            pf.risk.append(f"restores version {g.get('version_id')} (replaces the current draft content)")

        # ---------------- schedule actions -----------------
        reason = _is_noop(g, found, cur)
        if reason:
            pf.outcomes.append(Outcome(i, at, "noop", reason))
            continue
        if _simulate_one is None:
            pf.grounded.append(g)
            pf.outcomes.append(Outcome(i, at, "ok", "not simulated"))
            continue
        try:
            new_sched, res = _simulate_one(cur, act, db)
        except Exception as e:  # defensive: never let a simulation bug crash the chat
            block("simulation_error", f"Simulation failed: {_clip(e, 160)}", repairable=True)
            continue
        if not res.success:
            block("simulation", res.error or "action could not be applied", repairable=True)
            continue

        rule_msgs = check_rules(rules, cur, new_sched) if rules else []
        if rule_msgs:
            block("rule", " ".join(rule_msgs[:2]), repairable=True)
            continue

        schema_errs, viols = validate_schema(new_sched), validate_schedule(new_sched)
        new_s, new_v = _new_errors(base_schema, schema_errs), _new_errors(base_viol, viols)
        real_new = [v for v in new_s + new_v if not _involves_pending(v, pend)]
        if real_new:
            first = real_new[0]
            sugg = None
            if suggest_alternatives is not None:
                try:
                    cb, ca = Counter(_ek(e) for e in cur), Counter(_ek(e) for e in new_sched)
                    mut = next((e for e in new_sched if (ca - cb).get(_ek(e), 0) > 0), None)
                    orig = (act.target.model_dump(exclude_none=True) if hasattr(act, "target")
                            else act.target_a.model_dump(exclude_none=True) if hasattr(act, "target_a") else None)
                    need_lab = (getattr(getattr(act, "spec", None), "entry_type", "") == "Practical"
                                or getattr(getattr(act, "new_spec", None), "entry_type", "") == "Practical")
                    sugg = _compact_suggestions(suggest_alternatives(
                        cur, at, first, teacher=getattr(act, "new_teacher", None), need_lab=need_lab,
                        orig_target=orig, mutated_entry=mut))
                except Exception as e:
                    logger.debug(f"suggestions skipped: {e}")
            block("violation", _viol_text(first), repairable=True, sugg=sugg)
            continue

        cur = new_sched
        base_schema, base_viol = schema_errs, viols
        deferred = bool(pend.subjects or pend.teachers) and any(_involves_pending(v, pend) for v in new_s + new_v)
        pf.grounded.append(g)
        pf.outcomes.append(Outcome(i, at, "deferred" if deferred else "ok",
                                   "depends on new catalog entries; engine re-validates after they are created" if deferred else "",
                                   change_log=res.change_log or ""))

    pf.final_schedule = cur
    if sum(1 for o in pf.outcomes if o.status == "ok" and o.action in ("CANCEL_CLASS", "REMOVE_CLASS")) >= 3:
        pf.risk.append("removes several lessons")
    if sum(1 for g in pf.grounded) >= Cfg.CONFIRM_THRESHOLD:
        pf.risk.append(f"bulk change ({len(pf.grounded)} actions)")
    pf.risk = [r for r in dict.fromkeys(pf.risk) if r]
    return pf


# =====================================================================
# 12. REQUEST / RESPONSE MODELS
# =====================================================================
class AdvancedChatRequest(BaseModel):
    text: str = Field("", validation_alias=AliasChoices("text", "message"))
    execute: bool = False                  # False = preview only (plan + impact analysis)
    confirm: bool = False                  # required for risky plans
    plan_id: Optional[str] = None          # re-use a previewed plan: zero extra LLM calls
    session_id: Optional[str] = None
    version_id: Optional[int] = None
    history: Optional[List[Dict[str, str]]] = None
    partial_ok: bool = False               # default all-or-nothing


class AdvancedChatResponse(BaseModel):
    status: str
    interpretation: str
    decision: str = "execute"
    parsed_actions: List[Dict[str, Any]] = []
    action_count: int = 0
    executed: bool = False
    execution_result: Optional[ActionExecuteResponse] = None
    issues: List[Dict[str, Any]] = []
    questions: List[str] = []
    assumptions: List[str] = []
    preflight: List[Dict[str, Any]] = []
    plan_id: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_reasons: List[str] = []
    active_rules: List[Dict[str, Any]] = []
    base_version_id: Optional[int] = None
    new_version_id: Optional[int] = None
    verified: Optional[bool] = None
    llm_calls: int = 0
    cached: bool = False
    warnings: List[str] = []


# =====================================================================
# 13. ORCHESTRATION
# =====================================================================
_OVERRIDE_RE = re.compile(
    r"(bypass|skip|without|no need for|don'?t (?:ask|need|require)|do not (?:ask|require))\W+(?:\w+\W+){0,3}?"
    r"(approval|confirmation|confirm|backup|validation|permission|authori[sz]ation)"
    r"|treat (?:it|this|that) as approved|ignore (?:the )?(?:constraints?|rules?|validation|approval)"
    r"|override (?:the )?(?:constraints?|validation|approval|safeguards?)", re.I)

_UNDO_RE = re.compile(r"^\s*(?:please\s+)?(?:undo|revert|roll\s*back)(?:\s+(?:the\s+)?(?:last|latest|previous|that|this))?(?:\s+(?:change|action|edit|update))?\s*[.!]?\s*$", re.I)
_UNDO_ALL_RE = re.compile(r"^\s*(?:please\s+)?(?:undo|revert|roll\s*back)\s+(?:all|everything|all changes)(?:\s+(?:in this (?:session|chat)|i made))?\s*[.!]?\s*$", re.I)
_VALIDATE_RE = re.compile(r"^\s*(?:please\s+)?(?:validate|check|verify)(?:\s+(?:the\s+)?(?:current\s+)?timetable)?\s*[.!?]?\s*$", re.I)
_RULES_SHOW_RE = re.compile(r"^\s*(?:show|list|what are)\s+(?:my\s+|the\s+|active\s+)?rules\s*[.!?]?\s*$", re.I)
_RULES_CLEAR_RE = re.compile(r"^\s*(?:clear|forget|reset|remove)\s+(?:all\s+)?(?:my\s+|the\s+)?rules\s*[.!?]?\s*$", re.I)
_HELP_RE = re.compile(r"^\s*(?:help|\?|what can you do\??)\s*$", re.I)

HELP_TEXT = (
    "I can plan and apply timetable changes from plain English – moves, swaps, teacher/room/time changes, cancellations, "
    "new subjects/teachers/programs, teacher unavailability and multi-step requests.\n\n"
    "• I validate everything against the live timetable (H1–H11) before anything is saved, and never claim success unless it is stored.\n"
    "• Use **execute=false** to preview, then confirm. Large or destructive plans always ask for confirmation.\n"
    "• Say things like “never schedule X after 1 pm” or “don't touch other teachers' lessons” – I'll keep them as rules (say “show rules” / “clear rules”).\n"
    "• Quick commands: **undo**, **undo everything**, **validate**.")


def _is_admin(user: User) -> bool:
    role = getattr(user, "role", None)
    return "admin" in str(getattr(role, "value", role)).lower()


def _user_limit(user_id: Any):
    with _USER_LIM_LOCK:
        lim = _USER_LIMITERS.get(user_id)
        if lim is None:
            if len(_USER_LIMITERS) > 2000:
                _USER_LIMITERS.clear()
            lim = _USER_LIMITERS[user_id] = _SlidingWindow(Cfg.USER_RPM)
    w = lim.try_acquire()
    if w > 0:
        raise HTTPException(429, f"Too many chat requests. Please wait {int(w) + 1}s.", headers={"Retry-After": str(int(w) + 1)})


def _get_version(db: Session, vid: Optional[int]):
    try:
        if vid:
            return timetable_service.get_version(db, vid)
        return db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
    except Exception:
        return None


def _load_entries(db: Session, vid: Optional[int]) -> Tuple[List[dict], Optional[int]]:
    v = _get_version(db, vid)
    if v is None:
        return [], None
    return [e.to_dict() for e in v.entries], v.id


def _local_command(text: str, sess: _Session, db: Session, base: Optional[int]) -> Optional[dict]:
    """Zero-LLM commands. Returns a plan dict, or {'_reply': str} for immediate answers."""
    if _HELP_RE.match(text):
        return {"_reply": HELP_TEXT}
    if _RULES_SHOW_RE.match(text):
        if not sess.rules:
            return {"_reply": "No active rules in this session."}
        return {"_reply": "Active rules:\n" + "\n".join(f"- **{r['id']}** ({r['kind']}): {r.get('text') or json.dumps(r['match'])}" for r in sess.rules)}
    if _RULES_CLEAR_RE.match(text):
        n = len(sess.rules)
        sess.rules.clear()
        return {"_reply": f"Cleared {n} rule(s)."}
    if _VALIDATE_RE.match(text):
        return {"decision": "execute", "summary": "Validate the current timetable.", "issues": [], "questions": [],
                "assumptions": [], "rules": [], "drop_rules": [], "actions": [{"action": "VALIDATE_TIMETABLE"}], "_local": True}
    all_undo = bool(_UNDO_ALL_RE.match(text))
    if all_undo or _UNDO_RE.match(text):
        v = _get_version(db, base)
        target = sess.first_base if all_undo else getattr(v, "parent_id", None)
        if not target:
            return {"_reply": "There is no earlier version to restore." if not all_undo else "I have no record of changes made in this session to undo."}
        return {"decision": "execute", "summary": f"Restore version {target}.", "issues": [], "questions": [], "assumptions": [],
                "rules": [], "drop_rules": [], "actions": [{"action": "RESTORE_VERSION", "version_id": int(target),
                                                              "change_summary": "Undo via chat"}], "_local": True}
    return None


def _plan_with_llm(text: str, entries: List[dict], cat: Catalog, sess: _Session, history: List[dict],
                   budget: _Budget, warnings: List[str]) -> dict:
    mention_extra = " ".join(m["content"] for m in history if m["role"] == "user") + " " + " ".join(sess.changes)
    chunks = [text] if len(text) <= Cfg.SINGLE_CALL_CHARS else _split_text(text, Cfg.CHUNK_CHARS)
    if len(chunks) > Cfg.MAX_CHUNKS:
        return {"decision": "clarify", "summary": "", "issues": [], "assumptions": [], "rules": [], "drop_rules": [], "actions": [],
                "questions": [f"That request is very long ({len(chunks)} parts). Please send it in smaller batches of related changes."]}
    if len(chunks) > 1:
        budget.limit = max(budget.limit, len(chunks) + 1)
        warnings.append(f"Long request processed in {len(chunks)} parts.")

    merged = {"decision": "execute", "summary": "", "issues": [], "questions": [], "assumptions": [], "rules": [],
              "drop_rules": [], "actions": []}
    live_rules = list(sess.rules)
    for ci, chunk in enumerate(chunks):
        sched = entries
        if ci > 0 and merged["actions"]:  # later parts see the effect of earlier parts
            tmp = run_preflight(merged["actions"], entries, cat, live_rules, None, True, True)  # db unused for schedule sims
            sched = tmp.final_schedule or entries
        ctx = build_context(chunk, sched, cat, sess, live_rules, mention_extra)
        user_msg = "CONTEXT\n" + ctx + ("\n\nPARTS ALREADY PLANNED: " + _clip(merged["summary"], 600) if ci else "") + \
                   f"\n\nREQUEST{f' (part {ci + 1}/{len(chunks)})' if len(chunks) > 1 else ''}\n" + chunk
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + (history if ci == 0 else []) + [{"role": "user", "content": user_msg}]
        plan = _llm_plan(messages, budget)
        merged["summary"] = (merged["summary"] + " " + plan["summary"]).strip()
        for k in ("issues", "questions", "assumptions", "rules", "drop_rules", "actions"):
            merged[k] += plan[k]
        for r in plan["rules"]:
            cr = clean_rule(r, cat)
            if cr:
                live_rules.append({**cr, "id": f"N{len(live_rules) + 1}"})
        if plan["decision"] in ("clarify", "infeasible", "reject"):
            merged["decision"] = plan["decision"]
            break
    merged["actions"] = merged["actions"][: Cfg.MAX_ACTIONS]
    return normalize_plan(merged)


def _repair_plan(base_messages_ctx: Tuple[str, str, List[dict]], plan: dict, pf: Preflight, budget: _Budget) -> dict:
    ctx, text, history = base_messages_ctx
    fb = []
    for o in pf.outcomes:
        if o.status == "blocked":
            s = f"#{o.index} {o.action}: {o.detail}"
            if o.suggestions:
                s += " | feasible alternatives: " + json.dumps(o.suggestions, separators=(",", ":"))[:400]
            fb.append(s)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
        {"role": "user", "content": "CONTEXT\n" + ctx + "\n\nREQUEST\n" + text},
        {"role": "assistant", "content": json.dumps({"decision": plan["decision"], "summary": plan["summary"], "actions": plan["actions"]}, separators=(",", ":"))[:5000]},
        {"role": "user", "content": "VALIDATION FEEDBACK from the backend (authoritative):\n" + "\n".join(fb)[:1500] +
         "\nReturn a corrected FULL JSON object. Fix targets/slots using the facts and alternatives above. If the request truly cannot be satisfied, set decision to \"infeasible\" and explain which constraints clash, with one concrete relaxation. Do not repeat failing actions."}]
    return _llm_plan(messages, budget)


def _verify_commit(db: Session, new_vid: Optional[int], predicted: List[dict]) -> Optional[bool]:
    if not new_vid:
        return None
    try:
        v = timetable_service.get_version(db, new_vid)
        actual = Counter(_ek(e.to_dict()) for e in v.entries)
        return actual == Counter(_ek(e) for e in predicted)
    except Exception as e:
        logger.debug(f"verify skipped: {e}")
        return None


def _to_exec_schema(result) -> ActionExecuteResponse:
    return ActionExecuteResponse(
        success=result.success,
        results=[ActionResultSchema(action_type=str(r.action_type), success=r.success, change_log=r.change_log or "",
                                    error=r.error or "", violated_constraint=r.violated_constraint,
                                    suggestions=r.suggestions, before=r.before, after=r.after) for r in result.results],
        new_version_id=result.new_version_id, violations=result.violations, schema_errors=result.schema_errors,
        change_log=result.change_log, partial_applied=result.partial_applied)


_ICON = {"ok": "✓", "deferred": "◐", "noop": "≈", "blocked": "✗", "skipped": "–"}


def _compose(plan: dict, pf: Optional[Preflight], status: str, extra: str = "", rules: Optional[List[dict]] = None) -> str:
    out: List[str] = []
    if plan.get("summary"):
        out.append(f"**Understood:** {plan['summary']}")
    if plan.get("issues"):
        out.append("**Findings:**\n" + "\n".join(f"- {'⛔' if i['blocking'] else 'ℹ️'} _{i['type']}_: {i['detail']}" for i in plan["issues"]))
    if plan.get("assumptions"):
        out.append("**Assumptions I made:**\n" + "\n".join(f"- {a}" for a in plan["assumptions"]))
    if pf and pf.outcomes:
        lines = []
        for o in pf.outcomes:
            act = o.action
            lines.append(f"{_ICON.get(o.status, '?')} {o.index + 1}. **{act}**" + (f" – {o.detail}" if o.detail else "") +
                         (f"\n   ↳ options: {json.dumps(o.suggestions)[:300]}" if o.suggestions else ""))
        out.append("**Safety check:**\n" + "\n".join(lines))
    if plan.get("questions"):
        out.append("**I need a bit more information:**\n" + "\n".join(f"{n}. {q}" for n, q in enumerate(plan["questions"], 1)))
    if extra:
        out.append(extra)
    return "\n\n".join(out) or status


def _remember(sess: _Session, user_text: str, resp: AdvancedChatResponse):
    if user_text:
        sess.turns.append(("user", _clip(user_text, 600)))
    mem = resp.interpretation.split("\n\n")[0]
    if resp.questions:
        mem += " Questions: " + " ".join(resp.questions)
    sess.turns.append(("assistant", _clip(mem, 500)))


def _handle(req: AdvancedChatRequest, bg: BackgroundTasks, response: Response, db: Session, user: User,
            if_match: Optional[str], idem_key: Optional[str]) -> AdvancedChatResponse:
    text = (req.text or "").strip()
    if not text and not req.plan_id:
        raise HTTPException(422, "Empty request")
    if len(text) > Cfg.MAX_INPUT_CHARS:
        raise HTTPException(413, f"Request too long ({len(text)} chars, max {Cfg.MAX_INPUT_CHARS}). Please split it.")
    _user_limit(user.id)

    sess = _get_session(f"{user.id}:{req.session_id or 'default'}")
    base = resolve_expected_version(req.version_id, if_match) or current_head(db)
    entries, head = _load_entries(db, base)
    cat = Catalog.load(db, entries)
    admin = _is_admin(user)
    budget = _Budget(Cfg.MAX_LLM_CALLS)
    warnings: List[str] = []
    history = _history_messages(req.history, sess)

    def respond(status: str, plan: dict, msg: str, pf: Optional[Preflight] = None, **kw) -> AdvancedChatResponse:
        r = AdvancedChatResponse(
            status=status, interpretation=msg, decision=plan.get("decision", "execute"),
            parsed_actions=kw.pop("actions", []),
            issues=plan.get("issues", []), questions=plan.get("questions", []), assumptions=plan.get("assumptions", []),
            preflight=[asdict(o) for o in (pf.outcomes if pf else [])], active_rules=sess.rules,
            base_version_id=head, llm_calls=budget.used, warnings=warnings + (pf.warnings if pf else []), **kw)
        r.action_count = len(r.parsed_actions)
        _remember(sess, text, r)
        return r

    # ---------- obtain plan ----------
    plan: Optional[dict] = None
    cached = False
    from_llm = False
    ckey = _cache_key(text, head, sess, history)
    if req.plan_id:
        stored = _PLANS.get(req.plan_id)
        if stored and stored["user_id"] == user.id and stored.get("done"):
            return respond("duplicate", {"decision": "execute"},
                           f"✅ That plan was already applied as version #{stored['done']}. I did not apply it twice.",
                           new_version_id=stored["done"])
        if stored and stored["user_id"] == user.id:
            plan = copy.deepcopy(stored["plan"])
        else:
            if not text:
                return respond("rejected", {}, "That plan has expired or belongs to another user. Please send your request again.")
    if plan is None:
        local = _local_command(text, sess, db, head)
        if local is not None and "_reply" in local:
            return respond("info", {"decision": "execute"}, local["_reply"])
        if local is not None:
            plan = local
        else:
            hit = _PLAN_CACHE.get(ckey)
            if hit:
                plan, cached = copy.deepcopy(hit), True
            else:
                plan = _plan_with_llm(text, entries, cat, sess, history, budget, warnings)
                from_llm = True

    # ---------- rules (session + request scoped) ----------
    for rid in plan.get("drop_rules", []):
        sess.rules[:] = [r for r in sess.rules if r["id"].lower() != str(rid).lower()]
    transient: List[dict] = []
    if plan["decision"] in ("execute", "clarify"):
        for i, r in enumerate(plan.get("rules", [])):
            cr = clean_rule(r, cat)
            if not cr:
                continue
            if cr["scope"] == "session" and not req.plan_id:
                _add_session_rule(sess, cr)
            else:
                transient.append({**cr, "id": f"T{i + 1}"})
    rules = list(sess.rules) + [t for t in transient if t["scope"] == "request" or req.plan_id]
    rules = [r for r in rules if r["kind"] in ("forbid", "protect")]

    dec = plan["decision"]
    if from_llm and dec in ("clarify", "infeasible", "reject"):
        _PLAN_CACHE.set(ckey, copy.deepcopy(plan))
    if dec in ("clarify", "infeasible", "reject"):
        status = {"clarify": "clarify", "infeasible": "infeasible", "reject": "rejected"}[dec]
        return respond(status, plan, _compose(plan, None, status), cached=cached)
    if not plan["actions"]:
        msg = _compose(plan, None, "ok", "Noted. I saved your rules for this session." if plan.get("rules") else "Nothing to change.")
        return respond("rules_saved" if plan.get("rules") else "noop", plan, msg, cached=cached)

    # ---------- preflight (+ bounded LLM repair) ----------
    override = bool(_OVERRIDE_RE.search(text))
    pf = run_preflight(plan["actions"], entries, cat, rules, db, req.partial_ok, admin)
    ctx_for_repair = None
    rounds = 0
    while pf.blocked and pf.repairable and not pf.clarify and not pf.forbidden and from_llm and rounds < Cfg.REPAIR_ROUNDS and budget.left > 0:
        rounds += 1
        if ctx_for_repair is None:
            ctx_for_repair = (build_context(text, entries, cat, sess, rules, " ".join(m["content"] for m in history if m["role"] == "user")), text, history)
        try:
            new_plan = _repair_plan(ctx_for_repair, plan, pf, budget)
        except LLMError as e:
            warnings.append(f"Repair skipped: {e}")
            break
        if new_plan["decision"] != "execute" or not new_plan["actions"]:
            plan = {**plan, **{k: new_plan[k] for k in ("decision", "summary", "issues", "questions", "assumptions")}, "actions": []}
            st = {"clarify": "clarify", "infeasible": "infeasible", "reject": "rejected"}.get(plan["decision"], "blocked")
            return respond(st, plan, _compose(plan, pf, st), pf)
        plan = new_plan
        pf = run_preflight(plan["actions"], entries, cat, rules, db, req.partial_ok, admin)
    if from_llm and not pf.clarify:
        _PLAN_CACHE.set(ckey, copy.deepcopy(plan))

    shown = [dict(g) for g in pf.grounded]
    if pf.clarify:
        plan = {**plan, "decision": "clarify", "questions": pf.clarify[:3]}
        return respond("clarify", plan, _compose(plan, pf, "clarify"), pf, cached=cached)
    runnable = pf.ok_count > 0 and (not pf.blocked or req.partial_ok)
    if pf.blocked and not runnable:
        status = "forbidden" if pf.forbidden else "blocked"
        reasons = "Nothing was changed. " + ("You can tell me to relax a rule (e.g. “drop R1”) or choose another slot." if any(o.kind == "rule" for o in pf.outcomes) else "Tell me how you'd like to adjust the request.")
        return respond(status, plan, _compose(plan, pf, status, reasons), pf, actions=shown, cached=cached)
    if not pf.grounded:
        return respond("noop", plan, _compose(plan, pf, "noop", "Everything requested is already in place – nothing to change."), pf, cached=cached)

    # ---------- store plan, assess risk ----------
    risk = list(pf.risk)
    if override:
        risk.append("your message tried to bypass approval/validation – those safeguards stay on")
    if pf.blocked and req.partial_ok:
        risk.append("some actions are blocked; only the valid ones would run")
    plan_id = req.plan_id or uuid.uuid4().hex[:16]
    _PLANS.set(plan_id, {"user_id": user.id, "plan": {**plan, "actions": pf.grounded}, "created": time.time()})
    risk_needs = bool(risk)
    read_only = all(a.get("action") == "VALIDATE_TIMETABLE" for a in pf.grounded)

    if not req.execute and not read_only:
        msg = _compose(plan, pf, "preview", "**Preview only – nothing has been changed.** Re-send with `execute=true` and this `plan_id` to apply."
                       + (" Confirmation required: " + "; ".join(risk) + " (add `confirm=true`)." if risk_needs else ""))
        return respond("preview", plan, msg, pf, actions=shown, plan_id=plan_id, requires_confirmation=risk_needs,
                       confirmation_reasons=risk, cached=cached)
    if risk_needs and not req.confirm:
        msg = _compose(plan, pf, "confirm", "**Confirmation required before I change anything:** " + "; ".join(risk) +
                       ". Re-send with `confirm=true` and this `plan_id`.")
        return respond("confirmation_required", plan, msg, pf, actions=shown, plan_id=plan_id, requires_confirmation=True,
                       confirmation_reasons=risk, cached=cached)

    # ---------- execute through the authoritative, concurrency-safe engine ----------
    fp = hashlib.sha256(json.dumps([head, pf.grounded], sort_keys=True, default=str).encode()).hexdigest()[:32]
    try:
        parsed = [parse_action(a) for a in pf.grounded]
    except Exception as e:
        return respond("blocked", plan, f"Internal validation error: {e}", pf, actions=shown)
    guarded = SafeActionEngine(db=db, user=user, base_version_id=base, partial_ok=req.partial_ok,
                               skip_suggestions=False, idempotency_key=idem_key or f"adv:{fp}").execute(parsed)
    result = guarded.result
    response.headers.update(guarded.response_headers())
    if result.success or result.partial_applied:
        bg.add_task(notify_teachers_of_changes, result, db)

    exec_resp = _to_exec_schema(result)
    verified = None
    if result.new_version_id and not pf.has_busy and not pf.has_catalog and not req.partial_ok:
        verified = _verify_commit(db, result.new_version_id, pf.final_schedule)
    if result.new_version_id:
        if sess.first_base is None:
            sess.first_base = head
        if result.change_log:
            sess.changes.append(_clip(result.change_log.replace("\n", " "), 200))
    if result.new_version_id:
        _PLANS.set(plan_id, {"user_id": user.id, "done": result.new_version_id})
    else:
        _PLANS.pop(plan_id)

    if result.success:
        status = "executed"
        lines = "\n".join(f"  ✓ {r.action_type}" + (f" – {_clip(r.change_log, 140)}" if r.change_log else "") for r in result.results)
        if result.new_version_id:
            extra = f"✅ **Applied and stored** as new version #{result.new_version_id}.\n{lines}"
        else:
            extra = f"✅ **Completed** (no new timetable version was needed).\n{lines}"
            for r in result.results:
                if isinstance(r.after, dict) and "violations" in r.after:
                    extra += f"\n🔎 Validation: {len(r.after.get('violations') or [])} violation(s), {len(r.after.get('schema_errors') or [])} schema error(s)."
        if verified is False:
            extra += "\n⚠ Post-commit check: the stored timetable differs from the prediction – please review version #" + str(result.new_version_id) + "."
            warnings.append("post-commit verification mismatch")
        elif verified:
            extra += "\n🔎 Post-commit check passed: stored timetable matches the validated plan."
    else:
        status = "partial" if result.partial_applied else "failed"
        lines = []
        for r in result.results:
            if r.success:
                lines.append(f"  ✓ {r.action_type}")
            else:
                vc = f" [{r.violated_constraint.get('rule', 'constraint')}]" if r.violated_constraint else ""
                lines.append(f"  ✗ {r.action_type}: {_clip(r.error or 'failed', 200)}{vc}")
        extra = ("⚠ Not everything was applied:\n" if result.partial_applied else "❌ **Nothing was applied.**\n") + "\n".join(lines)
    return respond(status, plan, _compose(plan, pf, status, extra), pf, actions=shown, executed=bool(result.success or result.partial_applied),
                   execution_result=exec_resp, new_version_id=result.new_version_id, verified=verified, cached=cached,
                   plan_id=None)


# =====================================================================
# 14. ENDPOINTS
# =====================================================================
@router.post("/advanced-chat", response_model=AdvancedChatResponse)
def advanced_chat(
    req: AdvancedChatRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
    if_match: Optional[str] = Depends(if_match_header),
    idem_key: Optional[str] = Depends(idempotency_key_header),
):
    """
    Plan -> deterministic preflight -> (optional confirm) -> safe execute.

    execute=false (default): returns the plan, per-action safety check and a plan_id.
    execute=true: applies it; risky plans additionally need confirm=true.
    """
    try:
        return _handle(req, background_tasks, response, db, user, if_match, idem_key)
    except HTTPException:
        raise
    except LLMBusy as e:
        raise HTTPException(429, f"{e}. Retry in {int(e.retry_after) + 1}s.", headers={"Retry-After": str(int(e.retry_after) + 1)})
    except LLMError as e:
        raise HTTPException(503, f"AI planner temporarily unavailable: {e}")
    except Exception:
        logger.exception("advanced-chat failed")
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(500, "Unexpected error while processing the request. Nothing was changed.")


@router.get("/advanced-chat/status")
def advanced_chat_status(user: User = Depends(require_teacher_or_admin)):
    return {
        "llm_calls_last_minute": _LLM_LIMITER.used(), "llm_rpm_limit": Cfg.LLM_RPM,
        "daily_used": _DAILY.count if Cfg.LLM_DAILY_CAP else None, "daily_cap": Cfg.LLM_DAILY_CAP or None,
        "breaker_open": _BREAKER.is_open(), "plan_cache_entries": len(_PLAN_CACHE),
        "stored_plans": len(_PLANS), "sessions": len(_SESSIONS),
        "providers": [f"{p['name']}:{p['model']}" for p in _providers()],
        "engine_simulation": _simulate_one is not None,
    }


@router.delete("/advanced-chat/session")
def advanced_chat_reset(session_id: Optional[str] = None, user: User = Depends(require_teacher_or_admin)):
    _SESSIONS.pop(f"{user.id}:{session_id or 'default'}")
    return {"ok": True}
