# -*- coding: utf-8 -*-
"""
schedule_smart_weekly.py  —  Weekly-hours / preferred-days Smart Scheduler
===========================================================================

Drop into  app/api/schedule_smart_weekly.py  and mount it next to the existing
smart-schedule router:

    from app.api import schedule_smart_weekly
    api_router.include_router(
        schedule_smart_weekly.router,
        prefix="/actions/smart-schedule/weekly",
        tags=["smart-schedule-weekly"],
    )

The proposals it returns contain ``actions_to_apply`` (MOVE_CLASS / REMOVE_CLASS
followed by ADD_CLASS x N).  Execute them through the normal ActionEngine /
``/actions/execute`` with ``partial_ok=False`` so the bundle is atomic.

REQUEST (what the day buttons + inputs send)
--------------------------------------------
{
  "program": "B.Tech", "semester": "3rd",
  "subject_code": "ds", "subject_name": "Data Structure",
  "teacher": "SKS", "entry_type": "Theory", "room": null,
  "weekly_hours": 6,                              # total hours per week
  "selected_days": ["Tuesday","Wednesday","Thursday"],   # clicked day buttons
  "day_rule": "exactly" | "at_least" | "at_most", # how many teaching days
  "day_count": 3,                                 # k for the rule
  "outside_days": "auto" | "allow" | "deny"       # may other days substitute?
}

HOW THE USER'S SELECTION IS INTERPRETED
---------------------------------------
* FIXED mode  (selected days <= required days, e.g. click Tue/Wed/Thu, exactly 3):
  the clicked days are *wanted*.  If one has no valid slot it is REPLACED by the
  nearest conflict-free day that is not already in the plan (never a day that is
  already selected), keeping as many selected days as physically possible.
* POOL mode   (selected days > required days, e.g. "any 3 of Tue..Sat"):
  the clicked days are the *allowed range*.  Only they are used.  If fewer than
  the required number work, the engine says so and offers the best feasible
  option (fewer days, same weekly hours if a session may be that long) instead
  of fabricating an invalid schedule.
* ``outside_days`` overrides that default ("allow" / "deny").

SUGGESTION LADDER (strictly in this order)
------------------------------------------
 1. exact       every session on the user's days, nothing else touched
 2. substitute  conflict-free replacement day(s) for the blocked ones; the
                minimum number of substitutions; selected days are never repeated
 3. advanced    Advanced Suggestions: move (or, last resort, cancel) classes of
                the SAME program+semester to unlock the user's blocked days.
                Only offered when it honours MORE of the user's days than the
                best option above.  Moves are validated against the whole plan.
 4. partial     best feasible option that cannot meet the full request, with an
                explicit shortfall ({days, minutes}) and per-day reasons.

PERFORMANCE / CORRECTNESS NOTES
-------------------------------
* Exactly two DB round-trips per request, one in-memory board, per-resource
  interval lists, no deepcopy, no validate_schedule() in the search loops.
* Days are independent (H1/H2/H3 are per-day, H5 is per-day) so the engine
  computes the best windows per (day, duration) once, then enumerates day
  combinations (<= 63) -- the H6 "free day" rule is the only cross-day rule and
  is checked per combination.
* Every proposal is re-verified against an independent reference checker
  (H1,H2,H3,H5,H5b,H6,H12) on the FULL resulting schedule before it is shown;
  a proposal that fails is dropped (bug guard).  ``strict_verify`` additionally
  runs the real validate_schedule().
* Hard time budget (default 2.5 s) for the Advanced search; results are cached.

Run ``python schedule_smart_weekly.py`` for a built-in self-test (no app needed).
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal  # type: ignore

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Tunables
# ══════════════════════════════════════════════════════════════════════════════
DAY_START = 10 * 60                       # 10:00
DAY_END = 17 * 60 + 30                    # 17:30
STEP = 30                                 # search granularity (minutes)
GRID_STARTS = frozenset({10 * 60, 12 * 60, 14 * 60 + 30, 16 * 60 + 30})
BREAKS: Tuple[Tuple[int, int], ...] = ((14 * 60, 14 * 60 + 30),)   # soft penalty

THEORY_MIN, THEORY_MAX, THEORY_REF = 60, 180, 120   # session length limits
PRACTICAL_SESSION = 180
PRACTICAL_FORBIDDEN_DAYS = frozenset({"Saturday"})  # set() to disable

ALT_KEEP = 4                  # best window + 3 alternatives per session
ADV_MAX_DEPTH = 2             # max classes moved/cancelled per proposal
ADV_BRANCH = (8, 3)           # beam width per depth
ADV_MAX_SHAPES = 3
ADV_MAX_PROPOSALS = 3
PARTIAL_MAX = 2
DEFAULT_BUDGET_S = 2.5
CACHE_TTL_S = 60.0
CACHE_MAX = 128
_INF = 10 ** 9


# ══════════════════════════════════════════════════════════════════════════════
# Rules (injected so the engine is a pure function and testable without the app)
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Rules:
    working_days: List[str]
    internal_teachers: Set[str]
    room_facilities: Dict[str, Dict[str, Any]]
    subject_map: Dict[Tuple[str, str], Set[str]]
    teacher_set: Callable[[dict], Set[str]]
    normalize_program: Callable[[Optional[str]], str]

    @classmethod
    def from_app(cls) -> "Rules":
        from app.scheduler import constraints as C
        from app.scheduler.validator import teacher_set
        return cls(
            working_days=list(C.WORKING_DAYS),
            internal_teachers=set(C.INTERNAL_TEACHERS),
            room_facilities=dict(C.ROOM_FACILITIES),
            subject_map=C.PROGRAMME_SUBJECT_MAP,
            teacher_set=teacher_set,
            normalize_program=C.normalize_program,
        )


# ══════════════════════════════════════════════════════════════════════════════
# API models
# ══════════════════════════════════════════════════════════════════════════════
class WeeklyScheduleRequest(BaseModel):
    program: str
    semester: str
    subject_code: str
    subject_name: str
    teacher: str
    entry_type: str = "Theory"
    room: Optional[str] = None
    weekly_hours: float                                   # e.g. 6 or 4.5
    selected_days: List[str] = []                         # clicked day buttons
    day_rule: Literal["exactly", "at_least", "at_most"] = "exactly"
    day_count: Optional[int] = None                       # k for day_rule
    session_minutes: Optional[int] = None                 # force a session length
    outside_days: Literal["auto", "allow", "deny"] = "auto"
    include_advanced: bool = True
    allow_cancel: bool = True                             # last-resort REMOVE_CLASS
    max_proposals: int = 6
    time_budget_s: float = DEFAULT_BUDGET_S
    strict_verify: bool = False                           # also run real validator
    version_id: Optional[int] = None                      # specific version to solve on


class SlotOption(BaseModel):
    start: str
    end: str
    room: str


class SessionPlan(BaseModel):
    day: str
    start: str
    end: str
    room: str
    duration_minutes: int
    uses_requested_room: Optional[bool] = None
    alternatives: List[SlotOption] = []     # other valid windows, same day
    notes: List[str] = []


class Modification(BaseModel):
    type: str                                # "move" | "cancel"
    description: str
    unlocks_day: str
    affected_class: dict
    new_slot: Optional[dict] = None


class WeeklyProposal(BaseModel):
    id: str
    category: str                            # exact|substitute|advanced|partial
    satisfies_request: bool
    title: str
    description: str
    days: List[str]
    sessions: List[SessionPlan]
    total_minutes: int
    requested_minutes: int
    honored_days: List[str] = []             # selected days used
    dropped_days: List[str] = []             # selected days NOT used (fixed mode)
    substitute_days: List[str] = []          # days outside the selection used
    shortfall: Optional[dict] = None         # {"days": n, "minutes": m}
    modifications: List[Modification] = []
    actions_to_apply: List[dict] = []
    warnings: List[str] = []
    verified: bool = True


class DayReport(BaseModel):
    day: str
    selected: bool
    available: bool
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    windows: Dict[str, int] = {}             # duration(min) -> number of windows


class WeeklyScheduleResponse(BaseModel):
    found: bool
    status: str                              # exact|substituted|needs_changes|partial|none|invalid
    message: str = ""
    notes: List[str] = []
    proposals: List[WeeklyProposal] = []
    day_report: List[DayReport] = []
    valid_day_counts: List[int] = []         # day counts that can carry the weekly hours
    elapsed_ms: Optional[float] = None
    candidates_evaluated: int = 0
    truncated: bool = False
    verification_rejected: int = 0
    version_id: Optional[int] = None


# ══════════════════════════════════════════════════════════════════════════════
# Small helpers
# ══════════════════════════════════════════════════════════════════════════════
def _to_min(t: Any) -> int:
    h, m = str(t).strip().split(":")
    return int(h) * 60 + int(m)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _fmt_h(minutes: int) -> str:
    return f"{minutes / 60:g}h"


def _sem_key(e: dict) -> tuple:
    return (str(e.get("program", "")).strip().rstrip(".").lower(),
            str(e.get("semester", "")).strip().lower())


def _crosses_break(s: int, e: int) -> bool:
    return any(s < be and bs < e for bs, be in BREAKS)


def _same_subject(entry: dict, code: str, name: str) -> bool:
    want = {code.strip().lower(), name.strip().lower()} - {""}
    have = {str(entry.get("subject_code") or "").strip().lower(),
            str(entry.get("subject_name") or "").strip().lower()} - {""}
    return bool(want & have)


def _join(items: List[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ══════════════════════════════════════════════════════════════════════════════
# Request normalisation
# ══════════════════════════════════════════════════════════════════════════════
class RequestError(ValueError):
    """Invalid user input (→ HTTP 422)."""


@dataclass
class NormRequest:
    program: str
    semester: str
    subject_code: str
    subject_name: str
    teacher: str
    typ: str
    room: str
    W: int                      # weekly minutes
    days: List[str]             # selected days, canonical, ordered
    rule: str
    k: int
    session_minutes: Optional[int]
    outside_allowed: bool
    fixed_mode: bool
    include_advanced: bool
    allow_cancel: bool
    max_proposals: int
    strict_verify: bool
    skip_subject_days: bool = True


def normalize_request(req: WeeklyScheduleRequest, rules: Rules) -> NormRequest:
    wd = list(rules.working_days)

    # days
    days: List[str] = []
    for raw in req.selected_days or []:
        key = str(raw).strip().lower().rstrip(".")
        hit = [d for d in wd if d.lower() == key]
        if not hit and len(key) >= 3:
            hit = [d for d in wd if d.lower().startswith(key)]
        if len(hit) != 1:
            raise RequestError(f"Unknown or non-working day '{raw}'.")
        if hit[0] not in days:
            days.append(hit[0])
    days.sort(key=wd.index)

    # type
    typ = str(req.entry_type or "").strip().capitalize()
    if typ not in ("Theory", "Practical"):
        raise RequestError("entry_type must be 'Theory' or 'Practical'.")

    # weekly minutes
    W = int(round(float(req.weekly_hours) * 60))
    if W <= 0 or abs(float(req.weekly_hours) * 60 - W) > 1e-6 or W % STEP:
        raise RequestError("weekly_hours must be a positive multiple of 0.5.")
    if W > len(wd) * (PRACTICAL_SESSION if typ == "Practical" else THEORY_MAX):
        raise RequestError("weekly_hours is more than a week can hold.")

    # teacher
    from_teacher = {"teacher": req.teacher}
    if not rules.teacher_set(from_teacher):
        raise RequestError("teacher is required.")

    # rule / count
    rule, k = req.day_rule, req.day_count
    if k is None:
        if days:
            k = len(days)
        elif rule == "at_most":
            k = len(wd)
        elif rule == "at_least":
            k = 1
        else:
            raise RequestError("day_count is required for 'exactly' when no days are selected.")
    if not 1 <= k <= len(wd):
        raise RequestError(f"day_count must be between 1 and {len(wd)}.")

    sm = req.session_minutes
    if sm is not None:
        if sm % STEP or (typ == "Theory" and not THEORY_MIN <= sm <= THEORY_MAX) \
                or (typ == "Practical" and sm != PRACTICAL_SESSION):
            raise RequestError("session_minutes is outside the allowed session length.")

    fixed_mode = bool(days) and rule != "at_most" and len(days) <= k
    if not days:
        outside = True
    elif req.outside_days == "allow":
        outside = True
    elif req.outside_days == "deny":
        outside = False
    else:
        outside = fixed_mode

    return NormRequest(
        program=req.program.strip(), semester=req.semester.strip(),
        subject_code=req.subject_code.strip(), subject_name=req.subject_name.strip(),
        teacher=req.teacher.strip(), typ=typ, room=(req.room or "").strip(),
        W=W, days=days, rule=rule, k=k, session_minutes=sm,
        outside_allowed=outside, fixed_mode=fixed_mode,
        include_advanced=req.include_advanced, allow_cancel=req.allow_cancel,
        max_proposals=max(1, min(int(req.max_proposals), 12)),
        strict_verify=req.strict_verify,
    )


def subject_known(nr: NormRequest, rules: Rules) -> bool:
    allowed = rules.subject_map.get((nr.program.rstrip("."), nr.semester), set())
    if not allowed:
        return False
    code = nr.subject_code.lower()
    root = code[:-2] if code.endswith("-p") else code
    if code in allowed or root in allowed:
        return True
    return root in ("m", "mm") and ("m" in allowed or "mm" in allowed)


# ══════════════════════════════════════════════════════════════════════════════
# Session shapes: how the weekly minutes are cut into per-day sessions
# ══════════════════════════════════════════════════════════════════════════════
def _near_even_shape(W: int, n: int, dmin: int, dmax: int) -> Optional[Tuple[int, ...]]:
    """Most even split of W minutes into n sessions of 30-minute multiples."""
    if n <= 0:
        return None
    q = (W // n) // STEP * STEP
    rem = W - q * n
    if rem % STEP:
        return None
    extra = rem // STEP
    lo, hi = q, q + (STEP if extra else 0)
    if lo < dmin or hi > dmax or extra >= n:
        return None
    return tuple(sorted([hi] * extra + [lo] * (n - extra), reverse=True))


def _rule_ok(rule: str, k: int, n: int) -> bool:
    return n == k if rule == "exactly" else (n >= k if rule == "at_least" else n <= k)


def _shape_cost(shape: Tuple[int, ...], ref: int) -> int:
    if not shape:
        return 0
    return sum(abs(x - ref) for x in shape) // STEP + 2 * ((max(shape) - min(shape)) // STEP)


def build_shapes(nr: NormRequest, max_days: int) -> Tuple[List[Tuple[int, ...]], List[int]]:
    """Returns (shapes allowed by the day rule, every day-count that can carry W)."""
    shapes: List[Tuple[int, ...]] = []
    counts: List[int] = []
    for n in range(1, max_days + 1):
        if nr.typ == "Practical":
            shp = (PRACTICAL_SESSION,) * n if n * PRACTICAL_SESSION == nr.W else None
        elif nr.session_minutes:
            shp = (nr.session_minutes,) * n if n * nr.session_minutes == nr.W else None
        else:
            shp = _near_even_shape(nr.W, n, THEORY_MIN, THEORY_MAX)
        if shp:
            counts.append(n)
            if _rule_ok(nr.rule, nr.k, n):
                shapes.append(shp)
    ref = PRACTICAL_SESSION if nr.typ == "Practical" else THEORY_REF
    shapes.sort(key=lambda s: (_shape_cost(s, ref), len(s)))
    return shapes, counts


# ══════════════════════════════════════════════════════════════════════════════
# In-memory board
# ══════════════════════════════════════════════════════════════════════════════
class _Row:
    __slots__ = ("entry", "idx", "day", "s", "e", "sem", "teachers", "room", "typ", "res", "tag")

    def __init__(self, entry: dict, idx: int, rules: Rules, tag: str = ""):
        self.entry, self.idx, self.tag = entry, idx, tag
        self.day = entry.get("day")
        try:
            self.s, self.e = _to_min(entry["start"]), _to_min(entry["end"])
        except Exception:
            self.s = self.e = 0
        self.room = entry.get("room") or ""
        self.sem = _sem_key(entry)
        self.teachers = tuple(sorted(rules.teacher_set(entry)))
        self.typ = str(entry.get("type", "")).strip()
        res = [(0, self.sem)]
        res.extend((1, t) for t in self.teachers)
        if self.room:
            res.append((2, self.room))
        self.res = tuple(res)


class _Board:
    """Per-resource interval lists.  attach/detach are O(#resources of one class)."""

    def __init__(self, schedule: List[dict], busy: Dict[str, Set[str]], rules: Rules):
        self.rules, self.busy = rules, busy
        self.wd = list(rules.working_days)
        self.wd_set = set(self.wd)
        self.internal = set(rules.internal_teachers)
        self.by_res: Dict[tuple, List[_Row]] = defaultdict(list)
        self.tt: Dict[tuple, int] = defaultdict(int)                     # (t,day,type)
        self.tdays: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        fac = rules.room_facilities
        self.all_rooms = sorted(fac)
        self.lab_rooms = [r for r in self.all_rooms if fac[r].get("lab")]
        self.nonlab_rooms = [r for r in self.all_rooms if not fac[r].get("lab")]
        self.rows = [_Row(e, i, rules) for i, e in enumerate(schedule)]
        for r in self.rows:
            self.attach(r)
        # H6 already broken in the base schedule must not be reported as "new"
        self.base_full: Set[str] = set()
        for t in self.internal:
            days = self.tdays.get(t)
            if days and sum(1 for c in days.values() if c > 0) >= len(self.wd_set):
                self.base_full.add(t)

    def attach(self, r: _Row) -> None:
        for kind, key in r.res:
            self.by_res[(kind, key, r.day)].append(r)
        for t in r.teachers:
            self.tt[(t, r.day, r.typ)] += 1
            if r.day in self.wd_set:
                self.tdays[t][r.day] += 1

    def detach(self, r: _Row) -> None:
        for kind, key in r.res:
            self.by_res[(kind, key, r.day)].remove(r)
        for t in r.teachers:
            self.tt[(t, r.day, r.typ)] -= 1
            if r.day in self.wd_set:
                self.tdays[t][r.day] -= 1

    # -- day level rules: H12, H5/H5b, H6 (single new day) ------------------------
    def day_ok(self, teachers: tuple, typ: str, day: str) -> Optional[str]:
        check_type = typ in ("Theory", "Practical")
        for t in teachers:
            bd = self.busy.get(t)
            if bd and day in bd:
                return "busy"
            if check_type and self.tt.get((t, day, typ), 0) >= 1:
                return "same_type"
        if day in self.wd_set:
            need = len(self.wd_set)
            for t in teachers:
                if t in self.internal and t not in self.base_full:
                    days = self.tdays.get(t)
                    if days:
                        other = sum(1 for d, c in days.items() if c > 0 and d != day)
                        if other + 1 >= need:
                            return "no_free_day"
        return None

    def h6_ok(self, teachers: tuple, new_days: Tuple[str, ...]) -> bool:
        """H6 for a whole set of new days at once (the only cross-day rule)."""
        need = len(self.wd_set)
        add = {d for d in new_days if d in self.wd_set}
        for t in teachers:
            if t in self.internal and t not in self.base_full:
                cur = {d for d, c in (self.tdays.get(t) or {}).items() if c > 0}
                if len(cur | add) >= need:
                    return False
        return True

    # -- window level rules: H1, H2, H3 -----------------------------------------
    def clash(self, day: str, s: int, e: int, sem: tuple, teachers: tuple) -> Optional[str]:
        get = self.by_res.get
        for r in get((0, sem, day), ()):
            if r.s < e and s < r.e:
                return "sem"
        for t in teachers:
            for r in get((1, t, day), ()):
                if r.s < e and s < r.e:
                    return "teacher"
        return None

    def room_free(self, room: str, day: str, s: int, e: int) -> bool:
        for r in self.by_res.get((2, room, day), ()):
            if r.s < e and s < r.e:
                return False
        return True

    def pick_rooms(self, day: str, s: int, e: int, need_lab: bool, prefer: str, limit: int = 1) -> List[str]:
        out: List[str] = []
        if prefer and self.room_free(prefer, day, s, e):
            out.append(prefer)
        if len(out) >= limit:
            return out
        pool = self.lab_rooms if need_lab else self.nonlab_rooms + self.lab_rooms
        if not pool and not prefer:
            return [""]                       # no room master data → leave unassigned
        for r in pool:
            if r != prefer and self.room_free(r, day, s, e):
                out.append(r)
                if len(out) >= limit:
                    break
        return out


# ══════════════════════════════════════════════════════════════════════════════
# Independent reference checker (cross-checks the board on every proposal)
# ══════════════════════════════════════════════════════════════════════════════
def _eid(c: dict, rules: Rules) -> str:
    return "|".join(str(x) for x in (
        c.get("day"), rules.normalize_program(c.get("program")), c.get("semester"),
        c.get("start"), c.get("end"), c.get("subject_code") or c.get("subject_name"),
        c.get("teacher"), c.get("room")))


def violation_counter(schedule: List[dict], busy: Dict[str, Set[str]], rules: Rules) -> Counter:
    """Multiset of H1/H2/H3/H5/H5b/H6/H12 violations (same semantics as validator)."""
    cnt: Counter = Counter()
    by_day: Dict[Any, list] = defaultdict(list)
    theory: Counter = Counter()
    prac: Counter = Counter()
    tdays: Dict[str, Set[str]] = defaultdict(set)
    wd = set(rules.working_days)
    for c in schedule:
        try:
            s, e = _to_min(c["start"]), _to_min(c["end"])
        except Exception:
            continue
        ts = rules.teacher_set(c)
        eid = _eid(c, rules)
        by_day[c.get("day")].append((c, eid, s, e, ts, _sem_key(c)))
        typ = str(c.get("type", "")).strip()
        day = c.get("day", "")
        for t in ts:
            if day in busy.get(t, ()):
                cnt[("H12", t, day, eid)] += 1
            if typ == "Theory":
                theory[(t, day)] += 1
            elif typ == "Practical":
                prac[(t, day)] += 1
            if day in wd:
                tdays[t].add(day)
    for rows in by_day.values():
        n = len(rows)
        for i in range(n):
            ci, ei, si, ni, ti, semi = rows[i]
            for j in range(i + 1, n):
                cj, ej, sj, nj, tj, semj = rows[j]
                if not (si < nj and sj < ni):
                    continue
                pair = tuple(sorted((ei, ej)))
                if semi == semj:
                    cnt[("H1", pair)] += 1
                sh = ti & tj
                if sh:
                    cnt[("H2", tuple(sorted(sh)), pair)] += 1
                if ci.get("room") and ci.get("room") == cj.get("room"):
                    cnt[("H3", ci.get("room"), pair)] += 1
    for key, c in theory.items():
        if c > 1:
            cnt[("H5", key)] = 1
    for key, c in prac.items():
        if c > 1:
            cnt[("H5b", key)] = 1
    for t in rules.internal_teachers:
        if tdays.get(t) and tdays[t] >= wd:
            cnt[("H6", t)] = 1
    return cnt


# ══════════════════════════════════════════════════════════════════════════════
# Search objects
# ══════════════════════════════════════════════════════════════════════════════
class _Win:
    __slots__ = ("s", "dur", "room", "cost")

    def __init__(self, s: int, dur: int, room: str, cost: int):
        self.s, self.dur, self.room, self.cost = s, dur, room, cost

    @property
    def e(self) -> int:
        return self.s + self.dur


class _Cand:
    __slots__ = ("shape", "sess", "outside", "shape_cost", "cost", "key")

    def __init__(self, shape, sess, outside, shape_cost, cost, key):
        self.shape, self.sess, self.outside = shape, sess, outside
        self.shape_cost, self.cost, self.key = shape_cost, cost, key


class _DayInfo:
    __slots__ = ("block", "counts", "fails")

    def __init__(self):
        self.block: Optional[str] = None
        self.counts: Dict[int, int] = {}
        self.fails: Dict[int, Counter] = {}


class _Move:
    __slots__ = ("kind", "row", "new", "unlocks")

    def __init__(self, kind: str, row: _Row, new: Optional[dict], unlocks: str):
        self.kind, self.row, self.new, self.unlocks = kind, row, new, unlocks


class _Opt:
    __slots__ = ("a", "day", "dur", "win", "dest", "key")

    def __init__(self, a: _Row, day: str, dur: int, win: _Win, dest: Optional[tuple], key: tuple):
        self.a, self.day, self.dur, self.win, self.dest, self.key = a, day, dur, win, dest, key


class _AdvPlan:
    __slots__ = ("sess", "moves", "cands_outside", "key")

    def __init__(self, sess, moves, outside, key):
        self.sess, self.moves, self.cands_outside, self.key = sess, moves, outside, key


@dataclass
class _Spec:
    sem: tuple
    teachers: tuple
    typ: str
    need_lab: bool
    prefer_room: str
    forbidden_days: frozenset


# ══════════════════════════════════════════════════════════════════════════════
# The solver
# ══════════════════════════════════════════════════════════════════════════════
class _Solver:
    def __init__(self, nr: NormRequest, schedule: List[dict], busy: Dict[str, Set[str]],
                 rules: Rules, budget_s: float, real_check: Optional[Callable[[List[dict]], bool]] = None):
        self.nr, self.rules, self.schedule, self.busy = nr, rules, schedule, busy
        self.t0 = time.perf_counter()
        self.deadline = self.t0 + max(0.2, min(float(budget_s), 10.0))
        self.real_check = real_check
        self.board = _Board(schedule, busy, rules)
        self.wd = self.board.wd
        self.day_idx = {d: i for i, d in enumerate(self.wd)}
        self.P = list(nr.days)
        self.P_set = set(nr.days)
        self.allowed_days = [d for d in self.wd if (not self.P or nr.outside_allowed or d in self.P_set)]
        self.ref = PRACTICAL_SESSION if nr.typ == "Practical" else THEORY_REF

        self.entry_base = {
            "program": nr.program, "semester": nr.semester, "subject_code": nr.subject_code,
            "subject_name": nr.subject_name, "teacher": nr.teacher, "type": nr.typ,
        }
        self.spec = _Spec(
            sem=_sem_key(self.entry_base),
            teachers=tuple(sorted(rules.teacher_set({"teacher": nr.teacher}))),
            typ=nr.typ, need_lab=(nr.typ == "Practical"), prefer_room=nr.room,
            forbidden_days=(PRACTICAL_FORBIDDEN_DAYS if nr.typ == "Practical" else frozenset()),
        )

        prog = rules.normalize_program(nr.program)
        self.group_rows: List[_Row] = []
        self.subject_days: Set[str] = set()
        self.existing_minutes = 0
        for r in self.board.rows:
            same_grp = (rules.normalize_program(r.entry.get("program", "")) == prog
                        and str(r.entry.get("semester", "")).strip() == nr.semester)
            if not same_grp:
                continue
            if _same_subject(r.entry, nr.subject_code, nr.subject_name):
                if r.typ == nr.typ:
                    self.subject_days.add(r.day)
                    self.existing_minutes += max(0, r.e - r.s)
            elif r.e > r.s:
                self.group_rows.append(r)

        self.evaluated = 0
        self.truncated = False
        self.rejected = 0
        self.hard_days: Set[str] = set()
        self.shapes: List[Tuple[int, ...]] = []
        self.shape_durs: List[int] = []
        self.infos: Dict[str, _DayInfo] = {}
        self.base_counter = violation_counter(schedule, busy, rules)

    # ── tiny utilities ────────────────────────────────────────────────────────
    def _time_up(self) -> bool:
        return time.perf_counter() > self.deadline

    def new_entry(self, day: str, s: int, dur: int, room: str) -> dict:
        return dict(self.entry_base, day=day, start=_hhmm(s), end=_hhmm(s + dur), room=room)

    def _placeholder(self, day: str, w: _Win) -> _Row:
        return _Row(self.new_entry(day, w.s, w.dur, w.room), -1, self.rules, tag="new")

    def _win_cost(self, s: int, e: int, room: str) -> int:
        c = (s - DAY_START) // STEP
        if s not in GRID_STARTS:
            c += 90
        if _crosses_break(s, e):
            c += 400
        if self.spec.prefer_room and room != self.spec.prefer_room:
            c += 30
        return c

    # ── day / window computation ────────────────────────────────────────────
    def _day_block(self, d: str) -> Optional[str]:
        sp = self.spec
        if d in sp.forbidden_days:
            return "practical_day"
        for t in sp.teachers:
            if d in self.busy.get(t, ()):
                return "busy"
        if self.nr.skip_subject_days and d in self.subject_days:
            return "subject_exists"
        return self.board.day_ok(sp.teachers, sp.typ, d)

    def _day_windows(self, day: str, dur: int, fails: Optional[Counter] = None) -> List[_Win]:
        b, sp = self.board, self.spec
        out: List[_Win] = []
        for s in range(DAY_START, DAY_END - dur + 1, STEP):
            e = s + dur
            self.evaluated += 1
            why = b.clash(day, s, e, sp.sem, sp.teachers)
            if why:
                if fails is not None:
                    fails[why] += 1
                continue
            rooms = b.pick_rooms(day, s, e, sp.need_lab, sp.prefer_room, 1)
            if not rooms:
                if fails is not None:
                    fails["room"] += 1
                continue
            out.append(_Win(s, dur, rooms[0], self._win_cost(s, e, rooms[0])))
        out.sort(key=lambda w: (w.cost, w.s))
        return out

    def _compute_avail(self, durs, infos: Optional[Dict[str, _DayInfo]] = None) -> Dict[str, Dict[int, List[_Win]]]:
        avail: Dict[str, Dict[int, List[_Win]]] = {}
        for d in self.wd:
            blk = self._day_block(d)
            row: Dict[int, List[_Win]] = {}
            if infos is not None:
                infos[d].block = blk
            if not blk:
                for x in durs:
                    fails: Optional[Counter] = Counter() if infos is not None else None
                    ws = self._day_windows(d, x, fails)
                    row[x] = ws
                    if infos is not None:
                        infos[d].counts[x] = len(ws)
                        infos[d].fails[x] = fails  # type: ignore[assignment]
            avail[d] = row
        return avail

    # ── plan enumeration ───────────────────────────────────────────────────
    def _enumerate(self, shapes, avail, allowed_days, base_outside: int = 0) -> List[_Cand]:
        cands: List[_Cand] = []
        P = self.P_set
        pool = [d for d in self.wd if d in set(allowed_days)]
        for shape in shapes:
            n = len(shape)
            if n == 0:
                cands.append(_Cand((), [], base_outside, 0, 0, (base_outside, 0, 0, ())))
                continue
            need = set(shape)
            usable = [d for d in pool if any(avail.get(d, {}).get(x) for x in need)]
            if len(usable) < n:
                continue
            perms = set(itertools.permutations(shape)) if len(need) > 1 else {tuple(shape)}
            sc = _shape_cost(tuple(shape), self.ref)
            for combo in itertools.combinations(usable, n):
                if not self.board.h6_ok(self.spec.teachers, combo):
                    continue
                best = None
                for perm in perms:
                    tot, wins = 0, []
                    for d, x in zip(combo, perm):
                        ws = avail[d].get(x)
                        if not ws:
                            break
                        tot += ws[0].cost
                        wins.append(ws)
                    else:
                        if best is None or tot < best[0]:
                            best = (tot, perm, wins)
                if best is None:
                    continue
                outside = base_outside + (sum(1 for d in combo if d not in P) if P else 0)
                sess = [(d, ws[0], ws[1:ALT_KEEP]) for d, ws in zip(combo, best[2])]
                key = (outside, sc, best[0], tuple(self.day_idx[d] for d in combo))
                cands.append(_Cand(best[1], sess, outside, sc, best[0], key))
        cands.sort(key=lambda c: c.key)
        return cands

    @staticmethod
    def _dedupe(cands: List[_Cand]) -> List[_Cand]:
        seen, out = set(), []
        for c in cands:
            k = frozenset(d for d, _, _ in c.sess)
            if k not in seen:
                seen.add(k)
                out.append(c)
        return out

    def _harmonize(self, cand: _Cand) -> None:
        """Prefer ONE room for the whole week if it is free for every session."""
        if len(cand.sess) < 2:
            return
        b, sp = self.board, self.spec
        pool = ([sp.prefer_room] if sp.prefer_room else []) + (
            b.lab_rooms if sp.need_lab else b.nonlab_rooms + b.lab_rooms)
        seen: Set[str] = set()
        for room in pool:
            if not room or room in seen:
                continue
            seen.add(room)
            if all(b.room_free(room, d, w.s, w.e) for d, w, _ in cand.sess):
                cand.sess = [(d, _Win(w.s, w.dur, room, w.cost), alts) for d, w, alts in cand.sess]
                return

    # ── Advanced: move / cancel same program+semester classes ───────────────────
    def _best_move(self, a: _Row) -> Optional[tuple]:
        """Cheapest conflict-free new place for class `a` (a is detached)."""
        b = self.board
        dur = a.e - a.s
        if dur <= 0:
            return None
        need_lab = a.typ == "Practical"
        best = None
        for day in self.wd:
            if need_lab and day in PRACTICAL_FORBIDDEN_DAYS:
                continue
            if b.day_ok(a.teachers, a.typ, day):
                continue
            for s in range(DAY_START, DAY_END - dur + 1, STEP):
                if day == a.day and s == a.s:
                    continue
                e = s + dur
                if b.clash(day, s, e, a.sem, a.teachers):
                    continue
                rooms = b.pick_rooms(day, s, e, need_lab, a.room, 1)
                if not rooms:
                    continue
                room = rooms[0]
                cost = ((0 if day == a.day else 600) + abs(s - a.s)
                        + (0 if (not a.room or room == a.room) else 30)
                        + (0 if s in GRID_STARTS else 90)
                        + (400 if _crosses_break(s, e) else 0))
                key = (cost, self.day_idx.get(day, 99), s)
                if best is None or key < best[0]:
                    best = (key, day, s, room)
        if best is None:
            return None
        return best[0][0], best[1], best[2], best[3]

    def _blockers(self, day: str, moved: Set[int]) -> List[_Row]:
        tset = set(self.spec.teachers)
        return [r for r in self.group_rows
                if id(r) not in moved and (r.day == day or tset.intersection(r.teachers))]

    def _unlock_options(self, remaining, avail, allowed, moved: Set[int], cancels: int) -> List[_Opt]:
        b, sp = self.board, self.spec
        opts: List[_Opt] = []
        targets = [d for d in allowed if d not in self.hard_days]
        targets.sort(key=lambda d: (0 if (not self.P_set or d in self.P_set) else 1, self.day_idx[d]))
        for d in targets:
            for x in sorted(set(remaining), reverse=True):
                if avail.get(d, {}).get(x):
                    continue
                for a in self._blockers(d, moved):
                    if self._time_up():
                        self.truncated = True
                        return opts
                    b.detach(a)
                    try:
                        if b.day_ok(sp.teachers, sp.typ, d) is None:
                            for w in self._day_windows(d, x)[:2]:
                                ph = self._placeholder(d, w)
                                b.attach(ph)
                                try:
                                    dest = self._best_move(a)
                                finally:
                                    b.detach(ph)
                                if dest is None and (not self.nr.allow_cancel or cancels >= 1):
                                    continue
                                key = (1 if dest is None else 0,
                                       0 if (not self.P_set or d in self.P_set) else 1,
                                       (dest[0] if dest else 0) + w.cost, self.day_idx[d])
                                opts.append(_Opt(a, d, x, w, dest, key))
                    finally:
                        b.attach(a)
        opts.sort(key=lambda o: o.key)
        return opts

    def _apply(self, o: _Opt) -> Tuple[_Row, Optional[_Row]]:
        b = self.board
        b.detach(o.a)
        ph = self._placeholder(o.day, o.win)
        b.attach(ph)
        nrow = None
        if o.dest is not None:
            _c, nd, ns, nroom = o.dest
            dur = o.a.e - o.a.s
            nrow = _Row(dict(o.a.entry, day=nd, start=_hhmm(ns), end=_hhmm(ns + dur), room=nroom),
                        o.a.idx, self.rules, tag="moved")
            b.attach(nrow)
        return ph, nrow

    def _undo(self, o: _Opt, ph: _Row, nrow: Optional[_Row]) -> None:
        b = self.board
        if nrow is not None:
            b.detach(nrow)
        b.detach(ph)
        b.attach(o.a)

    def _dfs(self, remaining: Tuple[int, ...], depth: int, placed: list, moves: List[_Move],
             moved: Set[int], baseline_out: int, results: List[_AdvPlan]) -> None:
        if self._time_up():
            self.truncated = True
            return
        placed_days = {p[0] for p in placed}
        allowed = [d for d in self.allowed_days if d not in placed_days]
        base_out = sum(1 for p in placed if self.P_set and p[0] not in self.P_set)
        avail = self._compute_avail(set(remaining)) if remaining else {}
        cands = self._enumerate([remaining], avail, allowed, base_out)
        if moves:
            good = [c for c in cands if c.outside < baseline_out]
            if good:
                for c in good[:2]:
                    sess = [(d, w, []) for d, w, _ in placed] + list(c.sess)
                    cancels = sum(1 for m in moves if m.kind == "cancel")
                    key = (cancels, c.outside, len(moves), c.shape_cost, c.cost)
                    results.append(_AdvPlan(sess, list(moves), c.outside, key))
                return
        if depth >= ADV_MAX_DEPTH:
            return
        cancels = sum(1 for m in moves if m.kind == "cancel")
        opts = self._unlock_options(remaining, avail, allowed, moved, cancels)
        for o in opts[:ADV_BRANCH[min(depth, len(ADV_BRANCH) - 1)]]:
            if self._time_up():
                self.truncated = True
                return
            ph, nrow = self._apply(o)
            rem = list(remaining)
            rem.remove(o.dur)
            if o.dest is None:
                mv = _Move("cancel", o.a, None, o.day)
            else:
                mv = _Move("move", o.a, {"day": o.dest[1], "start": _hhmm(o.dest[2]),
                                        "end": _hhmm(o.dest[2] + (o.a.e - o.a.s)), "room": o.dest[3]}, o.day)
            placed.append((o.day, o.win, []))
            moves.append(mv)
            moved.add(id(o.a))
            try:
                self.evaluated += 1
                self._dfs(tuple(rem), depth + 1, placed, moves, moved, baseline_out, results)
            finally:
                moved.discard(id(o.a))
                moves.pop()
                placed.pop()
                self._undo(o, ph, nrow)

    def _advanced(self, baseline_out: int) -> List[_AdvPlan]:
        results: List[_AdvPlan] = []
        for shape in self.shapes[:ADV_MAX_SHAPES]:
            if self._time_up():
                self.truncated = True
                break
            self._dfs(tuple(sorted(shape, reverse=True)), 0, [], [], set(), baseline_out, results)
        results.sort(key=lambda p: p.key)
        seen, out = set(), []
        for p in results:
            sig = (frozenset(d for d, _, _ in p.sess),
                   frozenset((m.kind, m.row.idx, (m.new or {}).get("day"), (m.new or {}).get("start"))
                             for m in p.moves))
            if sig not in seen:
                seen.add(sig)
                out.append(p)
        return out

    # ── partial (cannot satisfy the full request) ─────────────────────────────
    def _partial_shapes(self) -> List[Tuple[int, ...]]:
        nr = self.nr
        out: Set[Tuple[int, ...]] = set()
        max_n = min(len(self.allowed_days), len(self.wd))
        if nr.typ == "Practical":
            durs = [PRACTICAL_SESSION]
        elif nr.session_minutes:
            durs = [nr.session_minutes]
        else:
            durs = list(range(THEORY_MIN, THEORY_MAX + 1, STEP))
        for n in range(1, max_n + 1):
            if nr.rule in ("exactly", "at_most") and n > nr.k:
                continue
            if nr.typ == "Theory" and not nr.session_minutes:
                shp = _near_even_shape(nr.W, n, THEORY_MIN, THEORY_MAX)
                if shp:
                    out.add(shp)
            for x in durs:
                if n * x < nr.W:
                    out.add((x,) * n)
        return sorted(out)

    # ── verification ───────────────────────────────────────────────────────────
    def _final_schedule(self, sess, moves: List[_Move]) -> List[dict]:
        removed, replaced = set(), {}
        for m in moves:
            if m.kind == "move":
                replaced[m.row.idx] = dict(m.row.entry, day=m.new["day"], start=m.new["start"],
                                           end=m.new["end"], room=m.new["room"])
            else:
                removed.add(m.row.idx)
        out = [replaced.get(i, e) for i, e in enumerate(self.schedule) if i not in removed]
        out.extend(self.new_entry(d, w.s, w.dur, w.room) for d, w, _ in sess)
        return out

    def _verify(self, final: List[dict]) -> bool:
        new = violation_counter(final, self.busy, self.rules) - self.base_counter
        if new:
            log.warning("weekly smart-schedule: proposal rejected by reference checker: %s", list(new)[:3])
            return False
        if self.nr.strict_verify and self.real_check is not None:
            try:
                return bool(self.real_check(final))
            except Exception:                                  # pragma: no cover
                log.exception("real validator check failed")
                return False
        return True

    # ── proposal construction ────────────────────────────────────────────────
    def _reason_text(self, d: str) -> str:
        nr, info = self.nr, self.infos.get(d)
        code = info.block if info else None
        if code is None:
            code = "no_window"
        if code == "practical_day":
            return f"practical sessions are not allowed on {d}"
        if code == "busy":
            return f"{nr.teacher} is marked unavailable on {d}"
        if code == "subject_exists":
            return f"{nr.subject_name} is already scheduled on {d} for {nr.program} {nr.semester}"
        if code == "same_type":
            return f"{nr.teacher} already has a {nr.typ.lower()} class on {d}"
        if code == "no_free_day":
            return f"using {d} would leave {nr.teacher} without a free working day"
        total: Counter = Counter()
        if info:
            for x in self.shape_durs:
                total.update(info.fails.get(x, Counter()))
        dur = _fmt_h(self.shape_durs[0]) if self.shape_durs else "a session"
        if total.get("sem", 0) >= max(total.get("teacher", 0), total.get("room", 0)) and total.get("sem", 0):
            return f"every {dur} window on {d} overlaps another {nr.program} {nr.semester} class"
        if total.get("teacher", 0) >= total.get("room", 0) and total.get("teacher", 0):
            return f"{nr.teacher} is already teaching in every {dur} window on {d}"
        return f"no free room for any {dur} window on {d}"

    def _make(self, category: str, sess: list, moves: List[_Move], satisfies: bool,
              shortfall: Optional[dict] = None) -> Optional[WeeklyProposal]:
        nr = self.nr
        sess = sorted(sess, key=lambda t: self.day_idx[t[0]])
        if not self._verify(self._final_schedule(sess, moves)):
            self.rejected += 1
            return None
        days = [d for d, _, _ in sess]
        P = self.P_set
        honored = [d for d in days if d in P]
        dropped = [d for d in self.P if d not in days] if nr.fixed_mode else []
        substitutes = [d for d in days if P and d not in P]
        total = sum(w.dur for _, w, _ in sess)

        sessions: List[SessionPlan] = []
        for d, w, alts in sess:
            notes = []
            if nr.room and w.room != nr.room:
                notes.append(f"Requested room {nr.room} is not free; using {w.room or 'unassigned'}.")
            if _crosses_break(w.s, w.e):
                notes.append("Spans the 14:00–14:30 break.")
            if w.s not in GRID_STARTS:
                notes.append("Non-standard start time.")
            sessions.append(SessionPlan(
                day=d, start=_hhmm(w.s), end=_hhmm(w.e), room=w.room, duration_minutes=w.dur,
                uses_requested_room=(w.room == nr.room) if nr.room else None,
                alternatives=[SlotOption(start=_hhmm(a.s), end=_hhmm(a.e), room=a.room) for a in alts],
                notes=notes))

        mods: List[Modification] = []
        actions: List[dict] = []
        for m in moves:
            ent = m.row.entry
            subj = ent.get("subject_name") or ent.get("subject_code") or "class"
            old = f"{ent.get('day')} {ent.get('start')}–{ent.get('end')}"
            target = {"day": ent.get("day"), "start_time": ent.get("start"), "end_time": ent.get("end"),
                      "program": ent.get("program"), "semester": ent.get("semester"),
                      "subject_code": ent.get("subject_code") or ent.get("subject")}
            affected = {"subject": subj, "day": ent.get("day", ""), "start": ent.get("start", ""),
                        "end": ent.get("end", ""), "teacher": ent.get("teacher", ""),
                        "program": ent.get("program"), "semester": ent.get("semester")}
            if m.kind == "move":
                n = m.new
                mods.append(Modification(
                    type="move", unlocks_day=m.unlocks, affected_class=affected, new_slot=n,
                    description=f"Move '{subj}' ({old}) → {n['day']} {n['start']}–{n['end']}"
                                f"{' in ' + n['room'] if n['room'] else ''} to free {m.unlocks}."))
                act = {"action": "MOVE_CLASS", "target": target, "new_day": n["day"],
                       "new_start_time": n["start"], "new_end_time": n["end"]}
                if n["room"]:
                    act["new_room"] = n["room"]
                actions.append(act)
            else:
                mods.append(Modification(
                    type="cancel", unlocks_day=m.unlocks, affected_class=affected, new_slot=None,
                    description=f"Cancel '{subj}' ({old}) — no conflict-free place exists for it — "
                                f"to free {m.unlocks}."))
                actions.append({"action": "REMOVE_CLASS", "target": target})
        for d, w, _ in sess:
            actions.append({"action": "ADD_CLASS", "spec": {
                "program": nr.program, "semester": nr.semester, "day": d,
                "start_time": _hhmm(w.s), "end_time": _hhmm(w.e),
                "subject_code": nr.subject_code, "subject_name": nr.subject_name,
                "teacher": nr.teacher, "entry_type": nr.typ, "room": w.room}})

        slots_txt = ", ".join(f"{d[:3]} {_hhmm(w.s)}–{_hhmm(w.e)}" for d, w, _ in sess)
        title = f"{_join([d for d in days])} · {_fmt_h(total)}/week"
        if category == "exact":
            desc = f"All sessions fall on your selected days; no existing class is changed. {slots_txt}."
        elif category == "substitute":
            why = "; ".join(self._reason_text(d) for d in dropped) if dropped else ""
            desc = (f"{_join(dropped)} can't be used ({why}). " if dropped else "") + \
                   f"Replaced with {_join(substitutes)}" + \
                   (f"; {_join(honored)} kept" if honored else "") + f". {slots_txt}."
        elif category == "advanced":
            moves_txt = " ".join(md.description for md in mods)
            desc = f"{moves_txt} Then schedule {slots_txt}."
        else:
            sf = shortfall or {}
            bits = []
            if sf.get("days"):
                bits.append(f"{sf['days']} day(s) fewer than requested")
            if sf.get("minutes"):
                bits.append(f"{_fmt_h(sf['minutes'])} short of the weekly hours")
            else:
                bits.append("weekly hours preserved")
            desc = f"Best feasible option: {slots_txt} ({'; '.join(bits)})."

        warnings: List[str] = []
        if any(_crosses_break(w.s, w.e) for _, w, _ in sess):
            warnings.append("At least one session spans the 14:00–14:30 break.")
        if moves:
            warnings.append("Applies changes to existing classes; execute the whole bundle atomically.")
        sig = category + "|" + ";".join(f"{d}@{w.s}-{w.e}@{w.room}" for d, w, _ in sess) + "|" + \
            ";".join(f"{m.kind}:{m.row.idx}:{(m.new or {}).get('day')}:{(m.new or {}).get('start')}" for m in moves)
        return WeeklyProposal(
            id=hashlib.md5(sig.encode()).hexdigest()[:12], category=category,
            satisfies_request=satisfies, title=title, description=desc, days=days, sessions=sessions,
            total_minutes=total, requested_minutes=nr.W, honored_days=honored, dropped_days=dropped,
            substitute_days=substitutes, shortfall=shortfall, modifications=mods,
            actions_to_apply=actions, warnings=warnings, verified=True)

    # ── main ───────────────────────────────────────────────────────────────────
    def _req_text(self) -> str:
        nr = self.nr
        rule = {"exactly": f"exactly {nr.k} day(s)", "at_least": f"at least {nr.k} day(s)",
                "at_most": f"at most {nr.k} day(s)"}[nr.rule]
        days = f" on {_join(self.P)}" if self.P else ""
        return f"{_fmt_h(nr.W)}/week, {rule}{days}"

    def _invalid(self, valid_counts: List[int]) -> WeeklyScheduleResponse:
        nr = self.nr
        if nr.typ == "Practical":
            msg = (f"A practical session is {_fmt_h(PRACTICAL_SESSION)} long, so the weekly hours must be a "
                   f"multiple of {_fmt_h(PRACTICAL_SESSION)} ({_req_hint(nr)}).")
        elif valid_counts:
            msg = (f"{_fmt_h(nr.W)}/week cannot be split into {_rule_text(nr)} "
                   f"(each session must be {_fmt_h(THEORY_MIN)}–{_fmt_h(THEORY_MAX)}). "
                   f"It can be split over {_join([str(c) for c in valid_counts])} day(s).")
        else:
            msg = f"{_fmt_h(nr.W)}/week cannot be split into valid sessions."
        return WeeklyScheduleResponse(
            found=False, status="invalid", message=msg, valid_day_counts=valid_counts,
            elapsed_ms=round((time.perf_counter() - self.t0) * 1000, 2))

    def run(self) -> WeeklyScheduleResponse:
        nr = self.nr
        shapes, valid_counts = build_shapes(nr, len(self.wd))
        self.shapes = shapes
        if not shapes:
            return self._invalid(valid_counts)

        shape_durs = sorted({x for s in shapes for x in s})
        self.shape_durs = shape_durs
        all_durs = sorted(set(shape_durs) | (
            {PRACTICAL_SESSION} if nr.typ == "Practical"
            else set(range(THEORY_MIN, THEORY_MAX + 1, STEP))))
        self.infos = {d: _DayInfo() for d in self.wd}
        avail = self._compute_avail(all_durs, self.infos)
        self.hard_days = {d for d in self.wd
                          if self.infos[d].block in ("busy", "practical_day", "subject_exists")}

        def day_has_window(d: str) -> bool:
            return any(avail[d].get(x) for x in shape_durs)

        # 1+2) exact / substitute (zero-disruption)
        cands = self._enumerate(shapes, avail, self.allowed_days)
        best_out = _INF
        primary: List[WeeklyProposal] = []
        if cands:
            best_out = cands[0].outside
            pool = self._dedupe([c for c in cands if c.outside == best_out])
            for c in pool:
                self._harmonize(c)
                p = self._make("exact" if c.outside == 0 else "substitute", c.sess, [], True)
                if p:
                    primary.append(p)
                if len(primary) >= nr.max_proposals:
                    break
            if not primary:
                best_out = _INF

        # 3) Advanced suggestions
        advanced: List[WeeklyProposal] = []
        if nr.include_advanced and best_out > 0 and not self._time_up():
            for ap in self._advanced(best_out):
                p = self._make("advanced", ap.sess, ap.moves, True)
                if p:
                    advanced.append(p)
                if len(advanced) >= ADV_MAX_PROPOSALS:
                    break

        # 4) partial fallback when nothing zero-disruption satisfies the request
        partial: List[WeeklyProposal] = []
        if not primary:
            pc = self._enumerate(self._partial_shapes(), avail, self.allowed_days)
            scored = []
            for c in pc:
                n, hours = len(c.sess), sum(c.shape)
                deficit = nr.W - hours
                rule_def = max(0, nr.k - n) if nr.rule in ("exactly", "at_least") else 0
                if deficit <= 0 and rule_def == 0:
                    continue
                scored.append(((deficit, rule_def, c.outside, c.shape_cost, c.cost), c, deficit, rule_def))
            scored.sort(key=lambda t: t[0])
            seen: Set[frozenset] = set()
            kept: List[Tuple[int, int]] = []
            for _k, c, deficit, rule_def in scored:
                dk = frozenset(d for d, _, _ in c.sess)
                if dk in seen:
                    continue
                if any(kd <= deficit and kr <= rule_def for kd, kr in kept):
                    continue                      # dominated: worse on hours AND days
                seen.add(dk)
                kept.append((deficit, rule_def))
                self._harmonize(c)
                p = self._make("partial", c.sess, [], False, {"days": rule_def, "minutes": deficit})
                if p:
                    partial.append(p)
                if len(partial) >= PARTIAL_MAX:
                    break

        proposals = primary + advanced + partial
        return self._respond(proposals, primary, advanced, partial, valid_counts, day_has_window)

    # ── response composition ──────────────────────────────────────────────────
    def _respond(self, proposals, primary, advanced, partial, valid_counts, day_has_window) -> WeeklyScheduleResponse:
        nr = self.nr
        report: List[DayReport] = []
        for d in self.wd:
            info = self.infos[d]
            avl = day_has_window(d) if info.block is None else False
            code = info.block if info.block else (None if avl else "no_window")
            report.append(DayReport(
                day=d, selected=d in self.P_set, available=avl, reason_code=code,
                reason=None if avl else self._reason_text(d),
                windows={str(x): info.counts.get(x, 0) for x in self.shape_durs}))

        notes: List[str] = []
        if self.existing_minutes:
            notes.append(f"{nr.subject_name} already has {_fmt_h(self.existing_minutes)} of {nr.typ.lower()} "
                         f"scheduled for {nr.program} {nr.semester}; this plan adds {_fmt_h(nr.W)}.")
        if nr.typ == "Practical" and PRACTICAL_FORBIDDEN_DAYS:
            notes.append(f"Practicals are not scheduled on {_join(sorted(PRACTICAL_FORBIDDEN_DAYS))}.")

        blocked_sel = [r for r in report if r.selected and not r.available]
        req = self._req_text()
        if primary and primary[0].category == "exact":
            status = "exact"
            msg = (f"Found {len(primary)} option(s) for {req}: all sessions are on your selected days "
                   f"and no existing class is changed.")
        elif primary:
            status = "substituted"
            lost = _join([r.day for r in blocked_sel]) if blocked_sel else "some selected days"
            why = "; ".join(r.reason or "" for r in blocked_sel)
            keep = primary[0].honored_days
            msg = (f"{lost} can't be scheduled ({why}). Suggested conflict-free replacement day(s): "
                   f"{_join(primary[0].substitute_days)}"
                   + (f"; {_join(keep)} stay as you selected." if keep else "."))
        elif advanced:
            status = "needs_changes"
            msg = (f"Your request ({req}) cannot be placed without changing other classes. "
                   f"Advanced suggestions below move/cancel classes of {nr.program} {nr.semester} "
                   f"to unlock your days.")
            if partial:
                p0 = partial[0]
                msg += (f" Without changing anything, the best feasible option is {len(p0.days)} day(s) "
                        f"({_join(p0.days)}), {_fmt_h(p0.total_minutes)} in total.")
        elif partial:
            status = "partial"
            p0 = partial[0]
            need = nr.k
            got = len(p0.days)
            why = "; ".join(r.reason or "" for r in report
                            if (r.selected or not self.P_set) and not r.available)
            if p0.shortfall and p0.shortfall["days"]:
                msg = (f"Only {got} of the {need} requested day(s) can be scheduled "
                       f"({_join(p0.days)}). I'm not presenting an invalid {need}-day schedule. "
                       f"Best feasible option: {got} day(s), {_fmt_h(p0.total_minutes)} in total"
                       + (f" ({_fmt_h(p0.total_minutes // got)} per session)." if got and
                          len({w.duration_minutes for w in p0.sessions}) == 1 else "."))
            else:
                msg = (f"All {got} days can be used but only {_fmt_h(p0.total_minutes)} of the requested "
                       f"{_fmt_h(nr.W)} fits.")
            if why:
                msg += f" Blocked: {why}."
            if not nr.outside_allowed and self.P:
                free_out = [r.day for r in report if not r.selected and r.available]
                if free_out:
                    notes.append(f"{_join(free_out)} are free but outside your selection — "
                                 f"allow other days to use them.")
        else:
            status = "none"
            why = "; ".join(r.reason or "" for r in report if not r.available and r.reason)
            msg = f"No valid slot found for {nr.teacher} ({req}). {why}."

        if self.truncated:
            notes.append("Advanced search stopped at the time limit; more options may exist.")
        return WeeklyScheduleResponse(
            found=status != "none", status=status, message=msg, notes=notes, proposals=proposals,
            day_report=report, valid_day_counts=valid_counts,
            elapsed_ms=round((time.perf_counter() - self.t0) * 1000, 2),
            candidates_evaluated=self.evaluated, truncated=self.truncated,
            verification_rejected=self.rejected)


def _rule_text(nr: NormRequest) -> str:
    return {"exactly": f"exactly {nr.k} day(s)", "at_least": f"at least {nr.k} day(s)",
            "at_most": f"at most {nr.k} day(s)"}[nr.rule]


def _req_hint(nr: NormRequest) -> str:
    return f"{_fmt_h(nr.W)} requested"


# ══════════════════════════════════════════════════════════════════════════════
# Public pure entry point
# ══════════════════════════════════════════════════════════════════════════════
def solve_weekly(req: WeeklyScheduleRequest, schedule: List[dict], busy_map: Dict[str, Set[str]],
                 rules: Rules, real_check: Optional[Callable[[List[dict]], bool]] = None) -> WeeklyScheduleResponse:
    """No DB, no I/O.  Raises RequestError for malformed input."""
    nr = normalize_request(req, rules)
    if not subject_known(nr, rules):
        return WeeklyScheduleResponse(
            found=False, status="invalid",
            message=(f"Subject '{nr.subject_code}' / '{nr.subject_name}' is not recognized for "
                     f"{nr.program} {nr.semester}. Please add it to the Catalog first."))
    return _Solver(nr, schedule, busy_map, rules, req.time_budget_s, real_check).run()


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI endpoint (only defined when the app is importable)
# ══════════════════════════════════════════════════════════════════════════════
try:
    from fastapi import APIRouter, Depends, HTTPException
    from sqlalchemy.orm import Session
    from app.core.database import get_db
    from app.api.auth import require_teacher_or_admin
    from app.models.user import User
    from app.models.timetable import TimetableVersion
    from app.models.busy_slot import TeacherBusySlot
    _APP_OK = True
except Exception:                                    # pragma: no cover - standalone / tests
    _APP_OK = False

_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> Optional[WeeklyScheduleResponse]:
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        if time.monotonic() - hit[0] > CACHE_TTL_S:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return hit[1]


def _cache_put(key: tuple, value: WeeklyScheduleResponse) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


if _APP_OK:
    router = APIRouter()

    @router.post("", response_model=WeeklyScheduleResponse)
    def weekly_smart_schedule(
        req: WeeklyScheduleRequest,
        db: Session = Depends(get_db),
        user: User = Depends(require_teacher_or_admin),
    ):
        """Propose conflict-free weekly plans honouring weekly hours + preferred days.
        Nothing is applied; execute ``actions_to_apply`` after user confirmation."""
        rules = Rules.from_app()
        try:
            normalize_request(req, rules)
        except RequestError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        v = None
        if req.version_id:
            try:
                from app.services import timetable_service
                v = timetable_service.get_version(db, req.version_id)
            except Exception:
                v = None
        if not v:
            v = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
        schedule: List[dict] = [e.to_dict() for e in v.entries] if v else []
        busy: Dict[str, Set[str]] = {}
        for b in db.query(TeacherBusySlot).filter(TeacherBusySlot.scope == "permanent").all():
            if b.day_of_week:
                busy.setdefault(b.teacher_short_name, set()).add(b.day_of_week)

        busy_sig = hash(frozenset((t, frozenset(d)) for t, d in busy.items()))
        key = (getattr(v, "id", 0), len(schedule), busy_sig, req.program, req.semester,
               req.subject_code, req.subject_name, req.teacher, req.entry_type, req.room,
               req.weekly_hours, tuple(req.selected_days), req.day_rule, req.day_count,
               req.session_minutes, req.outside_days, req.include_advanced, req.allow_cancel,
               req.max_proposals, req.strict_verify)
        cached = _cache_get(key)
        if cached is not None:
            return cached

        real_check = None
        if req.strict_verify:
            try:
                from app.actions.suggestions import _count_new_violations

                def real_check(final: List[dict]) -> bool:          # noqa: F811
                    return _count_new_violations(schedule, final) == 0
            except Exception:                                      # pragma: no cover
                real_check = None

        resp = solve_weekly(req, schedule, busy, rules, real_check)
        resp.version_id = v.id if v else None
        if not resp.truncated:
            _cache_put(key, resp)
        return resp
else:                                                # pragma: no cover
    router = None


# ══════════════════════════════════════════════════════════════════════════════
# Self-test:  python schedule_smart_weekly.py
# ══════════════════════════════════════════════════════════════════════════════
def _selftest() -> None:                              # pragma: no cover
    import random
    import re

    def tset(c: dict) -> Set[str]:
        return {p.strip() for p in re.split(r"[,&+/]", str(c.get("teacher", "") or "")) if p.strip()}

    def normp(p):
        s = str(p or "").strip().rstrip(".")
        return {"btech": "B.Tech", "b.tech": "B.Tech"}.get(s.lower().replace(" ", ""), s)

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    rooms = {f"R{i}": {"lab": False} for i in range(1, 5)}
    rooms.update({f"L{i}": {"lab": True} for i in range(1, 3)})
    rules = Rules(days, {"SKS", "RKP"}, rooms, {("B.Tech", "3rd"): {"ds", "ds-p", "dm", "dl", "x1", "x2", "x3"}},
                  tset, normp)

    def cls(day, st, en, code, teacher, typ="Theory", room="R1"):
        return {"day": day, "program": "B.Tech", "semester": "3rd", "start": st, "end": en,
                "subject_code": code, "subject_name": code.upper(), "teacher": teacher,
                "type": typ, "room": room}

    def req(**kw):
        base = dict(program="B.Tech", semester="3rd", subject_code="ds", subject_name="Data Structure",
                    teacher="SKS", entry_type="Theory", weekly_hours=6)
        base.update(kw)
        return WeeklyScheduleRequest(**base)

    def show(title, resp):
        print(f"\n=== {title} ===\n[{resp.status}] {resp.message}")
        for n in resp.notes:
            print("  note:", n)
        for p in resp.proposals:
            print(f"  - {p.category:<10} {p.title} | {p.description[:150]}")

    # Tuesday fully blocked for the semester; Wed/Thu/Mon/Fri/Sat are free.
    sched = [cls("Tuesday", "10:00", "17:30", "x1", "EXT1", room="R2")]
    show("fixed days Tue/Wed/Thu, Tuesday blocked → replace Tuesday only",
         solve_weekly(req(selected_days=["Tuesday", "Wednesday", "Thursday"]), sched, {}, rules))

    # Pool: any 3 of Tue..Sat but only Tue+Wed possible (Thu/Fri/Sat blocked)
    sched = [cls(d, "10:00", "17:30", f"x{i}", f"EXT{i}", room="R2")
             for i, d in enumerate(["Thursday", "Friday", "Saturday"], 1)]
    show("pool: any 3 of Tue-Sat but only 2 feasible",
         solve_weekly(req(selected_days=days[1:], day_count=3), sched, {}, rules))

    # Random stress: every proposal must be clean against the reference checker.
    rnd = random.Random(11)
    bad = total = 0
    for _ in range(60):
        sched = []
        for n in range(rnd.randint(10, 60)):
            st = rnd.choice([600, 720, 870, 990])
            en = st + rnd.choice([60, 120]) if st < 990 else 1050
            sched.append(cls(rnd.choice(days), _hhmm(st), _hhmm(en), rnd.choice(["x1", "x2", "x3", "dm", "dl"]),
                             rnd.choice(["EXT1", "EXT2", "EXT3", "RKP", "SKS"]),
                             rnd.choice(["Theory", "Practical"]), rnd.choice(list(rooms))))
        r = req(weekly_hours=rnd.choice([3, 4, 6]), selected_days=rnd.sample(days, rnd.randint(1, 4)),
                day_rule=rnd.choice(["exactly", "at_least", "at_most"]))
        resp = solve_weekly(r, sched, {"SKS": {"Friday"}} if rnd.random() < .3 else {}, rules)
        total += len(resp.proposals)
        bad += resp.verification_rejected
    print(f"\nstress: {total} proposals generated, {bad} rejected by the reference checker (expect 0)")


if __name__ == "__main__":                            # pragma: no cover
    _selftest()
