#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AUTOMATED TIMETABLE GENERATOR  (single file, standard library only, Python 3.8+)
================================================================================

Start from a blank SchedulingBoard, describe the institution, call generate().

    board = SchedulingBoard()
    board.add_semester("BT-3", "B.Tech", 3)
    board.add_teacher("I01", "Dr. Rao", internal=True,
                      preferences=[("Mon", 1, "both"), ("Wed", 2, "projector"), ("Fri", 1, "blackboard")])
    board.add_room("101", "blackboard")
    board.add_subject("CS301", "Data Structures", "BT-3", internal_teacher_id="I01",
                      class_type="theory", sessions_per_week=2.5)      # <- user defined: 2, 2.5, 3 ...
    result = board.generate()
    print(result.render_semester("BT-3"))

SESSION MODEL  (user-defined, fractional sessions)
--------------------------------------------------
 * One regular slot holds ONE FULL SESSION of 2 hours (10:00-12:00, 12:00-14:00, 14:30-16:30).
 * `sessions_per_week` is chosen per subject and may be any multiple of 0.5:
       2   -> two 2-hour classes                      (4 h / week)
       2.5 -> two 2-hour classes + one 1-hour class   (5 h / week)
       3   -> three 2-hour classes                    (6 h / week)
   0.5 of a session is a ONE-HOUR class.  `external_sessions` may be fractional too.
 * There is exactly ONE extension slot: 16:30-17:30 (1 hour).  Only 1-hour classes
   can use it (a 2-hour class does not fit), so 0.5-session remainders live there.
 * If a full 2-hour class cannot be placed in any regular slot (the week is full) it is
   delivered as two 1-hour classes on two DIFFERENT days, both in the 16:30-17:30 slot
   ("1h + 1h spanned across multiple days"), whenever the timetable permits
   (SolverConfig.allow_split_sessions).
 * Optional (off by default): SolverConfig.allow_short_in_regular_slot lets a 1-hour
   class occupy a whole regular slot as a last resort (heavily penalised).

ALGORITHM OVERVIEW
------------------
The problem is a Constraint Satisfaction + Optimisation problem.  It is solved in
four phases, each doing what it is best at:

 0. VALIDATE / COMPILE   Input is checked (references, capacity lower-bounds, teacher
                         load vs. usable days ...) and compiled to integer-indexed
                         arrays.  Impossible inputs are reported *before* searching.

 1. CONSTRUCT            Every weekly class is a "session" (full 2-hour or 1-hour).
                         Sessions are placed one by one with the Minimum-Remaining-Values
                         heuristic (most constrained first) on incrementally-maintained
                         domains.  The value (day, slot, room) is chosen by least
                         *soft-cost* increase, so teacher preferences are honoured while
                         building.  Full sessions only see regular slots; 1-hour sessions
                         see the 16:30-17:30 extension slot.

 2. REPAIR               When a session has no legal placement an ejection-chain step
                         (min-conflicts with tabu-like counters) evicts the cheapest set
                         of blockers, places the session and re-queues the blockers.
                         Rooms are also repaired by augmenting paths (a classic bipartite
                         matching idea): an occupied room is freed by moving its occupant
                         to another suitable room.  Random restarts cover the rest.
                         If full sessions are still left over, the problem is re-compiled
                         with those sessions SPLIT into two 1-hour pieces (see above) and
                         the construction is repeated; the best outcome is kept.

 3. OPTIMISE             Simulated annealing over relocate / swap moves.  Every move is
                         applied through a journaled state and fully rolled back if it
                         breaks a hard constraint or is rejected, so the schedule is
                         ALWAYS conflict-free.  Costs are decomposed per session / teacher
                         / semester and cached, so one move costs O(few entities).

HARD CONSTRAINTS (never violated, independently re-checked by verify())
    * no teacher / room / semester in two places at the same time
    * a teacher: at most 1 theory and at most 1 practical per day
    * every INTERNAL teacher keeps at least one completely free day per week
    * teacher availability (unavailable days / slots) respected
    * room facility (blackboard / projector / both) satisfies the class requirement
      (subject requirement, upgraded by the teacher's preference when that slot is used)
    * room kind (lab vs. classroom, if labs are defined) and capacity (if given)
    * a subject appears at most once per day for a semester
      (so the two halves of a split session always fall on different days)
    * a 2-hour class only sits in a regular slot; a 1-hour class sits in the extension slot

SOFT OBJECTIVES (weights in Weights)
    + honour teacher slot preferences (rank 1 > 2 > 3)
    - a 1-hour class wasting a regular slot, gaps inside a semester's day,
      unbalanced days, two practicals the same day, same subject on consecutive days,
      wasting a room with unneeded facilities, scattered visiting days for external teachers
"""
from __future__ import annotations

import argparse
import csv
import enum
import io
import json
import math
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# =====================================================================================
# 1. DOMAIN MODEL (what the user types into the blank board)
# =====================================================================================


class Facility(enum.IntFlag):
    """Room facilities / class requirements as a 2-bit mask."""
    NONE = 0
    BLACKBOARD = 1
    PROJECTOR = 2
    BOTH = 3

    @classmethod
    def parse(cls, value) -> "Facility":
        if isinstance(value, Facility):
            return value
        if value is None:
            return cls.NONE
        if isinstance(value, int):
            return cls(value & 3)
        key = (str(value).strip().lower().replace("&", "_and_").replace("+", "_and_")
               .replace("-", "_").replace(" ", "_"))
        while "__" in key:
            key = key.replace("__", "_")
        key = key.strip("_")
        table = {
            "": cls.NONE, "none": cls.NONE, "any": cls.NONE,
            "blackboard": cls.BLACKBOARD, "board": cls.BLACKBOARD, "bb": cls.BLACKBOARD,
            "projector": cls.PROJECTOR, "proj": cls.PROJECTOR,
            "both": cls.BOTH, "blackboard_and_projector": cls.BOTH,
            "projector_and_blackboard": cls.BOTH,
            "blackboardandprojector": cls.BOTH,
            "projectorandblackboard": cls.BOTH,
        }
        if key not in table:
            raise ValueError("Unknown facility %r (use blackboard / projector / both)" % (value,))
        return table[key]

    @property
    def text(self) -> str:
        return {0: "none", 1: "blackboard", 2: "projector", 3: "blackboard+projector"}[int(self)]


class ClassType(enum.Enum):
    THEORY = "Theory"
    PRACTICAL = "Practical"

    @classmethod
    def parse(cls, value) -> "ClassType":
        if isinstance(value, ClassType):
            return value
        key = str(value).strip().lower()
        if key in ("t", "theory", "lecture"):
            return cls.THEORY
        if key in ("p", "practical", "lab"):
            return cls.PRACTICAL
        raise ValueError("Class type must be Theory or Practical, got %r" % (value,))


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _pretty(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


@dataclass(frozen=True)
class TimeSlot:
    id: int
    start: str            # "HH:MM" 24h
    end: str
    tier: int = 0         # 0 = regular slot (full 2-hour session), 1 = extension slot (1-hour class)
    label: str = ""

    @property
    def start_min(self) -> int:
        return _minutes(self.start)

    @property
    def duration(self) -> int:
        """Length of the slot in minutes."""
        return _minutes(self.end) - _minutes(self.start)

    @property
    def title(self) -> str:
        return self.label or "Slot %d" % self.id

    @property
    def pretty(self) -> str:
        return "%s - %s" % (_pretty(self.start), _pretty(self.end))


DEFAULT_SLOTS = (
    TimeSlot(1, "10:00", "12:00"),
    TimeSlot(2, "12:00", "14:00"),
    TimeSlot(3, "14:30", "16:30"),
)
# The ONLY extension slot: one hour, 4:30 PM - 5:30 PM.  It hosts 1-hour classes
# (the 0.5-session remainder of e.g. a 2.5-session subject, or one half of a split 2-hour class).
DEFAULT_EXTENSION_SLOTS = (
    TimeSlot(4, "16:30", "17:30", tier=1, label="Ext 1h"),
)


def _min_regular_cells(full: int, short: int, ext_cells: int,
                       allow_split: bool, short_in_regular: bool) -> Optional[int]:
    """Lower bound on the number of REGULAR (2-hour) cells needed to hold `full` 2-hour classes
    and `short` 1-hour classes when only `ext_cells` extension cells exist.

    1-hour classes go to the extension cells first; leftovers need a regular cell (only if
    `short_in_regular`).  A full class may be split (allow_split) into two 1-hour pieces,
    which frees one regular cell but needs two extension cells.  Returns None if impossible."""
    best: Optional[int] = None
    for k in range(full + 1 if allow_split else 1):
        pieces = short + 2 * k
        in_ext = min(pieces, ext_cells)
        overflow = pieces - in_ext
        if overflow and not short_in_regular:
            continue
        need = (full - k) + overflow
        if best is None or need < best:
            best = need
    return best


@dataclass
class SlotPreference:
    day: str
    slot: int
    facility: Facility = Facility.NONE


@dataclass
class Teacher:
    id: str
    name: str
    internal: bool = True
    preferences: List[SlotPreference] = field(default_factory=list)        # up to 3, rank = order
    unavailable: Set[Tuple[str, int]] = field(default_factory=set)         # {(day, slot_id)}
    unavailable_days: Set[str] = field(default_factory=set)


@dataclass
class Semester:
    id: str
    programme: str                 # "B.Tech", "M.Tech", "M.Sc." ...
    number: int
    name: str = ""
    strength: Optional[int] = None  # students; only used if rooms declare a capacity

    @property
    def title(self) -> str:
        return self.name or "%s Sem %d" % (self.programme, self.number)


@dataclass
class Subject:
    code: str
    name: str
    semester_id: str
    class_type: ClassType = ClassType.THEORY
    internal_teacher_id: Optional[str] = None
    external_teacher_id: Optional[str] = None
    sessions_per_week: Optional[float] = None  # user defined, multiple of 0.5 (2, 2.5, 3 ...); default from config
    external_sessions: Optional[float] = None  # how many of them the external teacher takes (multiple of 0.5)
    facility: Facility = Facility.NONE         # hard requirement of the class itself


@dataclass
class Room:
    number: str
    facility: Facility = Facility.BLACKBOARD
    kind: str = "classroom"            # "classroom" | "lab"
    capacity: Optional[int] = None


@dataclass
class Weights:
    """Soft-objective weights (all are costs; a satisfied preference is a negative cost)."""
    pref_reward: Tuple[float, float, float] = (40.0, 30.0, 20.0)
    extension_slot: float = 0.0            # the 1-hour extension slot is the natural home of 1-hour classes
    short_in_regular_slot: float = 60.0    # only used when allow_short_in_regular_slot is on (> max reward)
    room_waste: float = 1.0
    group_gap: float = 6.0
    group_day_load: float = 1.0
    group_multi_practical: float = 8.0
    group_consecutive_subject: float = 2.0
    teacher_double_day: float = 1.0
    teacher_gap: float = 3.0
    external_visit_day: float = 4.0


@dataclass
class SolverConfig:
    working_days: Tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri")
    allow_extension_slots: bool = True
    default_theory_sessions: float = 3
    default_practical_sessions: float = 1
    full_session_minutes: int = 120            # one regular slot = one full session
    short_session_minutes: int = 60            # half a session = the 16:30-17:30 extension slot
    allow_split_sessions: bool = True          # a 2-hour class that cannot fit may become 1h + 1h on two days
    max_split_rounds: int = 4                  # how many times the solver re-compiles with more splits
    allow_short_in_regular_slot: bool = False  # last resort: a 1-hour class occupying a whole regular slot
    time_limit_s: float = 6.0
    seed: Optional[int] = 7
    max_restarts: int = 12
    sa_start_temp: float = 6.0
    sa_end_temp: float = 0.05
    sa_max_iterations: int = 80_000
    weights: Weights = field(default_factory=Weights)


@dataclass
class Issue:
    level: str       # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return "[%s] %s" % (self.level.upper(), self.message)


class InfeasibleInputError(ValueError):
    """Raised when validation finds errors that make a conflict-free timetable impossible."""


# =====================================================================================
# 2. THE BLANK BOARD (public API)
# =====================================================================================


class SchedulingBoard:
    def __init__(self, config: Optional[SolverConfig] = None,
                 slots: Optional[Sequence[TimeSlot]] = None,
                 extension_slots: Optional[Sequence[TimeSlot]] = None):
        self.config = config or SolverConfig()
        self.slots: List[TimeSlot] = list(slots) if slots else list(DEFAULT_SLOTS)
        self.extension_slots: List[TimeSlot] = (
            list(extension_slots) if extension_slots is not None else list(DEFAULT_EXTENSION_SLOTS))
        self.semesters: Dict[str, Semester] = {}
        self.teachers: Dict[str, Teacher] = {}
        self.subjects: Dict[str, Subject] = {}
        self.rooms: Dict[str, Room] = {}

    # ---- data entry -------------------------------------------------------------
    def add_semester(self, id: str, programme: str, number: int, name: str = "",
                     strength: Optional[int] = None) -> Semester:
        if id in self.semesters:
            raise ValueError("Semester %r already exists" % id)
        self.semesters[id] = Semester(id, programme, number, name, strength)
        return self.semesters[id]

    def add_teacher(self, id: str, name: str, internal: bool = True, preferences=(),
                    unavailable=(), unavailable_days=()) -> Teacher:
        if id in self.teachers:
            raise ValueError("Teacher %r already exists" % id)
        prefs = []
        for p in preferences:
            if isinstance(p, SlotPreference):
                prefs.append(SlotPreference(p.day, int(p.slot), Facility.parse(p.facility)))
            else:
                day, slot, fac = p
                prefs.append(SlotPreference(day, int(slot), Facility.parse(fac)))
        self.teachers[id] = Teacher(id, name, internal, prefs,
                                    {(d, int(s)) for d, s in unavailable}, set(unavailable_days))
        return self.teachers[id]

    def add_room(self, number: str, facility="blackboard", kind: str = "classroom",
                 capacity: Optional[int] = None) -> Room:
        if number in self.rooms:
            raise ValueError("Room %r already exists" % number)
        kind = kind.strip().lower()
        if kind not in ("classroom", "lab"):
            raise ValueError("Room kind must be 'classroom' or 'lab'")
        self.rooms[number] = Room(number, Facility.parse(facility), kind, capacity)
        return self.rooms[number]

    def add_subject(self, code: str, name: str, semester_id: str, internal_teacher_id: Optional[str] = None,
                    external_teacher_id: Optional[str] = None, class_type="theory",
                    sessions_per_week: Optional[float] = None, external_sessions: Optional[float] = None,
                    facility="none") -> Subject:
        """sessions_per_week / external_sessions are user-defined multiples of 0.5
        (e.g. 2, 2.5, 3).  One session = one 2-hour slot, 0.5 session = a 1-hour class."""
        if code in self.subjects:
            raise ValueError("Subject %r already exists" % code)
        self.subjects[code] = Subject(code, name, semester_id, ClassType.parse(class_type),
                                      internal_teacher_id, external_teacher_id, sessions_per_week,
                                      external_sessions, Facility.parse(facility))
        return self.subjects[code]

    # ---- helpers ------------------------------------------------------------------
    def day_index(self, day) -> int:
        days = self.config.working_days
        if isinstance(day, int) and 0 <= day < len(days):
            return day
        key = str(day).strip().lower()[:3]
        for i, d in enumerate(days):
            if d.lower()[:3] == key:
                return i
        raise ValueError("Unknown day %r (working days: %s)" % (day, ", ".join(days)))

    def all_slots(self) -> List[TimeSlot]:
        slots = list(self.slots)
        if self.config.allow_extension_slots:
            slots += self.extension_slots
        return sorted(slots, key=lambda s: (s.start_min, s.id))

    def units_of(self, subj: Subject) -> Tuple[int, int]:
        """(internal, external) weekly workload in HALF-SESSION units.
        1 unit = 0.5 session = a 1-hour class; 2 units = one full 2-hour class."""
        cfg = self.config
        n = subj.sessions_per_week
        if n is None:
            n = (cfg.default_practical_sessions if subj.class_type is ClassType.PRACTICAL
                 else cfg.default_theory_sessions)
        units = int(round(n * 2))
        has_i, has_e = bool(subj.internal_teacher_id), bool(subj.external_teacher_id)
        if has_i and has_e:
            if subj.external_sessions is not None:
                ext = int(round(subj.external_sessions * 2))
            else:
                ext = 2 * int(n // 3)
            ext = max(0, min(ext, units))
            return units - ext, ext
        if has_i:
            return units, 0
        if has_e:
            return 0, units
        return 0, 0

    def sessions_of(self, subj: Subject) -> Tuple[float, float]:
        """(internal sessions, external sessions) per week for a subject (may be x.5)."""
        i, e = self.units_of(subj)
        return i / 2.0, e / 2.0

    # ---- validation -------------------------------------------------------------
    def validate(self) -> List[Issue]:
        return self._validate_and_compile()[0]

    def _validate_and_compile(self):
        from collections import Counter
        issues: List[Issue] = []
        err = lambda m: issues.append(Issue("error", m))     # noqa: E731
        warn = lambda m: issues.append(Issue("warning", m))  # noqa: E731
        cfg = self.config

        def t_label(tid: str) -> str:
            t_obj = self.teachers.get(tid)
            if not t_obj:
                return f"Teacher '{tid}'"
            if t_obj.name and t_obj.name.strip() and t_obj.name.strip() != t_obj.id.strip():
                return f"{t_obj.name.strip()} ({t_obj.id.strip()})"
            return f"Teacher {t_obj.id.strip()}"

        if len(cfg.working_days) < 2:
            err(f"Schedule Configuration Error: At least 2 working days are required (configured: {len(cfg.working_days)} day(s) [{', '.join(cfg.working_days)}]). Internal faculty require at least 1 guaranteed free day.")
        if not self.semesters:
            err("No academic semesters configured. Please add at least one semester (e.g., B.Tech Sem 3, M.Tech Sem 1) or import a curriculum file.")
        if not self.rooms:
            err("No classrooms or laboratories configured. Please add rooms in the Rooms tab to host classes.")
        if not self.subjects:
            err("No course subjects configured. Please add subjects with teacher allocations or import a subject allocation file.")
        slot_ids = [s.id for s in self.all_slots()]
        if len(set(slot_ids)) != len(slot_ids):
            err(f"Time-slot ID collision: Slot IDs must be distinct, but found duplicates among {slot_ids}.")
        for s in self.all_slots():
            if _minutes(s.end) <= _minutes(s.start):
                err(f"Invalid Time Slot {s.id} ({s.label or ''}): End time ({s.end}) must be strictly after start time ({s.start}).")
            elif s.tier > 0 and s.duration != cfg.short_session_minutes:
                warn(f"Extension slot {s.id} ({s.start}-{s.end}) lasts {s.duration} min, but 1-hour classes are "
                     f"{cfg.short_session_minutes} min long. The extension slot is meant to be exactly one hour (e.g. 16:30-17:30).")
            elif s.tier == 0 and s.duration < cfg.full_session_minutes:
                warn(f"Regular slot {s.id} ({s.start}-{s.end}) lasts {s.duration} min, shorter than a full session "
                     f"({cfg.full_session_minutes} min).")

        for t in self.teachers.values():
            tl = t_label(t.id)
            if len(t.preferences) > 3:
                err(f"Teacher {tl} lists {len(t.preferences)} preferred slots, but the maximum allowed is 3.")
            seen = set()
            for p_idx, p in enumerate(t.preferences):
                try:
                    d = self.day_index(p.day)
                except ValueError as e:
                    err(f"Teacher {tl} preference #{p_idx + 1} has invalid day '{p.day}': {e}. Valid working days: {', '.join(cfg.working_days)}.")
                    continue
                if p.slot not in slot_ids:
                    err(f"Teacher {tl} preference #{p_idx + 1} requests unknown slot ID {p.slot}. Valid slot IDs: {', '.join(map(str, slot_ids))}.")
                if (d, p.slot) in seen:
                    warn(f"Teacher {tl} lists duplicate preferred slot on {p.day} Slot {p.slot}.")
                seen.add((d, p.slot))
            for day in list(t.unavailable_days) + [d for d, _ in t.unavailable]:
                try:
                    self.day_index(day)
                except ValueError as e:
                    err(f"Teacher {tl} availability specifies invalid day '{day}': {e}.")

        for sub in self.subjects.values():
            if sub.semester_id not in self.semesters:
                err(f"Subject {sub.code} ('{sub.name}') is assigned to unknown semester '{sub.semester_id}'. Configured semesters: {', '.join(self.semesters.keys())}.")
            for role, tid in (("internal", sub.internal_teacher_id), ("external", sub.external_teacher_id)):
                if tid is None:
                    continue
                if tid not in self.teachers:
                    err(f"Subject {sub.code} ('{sub.name}') references unknown {role} teacher ID '{tid}'. Please register this teacher or update the course allocation.")
                elif self.teachers[tid].internal != (role == "internal"):
                    actual_type = "internal faculty" if self.teachers[tid].internal else "external visiting faculty"
                    warn(
                        f"Subject {sub.code} ('{sub.name}'): {t_label(tid)} is registered as {actual_type}, but is assigned here as an {role} teacher. "
                        f"(To fix: Either update {t_label(tid)}'s profile to {role} or reassign this course)."
                    )
            if not sub.internal_teacher_id and not sub.external_teacher_id:
                err(f"Subject {sub.code} ('{sub.name}') has no teacher assigned. Please assign an internal or external faculty member.")
            for fld, val in (("sessions_per_week", sub.sessions_per_week), ("external_sessions", sub.external_sessions)):
                if val is not None and abs(val * 2 - round(val * 2)) > 1e-9:
                    err(f"Subject {sub.code} ('{sub.name}'): {fld} ({val}) must be a multiple of 0.5 (e.g. 2, 2.5, 3). "
                        f"One session = {cfg.full_session_minutes} min, half a session = {cfg.short_session_minutes} min.")
            if sub.sessions_per_week is not None and sub.sessions_per_week < 0.5:
                err(f"Subject {sub.code} ('{sub.name}'): sessions_per_week must be at least 0.5 (configured: {sub.sessions_per_week}).")
            if (sub.external_sessions is not None and sub.sessions_per_week is not None
                    and not 0 <= sub.external_sessions <= sub.sessions_per_week):
                err(f"Subject {sub.code} ('{sub.name}'): external_sessions ({sub.external_sessions}) cannot exceed total weekly sessions ({sub.sessions_per_week}).")
            i, e = self.sessions_of(sub)
            if sub.external_teacher_id and sub.internal_teacher_id and e == 0:
                warn(f"Subject {sub.code} ('{sub.name}'): External teacher {t_label(sub.external_teacher_id)} is assigned 0 sessions. Specify 'external_sessions' to allocate sessions.")

        if any(i.level == "error" for i in issues):
            return issues, None

        P = _Problem(self)
        D, NS = P.D, P.NS
        # --- capacity lower bounds ---
        for ti, t in enumerate(P.teachers):
            tl = t_label(t.id)
            blocked_days = sum(1 for d in range(D) if all(P.t_blocked[ti][d * NS + s] for s in range(NS)))
            usable = D - blocked_days
            cap = min(usable, P.t_max_days[ti])
            th_sessions = [P.sessions[x] for x in P.t_sessions[ti] if not P.sessions[x].practical]
            pr_sessions = [P.sessions[x] for x in P.t_sessions[ti] if P.sessions[x].practical]
            th = len(th_sessions)
            pr = len(pr_sessions)

            th_sub_counts = Counter(s.code for s in th_sessions)
            th_summary_parts = []
            for code, cnt in th_sub_counts.items():
                s_obj = self.subjects.get(code)
                th_summary_parts.append(f"{cnt}x {code}" + (f" ({s_obj.name})" if s_obj else ""))
            th_summary = ", ".join(th_summary_parts) if th_summary_parts else "none"

            pr_sub_counts = Counter(s.code for s in pr_sessions)
            pr_summary_parts = []
            for code, cnt in pr_sub_counts.items():
                s_obj = self.subjects.get(code)
                pr_summary_parts.append(f"{cnt}x {code}" + (f" ({s_obj.name})" if s_obj else ""))
            pr_summary = ", ".join(pr_summary_parts) if pr_summary_parts else "none"

            if th > cap:
                free_day_reason = "1 guaranteed weekly research/free day kept" if t.internal else "unavailable days"
                err(
                    f"Teacher Workload Clash: {tl} has {th} theory classes scheduled per week [{th_summary}], "
                    f"but has only {cap} usable teaching day(s) available in the week (out of {D} working days: {', '.join(cfg.working_days)}; {free_day_reason}). "
                    f"Institutional rule: Maximum 1 theory class per day per faculty member (a 1-hour class counts as a class). "
                    f"(To fix: Reassign at least {th - cap} theory class(es) to another teacher or co-teacher, "
                    f"or add another working day such as Saturday)."
                )
            if pr > cap:
                err(
                    f"Teacher Practical Workload Clash: {tl} has {pr} practical lab classes per week [{pr_summary}], "
                    f"but has only {cap} usable teaching day(s). "
                    f"(To fix: Reassign at least {pr - cap} practical lab session(s) to another instructor)."
                )
            free_cells = sum(1 for c in range(P.TS) if not P.t_blocked[ti][c])
            if len(P.t_sessions[ti]) > free_cells:
                err(
                    f"Teacher Over-allocation: {tl} is assigned {len(P.t_sessions[ti])} total classes per week, "
                    f"exceeding their {free_cells} available unblocked time slots. "
                    f"(To fix: Reduce weekly sessions or remove unavailable time slots)."
                )

        base_cells = D * len(P.base)
        ext_cells = D * len(P.ext_idx)
        ext_desc = ", ".join("%s-%s" % (P.slots[i].start, P.slots[i].end) for i in P.ext_idx) or "none"
        for gi, g in enumerate(P.groups):
            n = len(P.g_sessions[gi])
            n_short = sum(1 for x in P.g_sessions[gi] if P.sessions[x].short)
            n_full = n - n_short
            g_title = g.title
            g_id = g.id
            sem_name = g.name or g_title

            # Detail the subjects for this semester
            g_sub_counts = Counter(P.sessions[x].code for x in P.g_sessions[gi])
            g_sub_details = []
            for code, cnt in g_sub_counts.items():
                s_obj = self.subjects.get(code)
                s_name = f" ({s_obj.name})" if s_obj else ""
                g_sub_details.append(f"{code}{s_name}: {cnt} classes")
            g_summary = ", ".join(g_sub_details[:5]) + (f" ... (+{len(g_sub_details)-5} more subjects)" if len(g_sub_details) > 5 else "")

            need = _min_regular_cells(n_full, n_short, ext_cells, P.allow_split, P.short_in_base)
            if need is None or need > base_cells:
                err(
                    f"Semester Capacity Clash: {sem_name} (ID: {g_id}) has {n_full} full ({cfg.full_session_minutes}-min) class(es) and "
                    f"{n_short} 1-hour class(es) per week [{g_summary}], but the week only offers {base_cells} regular slots "
                    f"({D} working days x {len(P.base)} slots/day) for full classes and {ext_cells} extension-hour slot(s) [{ext_desc}] for 1-hour classes. "
                    f"Constraint: A single student cohort cannot attend two classes at the same time. "
                    f"(To fix: If these courses include electives or multiple student sections, register them under separate semester cohort IDs like '{g_id}-A' and '{g_id}-B'; "
                    f"add Saturday as a working day; reduce weekly session counts (e.g. 2.5 instead of 3); "
                    f"or, for 1-hour classes, enable allow_short_in_regular_slot / allow_split_sessions in the solver configuration)."
                )
            elif n_full > base_cells or n_short > ext_cells:
                warn(
                    f"Semester High Utilization: {sem_name} (ID: {g_id}) has {n_full} full class(es) and {n_short} 1-hour class(es) per week, "
                    f"more than the {base_cells} regular / {ext_cells} extension-hour slots available. "
                    f"Some full classes will be split into two 1-hour classes on different days ({ext_desc}) "
                    f"or 1-hour classes will use regular slots."
                )

        for sub in self.subjects.values():
            iu, eu = self.units_of(sub)
            pieces = (iu + 1) // 2 + (eu + 1) // 2
            if pieces > D:
                err(
                    f"Course Session Clash: Subject {sub.code} ('{sub.name}') requires {(iu + eu) / 2:g} sessions/week "
                    f"({pieces} separate classes), but there are only {D} working days ({', '.join(cfg.working_days)}). "
                    f"Rule: A student cohort can have at most 1 class of the same course per day. "
                    f"(To fix: Reduce sessions per week or add working days)."
                )

        for s in P.sessions:
            if not P.room_list(s.room_key, s.req):
                sub_obj = self.subjects.get(s.code)
                s_title = f"{s.code} ('{sub_obj.name}')" if sub_obj else s.code
                fac_req = Facility(s.req).text
                kind = s.room_key[0]
                cap = s.room_key[1]
                avail_rooms = ", ".join(f"{r.number} ({r.kind}, {r.facility.text}, cap={r.capacity or 'any'})" for r in self.rooms.values())
                err(
                    f"No Suitable Room for Course {s_title}: Requires room kind='{kind}', facility='{fac_req}', "
                    f"and student capacity >= {cap}. None of the {len(self.rooms)} configured room(s) [{avail_rooms}] "
                    f"satisfies these requirements. (To fix: Add or edit a room to provide '{kind}' with facility '{fac_req}' and capacity >= {cap})."
                )

        R = len(P.rooms)
        tot_short = sum(1 for s in P.sessions if s.short)
        tot_full = len(P.sessions) - tot_short
        r_base, r_ext = R * base_cells, R * ext_cells
        need = _min_regular_cells(tot_full, tot_short, r_ext, P.allow_split, P.short_in_base)
        if need is None or need > r_base:
            min_rooms = R
            while min_rooms < R + len(P.sessions) + 1:
                min_rooms += 1
                nd = _min_regular_cells(tot_full, tot_short, min_rooms * ext_cells, P.allow_split, P.short_in_base)
                if nd is not None and nd <= min_rooms * base_cells:
                    break
            room_list_str = ", ".join(r.number for r in P.rooms)
            err(
                f"Institution Room Capacity Exceeded: Total classes to schedule ({tot_full} full + {tot_short} 1-hour classes across all semesters) "
                f"exceed the available room-slots ({r_base} regular + {r_ext} extension-hour slots = {R} room(s) [{room_list_str}] x {D} working days). "
                f"(To fix: Add at least {min_rooms - R} more classroom(s)/lab(s) in the Rooms tab [need about {min_rooms} rooms total], "
                f"or add Saturday as a working day, or reduce weekly sessions)."
            )
        elif tot_full > r_base or tot_short > r_ext:
            warn(
                f"High Room Utilization: Total classes ({tot_full} full + {tot_short} 1-hour) exceed the {r_base} regular / {r_ext} extension-hour "
                f"room-slots ({R} room(s) x {D} days). Some full classes may be split into two 1-hour classes in the {ext_desc} slot."
            )
        return issues, P

    # ---- generation ---------------------------------------------------------------
    def generate(self, strict: bool = True) -> "TimetableResult":
        issues, P = self._validate_and_compile()
        errors = [i for i in issues if i.level == "error"]
        if errors and strict:
            raise InfeasibleInputError("\n".join(str(i) for i in errors))
        if P is None:
            raise InfeasibleInputError("\n".join(str(i) for i in errors))
        solver = _Solver(P)
        return solver.run(self, issues)

    def to_dict(self) -> dict:
        return {
            "config": {
                "working_days": list(self.config.working_days),
                "allow_extension_slots": self.config.allow_extension_slots,
                "default_theory_sessions": self.config.default_theory_sessions,
                "default_practical_sessions": self.config.default_practical_sessions,
                "full_session_minutes": self.config.full_session_minutes,
                "short_session_minutes": self.config.short_session_minutes,
                "allow_split_sessions": self.config.allow_split_sessions,
                "max_split_rounds": self.config.max_split_rounds,
                "allow_short_in_regular_slot": self.config.allow_short_in_regular_slot,
                "time_limit_s": self.config.time_limit_s,
                "seed": self.config.seed,
                "max_restarts": self.config.max_restarts,
                "sa_start_temp": self.config.sa_start_temp,
                "sa_end_temp": self.config.sa_end_temp,
                "sa_max_iterations": self.config.sa_max_iterations,
            },
            "slots": [
                {"id": s.id, "start": s.start, "end": s.end, "tier": s.tier, "label": s.label}
                for s in self.slots
            ],
            "extension_slots": [
                {"id": s.id, "start": s.start, "end": s.end, "tier": s.tier, "label": s.label}
                for s in self.extension_slots
            ],
            "semesters": [
                {"id": s.id, "programme": s.programme, "number": s.number, "name": s.name, "strength": s.strength}
                for s in self.semesters.values()
            ],
            "rooms": [
                {"number": r.number, "facility": r.facility.text, "kind": r.kind, "capacity": r.capacity}
                for r in self.rooms.values()
            ],
            "teachers": [
                {
                    "id": t.id,
                    "name": t.name,
                    "internal": t.internal,
                    "preferences": [
                        {"day": p.day, "slot": p.slot, "facility": p.facility.text}
                        for p in t.preferences
                    ],
                    "unavailable": list(t.unavailable),
                    "unavailable_days": list(t.unavailable_days),
                }
                for t in self.teachers.values()
            ],
            "subjects": [
                {
                    "code": sub.code,
                    "name": sub.name,
                    "semester_id": sub.semester_id,
                    "class_type": sub.class_type.value.lower(),
                    "internal_teacher_id": sub.internal_teacher_id,
                    "external_teacher_id": sub.external_teacher_id,
                    "sessions_per_week": sub.sessions_per_week,
                    "external_sessions": sub.external_sessions,
                    "facility": sub.facility.text,
                }
                for sub in self.subjects.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SchedulingBoard":
        cfg_data = data.get("config", {})
        cfg = SolverConfig(
            working_days=tuple(cfg_data.get("working_days", ("Mon", "Tue", "Wed", "Thu", "Fri"))),
            allow_extension_slots=cfg_data.get("allow_extension_slots", True),
            default_theory_sessions=float(cfg_data.get("default_theory_sessions", 3)),
            default_practical_sessions=float(cfg_data.get("default_practical_sessions", 1)),
            full_session_minutes=int(cfg_data.get("full_session_minutes", 120)),
            short_session_minutes=int(cfg_data.get("short_session_minutes", 60)),
            allow_split_sessions=bool(cfg_data.get("allow_split_sessions", True)),
            max_split_rounds=int(cfg_data.get("max_split_rounds", 4)),
            allow_short_in_regular_slot=bool(cfg_data.get("allow_short_in_regular_slot", False)),
            time_limit_s=float(cfg_data.get("time_limit_s", 6.0)),
            seed=int(cfg_data.get("seed", 7)) if cfg_data.get("seed") is not None else None,
            max_restarts=int(cfg_data.get("max_restarts", 12)),
            sa_start_temp=float(cfg_data.get("sa_start_temp", 6.0)),
            sa_end_temp=float(cfg_data.get("sa_end_temp", 0.05)),
            sa_max_iterations=int(cfg_data.get("sa_max_iterations", 80000)),
        )
        slots = [
            TimeSlot(id=int(s["id"]), start=str(s["start"]), end=str(s["end"]), tier=int(s.get("tier", 0)), label=str(s.get("label", "")))
            for s in data.get("slots", [])
        ] if data.get("slots") else None

        ext_slots = [
            TimeSlot(id=int(s["id"]), start=str(s["start"]), end=str(s["end"]), tier=int(s.get("tier", 1)), label=str(s.get("label", "")))
            for s in data.get("extension_slots", [])
        ] if "extension_slots" in data and data.get("extension_slots") is not None else None

        board = cls(config=cfg, slots=slots, extension_slots=ext_slots)

        for sem in data.get("semesters", []):
            board.add_semester(
                id=str(sem["id"]),
                programme=str(sem["programme"]),
                number=int(sem["number"]),
                name=str(sem.get("name", "")),
                strength=int(sem["strength"]) if sem.get("strength") is not None else None,
            )

        for r in data.get("rooms", []):
            board.add_room(
                number=str(r["number"]),
                facility=r.get("facility", "blackboard"),
                kind=str(r.get("kind", "classroom")),
                capacity=int(r["capacity"]) if r.get("capacity") is not None else None,
            )

        for t in data.get("teachers", []):
            prefs = [
                (p["day"], int(p["slot"]), p.get("facility", "none"))
                for p in t.get("preferences", [])
            ]
            unavail = [
                (u[0], int(u[1]))
                for u in t.get("unavailable", [])
            ]
            board.add_teacher(
                id=str(t["id"]),
                name=str(t.get("name", t["id"])),
                internal=bool(t.get("internal", True)),
                preferences=prefs,
                unavailable=unavail,
                unavailable_days=t.get("unavailable_days", []),
            )

        for sub in data.get("subjects", []):
            board.add_subject(
                code=str(sub["code"]),
                name=str(sub.get("name", sub["code"])),
                semester_id=str(sub["semester_id"]),
                internal_teacher_id=str(sub["internal_teacher_id"]) if sub.get("internal_teacher_id") else None,
                external_teacher_id=str(sub["external_teacher_id"]) if sub.get("external_teacher_id") else None,
                class_type=str(sub.get("class_type", "theory")),
                sessions_per_week=float(sub["sessions_per_week"]) if sub.get("sessions_per_week") is not None else None,
                external_sessions=float(sub["external_sessions"]) if sub.get("external_sessions") is not None else None,
                facility=str(sub.get("facility", "none")),
            )

        return board



# =====================================================================================
# 3. COMPILED PROBLEM (integer indexed, immutable during search)
# =====================================================================================

_POP2 = (0, 1, 1, 2)
Placement = Tuple[int, int, int, int, int]    # (day, slot_idx, room_idx, effective_facility_mask, pref_rank or -1)


class _Session:
    """One weekly class.  short=True -> a 1-hour class (0.5 session); split=True -> it is one
    half of a 2-hour class that had to be split into 1h + 1h."""
    __slots__ = ("sid", "subject", "group", "teacher", "practical", "code", "req", "room_key",
                 "short", "split")

    def __init__(self, sid, subject, group, teacher, practical, code, req, room_key,
                 short=False, split=False):
        self.sid, self.subject, self.group, self.teacher = sid, subject, group, teacher
        self.practical, self.code, self.req, self.room_key = practical, code, req, room_key
        self.short, self.split = bool(short), bool(split)


class _Problem:
    def __init__(self, board: SchedulingBoard, splits: Optional[Dict[Tuple[str, str], int]] = None):
        """`splits` maps (subject_code, teacher_id) -> number of that pair's full 2-hour classes
        that are delivered as two 1-hour classes instead (decided by the solver, see _Solver.run)."""
        cfg = board.config
        self.cfg, self.W = cfg, cfg.weights
        self.days = list(cfg.working_days)
        self.D = len(self.days)
        self.slots = board.all_slots()
        self.NS = len(self.slots)
        self.slot_idx = {s.id: i for i, s in enumerate(self.slots)}
        self.tier = [s.tier for s in self.slots]
        self.base = [i for i, t in enumerate(self.tier) if t == 0]
        self.ext_idx = [i for i, t in enumerate(self.tier) if t > 0]
        self.has_extension = bool(self.ext_idx)
        self.TS = self.D * self.NS
        self.full_min = cfg.full_session_minutes
        self.short_min = cfg.short_session_minutes
        self.splits = dict(splits or {})
        self.allow_split = cfg.allow_split_sessions
        # a 1-hour class may only use a regular slot when explicitly allowed (or if no extension slot exists)
        self.short_in_base = bool(cfg.allow_short_in_regular_slot or not self.ext_idx)
        # eligibility[is_short][slot_idx]: a full class -> regular slots only; a 1-hour class -> extension slot
        self.elig = [[t == 0 for t in self.tier],
                     [(t > 0) or self.short_in_base for t in self.tier]]
        self.elig_idx = [[i for i, ok in enumerate(row) if ok] for row in self.elig]
        D, NS, TS = self.D, self.NS, self.TS

        # teachers
        self.teachers = list(board.teachers.values())
        self.t_idx = {t.id: i for i, t in enumerate(self.teachers)}
        self.t_internal = [t.internal for t in self.teachers]
        self.t_max_days = [D - 1 if t.internal else D for t in self.teachers]
        self.t_blocked: List[List[bool]] = []
        self.pref_at: List[Dict[int, Tuple[int, int]]] = []
        for t in self.teachers:
            blocked = [False] * TS
            for day in t.unavailable_days:
                d = board.day_index(day)
                for s in range(NS):
                    blocked[d * NS + s] = True
            for day, sid_ in t.unavailable:
                s = self.slot_idx.get(sid_)
                if s is not None:
                    blocked[board.day_index(day) * NS + s] = True
            self.t_blocked.append(blocked)
            prefs: Dict[int, Tuple[int, int]] = {}
            for rank, p in enumerate(t.preferences[:3]):
                s = self.slot_idx.get(p.slot)
                if s is None:
                    continue
                prefs.setdefault(board.day_index(p.day) * NS + s, (rank, int(Facility.parse(p.facility))))
            self.pref_at.append(prefs)

        # groups (semesters) and rooms
        self.groups = list(board.semesters.values())
        self.g_idx = {g.id: i for i, g in enumerate(self.groups)}
        self.rooms = list(board.rooms.values())
        self.r_mask = [int(r.facility) for r in self.rooms]
        self.r_kind = [r.kind for r in self.rooms]
        self.r_cap = [r.capacity for r in self.rooms]
        self.has_labs = any(k == "lab" for k in self.r_kind)
        self._room_cache: Dict[tuple, List[int]] = {}

        # sessions
        self.sessions: List[_Session] = []
        self.t_sessions: List[List[int]] = [[] for _ in self.teachers]
        self.g_sessions: List[List[int]] = [[] for _ in self.groups]
        self.subject_codes: List[str] = []
        self.full_count: Dict[Tuple[str, str], int] = {}     # full 2-hour classes per (subject, teacher) before splitting
        for si, sub in enumerate(board.subjects.values()):
            self.subject_codes.append(sub.code)
            g = self.g_idx[sub.semester_id]
            practical = sub.class_type is ClassType.PRACTICAL
            kind = ("lab" if practical else "classroom") if self.has_labs else "any"
            room_key = (kind, self.groups[g].strength or 0)
            u_int, u_ext = board.units_of(sub)
            for tid, units in ((sub.internal_teacher_id, u_int), (sub.external_teacher_id, u_ext)):
                if not tid or units <= 0:
                    continue
                full, short = divmod(units, 2)           # 2 half-units = 1 full session
                self.full_count[(sub.code, tid)] = self.full_count.get((sub.code, tid), 0) + full
                k = min(full, self.splits.get((sub.code, tid), 0)) if self.allow_split else 0
                plan = [(False, False)] * (full - k) + [(True, False)] * short + [(True, True)] * (2 * k)
                t = self.t_idx[tid]
                for is_short, is_split in plan:
                    sid = len(self.sessions)
                    self.sessions.append(_Session(sid, si, g, t, practical, sub.code, int(sub.facility),
                                                  room_key, is_short, is_split))
                    self.t_sessions[t].append(sid)
                    self.g_sessions[g].append(sid)
        self.deg = [len(self.t_sessions[s.teacher]) + len(self.g_sessions[s.group]) for s in self.sessions]

    def room_list(self, key, req: int) -> List[int]:
        """Rooms able to host (kind, min-capacity, facility mask), best fit (least waste) first."""
        ck = (key, req)
        lst = self._room_cache.get(ck)
        if lst is None:
            kind, cap = key
            cand = []
            for r in range(len(self.rooms)):
                if kind != "any" and self.r_kind[r] != kind:
                    continue
                if cap and self.r_cap[r] is not None and self.r_cap[r] < cap:
                    continue
                m = self.r_mask[r]
                if m & req != req:
                    continue
                cand.append((_POP2[m & ~req & 3], r))
            cand.sort()
            lst = [r for _, r in cand]
            self._room_cache[ck] = lst
        return lst

    def needs_extension(self) -> bool:
        """True if the extension slot must be active from the start (1-hour classes exist, or the
        regular slots alone are too few)."""
        if not self.has_extension:
            return False
        if any(s.short for s in self.sessions):
            return True
        cap = self.D * len(self.base)
        if any(len(s) > cap for s in self.g_sessions):
            return True
        return len(self.sessions) > len(self.rooms) * cap


# =====================================================================================
# 4. SCHEDULE STATE (journaled, so any move can be rolled back exactly)
# =====================================================================================


class _State:
    def __init__(self, P: _Problem):
        self.P = P
        n, T, G, R, D, TS = len(P.sessions), len(P.teachers), len(P.groups), len(P.rooms), P.D, P.TS
        self.pl: List[Optional[Placement]] = [None] * n
        self.t_busy = [[-1] * TS for _ in range(T)]
        self.g_busy = [[-1] * TS for _ in range(G)]
        self.r_busy = [[-1] * TS for _ in range(R)]
        self.t_theory = [[-1] * D for _ in range(T)]
        self.t_prac = [[-1] * D for _ in range(T)]
        self.t_dayn = [[0] * D for _ in range(T)]
        self.t_days_used = [0] * T
        self.g_day: List[List[List[int]]] = [[[] for _ in range(D)] for _ in range(G)]
        self.g_subj: List[List[Dict[int, int]]] = [[{} for _ in range(D)] for _ in range(G)]
        self.journal: Optional[list] = None
        self.active: List[bool] = [True] * P.NS
        self.active_idx: List[int] = list(range(P.NS))

    def set_active(self, flags: Sequence[bool]) -> None:
        self.active = list(flags)
        self.active_idx = [i for i, f in enumerate(self.active) if f]

    # ---- primitive mutation (everything funnels through _set) ------------------------
    def _set(self, sid: int, new: Optional[Placement]) -> None:
        old = self.pl[sid]
        if self.journal is not None:
            self.journal.append((sid, old))
        P = self.P
        sess = P.sessions[sid]
        t, g, NS = sess.teacher, sess.group, P.NS
        if old is not None:
            d, s, r = old[0], old[1], old[2]
            ts = d * NS + s
            self.t_busy[t][ts] = -1
            self.g_busy[g][ts] = -1
            self.r_busy[r][ts] = -1
            (self.t_prac if sess.practical else self.t_theory)[t][d] = -1
            self.t_dayn[t][d] -= 1
            if self.t_dayn[t][d] == 0:
                self.t_days_used[t] -= 1
            self.g_day[g][d].remove(sid)
            del self.g_subj[g][d][sess.subject]
        if new is not None:
            d, s, r = new[0], new[1], new[2]
            ts = d * NS + s
            self.t_busy[t][ts] = sid
            self.g_busy[g][ts] = sid
            self.r_busy[r][ts] = sid
            (self.t_prac if sess.practical else self.t_theory)[t][d] = sid
            if self.t_dayn[t][d] == 0:
                self.t_days_used[t] += 1
            self.t_dayn[t][d] += 1
            self.g_day[g][d].append(sid)
            self.g_subj[g][d][sess.subject] = sid
        self.pl[sid] = new

    def begin(self) -> None:
        self.journal = []

    def end(self) -> list:
        j, self.journal = self.journal, None
        return j

    def rollback(self) -> None:
        j, self.journal = self.journal, None
        for sid, old in reversed(j):
            self._set(sid, old)

    def restore(self, snapshot: Sequence[Optional[Placement]]) -> None:
        for sid in range(len(self.pl)):
            self._set(sid, None)
        for sid, p in enumerate(snapshot):
            if p is not None:
                self._set(sid, p)

    # ---- feasibility -----------------------------------------------------------------
    def _time_ok(self, sess: _Session, d: int, s: int, ts: int) -> bool:
        P = self.P
        if not self.active[s] or not P.elig[sess.short][s]:   # full class -> regular slot, 1-hour class -> extension slot
            return False
        t, g = sess.teacher, sess.group
        if P.t_blocked[t][ts] or self.t_busy[t][ts] >= 0 or self.g_busy[g][ts] >= 0:
            return False
        if sess.practical:
            if self.t_prac[t][d] >= 0:
                return False
        elif self.t_theory[t][d] >= 0:
            return False
        if self.t_dayn[t][d] == 0 and P.t_internal[t] and self.t_days_used[t] >= P.t_max_days[t]:
            return False                      # would consume the internal teacher's last free day
        if sess.subject in self.g_subj[g][d]:
            return False
        return True

    def _free_via_chain(self, rooms, ts, depth, visited):
        """Find a room in `rooms` that can be vacated by relocating occupants (augmenting path)."""
        P = self.P
        for r in rooms:
            if r in visited:
                continue
            visited.add(r)
            occ = self.r_busy[r][ts]
            if occ < 0:
                return r, []
            alt = P.room_list(P.sessions[occ].room_key, self.pl[occ][3])
            for r2 in alt:
                if r2 != r and self.r_busy[r2][ts] < 0:
                    return r, [(occ, r2)]
            if depth > 1:
                res = self._free_via_chain([x for x in alt if x != r], ts, depth - 1, visited)
                if res is not None:
                    return r, res[1] + [(occ, res[0])]
        return None

    def _room_plan(self, key, req, ts):
        rooms = self.P.room_list(key, req)
        rb = self.r_busy
        for r in rooms:
            if rb[r][ts] < 0:
                return r, []
        return self._free_via_chain(rooms, ts, 3, set())

    def feasible(self, sid: int, d: int, s: int) -> bool:
        sess = self.P.sessions[sid]
        ts = d * self.P.NS + s
        return self._time_ok(sess, d, s, ts) and self._room_plan(sess.room_key, sess.req, ts) is not None

    def plan(self, sid: int, d: int, s: int):
        """Full legal placement for session at (d, s): (placement, room_moves) or None.
        If the teacher prefers this slot the preference's facility is added to the requirement
        (preference honoured); when no suitable room exists the class is still placed
        with its own requirement only."""
        P = self.P
        sess = P.sessions[sid]
        ts = d * P.NS + s
        if not self._time_ok(sess, d, s, ts):
            return None
        pref = P.pref_at[sess.teacher].get(ts)
        if pref is not None:
            rank, fac = pref
            req = sess.req | fac
            rp = self._room_plan(sess.room_key, req, ts)
            if rp is not None:
                return (d, s, rp[0], req, rank), rp[1]
        rp = self._room_plan(sess.room_key, sess.req, ts)
        if rp is None:
            return None
        return (d, s, rp[0], sess.req, -1), rp[1]

    def commit_plan(self, sid: int, plan) -> None:
        placement, moves = plan
        for occ, r2 in moves:
            o = self.pl[occ]
            self._set(occ, (o[0], o[1], r2, o[3], o[4]))
        self._set(sid, placement)

    # ---- soft costs (decomposed so moves only re-cost what they touch) ---------------------
    def s_cost(self, sid: int) -> float:
        p = self.pl[sid]
        if p is None:
            return 0.0
        P = self.P
        W = P.W
        c = _POP2[P.r_mask[p[2]] & ~p[3] & 3] * W.room_waste
        if P.tier[p[1]]:
            c += W.extension_slot
        elif P.sessions[sid].short:
            c += W.short_in_regular_slot        # a 1-hour class wasting a whole regular slot
        if p[4] >= 0:
            c -= W.pref_reward[p[4]]
        return c

    def t_cost(self, t: int) -> float:
        P = self.P
        W = P.W
        c = 0.0
        for d in range(P.D):
            if self.t_dayn[t][d] == 2:
                c += W.teacher_double_day
                a, b = self.t_theory[t][d], self.t_prac[t][d]
                c += W.teacher_gap * (abs(self.pl[a][1] - self.pl[b][1]) - 1)
        if not P.t_internal[t]:
            c += W.external_visit_day * self.t_days_used[t]
        return c

    def g_cost(self, g: int) -> float:
        P = self.P
        W = P.W
        pl, sessions = self.pl, P.sessions
        c = 0.0
        prev: frozenset = frozenset()
        for d in range(P.D):
            sids = self.g_day[g][d]
            if not sids:
                prev = frozenset()
                continue
            mask = npr = 0
            subs = []
            for sid in sids:
                mask |= 1 << pl[sid][1]
                ss = sessions[sid]
                npr += ss.practical
                subs.append(ss.subject)
            n = len(sids)
            span = mask.bit_length() - ((mask & -mask).bit_length() - 1)
            c += W.group_gap * (span - n) + W.group_day_load * n * n
            if npr > 1:
                c += W.group_multi_practical * (npr - 1)
            cur = frozenset(subs)
            if prev:
                c += W.group_consecutive_subject * len(cur & prev)
            prev = cur
        return c

    def cost_of(self, sids: Iterable[int]) -> float:
        P = self.P
        sids = set(sids)
        ts = {P.sessions[x].teacher for x in sids}
        gs = {P.sessions[x].group for x in sids}
        return (sum(self.s_cost(x) for x in sids) + sum(self.t_cost(t) for t in ts)
                + sum(self.g_cost(g) for g in gs))

    def total_cost(self) -> float:
        P = self.P
        return (sum(self.s_cost(i) for i in range(len(P.sessions)))
                + sum(self.t_cost(t) for t in range(len(P.teachers)))
                + sum(self.g_cost(g) for g in range(len(P.groups))))


# =====================================================================================
# 5. SOLVER
# =====================================================================================


class _Solver:
    def __init__(self, P: _Problem):
        self.P = P
        self.cfg = P.cfg
        self.stats: Counter = Counter()

    # ---- domains (incremental MRV) --------------------------------------------------
    def _domain(self, st: _State, sid: int) -> Set[int]:
        P = self.P
        NS = P.NS
        elig = P.elig[P.sessions[sid].short]
        out = set()
        for d in range(P.D):
            base = d * NS
            for s in st.active_idx:
                if elig[s] and st.feasible(sid, d, s):
                    out.add(base + s)
        return out

    def _refresh(self, st, dom, unplaced, journal, shrink_only) -> None:
        P = self.P
        NS = P.NS
        teachers, groups, cells = set(), set(), set()
        for sid, old in journal:
            ss = P.sessions[sid]
            teachers.add(ss.teacher)
            groups.add(ss.group)
            if old is not None:
                cells.add(old[0] * NS + old[1])
            new = st.pl[sid]
            if new is not None:
                cells.add(new[0] * NS + new[1])
        for y in unplaced:
            dy = dom.get(y)
            if dy is None:
                continue
            sy = P.sessions[y]
            if sy.teacher in teachers or sy.group in groups:
                dom[y] = self._domain(st, y)
                continue
            for ts in cells:
                if shrink_only and ts not in dy:
                    continue
                d, s = divmod(ts, NS)
                if st.feasible(y, d, s):
                    dy.add(ts)
                else:
                    dy.discard(ts)

    # ---- construction ------------------------------------------------------------------
    def _delta_place(self, st: _State, sid: int, plan) -> float:
        sids = [sid] + [o for o, _ in plan[1]]
        before = st.cost_of(sids)
        st.begin()
        st.commit_plan(sid, plan)
        after = st.cost_of(sids)
        st.rollback()
        return after - before

    def _choose(self, st, sid, cells, rng, noise):
        NS = self.P.NS
        best, best_score = None, 1e18
        for ts in cells:
            d, s = divmod(ts, NS)
            plan = st.plan(sid, d, s)
            if plan is None:
                continue
            score = self._delta_place(st, sid, plan) + rng.random() * noise
            if score < best_score:
                best, best_score = plan, score
        return best

    def _construct(self, st: _State, unplaced: Set[int], rng, deadline: float, noise: float) -> Set[int]:
        P = self.P
        unplaced = set(unplaced)
        stuck: Set[int] = set()
        dom = {sid: self._domain(st, sid) for sid in unplaced}
        tie = {sid: rng.random() for sid in unplaced}
        eject: Counter = Counter()
        repairs, cap = 0, 12 * len(P.sessions) + 50
        while unplaced:
            if time.perf_counter() > deadline:
                break
            sid = min(unplaced, key=lambda x: (len(dom[x]), -P.deg[x], tie.get(x, 0.5)))
            if dom[sid]:
                plan = self._choose(st, sid, dom[sid], rng, noise)
                if plan is not None:
                    st.begin()
                    st.commit_plan(sid, plan)
                    journal = st.end()
                    unplaced.discard(sid)
                    dom.pop(sid)
                    self._refresh(st, dom, unplaced, journal, True)
                    continue
                dom[sid] = set()
            # dead end -> ejection-chain repair
            if repairs >= cap:
                stuck.add(sid)
                unplaced.discard(sid)
                dom.pop(sid)
                continue
            repairs += 1
            self.stats["repairs"] += 1
            res = self._repair(st, sid, rng, eject)
            unplaced.discard(sid)
            dom.pop(sid)
            if res is None:
                stuck.add(sid)
                continue
            ejected, journal = res
            for e in ejected:
                unplaced.add(e)
                tie[e] = rng.random()
            self._refresh(st, dom, unplaced, journal, False)
            for e in ejected:
                dom[e] = self._domain(st, e)
        return unplaced | stuck

    def _repair(self, st: _State, sid: int, rng, eject: Counter):
        """Place `sid` by evicting the cheapest set of blocking sessions. Returns (ejected, journal)."""
        P = self.P
        NS = P.NS
        sess = P.sessions[sid]
        t, g = sess.teacher, sess.group
        elig = P.elig[sess.short]
        options = []
        for d in range(P.D):
            for s in st.active_idx:
                if not elig[s]:
                    continue
                ts = d * NS + s
                if P.t_blocked[t][ts]:
                    continue
                if P.t_internal[t] and st.t_dayn[t][d] == 0 and st.t_days_used[t] >= P.t_max_days[t]:
                    continue
                blockers = set()
                for b in (st.t_busy[t][ts], st.g_busy[g][ts],
                          (st.t_prac if sess.practical else st.t_theory)[t][d],
                          st.g_subj[g][d].get(sess.subject, -1)):
                    if b >= 0:
                        blockers.add(b)
                if len(blockers) > 3:
                    continue
                st.begin()
                for b in blockers:
                    st._set(b, None)
                plan = st.plan(sid, d, s)
                if plan is None and st._time_ok(sess, d, s, ts):
                    occs = [st.r_busy[r][ts] for r in P.room_list(sess.room_key, sess.req)
                            if st.r_busy[r][ts] >= 0]
                    if occs:
                        o = rng.choice(occs)
                        blockers.add(o)
                        st._set(o, None)
                        plan = st.plan(sid, d, s)
                st.rollback()
                if plan is not None:
                    score = 10 * len(blockers) + sum(eject[b] for b in blockers) + rng.random()
                    options.append((score, d, s, tuple(blockers)))
        if not options:
            return None
        _, d, s, blockers = min(options)
        st.begin()
        for b in blockers:
            st._set(b, None)
            eject[b] += 1
        plan = st.plan(sid, d, s)
        st.commit_plan(sid, plan)
        return set(blockers), st.end()

    # ---- optimisation -------------------------------------------------------------------
    def _anneal(self, st: _State, rng, deadline: float) -> None:
        P, cfg = self.P, self.cfg
        n = len(P.sessions)
        if n < 2:
            return
        NS, D = P.NS, P.D
        s_c = [st.s_cost(i) for i in range(n)]
        t_c = [st.t_cost(t) for t in range(len(P.teachers))]
        g_c = [st.g_cost(g) for g in range(len(P.groups))]
        cur = sum(s_c) + sum(t_c) + sum(g_c)
        best, best_pl = cur, list(st.pl)
        # slots / preferred cells a session of each kind (0 = full, 1 = 1-hour) may actually use
        elig_active = [[s for s in P.elig_idx[k] if st.active[s]] for k in (0, 1)]
        pref_cells = [[[ts for ts in P.pref_at[t] if st.active[ts % NS] and P.elig[k][ts % NS]]
                       for t in range(len(P.teachers))] for k in (0, 1)]
        T0, T1 = cfg.sa_start_temp, cfg.sa_end_temp
        t_begin = time.perf_counter()
        span = max(1e-9, deadline - t_begin)
        T, it, acc = T0, 0, 0
        max_it = cfg.sa_max_iterations
        while it < max_it:
            if (it & 127) == 0:
                now = time.perf_counter()
                if now >= deadline:
                    break
                frac = max((now - t_begin) / span, it / max_it)
                T = T0 * (T1 / T0) ** frac
            it += 1
            st.begin()
            ok = False
            if rng.random() < 0.6:
                sid = rng.randrange(n)
                cp = st.pl[sid]
                sess = P.sessions[sid]
                prefs = pref_cells[sess.short][sess.teacher]
                pool_s = elig_active[sess.short]
                if prefs and rng.random() < 0.35:
                    d, s = divmod(rng.choice(prefs), NS)
                else:
                    d, s = rng.randrange(D), (rng.choice(pool_s) if pool_s else cp[1])
                if (d, s) != (cp[0], cp[1]):
                    st._set(sid, None)
                    plan = st.plan(sid, d, s)
                    if plan is not None:
                        st.commit_plan(sid, plan)
                        ok = True
            else:
                a = rng.randrange(n)
                sa = P.sessions[a]
                r = rng.random()
                pool = (P.g_sessions[sa.group] if r < 0.5 else
                        P.t_sessions[sa.teacher] if r < 0.75 else range(n))
                b = pool[rng.randrange(len(pool))] if isinstance(pool, list) else rng.randrange(n)
                pa, pb = st.pl[a], st.pl[b]
                if a != b and (pa[0], pa[1]) != (pb[0], pb[1]):
                    st._set(a, None)
                    st._set(b, None)
                    p1 = st.plan(a, pb[0], pb[1])
                    if p1 is not None:
                        st.commit_plan(a, p1)
                        p2 = st.plan(b, pa[0], pa[1])
                        if p2 is not None:
                            st.commit_plan(b, p2)
                            ok = True
            if not ok:
                st.rollback()
                continue
            touched = {sid for sid, _ in st.journal}
            ts_ = {P.sessions[x].teacher for x in touched}
            gs_ = {P.sessions[x].group for x in touched}
            ns = {x: st.s_cost(x) for x in touched}
            nt = {x: st.t_cost(x) for x in ts_}
            ng = {x: st.g_cost(x) for x in gs_}
            delta = (sum(ns.values()) - sum(s_c[x] for x in touched)
                     + sum(nt.values()) - sum(t_c[x] for x in ts_)
                     + sum(ng.values()) - sum(g_c[x] for x in gs_))
            if delta <= 0 or rng.random() < math.exp(-delta / T):
                st.end()
                for x, v in ns.items():
                    s_c[x] = v
                for x, v in nt.items():
                    t_c[x] = v
                for x, v in ng.items():
                    g_c[x] = v
                cur += delta
                acc += 1
                if cur < best - 1e-9:
                    best, best_pl = cur, list(st.pl)
            else:
                st.rollback()
        if cur > best + 1e-9:
            st.restore(best_pl)
        self.stats["sa_iterations"] += it
        self.stats["sa_accepted"] += acc

    # ---- diagnostics ----------------------------------------------------------------------
    def _diagnose_unscheduled(self, st: _State, sid: int, board: Optional[SchedulingBoard] = None) -> dict:
        P = self.P
        NS = P.NS
        sess = P.sessions[sid]
        t, g = sess.teacher, sess.group
        t_obj = P.teachers[t]
        g_obj = P.groups[g]
        subj_name = sess.code
        if board and hasattr(board, "subjects"):
            subj_val = board.subjects.get(sess.code)
            if subj_val:
                subj_name = subj_val.name or sess.code

        t_name = t_obj.name or t_obj.id
        t_label = f"{t_name} ({t_obj.id})" if t_name != t_obj.id else t_obj.id
        g_label = g_obj.name or g_obj.id
        ctype = "practical" if sess.practical else "theory"
        elig = P.elig[sess.short]

        slot_conflicts = []
        semester_busy_slots = []
        teacher_busy_slots = []
        teacher_daily_limit_days = {}
        teacher_free_days = []
        teacher_unavailable_slots = []
        subject_repeat_days = {}
        room_unavailable_slots = []

        total_active_slots = 0
        for d in range(P.D):
            day_name = P.days[d]
            for s in st.active_idx:
                if not elig[s]:
                    continue          # a full class can never use the 1-hour extension slot (and vice versa)
                total_active_slots += 1
                ts = d * NS + s
                slot_obj = P.slots[s]
                slot_time = f"{day_name} {slot_obj.start}–{slot_obj.end}"

                if P.t_blocked[t][ts]:
                    conflict_type = "teacher_unavailable"
                    detail = f"Teacher {t_label} marked unavailable"
                    teacher_unavailable_slots.append(slot_time)
                elif st.t_busy[t][ts] >= 0:
                    conflict_type = "teacher_busy"
                    occ_sid = st.t_busy[t][ts]
                    occ_s = P.sessions[occ_sid]
                    occ_grp = P.groups[occ_s.group]
                    occ_rm = f" in R#{P.rooms[st.pl[occ_sid][2]].number}" if st.pl[occ_sid] else ""
                    detail = f"Teacher {t_label} teaching {occ_s.code} to {occ_grp.name or occ_grp.id}{occ_rm}"
                    teacher_busy_slots.append({"slot": slot_time, "detail": detail, "code": occ_s.code, "cohort": occ_grp.id})
                elif st.g_busy[g][ts] >= 0:
                    conflict_type = "semester_busy"
                    occ_sid = st.g_busy[g][ts]
                    occ_s = P.sessions[occ_sid]
                    occ_tch = P.teachers[occ_s.teacher]
                    occ_t_name = f"{occ_tch.name} ({occ_tch.id})" if occ_tch.name else occ_tch.id
                    occ_rm = f" in R#{P.rooms[st.pl[occ_sid][2]].number}" if st.pl[occ_sid] else ""
                    detail = f"Cohort {g_label} attending {occ_s.code} with {occ_t_name}{occ_rm}"
                    semester_busy_slots.append({"slot": slot_time, "detail": detail, "code": occ_s.code, "teacher": occ_tch.id})
                elif (st.t_prac if sess.practical else st.t_theory)[t][d] >= 0:
                    conflict_type = "teacher_daily_limit"
                    day_sid = (st.t_prac if sess.practical else st.t_theory)[t][d]
                    day_s = P.sessions[day_sid]
                    slot_idx = st.pl[day_sid][1] if st.pl[day_sid] else -1
                    slot_str = f" at {P.slots[slot_idx].start}–{P.slots[slot_idx].end}" if slot_idx >= 0 else ""
                    detail = f"Teacher {t_label} already teaching {ctype} {day_s.code}{slot_str} (max 1 {ctype}/day limit)"
                    teacher_daily_limit_days[day_name] = detail
                elif P.t_internal[t] and st.t_dayn[t][d] == 0 and st.t_days_used[t] >= P.t_max_days[t]:
                    conflict_type = "teacher_free_day"
                    detail = f"{day_name} preserved as teacher {t_label}'s guaranteed free day (max {P.t_max_days[t]} teaching days)"
                    if day_name not in teacher_free_days:
                        teacher_free_days.append(day_name)
                elif sess.subject in st.g_subj[g][d]:
                    conflict_type = "subject_double_day"
                    detail = f"Cohort {g_label} already has {sess.code} scheduled on {day_name}"
                    subject_repeat_days[day_name] = detail
                else:
                    conflict_type = "room_unavailable"
                    detail = f"No vacant room with required facility available at {slot_time}"
                    room_unavailable_slots.append(slot_time)

                slot_conflicts.append({
                    "day": day_name,
                    "slot_id": slot_obj.id,
                    "slot_label": slot_obj.label or f"Slot {slot_obj.id}",
                    "start": slot_obj.start,
                    "end": slot_obj.end,
                    "time_str": slot_time,
                    "conflict_type": conflict_type,
                    "detail": detail
                })

        parts = []
        if semester_busy_slots:
            slot_names = [s["slot"] for s in semester_busy_slots]
            if len(slot_names) <= 4:
                slots_str = ", ".join(slot_names)
            else:
                slots_str = ", ".join(slot_names[:3]) + f", ... and {len(slot_names) - 3} more"
            parts.append(f"Cohort {g_label} already has a class ({len(semester_busy_slots)} slots: {slots_str})")

        if teacher_busy_slots:
            slot_names = [s["slot"] for s in teacher_busy_slots]
            if len(slot_names) <= 3:
                slots_str = ", ".join(slot_names)
            else:
                slots_str = ", ".join(slot_names[:2]) + f", ... and {len(slot_names) - 2} more"
            parts.append(f"Teacher {t_label} already teaching another class ({len(teacher_busy_slots)} slots: {slots_str})")

        if teacher_daily_limit_days:
            days_str = ", ".join(teacher_daily_limit_days.keys())
            parts.append(f"Teacher {t_label} already has a {ctype} class on {days_str} (max 1 {ctype}/day limit)")

        if teacher_free_days:
            days_str = ", ".join(teacher_free_days)
            parts.append(f"Teacher {t_label}'s guaranteed free day kept on {days_str}")

        if teacher_unavailable_slots:
            parts.append(f"Teacher {t_label} unavailable ({len(teacher_unavailable_slots)} slots: {', '.join(teacher_unavailable_slots[:3])})")

        if subject_repeat_days:
            parts.append(f"{sess.code} already scheduled today on {', '.join(subject_repeat_days.keys())}")

        if room_unavailable_slots:
            parts.append(f"No free room with required facility ({len(room_unavailable_slots)} slots: {', '.join(room_unavailable_slots[:3])})")

        summary_reason = "; ".join(parts) if parts else "No valid slot found satisfying all hard constraints"

        # Formulate actionable remedy tip
        ext_desc = ", ".join(f"{sl.start}–{sl.end}" for sl in P.slots if sl.tier > 0) or "none configured"
        if sess.short:
            kind_hint = (f"This is a 1-hour class: it can only use the extension slot ({ext_desc})"
                         + ("" if P.short_in_base else "; enable allow_short_in_regular_slot to let it use a regular slot as a last resort") + ".")
        else:
            kind_hint = (f"This is a full {P.full_min}-minute class: it can only use the regular slots"
                         + (" (it was already tried as two 1-hour classes on different days)" if sess.split else
                            ("; it may be delivered as two 1-hour classes in " + ext_desc + " on different days if the timetable permits"
                             if (self.cfg.allow_split_sessions and P.has_extension) else
                             "; enable allow_split_sessions to deliver it as 1h + 1h on two days"
                             if P.has_extension else "")) + ".")
        if len(semester_busy_slots) >= total_active_slots - 2 or len(semester_busy_slots) >= 15:
            remedy = (
                f"Cohort {g_label} has nearly all usable slots occupied ({len(semester_busy_slots)}/{total_active_slots} slots full). {kind_hint} "
                f"Split parallel sections/electives into separate cohort IDs (e.g. {g_obj.id}-A and {g_obj.id}-B), "
                f"add Saturday as a working day, or reduce sessions per week for this cohort (e.g. 2.5 instead of 3)."
            )
        elif len(teacher_daily_limit_days) + len(teacher_free_days) >= P.D:
            remedy = (
                f"Faculty member {t_label} has reached the max limit of 1 {ctype}/day across {len(teacher_daily_limit_days)} teaching days "
                f"({', '.join(teacher_daily_limit_days.keys())}), with {', '.join(teacher_free_days)} kept as their weekly free day. "
                f"Reassign this session to another teacher or co-teacher, or toggle this faculty to External (waiving the free day constraint)."
            )
        elif teacher_busy_slots:
            remedy = (
                f"Faculty member {t_label} is occupied teaching during overlapping candidate slots ({len(teacher_busy_slots)} slots). "
                f"Reassign this course to a co-teacher or adjust conflicting class schedules. {kind_hint}"
            )
        elif room_unavailable_slots:
            remedy = (
                f"All classrooms or labs with the required facility are occupied. "
                f"Add additional venues under Rooms & Venues or upgrade existing rooms with blackboard/projector facilities. {kind_hint}"
            )
        else:
            remedy = f"Review teacher preferences and course allocations to provide additional scheduling flexibility. {kind_hint}"

        return {
            "summary": summary_reason,
            "details": parts,
            "slot_conflicts": slot_conflicts,
            "category_slots": {
                "semester_busy": semester_busy_slots,
                "teacher_busy": teacher_busy_slots,
                "teacher_daily_limit": [{"day": d, "detail": det} for d, det in teacher_daily_limit_days.items()],
                "teacher_free_days": teacher_free_days,
                "teacher_unavailable": teacher_unavailable_slots,
                "subject_repeat_days": [{"day": d, "detail": det} for d, det in subject_repeat_days.items()],
                "room_unavailable": room_unavailable_slots,
            },
            "remedy": remedy,
        }

    def _explain(self, st: _State, sid: int) -> str:
        return self._diagnose_unscheduled(st, sid)["summary"]

    # ---- driver ------------------------------------------------------------------------------
    def _unplaced_minutes(self, left: Iterable[int]) -> int:
        P = self.P
        return sum(P.short_min if P.sessions[x].short else P.full_min for x in left)

    def _search(self, seed: int, deadline: float, share: float) -> Tuple[_State, Set[int]]:
        """Construct + repair with random restarts on the CURRENT problem (self.P).
        `share` is the fraction of the remaining construct-time this call may use."""
        P, cfg = self.P, self.cfg
        n = len(P.sessions)
        now = time.perf_counter()
        round_deadline = now + max(0.0, deadline - now) * share
        best_st, best_left = None, None
        attempt = 0
        while True:
            rng = random.Random(seed + 7919 * attempt)
            st = _State(P)
            noise = 0.3 if attempt == 0 else 1.0 + 0.5 * min(attempt, 6)
            if P.needs_extension():
                st.set_active([True] * P.NS)
            else:
                st.set_active([t == 0 for t in P.tier])
            left = self._construct(st, set(range(n)), rng, round_deadline, noise)
            if (left and P.has_extension and not all(st.active)
                    and any(P.sessions[x].short for x in left)):
                st.set_active([True] * P.NS)           # unlock the extension slot only for the leftovers
                left = self._construct(st, left, rng, round_deadline, noise)
            self.stats["attempts"] += 1
            if best_st is None or self._unplaced_minutes(left) < self._unplaced_minutes(best_left):
                best_st, best_left = st, left
            attempt += 1
            if not left or attempt > cfg.max_restarts or time.perf_counter() >= round_deadline:
                break
        return best_st, best_left

    def _propose_splits(self, left: Iterable[int], splits: Dict[Tuple[str, str], int]):
        """Split one more full class (per leftover full class) into 1h + 1h. None if nothing can be split."""
        P = self.P
        new = dict(splits)
        changed = False
        for sid in sorted(left):
            s = P.sessions[sid]
            if s.short:
                continue
            key = (s.code, P.teachers[s.teacher].id)
            if new.get(key, 0) < P.full_count.get(key, 0):
                new[key] = new.get(key, 0) + 1
                changed = True
        return new if changed else None

    def run(self, board: SchedulingBoard, issues: List[Issue]) -> "TimetableResult":
        cfg = self.cfg
        t0 = time.perf_counter()
        seed = cfg.seed if cfg.seed is not None else random.randrange(1 << 30)
        construct_deadline = t0 + cfg.time_limit_s * 0.55
        splits: Dict[Tuple[str, str], int] = {}
        best = None
        rnd = 0
        while True:
            can_split = cfg.allow_split_sessions and self.P.has_extension and rnd < cfg.max_split_rounds
            st, left = self._search(seed, construct_deadline, 0.5 if can_split else 1.0)
            score = self._unplaced_minutes(left)
            if best is None or score < best[0]:
                best = (score, self.P, st, left, dict(splits))
            if not left or not can_split or time.perf_counter() >= construct_deadline:
                break
            nxt = self._propose_splits(left, splits)      # full classes left over -> try 1h + 1h on two days
            if nxt is None:
                break
            splits = nxt
            self.P = _Problem(board, splits)
            rnd += 1
        _, self.P, st, best_left, used = best
        P = self.P
        self.stats["split_rounds"] = rnd
        self.stats["split_sessions"] = sum(used.values())
        if not best_left:
            self._anneal(st, random.Random(seed ^ 0xBEEF), t0 + cfg.time_limit_s)
        unscheduled = []
        for sid in sorted(best_left):
            s = P.sessions[sid]
            diag = self._diagnose_unscheduled(st, sid, board)
            grp = P.groups[s.group]
            tch = P.teachers[s.teacher]
            subj_obj = next((sub for sub in board.subjects.values() if sub.code == s.code), None)
            unscheduled.append({
                "session_id": sid,
                "subject": s.code,
                "subject_name": subj_obj.name if subj_obj else s.code,
                "semester": grp.id,
                "semester_name": grp.name or grp.id,
                "programme": grp.programme,
                "semester_number": grp.number,
                "teacher": tch.id,
                "teacher_name": tch.name,
                "teacher_label": f"{tch.name} ({tch.id})" if tch.name and tch.name != tch.id else tch.id,
                "class_type": "practical" if s.practical else "theory",
                "duration_hours": (P.short_min if s.short else P.full_min) / 60.0,
                "is_half_session": s.short,
                "is_split_piece": s.split,
                "reason": diag["summary"],
                "detailed_reasons": diag["details"],
                "slot_conflicts": diag["slot_conflicts"],
                "category_slots": diag["category_slots"],
                "remedy": diag["remedy"],
            })
        self.stats["runtime_s"] = round(time.perf_counter() - t0, 3)
        self.stats["final_cost"] = round(st.total_cost(), 2)
        return TimetableResult._from_state(board, P, st, unscheduled, issues, dict(self.stats))


# =====================================================================================
# 6. RESULT, RENDERING, EXPORT, INDEPENDENT VERIFICATION
# =====================================================================================


@dataclass
class ScheduledClass:
    semester_id: str
    programme: str
    subject_code: str
    subject_name: str
    class_type: str
    teacher_id: str
    teacher_name: str
    day: str
    slot_id: int
    slot_title: str
    start: str
    end: str
    room: str
    preference_rank: Optional[int]      # 1..3 if this placement honours the teacher's n-th preference
    extension_slot: bool
    duration_hours: float = 2.0         # 2.0 = full session, 1.0 = half session (1-hour class)
    is_half_session: bool = False       # True for 1-hour classes
    is_split_piece: bool = False        # True if this 1-hour class is half of a split 2-hour class


class TimetableResult:
    def __init__(self, board, classes, unscheduled, issues, stats, teacher_free_days, preference_report):
        self.board = board
        self.classes: List[ScheduledClass] = classes
        self.unscheduled: List[dict] = unscheduled
        self.issues: List[Issue] = issues
        self.stats: dict = stats
        self.teacher_free_days: Dict[str, List[str]] = teacher_free_days
        self.preference_report: dict = preference_report
        self.violations: List[str] = verify(board, classes)

    @property
    def complete(self) -> bool:
        return not self.unscheduled

    @classmethod
    def _from_state(cls, board, P: _Problem, st: _State, unscheduled, issues, stats):
        classes: List[ScheduledClass] = []
        honoured: Dict[str, List[int]] = {t.id: [] for t in P.teachers}
        for sid, p in enumerate(st.pl):
            if p is None:
                continue
            s = P.sessions[sid]
            sub = board.subjects[s.code]
            slot = P.slots[p[1]]
            teacher = P.teachers[s.teacher]
            grp = P.groups[s.group]
            classes.append(ScheduledClass(
                grp.id, grp.programme, sub.code, sub.name, sub.class_type.value, teacher.id, teacher.name,
                P.days[p[0]], slot.id, slot.title, slot.start, slot.end, P.rooms[p[2]].number,
                p[4] + 1 if p[4] >= 0 else None, slot.tier > 0,
                (P.short_min if s.short else P.full_min) / 60.0, s.short, s.split))
            if p[4] >= 0:
                honoured[teacher.id].append(p[4] + 1)
        day_order = {d: i for i, d in enumerate(P.days)}
        classes.sort(key=lambda c: (c.semester_id, day_order[c.day], c.start))
        free = {t.id: [P.days[d] for d in range(P.D) if st.t_dayn[i][d] == 0]
                for i, t in enumerate(P.teachers)}
        report, tot_honoured, tot_possible = {}, 0, 0
        for i, t in enumerate(P.teachers):
            possible = min(len(t.preferences[:3]), len(P.t_sessions[i]))
            h = sorted(honoured[t.id])
            report[t.id] = {"preferences": len(t.preferences[:3]), "achievable": possible, "honoured_ranks": h}
            tot_honoured += len(h)
            tot_possible += possible
        report["_overall"] = {"honoured": tot_honoured, "achievable": tot_possible,
                              "percent": round(100.0 * tot_honoured / tot_possible, 1) if tot_possible else 100.0}
        stats["extension_slot_classes"] = sum(1 for c in classes if c.extension_slot)
        stats["half_session_classes"] = sum(1 for c in classes if c.is_half_session)
        stats["split_piece_classes"] = sum(1 for c in classes if c.is_split_piece)
        return cls(board, classes, unscheduled, issues, stats, free, report)

    # ---- views -----------------------------------------------------------------------------
    def for_semester(self, semester_id: str) -> List[ScheduledClass]:
        return [c for c in self.classes if c.semester_id == semester_id]

    def for_teacher(self, teacher_id: str) -> List[ScheduledClass]:
        return [c for c in self.classes if c.teacher_id == teacher_id]

    def render_semester(self, semester_id: str) -> str:
        board = self.board
        sem = board.semesters[semester_id]
        cls = self.for_semester(semester_id)
        used_ext = {c.slot_id for c in cls if c.extension_slot}
        slots = list(board.slots) + [s for s in board.extension_slots if s.id in used_ext]
        slots.sort(key=lambda s: (s.start_min, s.id))
        days = list(board.config.working_days)
        grid = {(c.day, c.slot_id): c for c in cls}
        trunc = lambda s, w=24: s if len(s) <= w else s[:w - 1] + "."      # noqa: E731
        header = ["Time"] + days
        rows = []
        for sl in slots:
            cells = [["%s" % sl.title, sl.pretty]]
            for d in days:
                c = grid.get((d, sl.id))
                if c is None:
                    cells.append(["-"])
                else:
                    cells.append([
                        "%s (%s)%s%s" % (c.subject_code, c.class_type[0],
                                         " 1h" if c.is_half_session else "",
                                         "*" if c.is_split_piece else ""),
                        trunc(c.teacher_name),
                        "Rm %s%s" % (c.room, "  [pref #%d]" % c.preference_rank if c.preference_rank else "")])
            rows.append(cells)
        widths = [max([len(header[i])] + [len(l) for r in rows for l in r[i]]) for i in range(len(header))]
        sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        fmt = lambda parts: "| " + " | ".join(p.ljust(w) for p, w in zip(parts, widths)) + " |"   # noqa: E731
        out = ["TIMETABLE - %s  [%s]" % (sem.title, sem.id), sep, fmt(header), sep]
        for r in rows:
            for k in range(max(len(c) for c in r)):
                out.append(fmt([c[k] if k < len(c) else "" for c in r]))
            out.append(sep)
        if any(c.is_half_session for c in cls):
            out.append("  1h = one-hour class (0.5 session)   * = one half of a split 2-hour class (1h + 1h on different days)")
        return "\n".join(out)

    def render_teacher(self, teacher_id: str) -> str:
        t = self.board.teachers[teacher_id]
        out = ["%s (%s, %s)  free day(s): %s" % (t.name, t.id, "internal" if t.internal else "external",
                                               ", ".join(self.teacher_free_days.get(t.id, [])) or "none")]
        for c in self.for_teacher(teacher_id):
            out.append("  %-3s %-8s %-22s %-4s %-9s room %s%s%s" % (
                c.day, c.slot_title, c.start + "-" + c.end, c.subject_code, c.class_type, c.room,
                "  [1h%s]" % (", split" if c.is_split_piece else "") if c.is_half_session else "",
                "  [pref #%d]" % c.preference_rank if c.preference_rank else ""))
        return "\n".join(out)

    def render_all(self) -> str:
        return "\n\n".join(self.render_semester(s) for s in self.board.semesters)

    def summary(self) -> str:
        o = self.preference_report["_overall"]
        sched_h = sum(c.duration_hours for c in self.classes)
        total_h = sched_h + sum(u.get("duration_hours", 2.0) for u in self.unscheduled)
        lines = [
            "Classes scheduled : %d   unscheduled: %d" % (len(self.classes), len(self.unscheduled)),
            "Weekly teaching hours scheduled: %g of %g required" % (sched_h, total_h),
            "1-hour classes: %d (of which %d are halves of split 2-hour classes)" % (
                self.stats.get("half_session_classes", 0), self.stats.get("split_piece_classes", 0)),
            "Hard-constraint violations (independent check): %d" % len(self.violations),
            "Teacher preferences honoured: %d / %d achievable (%.1f%%)" % (o["honoured"], o["achievable"], o["percent"]),
            "Classes in extension slot: %d" % self.stats.get("extension_slot_classes", 0),
            "Runtime: %.2fs | restarts: %d | repairs: %d | split rounds: %d | SA iterations: %d | final soft cost: %s" % (
                self.stats.get("runtime_s", 0), self.stats.get("attempts", 0), self.stats.get("repairs", 0),
                self.stats.get("split_rounds", 0), self.stats.get("sa_iterations", 0), self.stats.get("final_cost")),
        ]
        for u in self.unscheduled:
            s_label = f"{u['subject']}: {u.get('subject_name')}" if u.get('subject_name') and u.get('subject_name') != u['subject'] else u['subject']
            t_label = u.get('teacher_label') or (f"{u.get('teacher_name')} ({u['teacher']})" if u.get('teacher_name') else u['teacher'])
            sem_label = u.get('semester_name') or u['semester']
            lines.append("  UNSCHEDULED %s [%s, %gh%s] (%s, Faculty: %s):" % (
                s_label, u.get("class_type", "theory").upper(), u.get("duration_hours", 2.0),
                ", split piece" if u.get("is_split_piece") else "", sem_label, t_label))
            lines.append("    Specific Slot Conflicts: %s" % u["reason"])
            if u.get("remedy"):
                lines.append("    Remedy: %s" % u["remedy"])
        for v in self.violations:
            lines.append("  VIOLATION: " + v)
        return "\n".join(lines)

    # ---- export ------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "classes": [c.__dict__ for c in self.classes],
            "unscheduled": self.unscheduled,
            "teacher_free_days": self.teacher_free_days,
            "preferences": self.preference_report,
            "stats": self.stats,
            "violations": self.violations,
            "issues": [{"level": i.level, "message": i.message} for i in self.issues],
            "complete": self.complete,
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["semester", "programme", "day", "slot", "start", "end", "subject_code", "subject_name",
                    "type", "teacher_id", "teacher", "room", "preference_rank", "extension_slot",
                    "duration_hours", "half_session", "split_piece"])
        for c in self.classes:
            w.writerow([c.semester_id, c.programme, c.day, c.slot_title, c.start, c.end, c.subject_code,
                        c.subject_name, c.class_type, c.teacher_id, c.teacher_name, c.room,
                        c.preference_rank or "", int(c.extension_slot),
                        c.duration_hours, int(c.is_half_session), int(c.is_split_piece)])
        return buf.getvalue()


def verify(board: SchedulingBoard, classes: Sequence[ScheduledClass]) -> List[str]:
    """Independent re-check of every hard constraint using only names / ids (not solver state)."""
    out: List[str] = []
    cfg = board.config
    days = list(cfg.working_days)
    seen_t, seen_r, seen_g = {}, {}, {}
    per_day: Dict[tuple, Counter] = {}
    used_days: Dict[str, Set[str]] = {t: set() for t in board.teachers}
    per_subject: Counter = Counter()
    subj_day: Set[tuple] = set()
    slots = {s.id: s for s in board.all_slots()}
    has_ext = any(s.tier > 0 for s in slots.values())
    for c in classes:
        sub, teacher, room = board.subjects[c.subject_code], board.teachers[c.teacher_id], board.rooms[c.room]
        key = (c.day, c.slot_id)
        for table, who, label in ((seen_t, c.teacher_id, "teacher"), (seen_r, c.room, "room"),
                                  (seen_g, c.semester_id, "semester")):
            if (who,) + key in table:
                out.append("%s %s double-booked on %s slot %d" % (label, who, c.day, c.slot_id))
            table[(who,) + key] = c
        cnt = per_day.setdefault((c.teacher_id, c.day), Counter())
        cnt[c.class_type] += 1
        if cnt[c.class_type] > 1:
            out.append("teacher %s has two %s classes on %s" % (c.teacher_id, c.class_type, c.day))
        used_days[c.teacher_id].add(c.day)
        if (c.day, c.slot_id) in teacher.unavailable or c.day in teacher.unavailable_days:
            out.append("teacher %s scheduled while unavailable (%s slot %d)" % (c.teacher_id, c.day, c.slot_id))
        need = sub.facility
        if c.preference_rank:
            need |= teacher.preferences[c.preference_rank - 1].facility
        if (room.facility & need) != need:
            out.append("room %s lacks %s for %s" % (c.room, need.text, c.subject_code))
        sem = board.semesters[c.semester_id]
        if sem.strength and room.capacity and room.capacity < sem.strength:
            out.append("room %s too small for %s" % (c.room, sem.id))
        if any(r.kind == "lab" for r in board.rooms.values()):
            want = "lab" if sub.class_type is ClassType.PRACTICAL else "classroom"
            if room.kind != want:
                out.append("%s in %s room %s but needs a %s" % (c.subject_code, room.kind, c.room, want))
        if (c.semester_id, c.subject_code, c.day) in subj_day:
            out.append("subject %s appears twice on %s for %s" % (c.subject_code, c.day, c.semester_id))
        subj_day.add((c.semester_id, c.subject_code, c.day))
        per_subject[(c.subject_code, c.teacher_id)] += 1
        if c.slot_id not in slots or c.day not in days:
            out.append("invalid day/slot for %s" % c.subject_code)
        else:
            sl = slots[c.slot_id]
            # duration rules: a 2-hour class only in a regular slot, a 1-hour class in the 1-hour extension slot
            if not c.is_half_session and sl.tier > 0:
                out.append("full-length class %s placed in the %d-minute extension slot (%s %s)" % (
                    c.subject_code, sl.duration, c.day, sl.start))
            if c.is_half_session and sl.tier == 0 and has_ext and not cfg.allow_short_in_regular_slot:
                out.append("1-hour class %s placed in a regular slot (%s %s) although not allowed" % (
                    c.subject_code, c.day, sl.start))
            if not c.is_half_session and sl.duration < cfg.full_session_minutes:
                out.append("full-length class %s placed in a %d-minute slot" % (c.subject_code, sl.duration))
    for tid, t in board.teachers.items():
        if t.internal and len(used_days[tid]) > len(days) - 1:
            out.append("internal teacher %s has no free day" % tid)
    return out


# =====================================================================================
# 7. DEMO DATA + CLI
# =====================================================================================


def build_demo_board(config: Optional[SolverConfig] = None) -> SchedulingBoard:
    b = SchedulingBoard(config)
    for sid, prog, num in (("BT-3", "B.Tech", 3), ("BT-5", "B.Tech", 5), ("MT-1", "M.Tech", 1), ("MSC-2", "M.Sc.", 2)):
        b.add_semester(sid, prog, num)
    internal = [
        ("I01", "Dr. A. Rao", [("Mon", 1, "both"), ("Wed", 2, "projector"), ("Fri", 1, "blackboard")]),
        ("I02", "Dr. B. Iyer", [("Tue", 1, "blackboard"), ("Thu", 3, "both"), ("Mon", 2, "projector")]),
        ("I03", "Prof. C. Das", [("Mon", 1, "blackboard"), ("Tue", 2, "blackboard"), ("Thu", 1, "blackboard")]),
        ("I04", "Dr. D. Menon", [("Wed", 1, "projector"), ("Thu", 2, "both"), ("Fri", 3, "projector")]),
        ("I05", "Dr. E. Khan", [("Mon", 3, "both"), ("Tue", 3, "projector"), ("Wed", 3, "both")]),
        ("I06", "Prof. F. Nair", [("Tue", 1, "both"), ("Wed", 1, "blackboard"), ("Fri", 2, "both")]),
        ("I07", "Dr. G. Sen", [("Mon", 2, "blackboard"), ("Thu", 1, "blackboard"), ("Fri", 1, "projector")]),
        ("I08", "Dr. H. Joshi", [("Tue", 2, "projector"), ("Wed", 2, "both"), ("Thu", 3, "projector")]),
        ("I09", "Prof. I. Roy", [("Mon", 1, "both"), ("Tue", 1, "both"), ("Wed", 1, "both")]),
        ("I10", "Dr. J. Verma", [("Thu", 2, "blackboard"), ("Fri", 2, "blackboard"), ("Mon", 3, "blackboard")]),
    ]
    for tid, name, prefs in internal:
        b.add_teacher(tid, name, True, prefs, unavailable_days=("Wed",) if tid == "I10" else ())
    b.add_teacher("E01", "Mr. K. Patel (ext)", False, [("Tue", 1, "projector"), ("Wed", 1, "projector"), ("Thu", 1, "both")],
                  unavailable=[("Fri", 3)])
    b.add_teacher("E02", "Ms. L. Gupta (ext)", False, [("Tue", 2, "both"), ("Thu", 2, "both"), ("Wed", 2, "both")],
                  unavailable_days=("Mon",))
    b.add_teacher("E03", "Dr. M. Shah (ext)", False, [("Fri", 1, "projector"), ("Fri", 2, "projector"), ("Thu", 3, "projector")])
    b.add_teacher("E04", "Mr. N. Bose (ext)", False, [("Mon", 2, "blackboard"), ("Wed", 3, "blackboard"), ("Tue", 3, "blackboard")])
    for num, fac, kind in (("101", "blackboard", "classroom"), ("102", "both", "classroom"),
                           ("103", "projector", "classroom"), ("104", "both", "classroom"),
                           ("105", "blackboard", "classroom"), ("106", "both", "classroom"),
                           ("LAB-1", "both", "lab"), ("LAB-2", "blackboard", "lab")):
        b.add_room(num, fac, kind)
    theory = [
        ("CS301", "Data Structures", "BT-3", "I01", "E01"), ("CS302", "Database Systems", "BT-3", "I02", "E02"),
        ("MA301", "Discrete Mathematics", "BT-3", "I03", None), ("CS303", "Digital Logic", "BT-3", "I04", "E03"),
        ("CS501", "Operating Systems", "BT-5", "I05", "E01"), ("CS502", "Computer Networks", "BT-5", "I06", "E02"),
        ("CS503", "Compiler Design", "BT-5", "I07", None), ("CS504", "Machine Learning", "BT-5", "I08", "E04"),
        ("CS601", "Advanced Algorithms", "MT-1", "I01", "E01"), ("CS602", "Distributed Systems", "MT-1", "I02", "E04"),
        ("CS603", "Research Methodology", "MT-1", "I09", None), ("CS604", "Cloud Computing", "MT-1", "I10", "E03"),
        ("MS201", "Linear Algebra", "MSC-2", "I04", "E02"), ("MS202", "Statistics", "MSC-2", "I05", "E03"),
        ("MS203", "Numerical Methods", "MSC-2", "I06", "E01"), ("MS204", "Optimisation", "MSC-2", "I08", "E04"),
    ]
    # user-defined sessions per week (default 3); 2.5 = two 2-hour classes + one 1-hour class (16:30-17:30)
    sessions = {"MA301": 2.5, "CS503": 2.5, "CS603": 2.5, "MS202": 2.5}
    for code, name, sem, i, e in theory:
        b.add_subject(code, name, sem, i, e, "theory", sessions_per_week=sessions.get(code, 3),
                      external_sessions=1 if e else 0,
                      facility="blackboard" if code.startswith(("MA", "MS")) else "none")
    labs = [("CS351", "DS Lab", "BT-3", "I01", 1), ("CS352", "DBMS Lab", "BT-3", "I02", 1),
            ("CS551", "OS Lab", "BT-5", "I05", 1), ("CS552", "ML Lab", "BT-5", "I08", 1),
            ("CS651", "Algorithms Lab", "MT-1", "I03", 2), ("CS652", "Cloud Lab", "MT-1", "I10", 2),
            ("MS251", "Computing Lab", "MSC-2", "I04", 1)]
    for code, name, sem, i, n in labs:
        b.add_subject(code, name, sem, i, None, "practical", sessions_per_week=n)
    return b


def build_board_from_db(db, config: Optional[SolverConfig] = None) -> SchedulingBoard:
    """Build a SchedulingBoard using the institution catalog from database,
    with graceful fallback to demo board if DB catalog is incomplete."""
    from app.models.user import User  # noqa: F401
    from app.models.refresh_token import RefreshToken  # noqa: F401
    from app.models.timetable import TimetableVersion  # noqa: F401
    from app.models.timetable_entry import TimetableEntry  # noqa: F401
    from app.models.audit import AuditLog  # noqa: F401
    from app.models.program import Program
    from app.models.subject import Subject
    from app.models.teacher import Teacher
    from app.models.busy_slot import TeacherBusySlot
    from app.scheduler.constraints import ROOM_FACILITIES, INTERNAL_TEACHERS

    b = SchedulingBoard(config or SolverConfig())

    db_teachers = db.query(Teacher).all()
    db_subjects = db.query(Subject).all()
    db_programs = db.query(Program).filter_by(is_active=True).all()

    if not db_teachers or not db_subjects:
        return build_demo_board(config)

    # 1. Rooms
    for r_num, fac in ROOM_FACILITIES.items():
        facility_str = "both" if fac.get("blackboard") and fac.get("projector") else ("projector" if fac.get("projector") else "blackboard")
        kind_str = "lab" if fac.get("lab") else "classroom"
        b.add_room(r_num, facility=facility_str, kind=kind_str)

    # 2. Busy slots
    busy_map: Dict[str, Set[str]] = {}
    for bs in db.query(TeacherBusySlot).filter_by(scope="permanent").all():
        if bs.teacher_short_name and bs.day_of_week:
            busy_map.setdefault(bs.teacher_short_name, set()).add(bs.day_of_week[:3])

    # 3. Teachers
    added_teachers = set()
    for t in db_teachers:
        short = (t.short_name or "").strip()
        if not short or short in added_teachers:
            continue
        unavail_days = tuple(busy_map.get(short, ()))
        is_int = bool(t.is_internal or short in INTERNAL_TEACHERS)
        b.add_teacher(
            id=short,
            name=t.full_name or short,
            internal=is_int,
            preferences=(),
            unavailable=(),
            unavailable_days=unavail_days,
        )
        added_teachers.add(short)

    if not added_teachers:
        return build_demo_board(config)

    # 4. Semesters and Subjects
    added_sems = set()
    added_subjs = set()
    for s in db_subjects:
        prog = (s.program or "").strip()
        sem_str = (s.semester or "").strip()
        if not prog or not sem_str:
            continue
        sem_digits = "".join(filter(str.isdigit, sem_str)) or "1"
        sem_num = int(sem_digits)
        prog_clean = prog.replace(".", "").strip()
        sem_id = f"{prog_clean[:3].upper()}-{sem_num}"

        if sem_id not in added_sems:
            b.add_semester(sem_id, prog, sem_num, name=f"{prog} {sem_str}")
            added_sems.add(sem_id)

        code = (s.code or "").strip()
        if not code or code in added_subjs:
            continue

        # Find teacher for subject
        matched_tid = None
        code_lower = code.lower()
        for t in db_teachers:
            t_short = (t.short_name or "").strip()
            if not t_short or t_short not in added_teachers:
                continue
            subjects_list = [x.strip().lower() for x in (t.subjects_csv or "").split(",") if x.strip()]
            if code_lower in subjects_list:
                matched_tid = t_short
                break

        if not matched_tid:
            matched_tid = next((tid for tid, obj in b.teachers.items() if obj.internal), list(added_teachers)[0])

        is_lab = "practical" in (s.entry_type or "").lower() or code_lower.endswith("-p")
        # sessions per week: a user-defined value on the subject wins (any multiple of 0.5, e.g. 2.5);
        # otherwise derive it from weekly hours (2 hours = 1 session, 1 hour = 0.5 session).
        custom = getattr(s, "sessions_per_week", None)
        if custom:
            n_sessions = float(custom)
        elif is_lab:
            n_sessions = 1.0
        elif s.weekly_hours:
            n_sessions = max(0.5, min(3.0, round(float(s.weekly_hours)) / 2.0))
        else:
            n_sessions = 3.0
        b.add_subject(
            code=code,
            name=s.name or code,
            semester_id=sem_id,
            internal_teacher_id=matched_tid,
            class_type="practical" if is_lab else "theory",
            sessions_per_week=n_sessions,
        )
        added_subjs.add(code)

    if not added_subjs or not added_sems:
        return build_demo_board(config)

    return b


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Automated timetable generator (demo)")
    ap.add_argument("--time", type=float, default=6.0, help="time budget in seconds (default 6)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", metavar="FILE", help="write result as JSON")
    ap.add_argument("--csv", metavar="FILE", help="write result as CSV")
    ap.add_argument("--teachers", action="store_true", help="also print teacher timetables")
    ap.add_argument("--no-split", action="store_true",
                    help="never split a 2-hour class into two 1-hour classes on different days")
    ap.add_argument("--short-in-regular", action="store_true",
                    help="allow a 1-hour class to occupy a whole regular slot as a last resort")
    args = ap.parse_args(argv)
    board = build_demo_board(SolverConfig(time_limit_s=args.time, seed=args.seed,
                                          allow_split_sessions=not args.no_split,
                                          allow_short_in_regular_slot=args.short_in_regular))
    for issue in board.validate():
        print(issue)
    result = board.generate()
    print(result.render_all())
    if args.teachers:
        print()
        for tid in board.teachers:
            print(result.render_teacher(tid))
    print()
    print(result.summary())
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(result.to_json())
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            f.write(result.to_csv())
    return 0 if result.complete and not result.violations else 1


if __name__ == "__main__":
    sys.exit(main())
