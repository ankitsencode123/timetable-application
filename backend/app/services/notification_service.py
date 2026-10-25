"""
Notification Service  -  single-file, production-grade, event-driven email notifications
=========================================================================================

Place at:  app/services/notification_service.py

WHY THIS DESIGN  (Render web service + Supabase Postgres + Vercel frontend)
---------------------------------------------------------------------------
  request path                         worker threads (embedded and/or standalone process)
  ------------                         -------------------------------------------------
  engine commits version               [expand]   claim NEW events  (FOR UPDATE SKIP LOCKED + lease)
        |                                          diff version vs parent -> affected teachers
        v                                          resolve emails, render, INSERT deliveries (idempotent)
  publish_engine_result()  ----->  notification_events  (durable outbox, 1 cheap insert, never raises)
                                                   |
  reconciler (safety net)  ----->  events for any version that has none (dedupe_key = version:<id>)
                                                   v
                                   notification_deliveries  (1 row per recipient)
                                                   |
                                   [dispatch] claim PENDING (lease) -> Brevo HTTP API
                                        ok -> SENT | retryable -> backoff | permanent -> DEAD (+alert)
                                        config error (key/sender/credits) -> re-queue w/o burning attempts

GUARANTEES
  * At-least-once: a row leaves the queue only on provider 2xx (SENT) or after max attempts / max age (DEAD,
    visible + admin-retryable). Crashed workers are recovered by lease expiry. Duplicates are possible only in
    the narrow window "provider accepted but we could not record it" (inherent to at-least-once).
  * Idempotent publishing + fan-out: UNIQUE(dedupe_key) on events, UNIQUE(event_id, recipient_key) on deliveries.
  * Request path never raises and never blocks on the network.
  * Safe with N workers / N Render instances (SKIP LOCKED + leases). No session-level advisory locks, so it
    works through Supabase's transaction pooler (port 6543).

WIRING (4 small edits)
  1) app/main.py  (inside lifespan, after Base.metadata.create_all):
         from app.services import notification_service as notif
         notif.ensure_notification_schema(engine)
         notif.start_worker()                 # before `yield`
         ...
         yield
         notif.stop_worker()                  # after `yield`
  2) app/api/router.py:
         from app.services.notification_service import router as notifications_router
         api_router.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
  3) app/api/actions.py AND app/actions/advanced_chat.py: replace
         background_tasks.add_task(notify_teachers_of_changes, result, db)
     with (synchronous, ~1 ms, never raises):
         notif.publish_engine_result(result, actor=user)
     (A drop-in async shim `notify_teachers_of_changes(result, db)` is also provided.)
  4) Environment variables (Render dashboard) - see class Cfg below. Minimum:
         BREVO_API_KEY, SENDER_EMAIL (must be a verified Brevo sender), NOTIFY_APP_URL (your Vercel URL)
     While testing, set NOTIFY_REDIRECT_ALL_TO="you@gmail.com" so real teachers are never emailed.

STANDALONE WORKER (optional, e.g. Render "Background Worker"):
     python -m app.services.notification_service worker      # also: once | reconcile | stats
     (then set NOTIFY_EMBEDDED_WORKER=0 on the web service if you want only the dedicated worker)
"""
from __future__ import annotations

import asyncio
import hmac
import html
import json
import os
import random
import re
import socket
import threading
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import (
    JSON, Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text,
    UniqueConstraint, delete, exists, func, insert, select, text, update,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine as _default_engine
from app.api.auth import require_admin


# =====================================================================
# 1. CONFIG  (all via environment; safe defaults)
# =====================================================================
try:  # os.getenv below needs .env values in the process environment (pydantic Settings does not export them)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"), override=False)
    load_dotenv(override=False)
except Exception:  # pragma: no cover
    pass


def _env(name: str, default, cast=str):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw.strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_email(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower()
    return s if len(s) <= 254 and _EMAIL_RE.match(s) else None


def _parse_recipients(raw: str) -> List[Dict[str, str]]:
    """'a@x.com:Alice, b@y.com' -> [{'email','name'}]"""
    out: List[Dict[str, str]] = []
    for part in re.split(r"[;,]", raw or ""):
        email, _, name = part.strip().partition(":")
        email = _clean_email(email)
        if email:
            out.append({"email": email, "name": name.strip() or email.split("@")[0]})
    return out


class Cfg:
    # --- provider (Brevo) ---
    BREVO_API_URL = _env("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
    BREVO_API_KEY = _env("BREVO_API_KEY", "")
    SENDER_EMAIL = _env("SENDER_EMAIL", "")
    SENDER_NAME = _env("SENDER_NAME", "Timetable Administrator")
    HTTP_TIMEOUT = _env("NOTIFY_HTTP_TIMEOUT", 20, int)

    # --- who gets what ---
    APP_URL = _env("NOTIFY_APP_URL", _env("FRONTEND_URL", ""))
    NOTIFY_TEACHERS = _env_bool("NOTIFY_TEACHERS", True)
    ADMIN_RECIPIENTS = _parse_recipients(_env("NOTIFY_ADMIN_EMAILS", ""))     # always get a full copy
    REDIRECT_ALL_TO = _parse_recipients(_env("NOTIFY_REDIRECT_ALL_TO", ""))   # staging/test safety switch
    MAX_LINES = _env("NOTIFY_MAX_LINES", 40, int)

    # --- queue behaviour ---
    EMBEDDED_WORKER = _env_bool("NOTIFY_EMBEDDED_WORKER", True)
    POLL_S = _env("NOTIFY_POLL_SECONDS", 10, int)
    BATCH = _env("NOTIFY_BATCH", 5, int)
    LEASE_S = _env("NOTIFY_LEASE_SECONDS", 180, int)            # must exceed BATCH * HTTP_TIMEOUT
    MAX_ATTEMPTS = _env("NOTIFY_MAX_ATTEMPTS", 20, int)         # ~17h+ with 1h backoff cap
    EVENT_MAX_ATTEMPTS = _env("NOTIFY_EVENT_MAX_ATTEMPTS", 10, int)
    BACKOFF_BASE = _env("NOTIFY_BACKOFF_BASE", 30, int)
    BACKOFF_CAP = _env("NOTIFY_BACKOFF_CAP", 3600, int)
    CONFIG_RETRY_S = _env("NOTIFY_CONFIG_RETRY_SECONDS", 300, int)
    MAX_AGE_H = _env("NOTIFY_MAX_AGE_HOURS", 168, int)          # undelivered after this -> DEAD (visible)
    SEND_INTERVAL = _env("NOTIFY_SEND_INTERVAL", 0.25, float)
    DAILY_LIMIT = _env("NOTIFY_DAILY_LIMIT", 0, int)            # 0 = off (Brevo free plan: 300/day)

    # --- reconciler / housekeeping ---
    RECONCILE = _env_bool("NOTIFY_RECONCILE", True)
    RECONCILE_EVERY_S = _env("NOTIFY_RECONCILE_EVERY", 300, int)
    LOOKBACK_H = _env("NOTIFY_RECONCILE_LOOKBACK_HOURS", 48, int)
    GRACE_S = _env("NOTIFY_RECONCILE_GRACE_SECONDS", 45, int)
    RETENTION_DAYS = _env("NOTIFY_RETENTION_DAYS", 90, int)

    # --- secrets for machine endpoints ---
    CRON_SECRET = _env("NOTIFY_CRON_SECRET", "")                # POST /notifications/tick  (X-Cron-Secret)
    WEBHOOK_SECRET = _env("NOTIFY_WEBHOOK_SECRET", "")          # POST /notifications/webhooks/brevo?token=
    ALERT_URL = _env("NOTIFY_ALERT_WEBHOOK_URL", "")            # Slack/Discord-compatible


# =====================================================================
# 2. SCHEMA (Core tables; portable Postgres/SQLite)
# =====================================================================
_JSON = JSON().with_variant(postgresql.JSONB(), "postgresql")
_TS = DateTime(timezone=True)
metadata = MetaData()


class EvStatus:
    NEW, PROCESSING, EXPANDED, SKIPPED, FAILED = "new", "processing", "expanded", "skipped", "failed"


class DlStatus:
    PENDING, PROCESSING, SENT, DEAD, CANCELLED = "pending", "processing", "sent", "dead", "cancelled"


E = Table(
    "notification_events", metadata,
    Column("id", String(36), primary_key=True),
    Column("event_type", String(64), nullable=False),
    Column("dedupe_key", String(200), nullable=False),
    Column("source", String(20), nullable=False),
    Column("payload", _JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("next_attempt_at", _TS, nullable=False),
    Column("locked_by", String(100)),
    Column("lease_expires_at", _TS),
    Column("last_error", Text),
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False),
    Column("expanded_at", _TS),
    UniqueConstraint("dedupe_key", name="uq_notif_event_dedupe"),
    Index("ix_notif_event_poll", "status", "next_attempt_at"),
)

D = Table(
    "notification_deliveries", metadata,
    Column("id", String(36), primary_key=True),
    Column("event_id", String(36), ForeignKey("notification_events.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(16), nullable=False),                  # teacher | admin | test
    Column("recipient_email", String(254), nullable=False),
    Column("recipient_name", String(255)),
    Column("recipient_key", String(300), nullable=False),        # idempotency (pre-redirect identity)
    Column("subject", String(300), nullable=False),
    Column("html_body", Text, nullable=False),
    Column("text_body", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("next_attempt_at", _TS, nullable=False),
    Column("locked_by", String(100)),
    Column("lease_expires_at", _TS),
    Column("provider", String(20), nullable=False),
    Column("provider_message_id", String(200)),
    Column("provider_status", String(40)),                       # from webhook: delivered/bounced/...
    Column("provider_status_at", _TS),
    Column("last_status_code", Integer),
    Column("last_error", Text),
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False),
    Column("sent_at", _TS),
    Column("expires_at", _TS),
    UniqueConstraint("event_id", "recipient_key", name="uq_notif_delivery_event_recipient"),
    Index("ix_notif_delivery_poll", "status", "next_attempt_at"),
    Index("ix_notif_delivery_msgid", "provider_message_id"),
)

A = Table(
    "notification_attempts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("delivery_id", String(36), ForeignKey("notification_deliveries.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("attempt_no", Integer, nullable=False),
    Column("started_at", _TS, nullable=False),
    Column("duration_ms", Integer),
    Column("outcome", String(16), nullable=False),
    Column("status_code", Integer),
    Column("error", Text),
)

S = Table(
    "notification_state", metadata,
    Column("key", String(100), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", _TS, nullable=False),
)


def ensure_notification_schema(bind=None) -> None:
    """Idempotent. Also enables RLS on Postgres/Supabase so tables are NOT exposed via the public PostgREST API
    (the backend's direct DB role bypasses RLS)."""
    bind = bind or _default_engine
    metadata.create_all(bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        for name in metadata.tables:
            try:
                with bind.begin() as conn:
                    conn.execute(text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
            except Exception as e:  # pragma: no cover
                logger.debug(f"RLS enable skipped for {name}: {e}")
    logger.info("Notification schema ready")


# =====================================================================
# 3. SMALL UTILITIES
# =====================================================================
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clip(s: Any, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _n(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _backoff(attempt: int, base: float, cap: float) -> float:
    d = min(cap, base * (2 ** max(0, attempt - 1)))
    return d * (0.8 + 0.4 * random.random())


@contextmanager
def _session():
    s = SessionLocal()
    try:
        yield s
    except Exception:
        try:
            s.rollback()
        except Exception:
            pass
        raise
    finally:
        s.close()


def _insert_ignore(session, table: Table, values: dict) -> int:
    """INSERT ... ON CONFLICT DO NOTHING (rowcount 0 = already existed)."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        stmt = postgresql.insert(table).values(**values).on_conflict_do_nothing()
    elif dialect == "sqlite":
        stmt = sqlite.insert(table).values(**values).on_conflict_do_nothing()
    else:
        try:
            with session.begin_nested():
                session.execute(insert(table).values(**values))
            return 1
        except IntegrityError:
            return 0
    return session.execute(stmt).rowcount or 0


def _state_get(s, key: str) -> Optional[str]:
    return s.execute(select(S.c.value).where(S.c.key == key)).scalar()


def _state_set(s, key: str, value: str) -> None:
    now = _now()
    res = s.execute(update(S).where(S.c.key == key).values(value=value, updated_at=now))
    if not res.rowcount:
        _insert_ignore(s, S, {"key": key, "value": value, "updated_at": now})


_alert_last: Dict[str, float] = {}


def _alert(key: str, message: str) -> None:
    """Log loudly + (rate-limited) POST to an optional Slack/Discord-style webhook. Never raises."""
    logger.error(f"[notify-alert] {message}")
    if not Cfg.ALERT_URL:
        return
    now = time.time()
    if now - _alert_last.get(key, 0) < 600:
        return
    _alert_last[key] = now
    try:
        msg = f"🔔 Timetable notifications: {message}"
        httpx.post(Cfg.ALERT_URL, json={"text": msg, "content": msg}, timeout=5.0)
    except Exception as e:
        logger.debug(f"alert webhook failed: {e}")


# =====================================================================
# 4. CHANGE DETECTION  (schedule diff -> human-readable changes)
# =====================================================================
_DAY_ORDER = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}


def _flatten_entries(obj: Any) -> List[dict]:
    """Pull schedule-entry dicts out of ActionResult.before/after (handles swap's {'a':..,'b':..})."""
    if isinstance(obj, dict):
        if any(k in obj for k in ("subject_code", "start", "day")):
            return [obj]
        out: List[dict] = []
        for v in obj.values():
            out += _flatten_entries(v)
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for v in obj:
            out += _flatten_entries(v)
        return out
    return []


def _ek(e: dict) -> tuple:
    return tuple(str(e.get(k) or "").strip().lower() for k in
                 ("day", "program", "semester", "start", "end", "subject_code", "teacher", "room", "type"))


def _pair_key(e: dict) -> tuple:
    return (str(e.get("subject_code") or "").lower(), _n(e.get("program")), _n(e.get("semester")),
            str(e.get("type") or "")[:3].lower())


def _sort_key(e: dict) -> tuple:
    return (_DAY_ORDER.get(str(e.get("day") or "").lower(), 9), str(e.get("start") or ""))


@dataclass
class Change:
    kind: str                       # added | removed | changed
    before: Optional[dict]
    after: Optional[dict]

    def teacher_tokens(self) -> List[str]:
        toks: List[str] = []
        for e in (self.before, self.after):
            if e:
                toks += [t.strip() for t in re.split(r"[,/+&]", str(e.get("teacher") or "")) if t.strip()]
        return list(dict.fromkeys(toks))

    def sort_key(self) -> tuple:
        return _sort_key(self.after or self.before or {})


def compute_changes(before: Sequence[dict], after: Sequence[dict]) -> List[Change]:
    cb, ca = Counter(_ek(e) for e in before), Counter(_ek(e) for e in after)
    rep: Dict[tuple, dict] = {}
    for e in list(before) + list(after):
        rep.setdefault(_ek(e), e)
    removed = [rep[k] for k, c in (cb - ca).items() for _ in range(c)]
    added = [rep[k] for k, c in (ca - cb).items() for _ in range(c)]

    gr: Dict[tuple, List[dict]] = defaultdict(list)
    ga: Dict[tuple, List[dict]] = defaultdict(list)
    for e in removed:
        gr[_pair_key(e)].append(e)
    for e in added:
        ga[_pair_key(e)].append(e)
    out: List[Change] = []
    for k in set(gr) | set(ga):
        r, a = sorted(gr.get(k, []), key=_sort_key), sorted(ga.get(k, []), key=_sort_key)
        m = min(len(r), len(a))
        out += [Change("changed", r[i], a[i]) for i in range(m)]
        out += [Change("removed", e, None) for e in r[m:]]
        out += [Change("added", None, e) for e in a[m:]]
    out.sort(key=lambda c: c.sort_key())
    return out


# =====================================================================
# 5. RENDERING (escaped HTML + plain text)
# =====================================================================
def _when(e: dict) -> str:
    return f"{str(e.get('day') or '?')[:3]} {e.get('start') or '?'}–{e.get('end') or '?'}"


def _slot(e: dict) -> str:
    return _when(e) + (f", room {e['room']}" if e.get("room") else "")


def _lesson(e: dict) -> str:
    name = e.get("subject_name") or e.get("subject_code") or "Lesson"
    cls = " ".join(str(x) for x in (e.get("program"), e.get("semester")) if x)
    meta = ", ".join(x for x in (cls, e.get("type")) if x)
    return f"{name} ({meta})" if meta else str(name)


def describe_change(c: Change) -> Tuple[str, str, List[str]]:
    if c.kind == "added" and c.after:
        e = c.after
        return "Added", _lesson(e), [x for x in (_slot(e), f"Teacher {e['teacher']}" if e.get("teacher") else "") if x]
    if c.kind == "removed" and c.before:
        e = c.before
        return "Removed", _lesson(e), [x for x in (_slot(e), f"Teacher {e['teacher']}" if e.get("teacher") else "") if x]
    b, a = c.before or {}, c.after or {}
    details: List[str] = []
    timed = (b.get("day"), b.get("start"), b.get("end")) != (a.get("day"), a.get("start"), a.get("end"))
    if timed:
        details.append(f"Time: {_when(b)} → {_when(a)}")
    if _n(b.get("room")) != _n(a.get("room")):
        details.append(f"Room: {b.get('room') or '—'} → {a.get('room') or '—'}")
    if _n(b.get("teacher")) != _n(a.get("teacher")):
        details.append(f"Teacher: {b.get('teacher') or '—'} → {a.get('teacher') or '—'}")
    return ("Rescheduled" if timed else "Updated"), _lesson(a or b), details or ["Details updated"]


def _render_changes(changes: List[Change]) -> Tuple[str, str]:
    shown, extra = changes[: Cfg.MAX_LINES], max(0, len(changes) - Cfg.MAX_LINES)
    li, tx = [], []
    for c in shown:
        label, head, details = describe_change(c)
        li.append(f'<li style="margin:0 0 12px 0"><strong>{html.escape(label)}:</strong> {html.escape(head)}'
                  f'<br><span style="color:#4b5563;font-size:13px">{html.escape(" · ".join(details))}</span></li>')
        tx.append(f"- {label}: {head}\n    " + " | ".join(details))
    if extra:
        li.append(f'<li style="color:#4b5563">…and {extra} more change(s). Please open the timetable for the full list.</li>')
        tx.append(f"...and {extra} more change(s).")
    return '<ul style="padding-left:18px;margin:12px 0">' + "".join(li) + "</ul>", "\n".join(tx)


def _shell(heading: str, inner: str) -> str:
    link = (f'<p style="margin:20px 0"><a href="{html.escape(Cfg.APP_URL)}" style="background:#2563eb;color:#fff;'
            f'padding:10px 16px;border-radius:6px;text-decoration:none">Open timetable</a></p>') if Cfg.APP_URL else ""
    return (f'<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'</head><body style="margin:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#111827">'
            f'<div style="max-width:620px;margin:0 auto;padding:24px"><div style="background:#fff;border-radius:10px;'
            f'padding:24px;border:1px solid #e5e7eb"><h2 style="margin:0 0 12px 0;font-size:20px">{html.escape(heading)}</h2>'
            f'{inner}{link}<p style="color:#6b7280;font-size:12px;margin-top:24px">Automated message from the Timetable '
            f'Management System. Please do not reply.</p></div></div></body></html>')


def _context_line(ctx: dict) -> str:
    bits = []
    if ctx.get("actor"):
        bits.append(f"by {ctx['actor']}")
    if ctx.get("version_id"):
        draft = " (draft)" if "DRAFT" in str(ctx.get("status", "")).upper() else ""
        bits.append(f"saved as timetable version #{ctx['version_id']}{draft}")
    return "The timetable was updated" + (" " + ", ".join(bits) if bits else "") + "."


def render_teacher_email(name: str, changes: List[Change], ctx: dict) -> Tuple[str, str, str]:
    n = len(changes)
    subject = f"Timetable update: {n} change{'s' if n != 1 else ''} affecting your schedule"
    ul, tx = _render_changes(changes)
    intro = _context_line(ctx)
    inner = (f"<p>Hello {html.escape(name)},</p><p>{html.escape(intro)} These changes affect your classes:</p>{ul}")
    text_body = f"Hello {name},\n\n{intro} These changes affect your classes:\n\n{tx}\n" + (f"\nOpen the timetable: {Cfg.APP_URL}\n" if Cfg.APP_URL else "")
    return subject, _shell("Your timetable has changed", inner), text_body


def render_admin_email(changes: List[Change], ctx: dict, notified: List[str], unresolved: List[str]) -> Tuple[str, str, str]:
    vid = f" (v{ctx['version_id']})" if ctx.get("version_id") else ""
    subject = f"[Admin] Timetable updated{vid} – {len(notified)} teacher(s) notified"
    ul, tx = _render_changes(changes) if changes else ("", "")
    log = str(ctx.get("change_log") or "").strip()
    inner = f"<p>{html.escape(_context_line(ctx))}</p>"
    text_body = _context_line(ctx) + "\n\n"
    if ctx.get("partial"):
        inner += "<p><strong>Note:</strong> only part of the requested actions were applied.</p>"
        text_body += "NOTE: only part of the requested actions were applied.\n\n"
    if changes:
        inner += f"<h3 style='font-size:15px'>Changes ({len(changes)})</h3>{ul}"
        text_body += f"Changes ({len(changes)}):\n{tx}\n\n"
    if log:
        inner += (f'<h3 style="font-size:15px">Change log</h3><div style="background:#f9fafb;border:1px solid #e5e7eb;'
                  f'border-radius:8px;padding:12px;font-family:monospace;font-size:13px">{html.escape(_clip(log, 6000)).replace(chr(10), "<br>")}</div>')
        text_body += f"Change log:\n{_clip(log, 6000)}\n\n"
    inner += f"<p><strong>Notified:</strong> {html.escape(', '.join(notified) or 'nobody')}</p>"
    text_body += f"Notified: {', '.join(notified) or 'nobody'}\n"
    if unresolved:
        inner += (f'<p style="color:#b45309"><strong>Could not email (no account/email found):</strong> '
                  f'{html.escape(", ".join(unresolved))}</p>')
        text_body += f"Could NOT email (no account/email found): {', '.join(unresolved)}\n"
    return subject, _shell("Timetable change summary", inner), text_body


def render_test_email() -> Tuple[str, str, str]:
    return ("Timetable notifications: test email",
            _shell("Test email", "<p>If you can read this, the notification pipeline (queue → worker → Brevo) works.</p>"),
            "If you can read this, the notification pipeline (queue -> worker -> Brevo) works.")


# =====================================================================
# 6. EVENT PUBLISHING  (request path: one insert, never raises)
# =====================================================================
def _wake_worker() -> None:
    w = _WORKER
    if w is not None:
        w.wake()


def publish_event(event_type: str, payload: dict, *, dedupe_key: Optional[str] = None, source: str = "request") -> Optional[str]:
    """Durably record an event. Idempotent on dedupe_key. Returns event id (existing id if duplicate) or None."""
    try:
        payload = json.loads(json.dumps(payload, default=str))
        if len(json.dumps(payload)) > 400_000:
            payload.pop("changes", None)
            payload["truncated"] = True
    except Exception as e:
        logger.error(f"notification payload not serialisable: {e}")
        return None
    dedupe_key = dedupe_key or f"{event_type}:{uuid.uuid4().hex}"
    ev_id = str(uuid.uuid4())
    for attempt in range(1, 4):
        try:
            with _session() as s:
                now = _now()
                n = _insert_ignore(s, E, {
                    "id": ev_id, "event_type": event_type, "dedupe_key": dedupe_key, "source": source,
                    "payload": payload, "status": EvStatus.NEW, "attempts": 0, "max_attempts": Cfg.EVENT_MAX_ATTEMPTS,
                    "next_attempt_at": now, "created_at": now, "updated_at": now})
                if n == 0:
                    existing = s.execute(select(E.c.id).where(E.c.dedupe_key == dedupe_key)).scalar()
                    s.commit()
                    return existing
                s.commit()
            _wake_worker()
            return ev_id
        except Exception as e:
            logger.warning(f"publish_event attempt {attempt} failed: {e}")
            time.sleep(0.2 * attempt)
    logger.critical(f"FAILED to record notification event {dedupe_key}; reconciler will recover version-based events")
    return None


def publish_engine_result(result: Any, *, actor: Any = None, event_type: str = "timetable_changed") -> Optional[str]:
    """Call right after SafeActionEngine.execute(). Safe to call from sync endpoints. Never raises."""
    try:
        if result is None:
            return None
        vid = getattr(result, "new_version_id", None)
        results = list(getattr(result, "results", None) or [])
        if not vid:
            meaningful = [r for r in results if "VALIDATE" not in str(getattr(r, "action_type", "")).upper()
                          and getattr(r, "success", False)]
            if not (getattr(result, "success", False) and meaningful):
                return None                      # failed / read-only: nothing changed, nothing to announce
        changes, types = [], []
        for r in results:
            at = str(getattr(r, "action_type", ""))
            types.append(at)
            if "VALIDATE" in at.upper() or not getattr(r, "success", False):
                continue
            b, a = _flatten_entries(getattr(r, "before", None)), _flatten_entries(getattr(r, "after", None))
            if b or a:
                changes.append({"before": b[:20], "after": a[:20]})
        payload = {
            "version_id": vid,
            "parent_version_id": None,
            "actor_id": getattr(actor, "id", None),
            "actor_name": getattr(actor, "full_name", None),
            "change_log": _clip(getattr(result, "change_log", "") or "", 20000),
            "partial_applied": bool(getattr(result, "partial_applied", False)),
            "action_types": types[:100],
            "changes": changes[:100],
        }
        return publish_event(event_type, payload, dedupe_key=f"version:{vid}" if vid else None)
    except Exception:
        logger.exception("publish_engine_result failed (swallowed; reconciler is the safety net)")
        return None


async def notify_teachers_of_changes(result: Any, db: Any = None, actor: Any = None) -> None:
    """Drop-in replacement for the old email_service.notify_teachers_of_changes (same call signature)."""
    await asyncio.to_thread(publish_engine_result, result, actor=actor)


# =====================================================================
# 7. EXPANSION  (event -> per-recipient deliveries)
# =====================================================================
@dataclass
class Recipient:
    email: str
    name: str


def _load_directory(session) -> Dict[str, Recipient]:
    """normalised short_name / full_name -> Recipient (active users only)."""
    from app.models.teacher import Teacher
    from app.models.user import User
    out: Dict[str, Recipient] = {}
    rows = session.query(Teacher, User).join(User, User.id == Teacher.user_id).filter(User.is_active.is_(True)).all()
    for t, u in rows:
        email = _clean_email(u.email)
        if not email:
            continue
        r = Recipient(email, str(t.full_name or t.short_name))
        out.setdefault(_n(t.short_name), r)
        out.setdefault(_n(t.full_name), r)
    return out


def _load_version(session, vid: int) -> Tuple[List[dict], Optional[int], str]:
    from app.services import timetable_service
    v = timetable_service.get_version(session, vid)
    if v is None:
        raise LookupError(f"version {vid} not found")
    return [e.to_dict() for e in v.entries], getattr(v, "parent_version_id", None), str(getattr(v, "status", ""))


def _collect_changes(session, payload: dict) -> Tuple[List[Change], dict]:
    ctx = {"version_id": payload.get("version_id"), "actor": payload.get("actor_name"), "status": "",
           "change_log": payload.get("change_log") or "", "partial": bool(payload.get("partial_applied"))}
    changes: Optional[List[Change]] = None
    vid = payload.get("version_id")
    if vid:
        try:
            after, parent, status = _load_version(session, int(vid))
            ctx["status"] = status
            parent = payload.get("parent_version_id") or parent
            if parent:
                before, _, _ = _load_version(session, int(parent))
                changes = compute_changes(before, after)
            elif not payload.get("changes"):
                changes = compute_changes([], after)
        except Exception as e:
            session.rollback()
            logger.warning(f"version diff unavailable for v{vid}: {e}; using payload fallback")
    if not changes:
        rem: List[dict] = []
        add: List[dict] = []
        for ch in payload.get("changes") or []:
            rem += _flatten_entries(ch.get("before"))
            add += _flatten_entries(ch.get("after"))
        fallback = compute_changes(rem, add)
        changes = fallback or changes or []
    return changes, ctx


def _new_delivery(ev: dict, kind: str, email: str, name: str, subject: str, html_body: str, text_body: str) -> dict:
    return {"kind": kind, "recipient_email": email, "recipient_name": name, "recipient_key": email,
            "subject": _clip(subject, 300), "html_body": html_body, "text_body": text_body}


def _expand_event(session, ev: dict) -> Tuple[List[dict], str]:
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"] or "{}")
    out: List[dict] = []

    if ev["event_type"] == "test":
        r = payload.get("recipient") or {}
        email = _clean_email(r.get("email"))
        if not email:
            return [], "test event without a valid recipient"
        s, h, t = render_test_email()
        out.append(_new_delivery(ev, "test", email, r.get("name") or email, s, h, t))
    else:
        changes, ctx = _collect_changes(session, payload)
        notified_names: List[str] = []
        unresolved: List[str] = []
        directory = _load_directory(session) if (Cfg.NOTIFY_TEACHERS and changes) else {}

        per_email: Dict[str, Tuple[Recipient, List[Change]]] = {}
        if Cfg.NOTIFY_TEACHERS and changes:
            by_token: Dict[str, Tuple[str, List[Change]]] = {}
            for c in changes:
                for tok in c.teacher_tokens():
                    by_token.setdefault(_n(tok), (tok, []))[1].append(c)
            for key, (tok, chs) in by_token.items():
                rec = directory.get(key)
                if not rec:
                    unresolved.append(tok)
                    continue
                entry = per_email.setdefault(rec.email, (rec, []))
                entry[1].extend(c for c in chs if not any(c is x for x in entry[1]))
        for email, (rec, chs) in per_email.items():
            chs.sort(key=lambda c: c.sort_key())
            s, h, t = render_teacher_email(rec.name, chs, ctx)
            out.append(_new_delivery(ev, "teacher", email, rec.name, s, h, t))
            notified_names.append(rec.name)

        if Cfg.ADMIN_RECIPIENTS and (changes or str(ctx.get("change_log")).strip()):
            s, h, t = render_admin_email(changes, ctx, notified_names, unresolved)
            for r in Cfg.ADMIN_RECIPIENTS:
                out.append(_new_delivery(ev, "admin", r["email"], r["name"], s, h, t))
        if unresolved:
            logger.warning(f"event {ev['id']}: teachers without a resolvable email: {unresolved}")
            if not Cfg.ADMIN_RECIPIENTS:
                _alert("unresolved-teachers", f"teachers affected but not emailed (no account/email): {unresolved}")

    if Cfg.REDIRECT_ALL_TO:                      # staging / test safety switch
        redirected = []
        for d in out:
            for tgt in Cfg.REDIRECT_ALL_TO:
                nd = dict(d)
                nd.update(recipient_email=tgt["email"], recipient_name=tgt["name"],
                          recipient_key=f"{d['recipient_key']}->{tgt['email']}",
                          subject=_clip(f"[TEST → {d['recipient_email']}] {d['subject']}", 300))
                redirected.append(nd)
        out = redirected
    return out, ("" if out else "no recipients (no affected/resolvable teachers and no admin recipients configured)")


# =====================================================================
# 8. PROVIDER (Brevo) + CIRCUIT BREAKER
# =====================================================================
@dataclass
class SendOutcome:
    kind: str                         # ok | retry | permanent | config
    status_code: Optional[int] = None
    message_id: Optional[str] = None
    error: str = ""
    retry_after: float = 0.0


_CONFIG_CODES = {"not_enough_credits", "account_under_validation", "permission_denied", "unauthorized",
                 "sender_not_valid", "invalid_sender", "ip_not_authorized"}


class BrevoProvider:
    name = "brevo"

    def __init__(self) -> None:
        self._client: Optional[httpx.Client] = None
        self._lock = threading.Lock()

    def _http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(timeout=httpx.Timeout(Cfg.HTTP_TIMEOUT, connect=10.0))
            return self._client

    def send(self, d: dict) -> SendOutcome:
        if not Cfg.BREVO_API_KEY:
            return SendOutcome("config", error="BREVO_API_KEY is not set")
        if not _clean_email(Cfg.SENDER_EMAIL):
            return SendOutcome("config", error="SENDER_EMAIL is not set / invalid (must be a verified Brevo sender)")
        payload = {
            "sender": {"name": Cfg.SENDER_NAME, "email": Cfg.SENDER_EMAIL},
            "to": [{"email": d["recipient_email"], "name": d.get("recipient_name") or d["recipient_email"]}],
            "subject": d["subject"],
            "htmlContent": d["html_body"],
            "textContent": d["text_body"],
            "tags": ["timetable-change", str(d.get("kind") or "notification")],
            # Traceability; the idempotencyKey header is best-effort dedupe if the provider honours it.
            "headers": {"X-Mailin-custom": d["id"], "idempotencyKey": d["id"]},
        }
        headers = {"accept": "application/json", "api-key": Cfg.BREVO_API_KEY, "content-type": "application/json"}
        try:
            r = self._http().post(Cfg.BREVO_API_URL, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            return SendOutcome("retry", error=f"timeout: {e}")
        except httpx.HTTPError as e:
            return SendOutcome("retry", error=f"network error: {e}")
        except Exception as e:
            return SendOutcome("retry", error=f"unexpected client error: {e}")

        sc = r.status_code
        if 200 <= sc < 300:                         # NEVER retry a 2xx, even if the body is odd
            mid = None
            try:
                mid = str(r.json().get("messageId") or "").strip("<> ") or None
            except Exception:
                pass
            return SendOutcome("ok", sc, mid)
        body, code = _clip(r.text, 300), ""
        try:
            code = str(r.json().get("code") or "")
        except Exception:
            pass
        err = f"HTTP {sc} {code} {body}".strip()
        try:
            ra = min(3600.0, float(r.headers.get("Retry-After", "0")))
        except Exception:
            ra = 0.0
        if sc in (401, 402, 403) or code in _CONFIG_CODES:
            return SendOutcome("config", sc, error=err)
        if sc in (408, 425, 429) or sc >= 500:
            return SendOutcome("retry", sc, error=err, retry_after=ra)
        return SendOutcome("permanent", sc, error=err)   # 400 invalid payload/recipient etc.


class _Breaker:
    """Pauses sending after consecutive provider failures; cooldown doubles up to 10 min."""
    def __init__(self, threshold: int = 5, base: float = 30.0, cap: float = 600.0):
        self.threshold, self.base, self.cap = threshold, base, cap
        self.fails, self.opens, self.open_until = 0, 0, 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        return time.time() >= self.open_until

    def is_open(self) -> bool:
        return not self.allow()

    def success(self) -> None:
        with self._lock:
            self.fails = self.opens = 0
            self.open_until = 0.0

    def failure(self) -> None:
        with self._lock:
            self.fails += 1
            if self.fails >= self.threshold:
                cool = min(self.cap, self.base * (2 ** self.opens))
                self.opens += 1
                self.open_until = time.time() + cool
                _alert("provider-down", f"email provider failing ({self.fails} consecutive); pausing sends {int(cool)}s")


_PROVIDER = BrevoProvider()
_BREAKER = _Breaker()


# =====================================================================
# 9. QUEUE PRIMITIVES (claim / release / recover / finish)
# =====================================================================
def _claim(table: Table, ready_status: str, limit: int, worker_id: str) -> List[dict]:
    """Atomically lease up to `limit` due rows. attempts is incremented at claim time (crash-safe)."""
    now = _now()
    with _session() as s:
        ids = [r[0] for r in s.execute(
            select(table.c.id).where(table.c.status == ready_status, table.c.next_attempt_at <= now)
            .order_by(table.c.next_attempt_at, table.c.created_at).limit(limit).with_for_update(skip_locked=True))]
        if not ids:
            s.rollback()
            return []
        s.execute(update(table).where(table.c.id.in_(ids)).values(
            status="processing", locked_by=worker_id, lease_expires_at=now + timedelta(seconds=Cfg.LEASE_S),
            attempts=table.c.attempts + 1, updated_at=now))
        rows = [dict(r) for r in s.execute(select(table).where(table.c.id.in_(ids))).mappings().all()]
        s.commit()
        return rows


def _release(table: Table, ids: List[str], ready_status: str) -> None:
    """Give claimed-but-unprocessed rows back (refund the attempt)."""
    if not ids:
        return
    with _session() as s:
        s.execute(update(table).where(table.c.id.in_(ids), table.c.status == "processing").values(
            status=ready_status, attempts=table.c.attempts - 1, locked_by=None, lease_expires_at=None, updated_at=_now()))
        s.commit()


def _recover_leases() -> None:
    now = _now()
    with _session() as s:
        for t, ready, dead in ((E, EvStatus.NEW, EvStatus.FAILED), (D, DlStatus.PENDING, DlStatus.DEAD)):
            expired = (t.c.status == "processing") & (t.c.lease_expires_at < now)
            dead_n = s.execute(update(t).where(expired, t.c.attempts >= t.c.max_attempts).values(
                status=dead, last_error="lease expired after max attempts", locked_by=None,
                lease_expires_at=None, updated_at=now)).rowcount
            req_n = s.execute(update(t).where(expired, t.c.attempts < t.c.max_attempts).values(
                status=ready, next_attempt_at=now, locked_by=None, lease_expires_at=None,
                last_error="lease expired (worker stopped mid-flight); re-queued", updated_at=now)).rowcount
            if req_n:
                logger.warning(f"{t.name}: recovered {req_n} stuck row(s)")
            if dead_n:
                _alert(f"lease-dead-{t.name}", f"{dead_n} row(s) in {t.name} dead-lettered after repeated worker crashes")
        s.commit()


# ---- events ----
def _process_event(row: dict) -> None:
    try:
        with _session() as s:
            deliveries, skip_reason = _expand_event(s, row)
            now = _now()
            expires = now + timedelta(hours=Cfg.MAX_AGE_H)
            created = 0
            for d in deliveries:
                created += _insert_ignore(s, D, {
                    **d, "id": str(uuid.uuid4()), "event_id": row["id"], "status": DlStatus.PENDING, "attempts": 0,
                    "max_attempts": Cfg.MAX_ATTEMPTS, "next_attempt_at": now, "provider": _PROVIDER.name,
                    "created_at": now, "updated_at": now, "expires_at": expires})
            s.execute(update(E).where(E.c.id == row["id"]).values(
                status=EvStatus.EXPANDED if deliveries else EvStatus.SKIPPED, expanded_at=now,
                last_error=skip_reason or None, locked_by=None, lease_expires_at=None, updated_at=now))
            s.commit()
        logger.info(f"event {row['id']} ({row['event_type']}) -> {created} new delivery row(s)"
                    + (f" [{skip_reason}]" if skip_reason else ""))
    except Exception as e:
        logger.exception(f"event {row['id']} expansion failed")
        _fail_event(row, e)


def _fail_event(row: dict, err: Exception) -> None:
    now = _now()
    final = row["attempts"] >= row["max_attempts"]
    try:
        with _session() as s:
            s.execute(update(E).where(E.c.id == row["id"]).values(
                status=EvStatus.FAILED if final else EvStatus.NEW,
                next_attempt_at=now + timedelta(seconds=_backoff(row["attempts"], 15, 900)),
                last_error=_clip(err, 500), locked_by=None, lease_expires_at=None, updated_at=now))
            s.commit()
    except Exception:
        logger.exception("could not record event failure (lease expiry will recover it)")
    if final:
        _alert(f"event-{row['id']}", f"event {row['id']} ({row['event_type']}) FAILED to expand: {_clip(err, 200)}")


# ---- deliveries ----
def _finish_delivery(row: dict, out: SendOutcome, started: datetime, duration_ms: int) -> None:
    now = _now()
    attempts, max_a = row["attempts"], row["max_attempts"]
    values: Dict[str, Any] = {"updated_at": now, "locked_by": None, "lease_expires_at": None,
                              "last_status_code": out.status_code, "last_error": _clip(out.error, 500) or None}
    label = out.kind
    if out.kind == "ok":
        values.update(status=DlStatus.SENT, sent_at=now, provider_message_id=out.message_id, last_error=None)
    elif out.kind == "permanent":
        values.update(status=DlStatus.DEAD)
    elif out.kind == "config":                    # operator problem: don't burn attempts, keep trying slowly
        values.update(status=DlStatus.PENDING, attempts=max(0, attempts - 1),
                      next_attempt_at=now + timedelta(seconds=Cfg.CONFIG_RETRY_S))
    elif attempts >= max_a:
        values.update(status=DlStatus.DEAD)
        label = "exhausted"
    else:
        delay = max(out.retry_after, _backoff(attempts, Cfg.BACKOFF_BASE, Cfg.BACKOFF_CAP))
        values.update(status=DlStatus.PENDING, next_attempt_at=now + timedelta(seconds=delay))

    tries = 6 if out.kind == "ok" else 3            # recording a SUCCESS is the one write we really must not lose
    for i in range(tries):
        try:
            with _session() as s:
                s.execute(update(D).where(D.c.id == row["id"], D.c.status != DlStatus.SENT).values(**values))
                s.execute(insert(A).values(delivery_id=row["id"], attempt_no=attempts, started_at=started,
                                           duration_ms=duration_ms, outcome=label, status_code=out.status_code,
                                           error=_clip(out.error, 500) or None))
                s.commit()
            break
        except Exception as e:
            logger.warning(f"could not record outcome for delivery {row['id']} (try {i + 1}): {e}")
            time.sleep(min(2 ** i, 10))
    else:
        logger.critical(f"delivery {row['id']} outcome '{out.kind}' NOT recorded; lease expiry will re-queue it")

    if values.get("status") == DlStatus.DEAD:
        _alert(f"dead-{row['id']}", f"delivery {row['id']} to {row['recipient_email']} is DEAD ({label}): {_clip(out.error, 200)}")


def _process_delivery(row: dict) -> None:
    now = _now()
    exp = _aware(row.get("expires_at"))
    if exp and exp < now:
        _finish_delivery(row, SendOutcome("permanent", error=f"expired after {Cfg.MAX_AGE_H}h undelivered"), now, 0)
        return
    started, t0 = now, time.monotonic()
    try:
        out = _PROVIDER.send(row)
    except Exception as e:                           # provider bug must never kill the worker
        out = SendOutcome("retry", error=f"provider exception: {e}")
    ms = int((time.monotonic() - t0) * 1000)
    if out.kind == "ok":
        _BREAKER.success()
        logger.info(f"sent delivery {row['id']} to {row['recipient_email']} (msgid={out.message_id}, {ms}ms)")
    elif out.kind in ("retry", "config"):
        _BREAKER.failure()
        logger.warning(f"delivery {row['id']} attempt {row['attempts']} -> {out.kind}: {out.error}")
        if out.kind == "config":
            _alert("config", f"email provider configuration problem: {out.error}")
    else:
        logger.error(f"delivery {row['id']} permanent failure: {out.error}")
    _finish_delivery(row, out, started, ms)


# =====================================================================
# 10. RECONCILER (safety net: every version must have an event)
# =====================================================================
def reconcile_versions() -> int:
    """Publish events for recent timetable versions that never got one. No backfill of pre-existing history:
    the first run records a watermark (current max version id)."""
    if not Cfg.RECONCILE:
        return 0
    try:
        from app.models.timetable import TimetableVersion as V
    except Exception as e:
        logger.debug(f"reconciler disabled (models unavailable): {e}")
        return 0
    with _session() as s:
        wm = _state_get(s, "reconcile_watermark_version_id")
        if wm is None:
            top = s.query(func.max(V.id)).scalar() or 0
            _state_set(s, "reconcile_watermark_version_id", str(top))
            s.commit()
            logger.info(f"notification reconciler initialised at version id {top}")
            return 0
        now = _now()
        rows = (s.query(V.id, V.parent_version_id, V.created_by, V.change_summary)
                .filter(V.id > int(wm), V.created_at >= now - timedelta(hours=Cfg.LOOKBACK_H),
                        V.created_at <= now - timedelta(seconds=Cfg.GRACE_S))
                .order_by(V.id).limit(200).all())
        if not rows:
            return 0
        keys = [f"version:{r[0]}" for r in rows]
        have = {r[0] for r in s.execute(select(E.c.dedupe_key).where(E.c.dedupe_key.in_(keys)))}
    n = 0
    for vid, parent, created_by, summary in rows:
        if f"version:{vid}" in have:
            continue
        payload = {"version_id": vid, "parent_version_id": parent, "actor_id": created_by,
                   "actor_name": None, "change_log": summary or "", "partial_applied": False,
                   "action_types": [], "changes": [], "reconciled": True}
        if publish_event("timetable_changed", payload, dedupe_key=f"version:{vid}", source="reconciler"):
            n += 1
    if n:
        logger.warning(f"reconciler published {n} missing notification event(s)")
    return n


def _prune() -> None:
    cutoff = _now() - timedelta(days=Cfg.RETENTION_DAYS)
    with _session() as s:
        old = select(D.c.id).where(D.c.status.in_((DlStatus.SENT, DlStatus.CANCELLED)), D.c.created_at < cutoff)
        s.execute(delete(A).where(A.c.delivery_id.in_(old)))
        s.execute(delete(D).where(D.c.id.in_(old)))
        s.execute(delete(E).where(E.c.status.in_((EvStatus.EXPANDED, EvStatus.SKIPPED)), E.c.created_at < cutoff,
                                  ~exists().where(D.c.event_id == E.c.id)))
        s.commit()


# =====================================================================
# 11. WORKER
# =====================================================================
class NotificationWorker:
    def __init__(self) -> None:
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_recover = self._last_reconcile = -1e9
        self._last_prune = time.monotonic()

    # ---- lifecycle ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="notification-worker", daemon=True)
        self._thread.start()
        logger.info(f"notification worker started ({self.worker_id})")

    def stop(self, timeout: float = 25.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        logger.info("notification worker stopped")

    def wake(self) -> None:
        self._wake.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                n = self.run_once()
            except Exception:
                logger.exception("worker cycle crashed (continuing)")
                n = 0
            if n == 0:
                self._wake.wait(Cfg.POLL_S * (0.8 + 0.4 * random.random()))

    # ---- one pass over every stage ----
    def run_once(self, force_reconcile: bool = False) -> int:
        work, now = 0, time.monotonic()
        if now - self._last_recover >= 30:
            self._last_recover = now
            _recover_leases()
        work += self._expand_events()
        work += self._dispatch()
        if force_reconcile or (Cfg.RECONCILE and now - self._last_reconcile >= Cfg.RECONCILE_EVERY_S):
            self._last_reconcile = now
            try:
                work += reconcile_versions()
            except Exception:
                logger.exception("reconciler failed")
        if now - self._last_prune >= 86400:
            self._last_prune = now
            try:
                _prune()
            except Exception:
                logger.exception("prune failed")
        return work

    def _expand_events(self) -> int:
        rows = _claim(E, EvStatus.NEW, 10, self.worker_id)
        for r in rows:
            _process_event(r)
        return len(rows)

    def _dispatch(self) -> int:
        if not _BREAKER.allow():
            return 0
        limit = Cfg.BATCH
        if Cfg.DAILY_LIMIT > 0:
            midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0)
            with _session() as s:
                sent = s.execute(select(func.count()).select_from(D).where(
                    D.c.status == DlStatus.SENT, D.c.sent_at >= midnight)).scalar() or 0
            limit = min(limit, Cfg.DAILY_LIMIT - sent)
            if limit <= 0:
                return 0
        rows = _claim(D, DlStatus.PENDING, limit, self.worker_id)
        done = 0
        for i, row in enumerate(rows):
            if self._stop.is_set() or not _BREAKER.allow():
                _release(D, [r["id"] for r in rows[i:]], DlStatus.PENDING)
                break
            _process_delivery(row)
            done += 1
            if Cfg.SEND_INTERVAL:
                time.sleep(Cfg.SEND_INTERVAL)
        return done


_WORKER: Optional[NotificationWorker] = None
_WORKER_LOCK = threading.Lock()


def start_worker() -> Optional[NotificationWorker]:
    """Call in FastAPI lifespan. No-op if NOTIFY_EMBEDDED_WORKER=0."""
    global _WORKER
    _startup_checks()
    if not Cfg.EMBEDDED_WORKER:
        logger.info("embedded notification worker disabled (NOTIFY_EMBEDDED_WORKER=0)")
        return None
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = NotificationWorker()
        _WORKER.start()
        return _WORKER


def stop_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None:
            _WORKER.stop()
            _WORKER = None


def _startup_checks() -> None:
    if not Cfg.BREVO_API_KEY:
        logger.warning("BREVO_API_KEY not set: notifications will queue but not send until it is configured")
    if not _clean_email(Cfg.SENDER_EMAIL):
        logger.warning("SENDER_EMAIL not set/invalid: notifications will queue but not send")
    if Cfg.REDIRECT_ALL_TO:
        logger.warning(f"NOTIFY_REDIRECT_ALL_TO active: ALL mail goes to {[r['email'] for r in Cfg.REDIRECT_ALL_TO]}")
    if not Cfg.ADMIN_RECIPIENTS:
        logger.info("NOTIFY_ADMIN_EMAILS empty: no admin copy will be sent")
    if Cfg.LEASE_S <= Cfg.BATCH * Cfg.HTTP_TIMEOUT:
        logger.warning("NOTIFY_LEASE_SECONDS should exceed BATCH*HTTP_TIMEOUT to avoid duplicate sends")


# =====================================================================
# 12. STATS + HTTP API
# =====================================================================
def _ser(v: Any) -> Any:
    return _aware(v).isoformat() if isinstance(v, datetime) else v


def _row(r: Any) -> dict:
    return {k: _ser(v) for k, v in dict(r).items()}


def get_stats() -> dict:
    with _session() as s:
        def counts(t: Table) -> Dict[str, int]:
            return {r[0]: r[1] for r in s.execute(select(t.c.status, func.count()).group_by(t.c.status))}
        oldest = s.execute(select(func.min(D.c.created_at)).where(
            D.c.status.in_((DlStatus.PENDING, DlStatus.PROCESSING)))).scalar()
        bounced = s.execute(select(func.count()).select_from(D).where(
            D.c.provider_status.in_(("hard_bounce", "blocked", "invalid_email", "spam")))).scalar()
    age = (_now() - _aware(oldest)).total_seconds() if oldest else 0
    w = _WORKER
    return {"events": counts(E), "deliveries": counts(D), "oldest_undelivered_age_seconds": int(age),
            "bounced_or_blocked": bounced, "breaker_open": _BREAKER.is_open(),
            "embedded_worker_running": bool(w and w.running),
            "config": {"brevo_key_set": bool(Cfg.BREVO_API_KEY), "sender_set": bool(_clean_email(Cfg.SENDER_EMAIL)),
                       "admin_recipients": len(Cfg.ADMIN_RECIPIENTS), "redirect_active": bool(Cfg.REDIRECT_ALL_TO),
                       "reconciler": Cfg.RECONCILE, "daily_limit": Cfg.DAILY_LIMIT or None}}


router = APIRouter()
_ADMIN = [Depends(require_admin)]


class TestEmailRequest(BaseModel):
    email: str
    name: Optional[str] = None


@router.get("/stats", dependencies=_ADMIN)
def stats():
    return get_stats()


@router.get("/deliveries", dependencies=_ADMIN)
def list_deliveries(status: Optional[str] = None, limit: int = Query(50, ge=1, le=500)):
    cols = [c for c in D.c if c.name not in ("html_body", "text_body")]
    q = select(*cols).order_by(D.c.created_at.desc()).limit(limit)
    if status:
        q = q.where(D.c.status == status)
    with _session() as s:
        return [_row(r) for r in s.execute(q).mappings().all()]


@router.get("/events", dependencies=_ADMIN)
def list_events(status: Optional[str] = None, limit: int = Query(50, ge=1, le=500)):
    cols = [c for c in E.c if c.name != "payload"]
    q = select(*cols).order_by(E.c.created_at.desc()).limit(limit)
    if status:
        q = q.where(E.c.status == status)
    with _session() as s:
        return [_row(r) for r in s.execute(q).mappings().all()]


@router.post("/deliveries/{delivery_id}/retry", dependencies=_ADMIN)
def retry_delivery(delivery_id: str):
    now = _now()
    with _session() as s:
        n = s.execute(update(D).where(D.c.id == delivery_id, D.c.status.in_((DlStatus.DEAD, DlStatus.CANCELLED))).values(
            status=DlStatus.PENDING, attempts=0, next_attempt_at=now, locked_by=None, lease_expires_at=None,
            expires_at=now + timedelta(hours=Cfg.MAX_AGE_H), updated_at=now)).rowcount
        s.commit()
    if not n:
        raise HTTPException(404, "No dead/cancelled delivery with that id")
    _wake_worker()
    return {"ok": True}


@router.post("/events/{event_id}/replay", dependencies=_ADMIN)
def replay_event(event_id: str):
    """Re-run expansion. Existing recipients are NOT duplicated (unique constraint); only missing ones are created."""
    now = _now()
    with _session() as s:
        n = s.execute(update(E).where(E.c.id == event_id, E.c.status != EvStatus.PROCESSING).values(
            status=EvStatus.NEW, attempts=0, next_attempt_at=now, locked_by=None, lease_expires_at=None,
            updated_at=now)).rowcount
        s.commit()
    if not n:
        raise HTTPException(404, "Event not found or currently processing")
    _wake_worker()
    return {"ok": True}


@router.post("/test", dependencies=_ADMIN)
def send_test(req: TestEmailRequest):
    email = _clean_email(req.email)
    if not email:
        raise HTTPException(422, "Invalid email")
    ev = publish_event("test", {"recipient": {"email": email, "name": req.name or email}})
    if not ev:
        raise HTTPException(503, "Could not enqueue test notification")
    return {"queued": True, "event_id": ev}


@router.post("/tick")
def tick(x_cron_secret: Optional[str] = Header(None)):
    """Heartbeat for external schedulers (cron-job.org / Render Cron / GitHub Actions) - keeps the queue moving
    even if the web service slept or the embedded worker is disabled. Runs a bounded number of cycles inline."""
    if not Cfg.CRON_SECRET:
        raise HTTPException(503, "NOTIFY_CRON_SECRET is not configured")
    if not hmac.compare_digest(x_cron_secret or "", Cfg.CRON_SECRET):
        raise HTTPException(401, "Bad secret")
    worker = _WORKER or NotificationWorker()
    deadline, total = time.monotonic() + 25, 0
    for i in range(10):
        n = worker.run_once(force_reconcile=(i == 0))
        total += n
        if n == 0 or time.monotonic() > deadline:
            break
    return {"processed": total, **get_stats()}


_WEBHOOK_MAP = {"delivered": "delivered", "request": None, "opened": "opened", "click": "clicked",
                "soft_bounce": "soft_bounce", "softbounce": "soft_bounce", "hard_bounce": "hard_bounce",
                "hardbounce": "hard_bounce", "blocked": "blocked", "spam": "spam", "invalid_email": "invalid_email",
                "invalid": "invalid_email", "deferred": "deferred", "error": "error", "unsubscribed": "unsubscribed"}
_BAD = {"hard_bounce", "blocked", "invalid_email", "spam", "error"}


@router.post("/webhooks/brevo")
async def brevo_webhook(request: Request, token: str = Query("")):
    """Brevo transactional webhook. Configure URL: https://<api>/api/notifications/webhooks/brevo?token=<secret>"""
    if not Cfg.WEBHOOK_SECRET:
        raise HTTPException(503, "NOTIFY_WEBHOOK_SECRET is not configured")
    if not hmac.compare_digest(token, Cfg.WEBHOOK_SECRET):
        raise HTTPException(401, "Bad token")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    events = data if isinstance(data, list) else [data]

    def apply() -> int:
        n = 0
        with _session() as s:
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                status = _WEBHOOK_MAP.get(str(ev.get("event") or "").lower().replace("-", "_"))
                mid = str(ev.get("message-id") or ev.get("messageId") or "").strip("<> ")
                if not status or not mid:
                    continue
                res = s.execute(update(D).where(D.c.provider_message_id == mid).values(
                    provider_status=status, provider_status_at=_now()))
                n += res.rowcount or 0
                if status in _BAD:
                    _alert(f"bounce-{mid}", f"mail to {ev.get('email')} reported as {status} by provider")
            s.commit()
        return n

    return {"updated": await asyncio.to_thread(apply)}


# =====================================================================
# 13. CLI  (standalone worker process)
# =====================================================================
def main(argv: Optional[List[str]] = None) -> None:
    import argparse
    import signal

    p = argparse.ArgumentParser(description="Notification service")
    p.add_argument("command", choices=["worker", "once", "reconcile", "stats"])
    args = p.parse_args(argv)
    ensure_notification_schema()
    _startup_checks()
    if args.command == "stats":
        print(json.dumps(get_stats(), indent=2, default=str))
    elif args.command == "reconcile":
        print(f"published {reconcile_versions()} event(s)")
    elif args.command == "once":
        w, total = NotificationWorker(), 0
        for _ in range(20):
            n = w.run_once(force_reconcile=True)
            total += n
            if n == 0:
                break
        print(f"processed {total} item(s)")
    else:
        w = NotificationWorker()
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        w.start()
        stop.wait()
        w.stop()


if __name__ == "__main__":
    main()



