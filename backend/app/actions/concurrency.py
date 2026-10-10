"""
app/actions/concurrency.py
==========================
Concurrency protection for timetable mutations (single-file implementation).

THE PROBLEM THIS SOLVES
-----------------------
``ActionEngine.execute`` is a classic *read-modify-write*:

    load latest version -> simulate -> validate -> INSERT new child version

With two admins editing at once, both load version N, both insert a child of N,
and the version with the highest id silently wins. The other admin's change
disappears from the lineage (a *lost update*), and nobody is told.

THE SOLUTION (defence in depth, five layers)
--------------------------------------------
1. SERIALISATION      A per-scope distributed lock (PostgreSQL advisory lock /
                      MySQL GET_LOCK / in-process fallback) makes
                      "check head -> run engine -> commit" atomic across all
                      workers. It uses a dedicated connection with a timeout.
2. OPTIMISTIC CHECK   The client states which version it was looking at
                      (``If-Match`` header or body ``version_id``). If the
                      head has moved, we do not blindly overwrite.
3. 3-WAY MERGE        On a stale base we diff base->head (what others did)
                      against the simulated footprint of the caller's actions
                      on base (what this admin wants). Disjoint edits are
                      auto-rebased onto head; overlapping edits are rejected
                      with HTTP 409 and a precise, attributed conflict report.
4. IDEMPOTENCY        ``Idempotency-Key`` makes client retries safe: a replay
                      returns the stored result instead of applying twice.
5. DB SAFETY NET      Optional unique index so that no two versions can share
                      a parent, even if some other code path bypasses the lock.
                      Post-commit invariant checks log CRITICAL on violations.

The H1-H11 validation inside ``ActionEngine`` still runs on the rebased head,
so semantic clashes (teacher/room/semester double-booking) caused by the
merge are caught as usual.

INTEGRATION (about ten lines)
-----------------------------
Startup (main.py)::

    from app.actions.concurrency import (
        install_concurrency_handlers, ensure_concurrency_schema,
    )
    install_concurrency_handlers(app)
    ensure_concurrency_schema(engine)          # idempotency table (or use Alembic)
    # optional hard guarantee, Postgres/SQLite; read the docstring first:
    # ensure_lineage_integrity_index(engine)

Router (replace the ``ActionEngine(...)`` + ``engine.execute(parsed)`` pair)::

    from fastapi import Response
    from app.actions.concurrency import (
        SafeActionEngine, if_match_header, idempotency_key_header,
        resolve_expected_version, current_head,
    )

    @router.post("/execute", response_model=ActionExecuteResponse)
    def execute_actions(req: ActionExecuteRequest, background_tasks: BackgroundTasks,
                        response: Response,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_teacher_or_admin),
                        if_match: Optional[str] = Depends(if_match_header),
                        idem_key: Optional[str] = Depends(idempotency_key_header)):
        parsed = [parse_action(a) for a in req.actions]
        guarded = SafeActionEngine(
            db=db, user=user,
            base_version_id=resolve_expected_version(req.version_id, if_match),
            partial_ok=req.partial_ok, skip_suggestions=req.skip_suggestions,
            idempotency_key=idem_key,
        ).execute(parsed)
        result = guarded.result
        response.headers.update(guarded.response_headers())
        ...                                    # rest unchanged

For ``/chat`` capture the base BEFORE building the LLM context and use the same
value when executing. The LLM call takes seconds and is the widest stale window::

    base = resolve_expected_version(req.version_id, if_match) or current_head(db)
    schedule_ctx = _build_schedule_context(db, base)
    ...
    SafeActionEngine(db=db, user=user, base_version_id=base, partial_ok=True, ...)

Client contract
---------------
* Read responses carry ``ETag: "tt-<version_id>"``; send it back as ``If-Match``.
* 409 ``entry_conflict``   -> reload, show the conflict, let the admin re-apply.
* 409 ``stale_version`` / ``diverged_lineage`` -> reload and retry.
* 428 ``precondition_required`` -> send ``If-Match`` / ``version_id``.
* 503 ``lock_timeout`` / ``transient_backend_error`` -> retry after ``Retry-After``
  with the SAME ``Idempotency-Key``.

Known limitation: ``AddTeacherBusyAction`` inside ``_simulate_one`` calls
``db.add`` as a simulation side effect. If that action is later rejected under
``partial_ok`` the pending row can be committed by a later commit in the same
session. Moving that insert into the commit phase of the engine is recommended.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import hashlib
import json
import os
import random
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from itertools import chain
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.actions.engine import (
    ActionEngine,
    ActionResult,
    EngineResult,
    _load_schedule,
    _simulate_one,
)
from app.actions.types import (
    AddTeacherBusyAction,
    GenerateTimetableAction,
    OptimizeTimetableAction,
    ParsedAction,
    RestoreVersionAction,
)
from app.models.base import Base
from app.models.timetable import TimetableVersion

__all__ = [
    "ConcurrencyError", "StaleVersionError", "EntryConflictError", "DivergedLineageError",
    "PreconditionRequiredError", "LockTimeoutError", "LockLostError", "VersionNotFoundError",
    "IdempotencyKeyReuseError", "InvalidRequestError", "TransientBackendError",
    "StalePolicy", "ConflictGranularity", "GuardConfig", "GuardedResult", "SafeActionEngine",
    "ActionIdempotencyRecord", "ensure_concurrency_schema", "ensure_lineage_integrity_index",
    "install_concurrency_handlers", "if_match_header", "idempotency_key_header",
    "resolve_expected_version", "current_head", "etag_for_version", "parse_etag",
    "entry_fingerprint", "diff_schedules", "detect_conflicts",
]

_log = logger.bind(component="timetable.concurrency")

# =============================================================================
# Exceptions (each maps to one HTTP status via install_concurrency_handlers)
# =============================================================================


class ConcurrencyError(Exception):
    """Base class for every error raised by this module."""

    status_code: int = 409
    error_code: str = "concurrency_error"
    retry_after: Optional[int] = None

    def __init__(
        self,
        message: str,
        *,
        base_version_id: Optional[int] = None,
        current_version_id: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.base_version_id = base_version_id
        self.current_version_id = current_version_id
        self.details = details or {}

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "error": self.error_code,
            "message": self.message,
            "base_version_id": self.base_version_id,
            "current_version_id": self.current_version_id,
        }
        payload.update(self.details)
        return payload


class StaleVersionError(ConcurrencyError):
    """The caller's base version is behind head and cannot be auto-merged."""
    error_code = "stale_version"


class EntryConflictError(ConcurrencyError):
    """Another admin changed the same entries since the caller loaded the timetable."""
    error_code = "entry_conflict"


class DivergedLineageError(ConcurrencyError):
    """The base version is not an ancestor of head (regenerated or branched)."""
    error_code = "diverged_lineage"


class PreconditionRequiredError(ConcurrencyError):
    status_code = 428
    error_code = "precondition_required"


class VersionNotFoundError(ConcurrencyError):
    status_code = 404
    error_code = "version_not_found"


class IdempotencyKeyReuseError(ConcurrencyError):
    status_code = 422
    error_code = "idempotency_key_reuse"


class InvalidRequestError(ConcurrencyError):
    status_code = 422
    error_code = "invalid_request"


class LockTimeoutError(ConcurrencyError):
    """Another admin's change is committing right now; retry shortly."""
    status_code = 503
    error_code = "lock_timeout"
    retry_after = 2


class LockLostError(ConcurrencyError):
    status_code = 503
    error_code = "lock_lost"
    retry_after = 1


class TransientBackendError(ConcurrencyError):
    status_code = 503
    error_code = "transient_backend_error"
    retry_after = 1


# =============================================================================
# Configuration
# =============================================================================


class StalePolicy(str, Enum):
    REJECT = "reject"              # any head movement -> 409
    AUTO_REBASE = "auto_rebase"    # merge disjoint edits, reject overlapping ones


class ConflictGranularity(str, Enum):
    ENTRY = "entry"    # conflict only if the same concrete class entry was changed
    SERIES = "series"  # stricter: same (program, semester, subject, type) series


def default_head_resolver(db: Session) -> Optional[int]:
    """Head = newest version id (consistent with ActionEngine's own default)."""
    return db.query(func.max(TimetableVersion.id)).scalar()


@dataclass(frozen=True)
class GuardConfig:
    scope: str = "timetable"
    lock_timeout_s: float = 10.0
    stale_policy: StalePolicy = StalePolicy.AUTO_REBASE
    granularity: ConflictGranularity = ConflictGranularity.ENTRY
    require_base_version: bool = False       # set True once all clients send If-Match
    max_rebase_distance: int = 25            # refuse to merge across more versions
    max_actions: int = 50
    idempotency_ttl_s: int = 24 * 3600
    attribution_limit: int = 10              # max versions inspected to name "who changed it"
    head_resolver: Callable[[Session], Optional[int]] = default_head_resolver

    @classmethod
    def from_env(cls) -> "GuardConfig":
        def _num(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except ValueError:
                return default

        return cls(
            lock_timeout_s=_num("TIMETABLE_LOCK_TIMEOUT_S", cls.lock_timeout_s),
            stale_policy=StalePolicy(os.getenv("TIMETABLE_STALE_POLICY", cls.stale_policy.value)),
            granularity=ConflictGranularity(
                os.getenv("TIMETABLE_CONFLICT_GRANULARITY", cls.granularity.value)
            ),
            require_base_version=os.getenv("TIMETABLE_REQUIRE_BASE_VERSION", "0").lower()
            in ("1", "true", "yes"),
            max_rebase_distance=int(_num("TIMETABLE_MAX_REBASE_DISTANCE", cls.max_rebase_distance)),
        )


# =============================================================================
# ETag helpers
# =============================================================================

_ETAG_RE = re.compile(r'^(?:W/)?"?(?:tt-)?(\d+)"?$')


def etag_for_version(version_id: Optional[int]) -> str:
    return f'"tt-{version_id}"' if version_id is not None else '"tt-empty"'


def parse_etag(value: Optional[str]) -> Optional[int]:
    """Parse ``"tt-12"`` / ``W/"tt-12"`` / ``12`` into 12. ``*`` or empty -> None."""
    if value is None:
        return None
    value = value.strip()
    if not value or value == "*":
        return None
    match = _ETAG_RE.match(value)
    if not match:
        raise InvalidRequestError(f"Malformed If-Match value: {value!r}")
    return int(match.group(1))


def resolve_expected_version(body_version_id: Optional[int], if_match: Optional[str]) -> Optional[int]:
    """Combine the ``If-Match`` header with the body ``version_id`` (they must agree)."""
    header_version = parse_etag(if_match)
    if header_version is not None and body_version_id is not None and header_version != body_version_id:
        raise InvalidRequestError(
            "If-Match header and body version_id disagree.",
            base_version_id=body_version_id,
            details={"if_match_version_id": header_version},
        )
    return header_version if header_version is not None else body_version_id


def current_head(db: Session, config: Optional[GuardConfig] = None) -> Optional[int]:
    return (config or GuardConfig()).head_resolver(db)


# FastAPI dependencies -------------------------------------------------------

def if_match_header(if_match: Optional[str] = Header(default=None, alias="If-Match")) -> Optional[str]:
    return if_match


def idempotency_key_header(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Optional[str]:
    return idempotency_key


def install_concurrency_handlers(app: FastAPI) -> None:
    """Register one handler that turns every ConcurrencyError into a structured response."""

    @app.exception_handler(ConcurrencyError)
    async def _concurrency_handler(_: Request, exc: ConcurrencyError) -> JSONResponse:
        headers: Dict[str, str] = {}
        if exc.retry_after:
            headers["Retry-After"] = str(exc.retry_after)
        if exc.current_version_id is not None:
            headers["ETag"] = etag_for_version(exc.current_version_id)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload(), headers=headers)


# =============================================================================
# Fingerprints, diffs and conflict detection (pure functions, no DB)
# =============================================================================
#
# Entries are copied per version (copy-on-write), so their DB ids are not a
# stable identity. A content fingerprint is used instead, and schedules are
# compared as multisets. That correctly handles identical duplicate rows.

_VOLATILE_KEYS = frozenset({"id", "entry_id", "version_id", "created_at", "updated_at"})
_SERIES_FIELDS = ("program", "semester", "subject_code", "type")


def _norm(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def entry_fingerprint(entry: Dict[str, Any]) -> str:
    canon = json.dumps(
        {k: _norm(v) for k, v in entry.items() if k not in _VOLATILE_KEYS},
        sort_keys=True, default=str, separators=(",", ":"),
    )
    return hashlib.blake2b(canon.encode("utf-8"), digest_size=12).hexdigest()


def series_key(entry: Dict[str, Any]) -> str:
    return "|".join(str(entry.get(k, "")).strip() for k in _SERIES_FIELDS)


def describe_entry(entry: Dict[str, Any]) -> str:
    name = entry.get("subject_code") or entry.get("subject") or "?"
    return (
        f"{name} ({entry.get('program', '?')} {entry.get('semester', '?')}) "
        f"{entry.get('day', '?')} {entry.get('start', '?')}-{entry.get('end', '?')}"
    )


@dataclass
class ScheduleDiff:
    removed: Counter                 # fingerprint -> copies that disappeared / were changed
    added: Counter                   # fingerprint -> copies that appeared
    samples: Dict[str, Dict[str, Any]]

    @property
    def is_empty(self) -> bool:
        return not self.removed and not self.added

    def touched_series(self) -> set:
        return {series_key(self.samples[fp]) for fp in chain(self.removed, self.added)}


def diff_schedules(before: Sequence[Dict[str, Any]], after: Sequence[Dict[str, Any]]) -> ScheduleDiff:
    cb: Counter = Counter()
    ca: Counter = Counter()
    samples: Dict[str, Dict[str, Any]] = {}
    for e in before:
        fp = entry_fingerprint(e)
        cb[fp] += 1
        samples.setdefault(fp, e)
    for e in after:
        fp = entry_fingerprint(e)
        ca[fp] += 1
        samples.setdefault(fp, e)
    return ScheduleDiff(removed=cb - ca, added=ca - cb, samples=samples)


@dataclass
class EntryConflict:
    kind: str                                  # "same_entry" | "same_series"
    entry: Dict[str, Any]
    series: str
    message: str
    fingerprint: str = ""
    modified_by: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "entry": self.entry,
            "entry_etag": self.fingerprint,
            "series": self.series,
            "message": self.message,
            "modified_by": self.modified_by,
        }


def detect_conflicts(
    base_schedule: Sequence[Dict[str, Any]],
    mine: ScheduleDiff,
    theirs: ScheduleDiff,
    granularity: ConflictGranularity,
    base_version_id: Optional[int] = None,
) -> List[EntryConflict]:
    """
    ``mine``   = base -> (base + my actions);  ``theirs`` = base -> head.

    Write-write conflict on an entry exists when both sides consumed it. With
    n identical copies in base, k removed by me and m removed by them, the
    sides collide iff k + m > n.
    """
    base_counts = Counter(entry_fingerprint(e) for e in base_schedule)
    conflicts: List[EntryConflict] = []
    reported_series: set = set()

    for fp, k in mine.removed.items():
        m = theirs.removed.get(fp, 0)
        if m and (k + m) > base_counts.get(fp, 0):
            entry = dict(mine.samples.get(fp) or theirs.samples[fp])
            conflicts.append(EntryConflict(
                kind="same_entry", fingerprint=fp, entry=entry, series=series_key(entry),
                message=(f"{describe_entry(entry)} was changed by another admin after you "
                         f"loaded version {base_version_id}."),
            ))
            reported_series.add(series_key(entry))

    if granularity is ConflictGranularity.SERIES:
        overlap = (mine.touched_series() & theirs.touched_series()) - reported_series
        for s in sorted(overlap):
            sample = next(
                (theirs.samples[fp] for fp in chain(theirs.removed, theirs.added)
                 if series_key(theirs.samples[fp]) == s),
                {},
            )
            conflicts.append(EntryConflict(
                kind="same_series", fingerprint="", entry=dict(sample), series=s,
                message=(f"Another admin changed classes of the same subject "
                         f"({describe_entry(sample)}) after you loaded version {base_version_id}."),
            ))
    return conflicts


# =============================================================================
# Version lineage helpers
# =============================================================================


@dataclass
class VersionInfo:
    version_id: int
    parent_version_id: Optional[int]
    created_by: Optional[int]
    change_summary: str
    created_at: Optional[str]
    status: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _version_exists(db: Session, version_id: int) -> bool:
    return db.query(TimetableVersion.id).filter(TimetableVersion.id == version_id).first() is not None


def _collect_lineage(
    db: Session, head_id: int, base_id: int, max_distance: int
) -> List[VersionInfo]:
    """Versions strictly after ``base_id`` up to ``head_id``, oldest first."""
    chain_: List[VersionInfo] = []
    seen: set = set()
    cur: Optional[int] = head_id
    while cur is not None and cur != base_id:
        if cur in seen:  # corrupted parent pointers; never loop forever
            raise DivergedLineageError(
                "Version history is corrupted (cycle).", base_version_id=base_id, current_version_id=head_id)
        seen.add(cur)
        if len(chain_) >= max_distance:
            raise StaleVersionError(
                f"Your copy is {max_distance}+ versions behind. Reload the timetable.",
                base_version_id=base_id, current_version_id=head_id,
            )
        row = (
            db.query(
                TimetableVersion.id, TimetableVersion.parent_version_id, TimetableVersion.created_by,
                TimetableVersion.change_summary, TimetableVersion.created_at, TimetableVersion.status,
            ).filter(TimetableVersion.id == cur).one_or_none()
        )
        if row is None:
            break
        status = getattr(row.status, "value", row.status)
        chain_.append(VersionInfo(
            version_id=row.id, parent_version_id=row.parent_version_id, created_by=row.created_by,
            change_summary=row.change_summary or "",
            created_at=row.created_at.isoformat() if row.created_at else None,
            status=str(status) if status is not None else None,
        ))
        cur = row.parent_version_id
    if cur != base_id:
        raise DivergedLineageError(
            f"Version {base_id} is not an ancestor of the current timetable (version {head_id}); "
            "it was regenerated or branched. Reload the timetable.",
            base_version_id=base_id, current_version_id=head_id,
        )
    chain_.reverse()
    return chain_


_GLOBAL_ACTIONS = (RestoreVersionAction, GenerateTimetableAction, OptimizeTimetableAction)


def compute_footprint(
    db: Session, base_schedule: List[Dict[str, Any]], actions: Sequence[ParsedAction]
) -> ScheduleDiff:
    """
    What would these actions change if applied to the (stale) base schedule?
    Pure in-memory simulation, no validation, no persistence. Actions that fail
    to simulate contribute nothing; the real engine reports them properly later.
    """
    current = copy.deepcopy(base_schedule)
    for action in actions:
        try:
            if isinstance(action, AddTeacherBusyAction):
                # Do not call _simulate_one: it db.add()s a row as a side effect.
                try:
                    from app.api.busy_slots import _entry_conflicts_busy
                except ImportError:  # be conservative: every class of that teacher
                    def _entry_conflicts_busy(_e: dict, _d: Any) -> bool:  # type: ignore[misc]
                        return True
                affected = {
                    id(e) for e in current
                    if e.get("teacher") == action.teacher_short_name
                    and _entry_conflicts_busy(e, action.day_of_week)
                }
                current = [e for e in current if id(e) not in affected]
                continue
            new_sched, res = _simulate_one(copy.deepcopy(current), action, db)
            if res.success:
                current = new_sched
        except Exception as exc:  # noqa: BLE001 - footprint is best effort
            _log.debug("footprint simulation skipped for {}: {}", getattr(action, "action", "?"), exc)
    return diff_schedules(base_schedule, current)


def _attribute_conflicts(
    db: Session, conflicts: List[EntryConflict], lineage: List[VersionInfo],
    base_id: int, limit: int,
) -> None:
    """Best effort: name the version/admin that changed each conflicting entry."""
    if not conflicts or not lineage or len(lineage) > limit:
        return
    cache: Dict[int, List[Dict[str, Any]]] = {}

    def sched(vid: int) -> List[Dict[str, Any]]:
        if vid not in cache:
            cache[vid] = _load_schedule(db, vid)[0]
        return cache[vid]

    try:
        prev = base_id
        for info in lineage:
            step = diff_schedules(sched(prev), sched(info.version_id))
            step_series = {series_key(step.samples[fp]) for fp in chain(step.removed, step.added)}
            for c in conflicts:
                if c.modified_by is not None:
                    continue
                hit = (c.fingerprint in step.removed) if c.kind == "same_entry" else (c.series in step_series)
                if hit:
                    c.modified_by = info.to_dict()
            prev = info.version_id
    except Exception as exc:  # noqa: BLE001 - never mask the real conflict
        _log.warning("conflict attribution failed: {}", exc)


# =============================================================================
# Distributed lock
# =============================================================================

_LOCAL_LOCKS: Dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _local_lock_for(scope: str) -> threading.Lock:
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(scope, threading.Lock())


def _advisory_key(scope: str) -> int:
    digest = hashlib.blake2b(f"tt-lock:{scope}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _mysql_lock_name(scope: str) -> str:
    return "tt_" + hashlib.blake2b(scope.encode(), digest_size=16).hexdigest()  # <= 64 chars


class LockHandle:
    """Yielded inside the lock; lets callers verify the lock connection is still alive."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def ensure_held(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            raise LockLostError("Lost the database lock connection; aborting safely.") from exc


class ScopeLock:
    """
    Cross-process mutual exclusion for one scope.

    * PostgreSQL: session-level advisory lock on a DEDICATED autocommit connection.
      It is released on unlock, or automatically if the process or connection dies.
      A session lock must not live on the request session's connection, because
      the engine commits mid-request and the connection may return to the pool.
    * MySQL/MariaDB: GET_LOCK / RELEASE_LOCK on a dedicated connection.
    * Others (SQLite): in-process lock only; fine for dev and single-process use.

    Acquisition polls with try-lock + backoff so waiters never pin a pooled
    connection indefinitely, and gives up with ``LockTimeoutError``.
    """

    def __init__(self, scope: str, timeout_s: float) -> None:
        self.scope = scope
        self.timeout_s = timeout_s

    @contextlib.contextmanager
    def hold(self, db: Session) -> Iterator[LockHandle]:
        deadline = time.monotonic() + self.timeout_s
        local = _local_lock_for(self.scope)
        if not local.acquire(timeout=self.timeout_s):
            raise LockTimeoutError("Another timetable change is in progress. Please retry.")
        conn = None
        acquired = False
        unlock_sql = None
        params: Dict[str, Any] = {}
        try:
            engine = db.get_bind().engine
            dialect = engine.dialect.name
            if dialect in ("postgresql", "mysql", "mariadb"):
                conn = engine.connect()
                conn.execution_options(isolation_level="AUTOCOMMIT")
                if dialect == "postgresql":
                    params = {"k": _advisory_key(self.scope)}
                    try_sql = text("SELECT pg_try_advisory_lock(:k)")
                    unlock_sql = text("SELECT pg_advisory_unlock(:k)")
                else:
                    params = {"n": _mysql_lock_name(self.scope)}
                    try_sql = text("SELECT GET_LOCK(:n, 0)")
                    unlock_sql = text("SELECT RELEASE_LOCK(:n)")
                self._poll(conn, try_sql, params, deadline)
                acquired = True
            yield LockHandle(conn)
        finally:
            if conn is not None:
                try:
                    if acquired and unlock_sql is not None:
                        if not conn.execute(unlock_sql, params).scalar():
                            _log.warning("lock {} was not held at release time", self.scope)
                except Exception:  # noqa: BLE001
                    _log.exception("failed to release lock {}; invalidating connection", self.scope)
                    with contextlib.suppress(Exception):
                        conn.invalidate()  # closing the DBAPI connection drops the lock
                finally:
                    with contextlib.suppress(Exception):
                        conn.close()
            local.release()

    @staticmethod
    def _poll(conn: Any, stmt: Any, params: Dict[str, Any], deadline: float) -> None:
        delay = 0.01
        while True:
            if conn.execute(stmt, params).scalar():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LockTimeoutError("Another timetable change is in progress. Please retry.")
            time.sleep(min(delay + random.uniform(0, delay / 2), remaining))
            delay = min(delay * 2, 0.25)


# =============================================================================
# Idempotency storage
# =============================================================================


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ActionIdempotencyRecord(Base):
    """Stores the outcome of a committed request so client retries are replayed, not re-applied."""

    __tablename__ = "action_idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "user_id", "idempotency_key", name="uq_action_idem_scope_user_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


def ensure_concurrency_schema(bind: Any) -> None:
    """Create the idempotency table if missing (use Alembic in production if you prefer)."""
    ActionIdempotencyRecord.__table__.create(bind=bind, checkfirst=True)


_SINGLE_CHILD_INDEX = "uq_timetable_versions_single_child"


def ensure_lineage_integrity_index(bind: Any) -> None:
    """
    OPTIONAL hard guarantee: at most ONE child per parent version. Even code that
    bypasses the lock cannot fork history; the loser gets an IntegrityError that
    the guard converts into a 409.

    Only enable if your workflow never creates several children of one parent
    (e.g. multiple drafts cloned from one published version), and if existing
    data has no such duplicates, otherwise index creation fails. Supported on
    PostgreSQL and SQLite (partial unique index).
    """
    ddl = (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_SINGLE_CHILD_INDEX} "
        "ON timetable_versions (parent_version_id) WHERE parent_version_id IS NOT NULL"
    )
    with bind.begin() as conn:
        conn.execute(text(ddl))


def _engine_result_to_json(result: EngineResult) -> str:
    return json.dumps(dataclasses.asdict(result), default=str)


def _engine_result_from_json(raw: str) -> EngineResult:
    data = json.loads(raw)
    ar_fields = {f.name for f in dataclasses.fields(ActionResult)}
    data["results"] = [
        ActionResult(**{k: v for k, v in r.items() if k in ar_fields}) for r in data.get("results", [])
    ]
    er_fields = {f.name for f in dataclasses.fields(EngineResult)}
    return EngineResult(**{k: v for k, v in data.items() if k in er_fields})


_IDEM_KEY_RE = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")


# =============================================================================
# Public result type
# =============================================================================


@dataclass
class GuardedResult:
    result: EngineResult
    base_version_id: Optional[int]
    applied_on_version_id: Optional[int]
    rebased: bool = False
    replayed: bool = False
    intervening_versions: List[VersionInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def new_version_id(self) -> Optional[int]:
        return self.result.new_version_id

    @property
    def head_version_id(self) -> Optional[int]:
        return self.result.new_version_id or self.applied_on_version_id

    def response_headers(self) -> Dict[str, str]:
        headers = {"ETag": etag_for_version(self.head_version_id)}
        if self.rebased:
            headers["X-Timetable-Rebased"] = "true"
        if self.replayed:
            headers["X-Idempotent-Replay"] = "true"
        return headers


# =============================================================================
# The guarded engine
# =============================================================================


class SafeActionEngine:
    """
    Drop-in, concurrency-safe wrapper around ``ActionEngine``.

    Args:
        base_version_id: the version the admin was looking at (from If-Match or
            body ``version_id``). ``None`` means "no stale detection": changes
            are still serialised, so nothing is lost, but an admin acting on an
            outdated screen is not warned.
        idempotency_key: optional client-generated key making retries safe.
    """

    def __init__(
        self,
        db: Session,
        user: Any,
        *,
        base_version_id: Optional[int] = None,
        partial_ok: bool = False,
        skip_suggestions: bool = False,
        idempotency_key: Optional[str] = None,
        config: Optional[GuardConfig] = None,
    ) -> None:
        self.db = db
        self.user = user
        self.base_version_id = base_version_id
        self.partial_ok = partial_ok
        self.skip_suggestions = skip_suggestions
        self.idempotency_key = idempotency_key
        self.cfg = config or GuardConfig()

    # ------------------------------------------------------------------ public

    def execute(self, actions: Sequence[ParsedAction]) -> GuardedResult:
        actions = list(actions)
        self._validate_request(actions)
        user_id = getattr(self.user, "id", None)
        request_hash = self._request_hash(actions)
        t0 = time.monotonic()
        head_id: Optional[int] = None
        try:
            with ScopeLock(self.cfg.scope, self.cfg.lock_timeout_s).hold(self.db) as handle:
                waited = time.monotonic() - t0
                # End any open transaction so the reads below see everything committed
                # before we took the lock (matters for MySQL REPEATABLE READ snapshots).
                self.db.rollback()

                replay = self._lookup_replay(user_id, request_hash)
                if replay is not None:
                    _log.info("idempotent replay key={} user={}", self.idempotency_key, user_id)
                    return replay

                head_id = self.cfg.head_resolver(self.db)
                base_id = self._resolve_base(head_id)
                rebased, lineage = self._plan(actions, base_id, head_id)

                handle.ensure_held()
                result = self._run_engine(actions, head_id)
                warnings = self._post_checks(result, head_id)

                guarded = GuardedResult(
                    result=result, base_version_id=base_id, applied_on_version_id=head_id,
                    rebased=rebased, intervening_versions=lineage, warnings=warnings,
                )
                self._store_replay(user_id, request_hash, guarded)
                _log.info(
                    "timetable change user={} base={} head={} new={} rebased={} ok={} wait_ms={:.0f} hold_ms={:.0f}",
                    user_id, base_id, head_id, result.new_version_id, rebased, result.success,
                    waited * 1000, (time.monotonic() - t0 - waited) * 1000,
                )
                return guarded
        except ConcurrencyError:
            self._safe_rollback()
            raise
        except IntegrityError as exc:
            self._safe_rollback()
            if _SINGLE_CHILD_INDEX in str(getattr(exc, "orig", exc)):
                raise StaleVersionError(
                    "Another admin saved a new version at the same moment. Reload and retry.",
                    base_version_id=self.base_version_id,
                ) from exc
            raise
        except OperationalError as exc:
            self._safe_rollback()
            _log.error("transient DB failure (head before={}): {}", head_id, exc)
            raise TransientBackendError("Temporary database problem. Retry with the same Idempotency-Key.") from exc
        except Exception:
            self._safe_rollback()
            _log.exception("unexpected failure during guarded execute (head before={})", head_id)
            raise

    # ----------------------------------------------------------------- internals

    def _safe_rollback(self) -> None:
        with contextlib.suppress(Exception):
            self.db.rollback()

    def _validate_request(self, actions: List[ParsedAction]) -> None:
        if not actions:
            raise InvalidRequestError("No actions supplied.")
        if len(actions) > self.cfg.max_actions:
            raise InvalidRequestError(f"Too many actions in one request (max {self.cfg.max_actions}).")
        if self.base_version_id is not None and self.base_version_id <= 0:
            raise InvalidRequestError("version_id must be a positive integer.")
        if self.idempotency_key is not None and not _IDEM_KEY_RE.match(self.idempotency_key):
            raise InvalidRequestError("Idempotency-Key must be 8-128 chars of [A-Za-z0-9._:-].")

    def _request_hash(self, actions: List[ParsedAction]) -> str:
        def dump(a: Any) -> Any:
            if hasattr(a, "model_dump"):
                return a.model_dump(mode="json")
            if hasattr(a, "dict"):
                return a.dict()
            return repr(a)

        payload = json.dumps(
            {"actions": [dump(a) for a in actions], "base": self.base_version_id, "partial_ok": self.partial_ok},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _resolve_base(self, head_id: Optional[int]) -> Optional[int]:
        if self.base_version_id is None:
            if self.cfg.require_base_version:
                raise PreconditionRequiredError(
                    "Send If-Match (or version_id) identifying the version you are editing.",
                    current_version_id=head_id,
                )
            return head_id
        if not _version_exists(self.db, self.base_version_id):
            raise VersionNotFoundError(
                f"Version {self.base_version_id} does not exist.",
                base_version_id=self.base_version_id, current_version_id=head_id,
            )
        return self.base_version_id

    def _plan(
        self, actions: List[ParsedAction], base_id: Optional[int], head_id: Optional[int]
    ) -> "tuple[bool, List[VersionInfo]]":
        """Decide: apply directly, rebase onto head, or reject. Returns (rebased, lineage)."""
        if base_id is None or head_id is None or base_id == head_id:
            return False, []

        lineage = _collect_lineage(self.db, head_id, base_id, self.cfg.max_rebase_distance)
        versions = [v.to_dict() for v in lineage]
        _log.info("stale base={} head={} behind={} policy={}", base_id, head_id, len(lineage), self.cfg.stale_policy.value)

        if self.cfg.stale_policy is StalePolicy.REJECT:
            raise StaleVersionError(
                "The timetable changed since you loaded it. Reload and retry.",
                base_version_id=base_id, current_version_id=head_id,
                details={"intervening_versions": versions},
            )
        if any(isinstance(a, _GLOBAL_ACTIONS) for a in actions):
            raise StaleVersionError(
                "Restore/generate/optimise replace the whole timetable and need the latest version. "
                "Reload and retry.",
                base_version_id=base_id, current_version_id=head_id,
                details={"intervening_versions": versions},
            )

        base_schedule, _ = _load_schedule(self.db, base_id)
        head_schedule, _ = _load_schedule(self.db, head_id)
        theirs = diff_schedules(base_schedule, head_schedule)
        mine = compute_footprint(self.db, base_schedule, actions)
        conflicts = detect_conflicts(base_schedule, mine, theirs, self.cfg.granularity, base_id)

        if conflicts:
            _attribute_conflicts(self.db, conflicts, lineage, base_id, self.cfg.attribution_limit)
            raise EntryConflictError(
                f"{len(conflicts)} class(es) you are changing were modified by another admin. "
                "Nothing was applied.",
                base_version_id=base_id, current_version_id=head_id,
                details={
                    "conflicts": [c.to_dict() for c in conflicts],
                    "intervening_versions": versions,
                    "hint": f"Reload version {head_id} and re-apply your change.",
                },
            )
        _log.info("auto-rebase base={} -> head={} (mine={} entries, theirs={} entries)",
                  base_id, head_id, len(mine.removed) + len(mine.added), len(theirs.removed) + len(theirs.added))
        return True, lineage

    def _run_engine(self, actions: List[ParsedAction], head_id: Optional[int]) -> EngineResult:
        # Pinning version_id to the head observed under the lock guarantees the new
        # version is a direct child of the real head, so lineage stays linear.
        engine = ActionEngine(
            db=self.db, user=self.user, version_id=head_id,
            partial_ok=self.partial_ok, skip_suggestions=self.skip_suggestions,
        )
        return engine.execute(actions)

    def _post_checks(self, result: EngineResult, head_before: Optional[int]) -> List[str]:
        """Invariants that should always hold; violations are logged CRITICAL, never raised
        (the commit already happened) and surfaced as warnings."""
        warnings: List[str] = []
        new_id = result.new_version_id
        if new_id is None:
            return warnings
        parent = self.db.query(TimetableVersion.parent_version_id).filter(TimetableVersion.id == new_id).scalar()
        if head_before is not None and parent != head_before:
            msg = f"version {new_id} has parent {parent}, expected {head_before}"
            _log.critical("LINEAGE INVARIANT VIOLATED: {}", msg)
            warnings.append(msg)
        head_now = self.cfg.head_resolver(self.db)
        if head_now != new_id:
            msg = f"head is {head_now} but this request created {new_id}: a writer bypassed the lock"
            _log.critical("WRITE OUTSIDE GUARD DETECTED: {}", msg)
            warnings.append(msg)
        return warnings

    # --------------------------------------------------------------- idempotency

    def _lookup_replay(self, user_id: Optional[int], request_hash: str) -> Optional[GuardedResult]:
        if not self.idempotency_key or user_id is None:
            return None
        rec = (
            self.db.query(ActionIdempotencyRecord)
            .filter_by(scope=self.cfg.scope, user_id=user_id, idempotency_key=self.idempotency_key)
            .one_or_none()
        )
        if rec is None:
            return None
        if _aware(rec.expires_at) <= _utcnow():
            self.db.delete(rec)
            self.db.commit()
            return None
        if rec.request_hash != request_hash:
            raise IdempotencyKeyReuseError(
                "This Idempotency-Key was already used with a different request.",
                current_version_id=rec.result_version_id,
            )
        result = _engine_result_from_json(rec.response_json)
        return GuardedResult(
            result=result, base_version_id=self.base_version_id,
            applied_on_version_id=rec.result_version_id, replayed=True,
        )

    def _store_replay(self, user_id: Optional[int], request_hash: str, guarded: GuardedResult) -> None:
        # Only committed outcomes are stored; a failed attempt changed nothing and may be retried.
        if not self.idempotency_key or user_id is None or guarded.new_version_id is None:
            return
        try:
            now = _utcnow()
            self.db.query(ActionIdempotencyRecord).filter(ActionIdempotencyRecord.expires_at < now).delete(
                synchronize_session=False)
            self.db.add(ActionIdempotencyRecord(
                scope=self.cfg.scope, user_id=user_id, idempotency_key=self.idempotency_key,
                request_hash=request_hash, result_version_id=guarded.new_version_id,
                response_json=_engine_result_to_json(guarded.result),
                created_at=now, expires_at=now + timedelta(seconds=self.cfg.idempotency_ttl_s),
            ))
            self.db.commit()
        except Exception:  # noqa: BLE001 - the change is already committed; do not fail the request
            self._safe_rollback()
            _log.exception("could not store idempotency record for key {}", self.idempotency_key)
