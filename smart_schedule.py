"""Smart Auto-Schedule endpoints.

  POST /smart-schedule            -> unchanged (find best conflict-free time + room)
  POST /smart-schedule/advanced   -> REWRITTEN ("Analyzing Advanced Options...")

WHY THE OLD /advanced WAS SLOW (5-6 minutes)
  For every class in the semester x every candidate move slot it did:
    * copy.deepcopy(whole schedule)
    * validate_schedule() + validate_schema() on the BASE and on the CANDIDATE
      (the base was re-validated every single time), each O(n^2)
    * every validate_schedule() call opened a brand-new DB session to Supabase
      (fetch_global_busy_days) -> thousands of network round-trips
    * one extra TeacherBusySlot query per candidate class
    * a fresh index build + a second full validation for every hit.

WHAT THE NEW /advanced DOES
  * 2 DB queries per request (latest version + ALL permanent busy slots), nothing else.
  * One in-memory "board" of the timetable with per-resource occupancy lists
    (semester group / teacher / room, per day) and per-teacher counters.
    A class can be detached / attached in O(1)-ish, so "what if X moves?" is a few
    dictionary look-ups instead of a full re-validation.
  * Same rules as validate_schedule(): H1 semester clash, H2 teacher clash,
    H3 room clash, H5/H5b one theory + one practical per teacher per day,
    H6 internal teacher keeps a free day (only if not already broken), H12 busy days.
    (H11 / schema cannot change when a class only moves in time, so they are not re-checked.)
  * Search is "goal driven": for each class A of the same program+semester we first ask
    "which windows for the NEW class become free if A disappears?".  If none -> A is skipped
    immediately (this prunes most classes).  Only then do we look for a home for A.
  * Proposals are ranked (preferred day -> least disruption -> earliest), moves first,
    cancels only fill what is left.  Hard time budget so the request can never hang.
  * Tiny TTL cache keyed on (timetable version, busy-slots, request).

Everything else (request/response shapes, MOVE_CLASS / ADD_CLASS / REMOVE_CLASS payloads,
max 5 proposals, same-program/semester scope) is unchanged.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, defaultdict
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_teacher_or_admin
from app.models.user import User
from app.models.timetable import TimetableVersion
from app.models.busy_slot import TeacherBusySlot
from app.actions.suggestions import (
    suggest_free_slots_for_teacher,
    suggest_free_rooms,
    _fingerprint,
)
from app.actions.engine import ActionEngine
from app.actions.types import parse_action
from app.scheduler.validator import validate_schedule, to_minutes, teacher_set
import app.scheduler.constraints as C

router = APIRouter()


# ═════════════════════════════════════════════════════════════════════════════
# 1) NORMAL SMART SCHEDULE  (unchanged)
# ═════════════════════════════════════════════════════════════════════════════

class SmartScheduleRequest(BaseModel):
    program: str
    semester: str
    subject_code: str
    subject_name: str
    teacher: str
    entry_type: str = "Theory"           # "Theory" | "Practical"
    room: Optional[str] = None           # leave blank to auto-assign
    preferred_day: Optional[str] = None  # optional day preference
    auto_execute: bool = False           # if True, immediately book the FIRST slot (legacy)


class NormalProposal(BaseModel):
    day: str
    start: str
    end: str
    room: str
    proposed_action: dict
    constraint_issue: Optional[str] = None  # If present, this is an imperfect slot


class SmartScheduleResponse(BaseModel):
    found: bool
    is_perfect_match: bool = True
    proposals: List[NormalProposal] = []
    message: str = ""
    # Legacy fields (will map to the first proposal for backwards compatibility)
    proposed_action: Optional[dict] = None
    day: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    room: Optional[str] = None
    new_version_id: Optional[int] = None


@router.post("", response_model=SmartScheduleResponse)
def smart_schedule(
    req: SmartScheduleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    # ── Load current schedule ─────────────────────────────────────────────
    v = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
    schedule: List[dict] = [e.to_dict() for e in v.entries] if v else []
    base_v = validate_schedule(schedule)
    base_fps = { _fingerprint(x) for x in base_v }

    # ── Early Validation: Ensure Subject exists for this Program/Sem ────────
    prog_raw = req.program.strip().rstrip('.')
    sem_raw = req.semester.strip()
    subj_code = req.subject_code.strip().lower()
    allowed = C.PROGRAMME_SUBJECT_MAP.get((prog_raw, sem_raw), set())
    if not allowed or (subj_code not in allowed and subj_code[:-2] not in allowed):
        if not (subj_code in ("m", "mm") and ("m" in allowed or "mm" in allowed)):
            return SmartScheduleResponse(
                found=False,
                message=(
                    f"Subject '{req.subject_code}' / '{req.subject_name}' is not recognized "
                    f"for {req.program} {req.semester}. Please add it to the Catalog first."
                )
            )

    # ── Collect permanent busy days for teacher ───────────────────────────
    busy_records = db.query(TeacherBusySlot).filter(
        TeacherBusySlot.teacher_short_name == req.teacher,
    ).all()
    today = date.today()
    busy_days: set[str] = set()
    for b in busy_records:
        if b.scope == "permanent" and b.day_of_week:
            busy_days.add(b.day_of_week)
        elif b.scope == "temporary" and b.specific_date == today:
            pass

    # ── Find free slots for the teacher ──────────────────────────────────
    duration = 180 if req.entry_type == "Practical" else 120
    all_free_slots = suggest_free_slots_for_teacher(schedule, req.teacher, duration, busy_days=busy_days)

    # Preferred-day: search that day FIRST, fall back to all days if needed
    preferred_day_only_slots = (
        [s for s in all_free_slots if s["day"] == req.preferred_day]
        if req.preferred_day else []
    )
    free_slots = preferred_day_only_slots or all_free_slots
    expanded_to_all_days = bool(req.preferred_day and not preferred_day_only_slots)

    need_lab = req.entry_type == "Practical"

    perfect_options: List[NormalProposal] = []
    imperfect_options: List[NormalProposal] = []

    # --- Pass 1: search within free_slots (may be restricted to preferred day) ---
    for slot in free_slots:
        if len(perfect_options) >= 3:
            break

        candidate_entry = {
            "day": slot["day"], "start": slot["start"], "end": slot["end"],
            "program": req.program, "semester": req.semester,
            "subject_code": req.subject_code, "subject_name": req.subject_name,
            "teacher": req.teacher, "type": req.entry_type,
            "room": req.room or "",
        }
        rooms_to_try = ([req.room] if req.room else []) + suggest_free_rooms(
            schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab
        )

        # Try the provided room or the first available room to avoid span
        if rooms_to_try:
            room = rooms_to_try[0]
            trial_entry = {**candidate_entry, "room": room}
            candidate_schedule = schedule + [trial_entry]

            cand_v = validate_schedule(candidate_schedule)
            new_violations = [item for item in cand_v if _fingerprint(item) not in base_fps]

            proposed = {
                "action": "ADD_CLASS",
                "spec": {
                    "program": req.program, "semester": req.semester,
                    "day": slot["day"], "start_time": slot["start"], "end_time": slot["end"],
                    "subject_code": req.subject_code, "subject_name": req.subject_name,
                    "teacher": req.teacher, "entry_type": req.entry_type,
                    "room": room,
                },
            }

            if len(new_violations) == 0:
                perfect_options.append(NormalProposal(
                    day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                    proposed_action=proposed
                ))
            else:
                # Check for hard physical conflicts. If H1, H2, H3 exist, this slot is physically impossible.
                hard_rules = {"H1_semester_clash", "H2_teacher_clash", "H3_room_clash", "H12_teacher_busy"}
                if not any(v.get("rule") in hard_rules for v in new_violations):
                    issue_msgs = []
                    for v in new_violations:
                        rule = v.get("rule", "")
                        note = v.get("note")
                        if note:
                            issue_msgs.append(note)
                        elif rule == "H5_multiple_theory_same_day":
                            issue_msgs.append("Exceeds 1 theory class per day for teacher.")
                        elif rule == "H5_multiple_practical_same_day":
                            issue_msgs.append("Exceeds 1 practical class per day for teacher.")
                        elif rule == "H6_no_free_day":
                            issue_msgs.append("Teacher would have no free days.")
                        elif rule == "H11_wrong_semester_subject":
                            issue_msgs.append("Subject is not officially listed for this semester.")
                        else:
                            issue_msgs.append(f"Soft constraint violation: {rule}")

                    imperfect_options.append(NormalProposal(
                        day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                        proposed_action=proposed,
                        constraint_issue=" | ".join(issue_msgs)
                    ))

    final_proposals = perfect_options if perfect_options else imperfect_options[:3]
    is_perfect = len(perfect_options) > 0

    # --- Pass 2: if preferred-day search found nothing, expand to all days ---
    if not final_proposals and preferred_day_only_slots and all_free_slots:
        # Re-run search over all remaining days (excluding preferred, already tried)
        other_slots = [s for s in all_free_slots if s["day"] != req.preferred_day]
        perfect_options2: List[NormalProposal] = []
        imperfect_options2: List[NormalProposal] = []
        for slot in other_slots:
            if len(perfect_options2) >= 3:
                break
            candidate_entry = {
                "day": slot["day"], "start": slot["start"], "end": slot["end"],
                "program": req.program, "semester": req.semester,
                "subject_code": req.subject_code, "subject_name": req.subject_name,
                "teacher": req.teacher, "type": req.entry_type,
                "room": req.room or "",
            }
            rooms_to_try = ([req.room] if req.room else []) + suggest_free_rooms(
                schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab
            )
            if rooms_to_try:
                room = rooms_to_try[0]
                trial_entry = {**candidate_entry, "room": room}
                candidate_schedule = schedule + [trial_entry]
                cand_v = validate_schedule(candidate_schedule)
                new_violations = [item for item in cand_v if _fingerprint(item) not in base_fps]
                proposed = {
                    "action": "ADD_CLASS",
                    "spec": {
                        "program": req.program, "semester": req.semester,
                        "day": slot["day"], "start_time": slot["start"], "end_time": slot["end"],
                        "subject_code": req.subject_code, "subject_name": req.subject_name,
                        "teacher": req.teacher, "entry_type": req.entry_type,
                        "room": room,
                    },
                }
                fallback_note = f"Preferred day ({req.preferred_day}) unavailable — expanded search."
                if len(new_violations) == 0:
                    perfect_options2.append(NormalProposal(
                        day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                        proposed_action=proposed,
                        constraint_issue=fallback_note,
                    ))
                else:
                    hard_rules = {"H1_semester_clash", "H2_teacher_clash", "H3_room_clash", "H12_teacher_busy"}
                    if not any(v.get("rule") in hard_rules for v in new_violations):
                        imperfect_options2.append(NormalProposal(
                            day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                            proposed_action=proposed,
                            constraint_issue=fallback_note,
                        ))
        final_proposals = perfect_options2 if perfect_options2 else imperfect_options2[:3]
        is_perfect = False  # Expanded fallback — not a perfect preferred-day match

    if not final_proposals and expanded_to_all_days:
        # All-days search: teacher fully booked everywhere
        return SmartScheduleResponse(
            found=False,
            message=(
                f"No slots found for {req.teacher} on {req.preferred_day} or any other day. "
                f"Teacher may be fully booked or blocked by hard constraints."
            ),
        )

    if not final_proposals:
        return SmartScheduleResponse(
            found=False,
            message=(
                f"No slots found for {req.teacher} "
                f"({'any day' if not req.preferred_day else req.preferred_day}). "
                f"Teacher may be fully booked or blocked by hard constraints."
            ),
        )

    # Legacy auto-execute support
    if req.auto_execute and is_perfect:
        first_prop = final_proposals[0].proposed_action
        try:
            action = parse_action(first_prop)
            engine = ActionEngine(db=db, user=user)
            result = engine.execute([action])
            if result.success:
                return SmartScheduleResponse(
                    found=True, is_perfect_match=True, proposals=final_proposals,
                    proposed_action=first_prop, day=final_proposals[0].day,
                    start=final_proposals[0].start, end=final_proposals[0].end, room=final_proposals[0].room,
                    new_version_id=result.new_version_id,
                    message=f"Booked! {final_proposals[0].day} {final_proposals[0].start}–{final_proposals[0].end} in {final_proposals[0].room}. Version #{result.new_version_id}.",
                )
        except Exception:
            pass

    return SmartScheduleResponse(
        found=True,
        is_perfect_match=is_perfect,
        proposals=final_proposals,
        proposed_action=final_proposals[0].proposed_action,
        day=final_proposals[0].day, start=final_proposals[0].start, end=final_proposals[0].end, room=final_proposals[0].room,
        message="Found options" if is_perfect else "Found closest feasible options (with constraint warnings)."
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2) ADVANCED SMART SCHEDULE  (rewritten for speed)
# ═════════════════════════════════════════════════════════════════════════════

class AdvancedSmartScheduleRequest(BaseModel):
    program: str
    semester: str
    subject_code: str
    subject_name: str
    teacher: str
    entry_type: str = "Theory"
    room: Optional[str] = None
    preferred_day: Optional[str] = None


class ProposedModification(BaseModel):
    description: str
    modification_type: str            # "none" | "move" | "cancel"
    affected_class: dict
    new_slot: Optional[dict] = None
    priority_slot: dict
    actions_to_apply: List[dict]


class AdvancedSmartScheduleResponse(BaseModel):
    found: bool
    proposals: List[ProposedModification] = []
    message: str = ""
    # diagnostics (new, optional – safe for existing clients to ignore)
    elapsed_ms: Optional[float] = None
    candidates_evaluated: int = 0


# ── Tunables ─────────────────────────────────────────────────────────────────
_DAY_START = 9 * 60                      # earliest start considered   (09:00)
_DAY_END = 17 * 60 + 30                  # latest end considered       (17:30)
_STEP = 30                               # search granularity (minutes)
_GRID_STARTS = frozenset({10 * 60, 12 * 60, 14 * 60 + 30, 16 * 60 + 30})  # usual period starts
_MAX_PROPOSALS = 5                       # same cap as before (incl. the "no change" option)
_MAX_FREED_TRIES = 12                    # per displaced class: how many freed windows to try
_TIME_BUDGET_S = 10.0                    # hard stop for the search
_CACHE_TTL_S = 90.0
_CACHE_MAX = 128


# ── Small helpers ────────────────────────────────────────────────────────────
def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _sem_key(e: dict) -> Tuple[str, str]:
    """Identity used by validator H1 (program/semester group)."""
    return (str(e.get("program", "")).strip().rstrip(".").lower(),
            str(e.get("semester", "")).strip().lower())


class _Row:
    """One class occupying (day, s..e minutes) with its resources."""
    __slots__ = ("entry", "day", "s", "e", "sem", "teachers", "room", "typ", "res")

    def __init__(self, entry: dict, day: str, s: int, e: int, room: str):
        self.entry = entry
        self.day = day
        self.s = s
        self.e = e
        self.room = room or ""
        self.sem = _sem_key(entry)
        self.teachers = tuple(sorted(teacher_set(entry)))
        self.typ = str(entry.get("type", "")).strip()
        res = [(0, self.sem)]                       # kind 0 = semester group
        res.extend((1, t) for t in self.teachers)   # kind 1 = teacher
        if self.room:
            res.append((2, self.room))              # kind 2 = room
        self.res = tuple(res)


def _row_from_entry(entry: dict) -> _Row:
    try:
        s, e = to_minutes(entry["start"]), to_minutes(entry["end"])
    except Exception:
        s = e = 0
    return _Row(entry, entry.get("day"), s, e, entry.get("room") or "")


class _Spec:
    """What we need to know to place a class somewhere."""
    __slots__ = ("sem", "teachers", "typ", "dur", "need_lab", "prefer_room")

    def __init__(self, sem, teachers, typ, dur, need_lab, prefer_room):
        self.sem, self.teachers, self.typ = sem, teachers, typ
        self.dur, self.need_lab, self.prefer_room = dur, need_lab, prefer_room


class _Board:
    """In-memory timetable with O(1) attach/detach and cheap conflict queries."""

    def __init__(self, schedule: List[dict], busy: Dict[str, Set[str]]):
        self.busy = busy
        self.internal = set(C.INTERNAL_TEACHERS)
        self.wd = list(C.WORKING_DAYS)
        self.wd_set = set(self.wd)
        self.by_res: Dict[tuple, List[_Row]] = defaultdict(list)
        self.tt: Dict[tuple, int] = defaultdict(int)                   # (teacher, day, type) -> n
        self.tdays: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        facilities = C.ROOM_FACILITIES
        self.all_rooms = sorted(facilities)
        self.lab_rooms = [r for r in self.all_rooms if facilities[r].get("lab")]
        self.rows: List[_Row] = [_row_from_entry(e) for e in schedule]
        for r in self.rows:
            self.attach(r)
        # H6 already broken in the base schedule -> must not be reported as "new"
        self.base_full: Set[str] = set()
        for t in self.internal:
            days = self.tdays.get(t)
            if days and sum(1 for c in days.values() if c > 0) == len(self.wd_set):
                self.base_full.add(t)

    # ── mutation ────────────────────────────────────────────────────────────
    def attach(self, r: _Row) -> None:
        for k in r.res:
            self.by_res[(k[0], k[1], r.day)].append(r)
        for t in r.teachers:
            self.tt[(t, r.day, r.typ)] += 1
            if r.day in self.wd_set:
                self.tdays[t][r.day] += 1

    def detach(self, r: _Row) -> None:
        for k in r.res:
            self.by_res[(k[0], k[1], r.day)].remove(r)
        for t in r.teachers:
            self.tt[(t, r.day, r.typ)] -= 1
            if r.day in self.wd_set:
                self.tdays[t][r.day] -= 1

    # ── queries ─────────────────────────────────────────────────────────────
    def day_ok(self, teachers: tuple, typ: str, day: str) -> bool:
        """Slot-independent rules for putting a class on `day`: H12, H5/H5b, H6."""
        check_type = typ in ("Theory", "Practical")
        for t in teachers:
            bd = self.busy.get(t)
            if bd and day in bd:                                        # H12
                return False
            if check_type and self.tt.get((t, day, typ), 0) >= 1:       # H5 / H5b
                return False
        if day in self.wd_set:                                          # H6 (only if newly broken)
            need = len(self.wd_set)
            for t in teachers:
                if t in self.internal and t not in self.base_full:
                    days = self.tdays.get(t)
                    if days:
                        other = sum(1 for d, c in days.items() if c > 0 and d != day)
                        if other + 1 >= need:
                            return False
        return True

    def window_ok(self, day: str, s: int, e: int, sem: tuple, teachers: tuple) -> bool:
        """H1 (semester group) and H2 (teacher) overlap checks."""
        get = self.by_res.get
        for r in get((0, sem, day), ()):
            if r.s < e and s < r.e:
                return False
        for t in teachers:
            for r in get((1, t, day), ()):
                if r.s < e and s < r.e:
                    return False
        return True

    def room_free(self, room: str, day: str, s: int, e: int) -> bool:     # H3
        for r in self.by_res.get((2, room, day), ()):
            if r.s < e and s < r.e:
                return False
        return True

    def pick_room(self, day: str, s: int, e: int, need_lab: bool, prefer: str) -> Optional[str]:
        """Preferred room if free, otherwise first free compatible room. None = no room at all."""
        if prefer and self.room_free(prefer, day, s, e):
            return prefer
        pool = self.lab_rooms if need_lab else self.all_rooms
        for room in pool:
            if room != prefer and self.room_free(room, day, s, e):
                return room
        return "" if (not pool and not prefer) else None


def _windows(board: _Board, dur: int):
    if dur <= 0:
        return
    for day in board.wd:
        for s in range(_DAY_START, _DAY_END - dur + 1, _STEP):
            yield day, s


def _valid_windows(board: _Board, sp: _Spec) -> List[Tuple[str, int, str]]:
    """Every (day, start, room) where the spec fits with ZERO new violations."""
    out: List[Tuple[str, int, str]] = []
    day_cache: Dict[str, bool] = {}
    for day, s in _windows(board, sp.dur):
        ok = day_cache.get(day)
        if ok is None:
            ok = day_cache[day] = board.day_ok(sp.teachers, sp.typ, day)
        if not ok:
            continue
        e = s + sp.dur
        if not board.window_ok(day, s, e, sp.sem, sp.teachers):
            continue
        room = board.pick_room(day, s, e, sp.need_lab, sp.prefer_room)
        if room is None:
            continue
        out.append((day, s, room))
    return out


def _best_move(board: _Board, a: _Row) -> Optional[Tuple[int, str, int, str]]:
    """Least-disruptive new home for detached class `a` -> (cost, day, start, room)."""
    dur = a.e - a.s
    if dur <= 0:
        return None
    need_lab = a.typ == "Practical"
    day_idx = {d: i for i, d in enumerate(board.wd)}
    day_cache: Dict[str, bool] = {}
    best: Optional[tuple] = None
    for day, s in _windows(board, dur):
        if day == a.day and s == a.s:
            continue
        ok = day_cache.get(day)
        if ok is None:
            ok = day_cache[day] = board.day_ok(a.teachers, a.typ, day)
        if not ok:
            continue
        e = s + dur
        if not board.window_ok(day, s, e, a.sem, a.teachers):
            continue
        room = board.pick_room(day, s, e, need_lab, a.room)
        if room is None:
            continue
        cost = ((0 if day == a.day else 600)
                + abs(s - a.s)
                + (0 if (not a.room or room == a.room) else 30)
                + (0 if s in _GRID_STARTS else 90))
        key = (cost, day_idx.get(day, 99), s)
        if best is None or key < best[0]:
            best = (key, day, room)
    if best is None:
        return None
    (cost, _di, s), day, room = best
    return cost, day, s, room


def _same_subject(entry: dict, req: AdvancedSmartScheduleRequest) -> bool:
    """Never propose 'cancel/move class X to add class X'."""
    want = {req.subject_code.strip().lower(), req.subject_name.strip().lower()} - {""}
    have = {str(entry.get("subject_code") or "").strip().lower(),
            str(entry.get("subject_name") or "").strip().lower()} - {""}
    return bool(want & have)


def _subject_known(req) -> bool:
    prog_raw = req.program.strip().rstrip('.')
    sem_raw = req.semester.strip()
    subj_code = req.subject_code.strip().lower()
    allowed = C.PROGRAMME_SUBJECT_MAP.get((prog_raw, sem_raw), set())
    if not allowed:
        return False
    if subj_code in allowed or subj_code[:-2] in allowed:
        return True
    return subj_code in ("m", "mm") and ("m" in allowed or "mm" in allowed)


def _load_busy_map(db: Session) -> Dict[str, Set[str]]:
    """ONE query for every teacher's permanent busy days (same data H12 uses)."""
    busy: Dict[str, Set[str]] = {}
    for b in db.query(TeacherBusySlot).filter(TeacherBusySlot.scope == "permanent").all():
        if b.day_of_week:
            busy.setdefault(b.teacher_short_name, set()).add(b.day_of_week)
    return busy


# ── Proposal builders (payloads identical to the previous implementation) ─────
def _target_of(entry: dict) -> dict:
    return {
        "day": entry.get("day"), "start_time": entry.get("start"), "end_time": entry.get("end"),
        "program": entry.get("program"), "semester": entry.get("semester"),
        "subject_code": entry.get("subject_code") or entry.get("subject"),
    }


def _affected_of(entry: dict) -> dict:
    return {
        "subject": entry.get("subject_name") or entry.get("subject_code", ""),
        "day": entry.get("day", ""), "start": entry.get("start", ""), "end": entry.get("end", ""),
        "teacher": entry.get("teacher", ""),
        "program": entry.get("program"), "semester": entry.get("semester"),
    }


def _add_action(req, slot: dict) -> dict:
    return {
        "action": "ADD_CLASS",
        "spec": {
            "program": req.program, "semester": req.semester,
            "day": slot["day"], "start_time": slot["start"], "end_time": slot["end"],
            "subject_code": req.subject_code, "subject_name": req.subject_name,
            "teacher": req.teacher, "entry_type": req.entry_type,
            "room": slot["room"],
        },
    }


def _slot(day: str, s: int, dur: int, room: str) -> dict:
    return {"day": day, "start": _hhmm(s), "end": _hhmm(s + dur), "room": room}


def _room_note(req, room: str) -> str:
    want = (req.room or "").strip()
    return f" (requested room {want} is unavailable)" if want and room != want else ""


# ── Core search (pure function: easy to test, no DB) ──────────────────────────
def _advanced_core(
    req: AdvancedSmartScheduleRequest,
    schedule: List[dict],
    busy_map: Dict[str, Set[str]],
) -> AdvancedSmartScheduleResponse:
    t0 = time.perf_counter()
    deadline = t0 + _TIME_BUDGET_S
    board = _Board(schedule, busy_map)
    day_idx = {d: i for i, d in enumerate(board.wd)}
    pref = req.preferred_day or None

    practical = req.entry_type == "Practical"
    dur = 180 if practical else 120
    n_entry = {
        "day": "", "start": "", "end": "", "program": req.program, "semester": req.semester,
        "subject_code": req.subject_code, "subject_name": req.subject_name,
        "teacher": req.teacher, "type": req.entry_type, "room": "",
    }
    spec = _Spec(
        sem=_sem_key(n_entry),
        teachers=tuple(sorted(teacher_set({"teacher": req.teacher}))),
        typ=req.entry_type, dur=dur, need_lab=practical,
        prefer_room=(req.room or "").strip(),
    )

    def n_rank(day: str, s: int) -> tuple:
        return (0 if (not pref or day == pref) else 1, day_idx.get(day, 99), s)

    proposals: List[ProposedModification] = []

    # ── Step 1: free slot without touching anything ─────────────────────────
    orig_windows = _valid_windows(board, spec)
    orig_keys = {(d, s) for d, s, _ in orig_windows}
    if orig_windows:
        d, s, room = min(orig_windows, key=lambda w: n_rank(w[0], w[1]))
        slot = _slot(d, s, dur, room)
        proposals.append(ProposedModification(
            description=(
                f"No modifications needed. Best free slot available: "
                f"{slot['day']} {slot['start']}–{slot['end']} in {slot['room']}."
                f"{_room_note(req, room)}"
            ),
            modification_type="none",
            affected_class={},
            new_slot=slot,
            priority_slot=slot,
            actions_to_apply=[_add_action(req, slot)],
        ))

    # ── Step 2: same program + semester classes that could make room ────────
    prog_norm = C.normalize_program(req.program)
    sem_raw = req.semester.strip()
    group = [
        r for r in board.rows
        if C.normalize_program(r.entry.get("program", "")) == prog_norm
        and str(r.entry.get("semester", "")).strip() == sem_raw
    ]

    moves: List[tuple] = []     # (sort_key, row, dest, n_window)
    cancels: List[tuple] = []   # (sort_key, row, n_window)
    evaluated = 0
    truncated = False

    for a in group:
        if time.perf_counter() > deadline:
            truncated = True
            break
        if a.e - a.s <= 0 or _same_subject(a.entry, req):
            continue
        evaluated += 1

        board.detach(a)
        try:
            # windows for the NEW class that only exist because `a` is gone
            freed = [w for w in _valid_windows(board, spec) if (w[0], w[1]) not in orig_keys]
            if not freed:
                continue
            freed.sort(key=lambda w: n_rank(w[0], w[1]))

            # Strategy A (preferred): move `a` somewhere else
            found = False
            for nd, ns, nroom in freed[:_MAX_FREED_TRIES]:
                n_row = _Row(n_entry, nd, ns, ns + dur, nroom)
                board.attach(n_row)
                try:
                    dest = _best_move(board, a)
                finally:
                    board.detach(n_row)
                if dest is not None:
                    key = (n_rank(nd, ns)[0], dest[0], n_rank(nd, ns))
                    moves.append((key, a, dest, (nd, ns, nroom)))
                    found = True
                    break

            # Strategy B (last resort): cancel `a`
            if not found:
                nd, ns, nroom = freed[0]
                cancels.append(((n_rank(nd, ns),), a, (nd, ns, nroom)))
        finally:
            board.attach(a)

    moves.sort(key=lambda x: x[0])
    cancels.sort(key=lambda x: x[0])
    room_left = max(0, _MAX_PROPOSALS - len(proposals))
    chosen_moves = moves[:room_left]
    chosen_cancels = cancels[: max(0, room_left - len(chosen_moves))]

    for _key, a, dest, (nd, ns, nroom) in chosen_moves:
        ent = a.entry
        a_dur = a.e - a.s
        _cost, mday, ms, mroom = dest
        priority = _slot(nd, ns, dur, nroom)
        new_slot = _slot(mday, ms, a_dur, mroom)
        affected = _affected_of(ent)
        subj = affected["subject"]
        proposals.append(ProposedModification(
            description=(
                f"Move '{subj}' ({ent.get('day', '')} {ent.get('start', '')}–{ent.get('end', '')}) → "
                f"{new_slot['day']} {new_slot['start']}–{new_slot['end']}, "
                f"then place '{req.subject_name}' on "
                f"{priority['day']} {priority['start']}–{priority['end']} in {priority['room']}."
                f"{_room_note(req, nroom)}"
            ),
            modification_type="move",
            affected_class=affected,
            new_slot=new_slot,
            priority_slot=priority,
            actions_to_apply=[
                {
                    "action": "MOVE_CLASS",
                    "target": _target_of(ent),
                    "new_day": new_slot["day"],
                    "new_start_time": new_slot["start"],
                    "new_end_time": new_slot["end"],
                    **({"new_room": mroom} if mroom else {}),
                },
                _add_action(req, priority),
            ],
        ))

    for _key, a, (nd, ns, nroom) in chosen_cancels:
        ent = a.entry
        priority = _slot(nd, ns, dur, nroom)
        affected = _affected_of(ent)
        proposals.append(ProposedModification(
            description=(
                f"Cancel '{affected['subject']}' "
                f"({ent.get('day', '')} {ent.get('start', '')}–{ent.get('end', '')}), "
                f"then place '{req.subject_name}' on "
                f"{priority['day']} {priority['start']}–{priority['end']} in {priority['room']}."
                f"{_room_note(req, nroom)}"
            ),
            modification_type="cancel",
            affected_class=affected,
            new_slot=None,
            priority_slot=priority,
            actions_to_apply=[
                {"action": "REMOVE_CLASS", "target": _target_of(ent)},
                _add_action(req, priority),
            ],
        ))

    elapsed = round((time.perf_counter() - t0) * 1000, 2)

    if not proposals:
        reason = ""
        if spec.teachers and all(
            any(d in busy_map.get(t, ()) for t in spec.teachers) for d in board.wd
        ):
            reason = f" {req.teacher} is marked busy on every working day."
        elif not group:
            reason = f" No classes exist for {req.program} {req.semester} to rearrange."
        return AdvancedSmartScheduleResponse(
            found=False,
            message=(
                f"Could not find any valid modifications within "
                f"{req.program} {req.semester} to accommodate '{req.subject_name}'.{reason}"
                + (" (search stopped at the time limit)" if truncated else "")
            ),
            elapsed_ms=elapsed, candidates_evaluated=evaluated,
        )

    return AdvancedSmartScheduleResponse(
        found=True,
        proposals=proposals,
        message=f"Found {len(proposals)} proposal(s)."
                + (" (search stopped at the time limit; more options may exist)" if truncated else ""),
        elapsed_ms=elapsed, candidates_evaluated=evaluated,
    )


# ── Tiny TTL cache (per process) ──────────────────────────────────────────────
_cache: "OrderedDict[tuple, Tuple[float, AdvancedSmartScheduleResponse]]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> Optional[AdvancedSmartScheduleResponse]:
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        if time.monotonic() - hit[0] > _CACHE_TTL_S:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return hit[1]


def _cache_put(key: tuple, value: AdvancedSmartScheduleResponse) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


# ── Endpoint ──────────────────────────────────────────────────────────────────
@router.post("/advanced", response_model=AdvancedSmartScheduleResponse)
def advanced_smart_schedule(
    req: AdvancedSmartScheduleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    """
    Advanced Smart Schedule — finds modifications strictly within the same
    program+semester to make room for a priority class, OR simply returns the
    free slot if no modification is necessary.

    Returns proposals without applying any changes.
    The caller must explicitly execute the actions_to_apply after user confirmation.
    """
    t0 = time.perf_counter()

    # ── Validate subject is in catalog (no DB needed) ────────────────────
    if not _subject_known(req):
        return AdvancedSmartScheduleResponse(
            found=False,
            message=(
                f"Subject '{req.subject_code}' is not recognized for "
                f"{req.program} {req.semester}. Add it to the Catalog first."
            ),
        )

    # ── Exactly two DB round-trips ───────────────────────────────────────
    v = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
    schedule: List[dict] = [e.to_dict() for e in v.entries] if v else []
    busy_map = _load_busy_map(db)

    busy_sig = hash(frozenset((t, frozenset(d)) for t, d in busy_map.items()))
    cache_key = (
        getattr(v, "id", 0), len(schedule), busy_sig,
        req.program, req.semester, req.subject_code, req.subject_name,
        req.teacher, req.entry_type, req.room, req.preferred_day,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    resp = _advanced_core(req, schedule, busy_map)
    resp.elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    if "time limit" not in resp.message:
        _cache_put(cache_key, resp)
    return resp
