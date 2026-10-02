"""
calendar_system.py  —  Date-aware timetable layer (single file)
================================================================

Drop this file at  app/calendar_system.py  and wire it up in 3 lines (bottom of file).

WHAT IT ADDS
------------
Your weekly timetable (TimetableVersion -> entries, keyed by weekday) stays the
source of truth.  This module adds two *date-aware* layers on top of it:

1. VALIDITY WINDOWS   "Version #12 is valid for this WEEK / this MONTH / this
                       DATE RANGE."  Outside any window the currently PUBLISHED
                       version applies (exactly the old behaviour).
2. DAY OVERRIDES      Changes that apply to ONE calendar date only:
                         ADD     add a class on that date
                         CANCEL  cancel one class on that date
                         MODIFY  change time/teacher/room/subject/type of one class
                         DAY_OFF holiday (whole day, or one program/semester)

Resolution for a date D (deterministic, pure function of DB state):
    1. pick the validity window covering D   (priority desc, narrowest span, newest)
       else the PUBLISHED version
    2. take that version's entries whose weekday == weekday(D)
    3. apply overrides of D in the fixed order  MODIFY -> CANCEL -> DAY_OFF -> ADD

SAFETY GUARANTEES
-----------------
* Every ADD / MODIFY is run through your validate_schema + validate_schedule
  (H1 semester clash, H2 teacher clash, H3 room clash, H5, H11, H12 ...) on the
  *resulting day*.  Only violations INTRODUCED by the change count (pre-existing
  ones are not blamed on the admin).  New violations => HTTP 409, unless the admin
  sends force=true.  Schema/time-format errors can NEVER be forced.
* Deleting a CANCEL/override or a validity window is re-validated the same way
  (e.g. removing a CANCEL could resurrect a clash with an ADDed class).
* All writes are serialised (process lock + Postgres advisory lock), run in one
  transaction, and are protected by DB unique/check constraints.
* Past dates are read-only by default (CALENDAR_ALLOW_PAST_EDITS=1 to lift).
* Locale-independent weekday names, strict date/time parsing, bounded ranges,
  whitelisted payload keys, full audit log.

ENDPOINTS
---------
Public (no auth, read-only):
    GET  /calendar/day/{date}                ?program=&semester=&teacher=
    GET  /calendar/range?start=&end=         ?program=&semester=&teacher=   (<= 62 days)
    GET  /calendar/month/{year}/{month}      (per-day markers for a calendar grid)
Admin:
    GET    /admin/calendar/day/{date}        preview + live constraint check
    POST   /admin/calendar/overrides         create ADD / CANCEL / MODIFY / DAY_OFF
    GET    /admin/calendar/overrides         ?start=&end=
    DELETE /admin/calendar/overrides/{id}    ?force=
    POST   /admin/calendar/validity          make a version valid for WEEK/MONTH/RANGE
    GET    /admin/calendar/validity          ?start=&end=
    DELETE /admin/calendar/validity/{id}     ?force=
    GET    /admin/calendar/audit

WIRING (app/main.py)
--------------------
    from app.calendar_system import router as calendar_router, init_calendar_tables
    init_calendar_tables(engine)          # or generate an Alembic migration
    app.include_router(calendar_router, prefix="/api")   # adjust prefix to yours

IMPORT PATHS TO CHECK  (guessed from your snippets — adjust the 4 lines marked ###)
"""
from __future__ import annotations

import calendar as _pycal
import datetime as dt
import hashlib
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, select, text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.database import get_db                                              ###
from app.core.dependencies import get_current_user                               ###
from app.models.base import Base                                                  ###
from app.models.timetable import TimetableVersion, VersionStatus                  ###
from app.scheduler.constraints import (
    PROGRAMME_SUBJECT_MAP, SCHEDULE_REQUIRED_KEYS, VALID_PROGRAMS, normalize_program,
)
from app.scheduler.validator import teacher_set, to_minutes, validate_schedule, validate_schema
from app.services import timetable_service

log = logging.getLogger("calendar_system")

# ═════════════════════════════════════════════════════════════════════════════
# Configuration
# ═════════════════════════════════════════════════════════════════════════════
CAL_TZ_NAME = os.getenv("CALENDAR_TIMEZONE", "Asia/Kolkata")
ALLOW_PAST_EDITS = os.getenv("CALENDAR_ALLOW_PAST_EDITS", "0").strip().lower() in {"1", "true", "yes"}
MAX_FUTURE_DAYS = 730          # reject typos like year 2062
MAX_WINDOW_DAYS = 366          # longest validity window
MAX_PUBLIC_RANGE_DAYS = 62
MAX_ADMIN_RANGE_DAYS = 366
MIN_DATE, MAX_DATE = dt.date(2000, 1, 1), dt.date(2100, 12, 31)
PUBLIC_CACHE_SECONDS = 30

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SEMESTERS = {"1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"}
EDITABLE_FIELDS = ("start", "end", "subject_code", "subject_name", "teacher", "room", "type")
ENTRY_KEYS = set(SCHEDULE_REQUIRED_KEYS) | set(EDITABLE_FIELDS) | {"program", "semester", "day"}
ACTION_ORDER = {"MODIFY": 0, "CANCEL": 1, "DAY_OFF": 2, "ADD": 3}
META = "cal_"                   # prefix of every metadata key we attach to an entry
TEXT_MAX = 120
UNFORCEABLE_RULES = {"start_not_before_end", "bad_time_format"}   # + anything "schema_*"


def _today() -> dt.date:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(CAL_TZ_NAME)).date()
    except Exception:  # tzdata missing (e.g. Windows) -> UTC
        return dt.datetime.now(dt.timezone.utc).date()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ═════════════════════════════════════════════════════════════════════════════
# Errors
# ═════════════════════════════════════════════════════════════════════════════
def _err(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


# ═════════════════════════════════════════════════════════════════════════════
# ORM models
# ═════════════════════════════════════════════════════════════════════════════
class CalendarValidityWindow(Base):
    """'Version X is the timetable in force from start_date to end_date (inclusive)'."""
    __tablename__ = "calendar_validity_windows"
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="ck_cal_win_range"),
        CheckConstraint("scope IN ('WEEK','MONTH','RANGE')", name="ck_cal_win_scope"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("timetable_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    start_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CalendarOverride(Base):
    """A change that applies to exactly one calendar date."""
    __tablename__ = "calendar_overrides"
    __table_args__ = (
        UniqueConstraint("on_date", "action", "target_key", name="uq_cal_override_identity"),
        CheckConstraint("action IN ('ADD','CANCEL','MODIFY','DAY_OFF')", name="ck_cal_override_action"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    on_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    # CANCEL/MODIFY: key of the base entry.  ADD: fingerprint of the new entry.
    # DAY_OFF: "<program|*>|<semester|*>".
    target_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    target_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")   # snapshot of base entry
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    validation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # violations accepted by force
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CalendarAuditLog(Base):
    __tablename__ = "calendar_audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


def init_calendar_tables(engine) -> None:
    """Create the three calendar tables (idempotent). Prefer Alembic in production."""
    Base.metadata.create_all(bind=engine, tables=[
        CalendarValidityWindow.__table__, CalendarOverride.__table__, CalendarAuditLog.__table__])


# ═════════════════════════════════════════════════════════════════════════════
# Small helpers
# ═════════════════════════════════════════════════════════════════════════════
def _json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)


def _loads(s: Optional[str]) -> dict:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _tn(s: Any) -> str:
    """Lenient time normaliser used for identity keys: '9:00' -> '09:00'."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(s or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else str(s or "").strip()


def _time_strict(field_name: str, s: str) -> str:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", s)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise _err(422, "BAD_TIME", f"'{field_name}' must be HH:MM (24h), got {s!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _safe_minutes(s: Any) -> int:
    try:
        return to_minutes(str(s))
    except Exception:
        return 0


def _pkey(p: Any) -> str:
    try:
        return str(normalize_program(p) or "").strip().lower()
    except Exception:
        return str(p or "").strip().lower().replace(".", "")


def _canonical_program(raw: Any) -> str:
    """Map any spelling ('btech', 'B.Tech.') to the display form your data uses ('B.Tech')."""
    raw_s = str(raw or "").strip().rstrip(".")
    display = {_pkey(p): p for (p, _s) in PROGRAMME_SUBJECT_MAP.keys()}
    return display.get(_pkey(raw_s), raw_s)


def entry_key(e: dict) -> str:
    """Stable identity of a weekly entry (survives re-reads; changes if the entry's content changes)."""
    teachers = ",".join(sorted(t.lower() for t in teacher_set(e)))
    parts = [
        _pkey(e.get("program")), str(e.get("semester", "")).strip().lower(),
        str(e.get("day", "")).strip().lower(), _tn(e.get("start")), _tn(e.get("end")),
        str(e.get("subject_code") or e.get("subject_name") or "").strip().lower(),
        teachers, str(e.get("room") or "").strip().lower(), str(e.get("type") or "").strip().lower(),
    ]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def _assert_sane_date(d: dt.date) -> None:
    if not (MIN_DATE <= d <= MAX_DATE):
        raise _err(422, "DATE_OUT_OF_RANGE", f"Date must be between {MIN_DATE} and {MAX_DATE}.")


def _assert_editable_date(d: dt.date) -> None:
    _assert_sane_date(d)
    today = _today()
    if d < today and not ALLOW_PAST_EDITS:
        raise _err(409, "PAST_DATE", f"{d} is in the past; past days are read-only.")
    if d > today + dt.timedelta(days=MAX_FUTURE_DAYS):
        raise _err(422, "DATE_TOO_FAR", f"{d} is more than {MAX_FUTURE_DAYS} days ahead.")


def _parse_filters(program: Optional[str], semester: Optional[str], teacher: Optional[str]):
    prog = None
    if program:
        prog = _canonical_program(program)
        if _pkey(prog) not in {_pkey(p) for p in VALID_PROGRAMS} and normalize_program(prog) not in VALID_PROGRAMS:
            raise _err(422, "BAD_PROGRAM", f"Unknown program {program!r}.")
    sem = None
    if semester:
        sem = semester.strip().lower()
        if sem not in SEMESTERS:
            raise _err(422, "BAD_SEMESTER", f"Semester must be one of {sorted(SEMESTERS)}.")
    t = teacher.strip().lower() if teacher and teacher.strip() else None
    return prog, sem, t


# ═════════════════════════════════════════════════════════════════════════════
# In-memory records + resolution context (pure logic — no writes)
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class WinRec:
    id: Optional[int]
    version_id: int
    scope: str
    start: dt.date
    end: dt.date
    label: str
    priority: int


@dataclass
class OvRec:
    id: Optional[int]
    on_date: dt.date
    action: str
    target_key: str
    payload: dict
    target: dict
    reason: str
    forced: bool = False


@dataclass
class Ctx:
    db: Session
    windows: list
    overrides: dict
    published_id: Optional[int]
    entries_cache: dict = field(default_factory=dict)
    status_cache: dict = field(default_factory=dict)

    def entries(self, vid: int) -> list:
        if vid not in self.entries_cache:
            v = self.db.get(TimetableVersion, vid)
            self.entries_cache[vid] = [e.to_dict() for e in v.entries] if v else []
        return self.entries_cache[vid]

    def status(self, vid: int) -> Optional[str]:
        if vid not in self.status_cache:
            v = self.db.get(TimetableVersion, vid)
            self.status_cache[vid] = (getattr(v.status, "value", v.status) if v else None)
        return self.status_cache[vid]


def _win_rec(w: CalendarValidityWindow) -> WinRec:
    return WinRec(w.id, w.version_id, w.scope, w.start_date, w.end_date, w.label, w.priority)


def _ov_rec(o: CalendarOverride) -> OvRec:
    return OvRec(o.id, o.on_date, o.action, o.target_key, _loads(o.payload_json),
                 _loads(o.target_json), o.reason, o.forced)


def _load_ctx(db: Session, start: dt.date, end: dt.date) -> Ctx:
    wins = db.execute(select(CalendarValidityWindow).where(
        CalendarValidityWindow.start_date <= end, CalendarValidityWindow.end_date >= start)).scalars().all()
    ovs = db.execute(select(CalendarOverride).where(
        CalendarOverride.on_date >= start, CalendarOverride.on_date <= end)).scalars().all()
    by_date: dict = {}
    for o in ovs:
        by_date.setdefault(o.on_date, []).append(_ov_rec(o))
    pub = timetable_service.get_published_version(db)
    ctx = Ctx(db=db, windows=[_win_rec(w) for w in wins], overrides=by_date,
              published_id=pub.id if pub else None)
    if pub:
        ctx.entries_cache[pub.id] = [e.to_dict() for e in pub.entries]
        ctx.status_cache[pub.id] = getattr(pub.status, "value", pub.status)
    return ctx


def _pick_window(ctx: Ctx, d: dt.date) -> Optional[WinRec]:
    cands = [w for w in ctx.windows
             if w.start <= d <= w.end and ctx.status(w.version_id) not in (None, "DRAFT")]
    if not cands:
        return None
    # highest priority, then narrowest span, then newest
    return min(cands, key=lambda w: (-w.priority, (w.end - w.start).days,
                                     -(w.id if w.id is not None else 10 ** 12)))


def _base_for_date(ctx: Ctx, d: dt.date):
    win = _pick_window(ctx, d)
    vid = win.version_id if win else ctx.published_id
    weekday = WEEKDAYS[d.weekday()].lower()
    base: list = []
    if vid is not None:
        for raw in ctx.entries(vid):
            if str(raw.get("day", "")).strip().lower() == weekday:
                e = dict(raw)
                e[META + "key"] = entry_key(e)
                e[META + "source"] = "base"
                e[META + "version_id"] = vid
                base.append(e)
    return vid, win, base


def _resolve_day(ctx: Ctx, d: dt.date, extra: Optional[list] = None) -> dict:
    vid, win, live = _base_for_date(ctx, d)
    weekday = WEEKDAYS[d.weekday()]
    recs = list(ctx.overrides.get(d, [])) + list(extra or [])
    recs.sort(key=lambda o: (ACTION_ORDER[o.action], o.id if o.id is not None else 10 ** 12))
    cancelled: list = []
    warnings: list = []
    day_offs: list = []

    for o in recs:
        if o.action == "MODIFY":
            hit = [e for e in live if e[META + "key"] == o.target_key]
            if not hit:
                warnings.append(f"Override #{o.id} (MODIFY) targets a class that is not in the "
                                f"timetable now in force; it was ignored.")
                continue
            changes = o.payload.get("changes", {})
            for e in hit:
                e[META + "original"] = {k: e.get(k) for k in changes}
                e.update(changes)
                e[META + "source"] = "modified"
                e[META + "override_id"] = o.id
                if o.reason:
                    e[META + "note"] = o.reason
        elif o.action == "CANCEL":
            hit = [e for e in live if e[META + "key"] == o.target_key]
            if not hit:
                warnings.append(f"Override #{o.id} (CANCEL) targets a class that is not in the "
                                f"timetable now in force; it was ignored.")
                continue
            for e in hit:
                live.remove(e)
                cancelled.append({**e, META + "cancelled_by": "cancel",
                                  META + "override_id": o.id, META + "note": o.reason})
        elif o.action == "DAY_OFF":
            prog, sem = o.payload.get("program"), o.payload.get("semester")
            day_offs.append({"override_id": o.id, "program": prog, "semester": sem, "reason": o.reason})
            for e in list(live):
                if ((prog is None or _pkey(e.get("program")) == _pkey(prog)) and
                        (sem is None or str(e.get("semester", "")).strip().lower() == sem.lower())):
                    live.remove(e)
                    cancelled.append({**e, META + "cancelled_by": "day_off",
                                      META + "override_id": o.id, META + "note": o.reason})
        elif o.action == "ADD":
            e = dict(o.payload.get("entry", {}))
            e["day"] = weekday
            e[META + "key"] = o.target_key
            e[META + "source"] = "added"
            e[META + "override_id"] = o.id
            e[META + "version_id"] = None
            if o.reason:
                e[META + "note"] = o.reason
            live.append(e)

    live.sort(key=lambda e: (_safe_minutes(e.get("start")), str(e.get("program", "")),
                             str(e.get("semester", "")), str(e.get("subject_code") or e.get("subject_name") or "")))
    cancelled.sort(key=lambda e: (_safe_minutes(e.get("start")), str(e.get("program", ""))))

    notices: list = []
    for x in day_offs:
        scope = " / ".join(s for s in (x["program"], x["semester"]) if s) or "all classes"
        notices.append(f"No classes ({scope})" + (f": {x['reason']}" if x["reason"] else "."))
    if vid is None:
        notices.append("No timetable is published for this date.")

    return {
        "date": d.isoformat(), "weekday": weekday, "version_id": vid,
        "validity_id": win.id if win else None, "validity_label": win.label if win else None,
        "is_holiday": any(x["program"] is None and x["semester"] is None for x in day_offs),
        "has_overrides": bool(recs), "day_offs": day_offs,
        "entries": live, "cancelled": cancelled, "notices": notices, "warnings": warnings,
    }


def _resolve_range(ctx: Ctx, start: dt.date, end: dt.date) -> list:
    out, d = [], start
    while d <= end:
        out.append(_resolve_day(ctx, d))
        d += dt.timedelta(days=1)
    return out


# ── filtering (presentation only; validation always runs on the unfiltered day) ──
def _dayoff_applies(df: dict, prog: Optional[str], sem: Optional[str]) -> bool:
    if df["program"] is None and df["semester"] is None:
        return True
    ok_p = df["program"] is None or (prog is not None and _pkey(prog) == _pkey(df["program"]))
    ok_s = df["semester"] is None or (sem is not None and sem == str(df["semester"]).lower())
    return ok_p and ok_s


def _apply_filters(day: dict, prog: Optional[str], sem: Optional[str], teacher: Optional[str]) -> dict:
    def keep(e: dict) -> bool:
        if prog and _pkey(e.get("program")) != _pkey(prog):
            return False
        if sem and str(e.get("semester", "")).strip().lower() != sem:
            return False
        if teacher and teacher not in {t.lower() for t in teacher_set(e)}:
            return False
        return True
    out = dict(day)
    out["entries"] = [e for e in day["entries"] if keep(e)]
    out["cancelled"] = [e for e in day["cancelled"] if keep(e)]
    if prog or sem:
        out["is_holiday"] = any(_dayoff_applies(x, prog, sem) for x in day["day_offs"])
        out["notices"] = [n for n, x in zip(
            [n for n in day["notices"] if n.startswith("No classes (")], day["day_offs"])
            if _dayoff_applies(x, prog, sem)] + [n for n in day["notices"] if not n.startswith("No classes (")]
    return out


def _public_view(day: dict) -> dict:
    d = {k: v for k, v in day.items() if k != "warnings"}
    return d


# ═════════════════════════════════════════════════════════════════════════════
# Validation (delta based)
# ═════════════════════════════════════════════════════════════════════════════
def _strip_meta(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith(META)}


def _violations(entries: list) -> dict:
    clean = [_strip_meta(e) for e in entries]
    try:
        found = list(validate_schema(clean)) + list(validate_schedule(clean))
    except Exception as exc:  # validator crashed -> never silently accept
        log.exception("validator crashed")
        raise _err(500, "VALIDATOR_ERROR", f"Constraint validator failed: {exc}")
    return {_json(v): v for v in found}


def _unforceable(v: dict) -> bool:
    rule = str(v.get("rule", ""))
    return rule.startswith("schema_") or rule in UNFORCEABLE_RULES


def _check_delta(before: dict, after: dict, force: bool) -> list:
    """Return accepted-by-force violations; raise 409 on blocking ones."""
    new = [v for sig, v in after.items() if sig not in before]
    if not new:
        return []
    hard = [v for v in new if _unforceable(v)]
    if hard or not force:
        raise _err(409, "CONSTRAINT_VIOLATION",
                   "This change would introduce hard-constraint violations.",
                   violations=new[:50], can_force=not hard)
    return new


# ═════════════════════════════════════════════════════════════════════════════
# Concurrency guard + audit
# ═════════════════════════════════════════════════════════════════════════════
_WRITE_LOCK = threading.RLock()


@contextmanager
def _write_guard(db: Session):
    """Serialise calendar writes (process lock + Postgres advisory xact lock); rollback on error."""
    with _WRITE_LOCK:
        try:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": 7_310_042})
            yield
        except Exception:
            db.rollback()
            raise


def _audit(db: Session, user: Any, action: str, entity: str, entity_id: Optional[int], payload: dict) -> None:
    db.add(CalendarAuditLog(actor_id=getattr(user, "id", None), action=action, entity=entity,
                            entity_id=entity_id, payload_json=_json(payload)))


# ═════════════════════════════════════════════════════════════════════════════
# Auth
# ═════════════════════════════════════════════════════════════════════════════
def require_admin(user: Any = Depends(get_current_user)):
    """Swap for your own admin dependency if you have one."""
    role = getattr(user, "role", None)
    role = getattr(role, "value", role)
    if bool(getattr(user, "is_admin", False)) or str(role or "").strip().lower() in {"admin", "administrator", "superadmin"}:
        return user
    raise _err(403, "FORBIDDEN", "Administrator access required.")


# ═════════════════════════════════════════════════════════════════════════════
# Request schemas
# ═════════════════════════════════════════════════════════════════════════════
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ValidityCreate(_Strict):
    version_id: int = Field(gt=0)
    scope: Literal["WEEK", "MONTH", "RANGE"]
    anchor_date: Optional[dt.date] = None      # WEEK: any date in the week (Mon–Sun). MONTH: any date in the month.
    start_date: Optional[dt.date] = None       # RANGE only
    end_date: Optional[dt.date] = None         # RANGE only
    label: str = Field("", max_length=120)
    priority: int = Field(0, ge=-100, le=100)
    force: bool = False

    @model_validator(mode="after")
    def _shape(self):
        if self.scope in ("WEEK", "MONTH"):
            if self.anchor_date is None:
                raise ValueError(f"anchor_date is required for scope {self.scope}")
            if self.start_date or self.end_date:
                raise ValueError("start_date/end_date are only valid for scope RANGE")
        else:
            if self.start_date is None or self.end_date is None:
                raise ValueError("start_date and end_date are required for scope RANGE")
            if self.anchor_date:
                raise ValueError("anchor_date is not valid for scope RANGE")
        self.label = self.label.strip()
        return self


class OverrideCreate(_Strict):
    date: dt.date
    action: Literal["ADD", "CANCEL", "MODIFY", "DAY_OFF"]
    target_key: Optional[str] = Field(None, min_length=1, max_length=64)   # CANCEL / MODIFY (the entry's cal_key)
    entry: Optional[dict] = None                                           # ADD
    changes: Optional[dict] = None                                         # MODIFY
    program: Optional[str] = Field(None, max_length=30)                    # DAY_OFF filter
    semester: Optional[str] = Field(None, max_length=10)                   # DAY_OFF filter
    reason: str = Field("", max_length=300)
    force: bool = False

    @model_validator(mode="after")
    def _shape(self):
        a = self.action
        has = {"target_key": self.target_key is not None, "entry": self.entry is not None,
               "changes": self.changes is not None, "program/semester": bool(self.program or self.semester)}
        need = {"ADD": {"entry"}, "CANCEL": {"target_key"}, "MODIFY": {"target_key", "changes"},
                "DAY_OFF": set()}[a]
        allowed = need | ({"program/semester"} if a == "DAY_OFF" else set())
        for k, present in has.items():
            if k in need and not present:
                raise ValueError(f"'{k}' is required for action {a}")
            if present and k not in allowed:
                raise ValueError(f"'{k}' is not valid for action {a}")
        self.reason = self.reason.strip()
        return self


# ═════════════════════════════════════════════════════════════════════════════
# Payload normalisation
# ═════════════════════════════════════════════════════════════════════════════
def _clean_value(k: str, v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise _err(422, "BAD_FIELD", f"Field '{k}' must be a string.")
    s = str(v).strip()
    if len(s) > TEXT_MAX:
        raise _err(422, "BAD_FIELD", f"Field '{k}' is longer than {TEXT_MAX} characters.")
    if k in ("start", "end"):
        s = _time_strict(k, s)
    return s


def _normalize_entry(raw: dict, d: dt.date) -> dict:
    unknown = set(raw) - ENTRY_KEYS
    if unknown:
        raise _err(422, "UNKNOWN_FIELDS", f"Unknown entry fields: {sorted(unknown)}")
    e = {k: _clean_value(k, v) for k, v in raw.items()}
    weekday = WEEKDAYS[d.weekday()]
    if e.get("day") and e["day"].lower() != weekday.lower():
        raise _err(422, "DAY_MISMATCH", f"{d} is a {weekday}, but entry.day is {e['day']!r}.")
    e["day"] = weekday
    missing = [k for k in ("program", "semester", "start", "end", "type", "teacher", "room") if not e.get(k)]
    if not (e.get("subject_code") or e.get("subject_name")):
        missing.append("subject_code|subject_name")
    if missing:
        raise _err(422, "MISSING_FIELDS", f"Missing required entry fields: {missing}")
    e["program"] = _canonical_program(e["program"])
    e["semester"] = e["semester"].lower()
    if to_minutes(e["start"]) >= to_minutes(e["end"]):
        raise _err(422, "BAD_TIME", "start must be before end.")
    return e


def _normalize_changes(raw: dict, base: dict) -> dict:
    if not raw:
        raise _err(422, "NO_CHANGES", "'changes' is empty.")
    bad = set(raw) - set(EDITABLE_FIELDS)
    if bad:
        raise _err(422, "UNKNOWN_FIELDS",
                   f"Cannot modify {sorted(bad)}. Allowed: {list(EDITABLE_FIELDS)} "
                   f"(to move a class to another program/semester/day, CANCEL it and ADD a new one).")
    ch = {k: _clean_value(k, v) for k, v in raw.items()}
    for k in ("teacher", "room", "type", "start", "end"):
        if k in ch and not ch[k]:
            raise _err(422, "BAD_FIELD", f"'{k}' cannot be empty.")
    ch = {k: v for k, v in ch.items() if v != str(base.get(k) or "").strip()}   # drop no-ops
    if not ch:
        raise _err(422, "NO_CHANGES", "None of the supplied values differ from the current class.")
    merged_start = ch.get("start", _tn(base.get("start")))
    merged_end = ch.get("end", _tn(base.get("end")))
    if to_minutes(merged_start) >= to_minutes(merged_end):
        raise _err(422, "BAD_TIME", "start must be before end.")
    if "subject_code" in ch and not ch["subject_code"] and not (ch.get("subject_name") or base.get("subject_name")):
        raise _err(422, "BAD_FIELD", "A class needs a subject_code or subject_name.")
    return ch


def _build_candidate(ctx: Ctx, d: dt.date, p: OverrideCreate, before_day: dict):
    """Return the (unsaved) OvRec for the request, or raise a precise HTTP error."""
    existing = ctx.overrides.get(d, [])
    if p.action == "ADD":
        entry = _normalize_entry(p.entry or {}, d)
        key = entry_key(entry)
        if any(e[META + "key"] == key for e in before_day["entries"]):
            raise _err(409, "ALREADY_SCHEDULED", "An identical class already exists on this date.")
        return OvRec(None, d, "ADD", key, {"entry": entry}, {}, p.reason)

    if p.action in ("CANCEL", "MODIFY"):
        _vid, _win, base = _base_for_date(ctx, d)
        target = next((e for e in base if e[META + "key"] == p.target_key), None)
        if target is None:
            raise _err(404, "TARGET_NOT_FOUND",
                       "No such class in the timetable in force on this date (it may be an added class, "
                       "or the key is stale — reload the day).")
        if any(o.action in ("CANCEL", "MODIFY") and o.target_key == p.target_key for o in existing):
            raise _err(409, "ALREADY_OVERRIDDEN",
                       "This class already has a CANCEL/MODIFY for this date. Delete it first.")
        snap = _strip_meta(target)
        if p.action == "CANCEL":
            return OvRec(None, d, "CANCEL", p.target_key, {}, snap, p.reason)
        changes = _normalize_changes(p.changes or {}, target)
        return OvRec(None, d, "MODIFY", p.target_key, {"changes": changes}, snap, p.reason)

    # DAY_OFF
    prog = _canonical_program(p.program) if p.program else None
    if prog and _pkey(prog) not in {_pkey(x) for (x, _s) in PROGRAMME_SUBJECT_MAP.keys()}:
        raise _err(422, "BAD_PROGRAM", f"Unknown program {p.program!r}.")
    sem = p.semester.strip().lower() if p.semester else None
    if sem and sem not in SEMESTERS:
        raise _err(422, "BAD_SEMESTER", f"Semester must be one of {sorted(SEMESTERS)}.")
    key = f"{_pkey(prog) if prog else '*'}|{sem or '*'}"
    return OvRec(None, d, "DAY_OFF", key, {"program": prog, "semester": sem}, {}, p.reason)


# ═════════════════════════════════════════════════════════════════════════════
# Serialisation
# ═════════════════════════════════════════════════════════════════════════════
def _ov_out(o: CalendarOverride) -> dict:
    payload = _loads(o.payload_json)
    return {
        "id": o.id, "date": o.on_date.isoformat(), "weekday": WEEKDAYS[o.on_date.weekday()],
        "action": o.action, "target_key": o.target_key,
        "entry": payload.get("entry"), "changes": payload.get("changes"),
        "program": payload.get("program"), "semester": payload.get("semester"),
        "target": _loads(o.target_json) or None, "reason": o.reason, "forced": o.forced,
        "forced_violations": json.loads(o.validation_json) if o.validation_json else None,
        "created_by": o.created_by, "created_at": o.created_at.isoformat() if o.created_at else None,
    }


def _win_out(w: CalendarValidityWindow) -> dict:
    return {"id": w.id, "version_id": w.version_id, "scope": w.scope,
            "start_date": w.start_date.isoformat(), "end_date": w.end_date.isoformat(),
            "label": w.label, "priority": w.priority, "created_by": w.created_by,
            "created_at": w.created_at.isoformat() if w.created_at else None}


# ═════════════════════════════════════════════════════════════════════════════
# Services — overrides
# ═════════════════════════════════════════════════════════════════════════════
def create_override(db: Session, user: Any, p: OverrideCreate) -> dict:
    d = p.date
    _assert_editable_date(d)
    with _write_guard(db):
        ctx = _load_ctx(db, d, d)
        before_day = _resolve_day(ctx, d)
        before_v = _violations(before_day["entries"])
        cand = _build_candidate(ctx, d, p, before_day)
        after_day = _resolve_day(ctx, d, extra=[cand])
        accepted = _check_delta(before_v, _violations(after_day["entries"]), p.force)

        row = CalendarOverride(
            on_date=d, action=cand.action, target_key=cand.target_key,
            payload_json=_json(cand.payload), target_json=_json(cand.target), reason=cand.reason,
            forced=bool(accepted), validation_json=_json(accepted) if accepted else None,
            created_by=getattr(user, "id", None))
        try:
            db.add(row)
            db.flush()
        except IntegrityError:
            db.rollback()
            raise _err(409, "DUPLICATE", "An identical override already exists for this date.")
        _audit(db, user, "CREATE", "override", row.id,
               {"date": d.isoformat(), "action": cand.action, "payload": cand.payload,
                "target": cand.target, "forced": bool(accepted)})
        db.commit()
        db.refresh(row)
        out = _ov_out(row)

    day = _resolve_day(_load_ctx(db, d, d), d)
    return {"override": out, "forced_violations": accepted, "day": day}


def delete_override(db: Session, user: Any, override_id: int, force: bool) -> dict:
    with _write_guard(db):
        row = db.get(CalendarOverride, override_id)
        if row is None:
            raise _err(404, "NOT_FOUND", f"Override #{override_id} does not exist.")
        d = row.on_date
        _assert_editable_date(d)
        ctx = _load_ctx(db, d, d)
        before_v = _violations(_resolve_day(ctx, d)["entries"])
        ctx.overrides[d] = [o for o in ctx.overrides.get(d, []) if o.id != override_id]
        after_v = _violations(_resolve_day(ctx, d)["entries"])
        _check_delta(before_v, after_v, force)
        snap = _ov_out(row)
        db.delete(row)
        _audit(db, user, "DELETE", "override", override_id, snap)
        db.commit()
    return {"deleted": override_id, "day": _resolve_day(_load_ctx(db, d, d), d)}


# ═════════════════════════════════════════════════════════════════════════════
# Services — validity windows
# ═════════════════════════════════════════════════════════════════════════════
def _span(p: ValidityCreate) -> tuple:
    if p.scope == "WEEK":
        a = p.anchor_date
        start = a - dt.timedelta(days=a.weekday())
        return start, start + dt.timedelta(days=6)
    if p.scope == "MONTH":
        a = p.anchor_date
        return a.replace(day=1), a.replace(day=_pycal.monthrange(a.year, a.month)[1])
    return p.start_date, p.end_date


def _impact(db: Session, start: dt.date, end: dt.date, add: Optional[WinRec] = None,
            remove_id: Optional[int] = None) -> tuple:
    """Days that carry overrides are re-resolved with/without the window: report NEW violations + orphans."""
    ctx = _load_ctx(db, start, end)
    ctx2 = replace(ctx, windows=[w for w in ctx.windows if w.id != remove_id] + ([add] if add else []))
    new_viol, warnings = [], []
    for d in sorted(ctx.overrides):
        if not (start <= d <= end):
            continue
        bv = _violations(_resolve_day(ctx, d)["entries"])
        after = _resolve_day(ctx2, d)
        av = _violations(after["entries"])
        new_viol += [{"date": d.isoformat(), "violation": v} for s, v in av.items() if s not in bv]
        warnings += [f"{d.isoformat()}: {w}" for w in after["warnings"]]
    return new_viol, warnings


def create_validity(db: Session, user: Any, p: ValidityCreate) -> dict:
    start, end = _span(p)
    _assert_sane_date(start)
    _assert_sane_date(end)
    if end < start:
        raise _err(422, "BAD_RANGE", "end_date is before start_date.")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise _err(422, "RANGE_TOO_LONG", f"A validity window may span at most {MAX_WINDOW_DAYS} days.")
    today = _today()
    if end < today and not ALLOW_PAST_EDITS:
        raise _err(409, "PAST_DATE", "The window lies entirely in the past; past days are read-only.")
    if start > today + dt.timedelta(days=MAX_FUTURE_DAYS):
        raise _err(422, "DATE_TOO_FAR", f"Window starts more than {MAX_FUTURE_DAYS} days ahead.")

    with _write_guard(db):
        ver = db.get(TimetableVersion, p.version_id)
        if ver is None:
            raise _err(404, "VERSION_NOT_FOUND", f"Timetable version #{p.version_id} does not exist.")
        status = getattr(ver.status, "value", ver.status)
        if status not in (VersionStatus.VALIDATED.value, VersionStatus.PUBLISHED.value):
            raise _err(409, "VERSION_NOT_READY",
                       f"Version #{ver.id} is {status}; only VALIDATED or PUBLISHED versions can be scheduled.")
        entries = [e.to_dict() for e in ver.entries]
        if not entries:
            raise _err(422, "EMPTY_VERSION", f"Version #{ver.id} has no entries.")
        vv = list(_violations(entries).values())
        if vv and not (p.force and not any(_unforceable(v) for v in vv)):
            raise _err(409, "VERSION_INVALID", f"Version #{ver.id} violates hard constraints.",
                       violations=vv[:50], can_force=not any(_unforceable(v) for v in vv))

        label = p.label or f"{p.scope.title()} {start.isoformat()} → {end.isoformat()}"
        cand = WinRec(None, ver.id, p.scope, start, end, label, p.priority)
        new_viol, warnings = _impact(db, start, end, add=cand)
        if new_viol and not p.force:
            raise _err(409, "OVERRIDE_CONFLICT",
                       "Existing day overrides would conflict with this version on some dates.",
                       violations=new_viol[:50], can_force=True)
        overlaps = [{"id": w.id, "version_id": w.version_id, "start_date": w.start.isoformat(),
                     "end_date": w.end.isoformat(), "priority": w.priority}
                    for w in _load_ctx(db, start, end).windows]

        row = CalendarValidityWindow(version_id=ver.id, scope=p.scope, start_date=start, end_date=end,
                                     label=label, priority=p.priority, created_by=getattr(user, "id", None))
        db.add(row)
        db.flush()
        _audit(db, user, "CREATE", "validity", row.id,
               {"version_id": ver.id, "start": start.isoformat(), "end": end.isoformat(),
                "scope": p.scope, "priority": p.priority, "forced": bool(p.force and (vv or new_viol))})
        db.commit()
        db.refresh(row)
        out = _win_out(row)
    return {"window": out, "overlapping_windows": overlaps, "warnings": warnings,
            "note": "Where windows overlap, the highest priority wins, then the narrowest span, then the newest."}


def delete_validity(db: Session, user: Any, window_id: int, force: bool) -> dict:
    with _write_guard(db):
        row = db.get(CalendarValidityWindow, window_id)
        if row is None:
            raise _err(404, "NOT_FOUND", f"Validity window #{window_id} does not exist.")
        if row.end_date < _today() and not ALLOW_PAST_EDITS:
            raise _err(409, "PAST_DATE", "The window lies entirely in the past; past days are read-only.")
        new_viol, warnings = _impact(db, row.start_date, row.end_date, remove_id=window_id)
        if new_viol and not force:
            raise _err(409, "OVERRIDE_CONFLICT",
                       "Removing this window would make existing day overrides conflict.",
                       violations=new_viol[:50], can_force=True)
        snap = _win_out(row)
        db.delete(row)
        _audit(db, user, "DELETE", "validity", window_id, snap)
        db.commit()
    return {"deleted": window_id, "warnings": warnings}


# ═════════════════════════════════════════════════════════════════════════════
# Routers
# ═════════════════════════════════════════════════════════════════════════════
public_router = APIRouter(prefix="/calendar", tags=["calendar (public)"])
admin_router = APIRouter(prefix="/admin/calendar", tags=["calendar (admin)"])


def _range_check(start: dt.date, end: dt.date, limit: int) -> None:
    _assert_sane_date(start)
    _assert_sane_date(end)
    if end < start:
        raise _err(422, "BAD_RANGE", "end is before start.")
    if (end - start).days + 1 > limit:
        raise _err(422, "RANGE_TOO_LONG", f"At most {limit} days per request.")


def _cache(response: Response) -> None:
    response.headers["Cache-Control"] = f"public, max-age={PUBLIC_CACHE_SECONDS}"


# ── public ──────────────────────────────────────────────────────────────────
@public_router.get("/day/{on_date}")
def public_day(on_date: dt.date, response: Response,
               program: Optional[str] = Query(None, max_length=30),
               semester: Optional[str] = Query(None, max_length=10),
               teacher: Optional[str] = Query(None, max_length=40),
               db: Session = Depends(get_db)):
    """Effective timetable for one calendar date (base + validity window + day overrides)."""
    _assert_sane_date(on_date)
    prog, sem, t = _parse_filters(program, semester, teacher)
    day = _resolve_day(_load_ctx(db, on_date, on_date), on_date)
    _cache(response)
    return _public_view(_apply_filters(day, prog, sem, t))


@public_router.get("/range")
def public_range(start: dt.date, end: dt.date, response: Response,
                 program: Optional[str] = Query(None, max_length=30),
                 semester: Optional[str] = Query(None, max_length=10),
                 teacher: Optional[str] = Query(None, max_length=40),
                 db: Session = Depends(get_db)):
    """Effective timetable for each date in [start, end] (max 62 days) — use for week views."""
    _range_check(start, end, MAX_PUBLIC_RANGE_DAYS)
    prog, sem, t = _parse_filters(program, semester, teacher)
    days = _resolve_range(_load_ctx(db, start, end), start, end)
    _cache(response)
    return {"start": start.isoformat(), "end": end.isoformat(),
            "days": [_public_view(_apply_filters(x, prog, sem, t)) for x in days]}


@public_router.get("/month/{year}/{month}")
def public_month(year: int, month: int, response: Response,
                 program: Optional[str] = Query(None, max_length=30),
                 semester: Optional[str] = Query(None, max_length=10),
                 teacher: Optional[str] = Query(None, max_length=40),
                 db: Session = Depends(get_db)):
    """Lightweight per-day markers for rendering a month grid (click a day -> /calendar/day/{date})."""
    if not (MIN_DATE.year <= year <= MAX_DATE.year) or not (1 <= month <= 12):
        raise _err(422, "BAD_MONTH", "year/month out of range.")
    start = dt.date(year, month, 1)
    end = dt.date(year, month, _pycal.monthrange(year, month)[1])
    prog, sem, t = _parse_filters(program, semester, teacher)
    out = []
    for x in _resolve_range(_load_ctx(db, start, end), start, end):
        f = _apply_filters(x, prog, sem, t)
        out.append({"date": x["date"], "weekday": x["weekday"], "classes": len(f["entries"]),
                    "cancelled": len(f["cancelled"]), "is_holiday": f["is_holiday"],
                    "has_changes": x["has_overrides"], "validity_id": x["validity_id"],
                    "validity_label": x["validity_label"], "version_id": x["version_id"]})
    _cache(response)
    return {"year": year, "month": month, "days": out}


# ── admin ───────────────────────────────────────────────────────────────────
@admin_router.get("/day/{on_date}")
def admin_day(on_date: dt.date, db: Session = Depends(get_db), user: Any = Depends(require_admin)):
    """Admin preview of a date: effective classes, cancelled classes, orphan warnings and live violations."""
    _assert_sane_date(on_date)
    day = _resolve_day(_load_ctx(db, on_date, on_date), on_date)
    day["violations"] = list(_violations(day["entries"]).values())
    day["overrides"] = [_ov_out(o) for o in db.execute(
        select(CalendarOverride).where(CalendarOverride.on_date == on_date)
        .order_by(CalendarOverride.id)).scalars().all()]
    return day


@admin_router.post("/overrides", status_code=201)
def admin_create_override(payload: OverrideCreate, db: Session = Depends(get_db),
                          user: Any = Depends(require_admin)):
    return create_override(db, user, payload)


@admin_router.get("/overrides")
def admin_list_overrides(start: Optional[dt.date] = None, end: Optional[dt.date] = None,
                         db: Session = Depends(get_db), user: Any = Depends(require_admin)):
    start = start or _today() - dt.timedelta(days=7)
    end = end or start + dt.timedelta(days=90)
    _range_check(start, end, MAX_ADMIN_RANGE_DAYS)
    rows = db.execute(select(CalendarOverride).where(
        CalendarOverride.on_date >= start, CalendarOverride.on_date <= end)
        .order_by(CalendarOverride.on_date, CalendarOverride.id)).scalars().all()
    return [_ov_out(r) for r in rows]


@admin_router.delete("/overrides/{override_id}")
def admin_delete_override(override_id: int, force: bool = False, db: Session = Depends(get_db),
                          user: Any = Depends(require_admin)):
    return delete_override(db, user, override_id, force)


@admin_router.post("/validity", status_code=201)
def admin_create_validity(payload: ValidityCreate, db: Session = Depends(get_db),
                          user: Any = Depends(require_admin)):
    return create_validity(db, user, payload)


@admin_router.get("/validity")
def admin_list_validity(start: Optional[dt.date] = None, end: Optional[dt.date] = None,
                        db: Session = Depends(get_db), user: Any = Depends(require_admin)):
    start = start or _today() - dt.timedelta(days=7)
    end = end or start + dt.timedelta(days=180)
    _range_check(start, end, 3 * MAX_ADMIN_RANGE_DAYS)
    rows = db.execute(select(CalendarValidityWindow).where(
        CalendarValidityWindow.start_date <= end, CalendarValidityWindow.end_date >= start)
        .order_by(CalendarValidityWindow.start_date, CalendarValidityWindow.id)).scalars().all()
    return [_win_out(r) for r in rows]


@admin_router.delete("/validity/{window_id}")
def admin_delete_validity(window_id: int, force: bool = False, db: Session = Depends(get_db),
                          user: Any = Depends(require_admin)):
    return delete_validity(db, user, window_id, force)


@admin_router.get("/audit")
def admin_audit(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                user: Any = Depends(require_admin)):
    rows = db.execute(select(CalendarAuditLog).order_by(CalendarAuditLog.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "actor_id": r.actor_id, "action": r.action, "entity": r.entity,
             "entity_id": r.entity_id, "payload": _loads(r.payload_json),
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


router = APIRouter()
router.include_router(public_router)
router.include_router(admin_router)

__all__ = ["router", "public_router", "admin_router", "init_calendar_tables", "entry_key",
           "CalendarValidityWindow", "CalendarOverride", "CalendarAuditLog"]
