"""
Pure (stateless) apply-functions for every supported action type.

Each function takes a schedule (list[dict]) and returns a NEW list[dict].
No database access, no side effects — safe to call during simulation.

All target matching uses `_find_entry()` which resolves a ClassTarget
against the schedule using provided fields.
"""
from __future__ import annotations

import copy
from typing import List, Optional, Tuple

from app.actions.types import ClassTarget, ClassSpec


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _matches(entry: dict, target: ClassTarget) -> bool:
    """Return True if an entry matches all non-None target fields with fuzzy logic."""
    import re
    
    def norm_time(t: str) -> str:
        t = str(t).strip().lower()
        t = re.sub(r'[^0-9:]', '', t) # strip 'pm', 'am', spaces
        if t.startswith("0") and len(t) > 3: t = t[1:] # 09:00 -> 9:00
        return t
        
    def norm_sem(s: str) -> str:
        s = str(s).strip().lower()
        nums = re.sub(r'[^0-9]', '', s)
        return nums if nums else s

    checks = {
        "day":      (target.day, lambda x: str(x).strip().lower()),
        "program":  (target.program, lambda x: str(x).strip().lower().replace(".", "")),
        "semester": (target.semester, norm_sem),
        "start":    (target.start_time, norm_time),
        "end":      (target.end_time, norm_time),
        "teacher":  (target.teacher, lambda x: str(x).strip().lower()),
        "room":     (target.room, lambda x: str(x).strip().lower()),
        "type":     (target.entry_type, lambda x: str(x).strip().lower()),
    }
    
    for field, (val, norm_func) in checks.items():
        if val is not None:
            e_val = entry.get(field, "")
            if norm_func(e_val) != norm_func(val):
                return False

    # subject matching — compare code OR name
    if target.subject_code is not None:
        sc = str(entry.get("subject_code", "")).lower().strip()
        tc = target.subject_code.lower().strip()
        if sc != tc:
            # Fallback to checking name if code doesn't match or is missing
            sn = str(entry.get("subject_name", "")).lower().strip()
            if tc not in sn and sn not in tc:
                return False
    elif target.subject_name is not None:
        sn = str(entry.get("subject_name", "")).lower().strip()
        if target.subject_name.lower().strip() not in sn and sn not in target.subject_name.lower().strip():
            return False
    return True


def _find_entry(schedule: List[dict], target: ClassTarget) -> Optional[int]:
    """Return the index of the first matching entry, or None."""
    for i, entry in enumerate(schedule):
        if _matches(entry, target):
            return i
    return None


def _find_all(schedule: List[dict], target: ClassTarget) -> List[int]:
    """Return all matching indices."""
    return [i for i, e in enumerate(schedule) if _matches(e, target)]


def _clone(schedule: List[dict]) -> List[dict]:
    return copy.deepcopy(schedule)


# ---------------------------------------------------------------------------
# ADD_CLASS
# ---------------------------------------------------------------------------

def apply_add_class(schedule: List[dict], spec: ClassSpec) -> List[dict]:
    new_schedule = _clone(schedule)
    new_schedule.append(spec.to_schedule_dict())
    return new_schedule


# ---------------------------------------------------------------------------
# REMOVE_CLASS / CANCEL_CLASS  (both remove the matching slot)
# ---------------------------------------------------------------------------

def apply_remove_class(schedule: List[dict], target: ClassTarget) -> Tuple[List[dict], dict]:
    """
    Returns (new_schedule, removed_entry).
    Raises ValueError if no matching entry found.
    """
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for target: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    removed = new_schedule.pop(idx)
    return new_schedule, removed


def apply_cancel_class(schedule: List[dict], target: ClassTarget) -> Tuple[List[dict], dict]:
    """Alias for remove — 'cancel' semantically removes the slot from the live schedule."""
    return apply_remove_class(schedule, target)


# ---------------------------------------------------------------------------
# EXTEND_CLASS / SHORTEN_CLASS
# ---------------------------------------------------------------------------

def apply_extend_class(
    schedule: List[dict], target: ClassTarget, new_end_time: str
) -> Tuple[List[dict], dict, dict]:
    """
    Returns (new_schedule, before_entry, after_entry).
    Raises ValueError if not found or new_end_time <= current end.
    """
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    new_schedule[idx]["end"] = new_end_time
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


def apply_shorten_class(
    schedule: List[dict], target: ClassTarget, new_end_time: str
) -> Tuple[List[dict], dict, dict]:
    """
    Returns (new_schedule, before_entry, after_entry).
    Raises ValueError if not found or new_end_time >= current end.
    """
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    new_schedule[idx]["end"] = new_end_time
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


# ---------------------------------------------------------------------------
# MOVE_CLASS
# ---------------------------------------------------------------------------

def apply_move_class(
    schedule: List[dict],
    target: ClassTarget,
    new_day: Optional[str],
    new_start_time: Optional[str],
    new_end_time: Optional[str],
    new_room: Optional[str] = None,
) -> Tuple[List[dict], dict, dict]:
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    if new_day:
        new_schedule[idx]["day"] = new_day
    if new_start_time:
        new_schedule[idx]["start"] = new_start_time
    if new_end_time:
        new_schedule[idx]["end"] = new_end_time
    if new_room:
        new_schedule[idx]["room"] = new_room
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


# ---------------------------------------------------------------------------
# SWAP_CLASSES — swap only day+time between two classes
# ---------------------------------------------------------------------------

def apply_swap_classes(
    schedule: List[dict], target_a: ClassTarget, target_b: ClassTarget
) -> Tuple[List[dict], dict, dict, dict, dict]:
    """
    Returns (new_schedule, a_before, a_after, b_before, b_after).
    """
    idx_a = _find_entry(schedule, target_a)
    if idx_a is None:
        raise ValueError(f"Class A not found: {target_a.model_dump(exclude_none=True)}")
    idx_b = _find_entry(schedule, target_b)
    if idx_b is None:
        raise ValueError(f"Class B not found: {target_b.model_dump(exclude_none=True)}")
    if idx_a == idx_b:
        raise ValueError("Class A and Class B resolve to the same entry — cannot swap with itself.")

    new_schedule = _clone(schedule)
    a_before = copy.deepcopy(new_schedule[idx_a])
    b_before = copy.deepcopy(new_schedule[idx_b])

    # Swap only day + start + end + room
    new_schedule[idx_a]["day"],   new_schedule[idx_b]["day"]   = (
        new_schedule[idx_b]["day"],   new_schedule[idx_a]["day"]
    )
    new_schedule[idx_a]["start"], new_schedule[idx_b]["start"] = (
        new_schedule[idx_b]["start"], new_schedule[idx_a]["start"]
    )
    new_schedule[idx_a]["end"],   new_schedule[idx_b]["end"]   = (
        new_schedule[idx_b]["end"],   new_schedule[idx_a]["end"]
    )
    new_schedule[idx_a]["room"],  new_schedule[idx_b]["room"]  = (
        new_schedule[idx_b]["room"],  new_schedule[idx_a]["room"]
    )

    a_after = copy.deepcopy(new_schedule[idx_a])
    b_after = copy.deepcopy(new_schedule[idx_b])
    return new_schedule, a_before, a_after, b_before, b_after


# ---------------------------------------------------------------------------
# INTERCHANGE_CLASSES — swap ALL fields between two classes
# ---------------------------------------------------------------------------

def apply_interchange_classes(
    schedule: List[dict], target_a: ClassTarget, target_b: ClassTarget
) -> Tuple[List[dict], dict, dict, dict, dict]:
    """Fully swap every field between two entries."""
    idx_a = _find_entry(schedule, target_a)
    if idx_a is None:
        raise ValueError(f"Class A not found: {target_a.model_dump(exclude_none=True)}")
    idx_b = _find_entry(schedule, target_b)
    if idx_b is None:
        raise ValueError(f"Class B not found: {target_b.model_dump(exclude_none=True)}")
    if idx_a == idx_b:
        raise ValueError("Class A and Class B resolve to the same entry — cannot interchange.")

    new_schedule = _clone(schedule)
    a_before = copy.deepcopy(new_schedule[idx_a])
    b_before = copy.deepcopy(new_schedule[idx_b])

    # Swap only the scheduling physical properties (time, room) between the two classes.
    # The academic identity (subject, program, semester, teacher) stays with the class. 
    keys_to_swap = ["room", "day", "start", "end"]
    for k in keys_to_swap:
        # We assign directly, defaulting to "" if a side is missing the key
        new_schedule[idx_a][k], new_schedule[idx_b][k] = (
            new_schedule[idx_b].get(k, ""),
            new_schedule[idx_a].get(k, "")
        )

    a_after = copy.deepcopy(new_schedule[idx_a])
    b_after = copy.deepcopy(new_schedule[idx_b])
    return new_schedule, a_before, a_after, b_before, b_after


# ---------------------------------------------------------------------------
# CHANGE_TEACHER
# ---------------------------------------------------------------------------

def apply_change_teacher(
    schedule: List[dict], target: ClassTarget, new_teacher: str
) -> Tuple[List[dict], dict, dict]:
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    new_schedule[idx]["teacher"] = new_teacher
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


# ---------------------------------------------------------------------------
# CHANGE_ROOM
# ---------------------------------------------------------------------------

def apply_change_room(
    schedule: List[dict], target: ClassTarget, new_room: str
) -> Tuple[List[dict], dict, dict]:
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    new_schedule[idx]["room"] = new_room
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


# ---------------------------------------------------------------------------
# CHANGE_TIME
# ---------------------------------------------------------------------------

def apply_change_time(
    schedule: List[dict], target: ClassTarget, new_start: str, new_end: str
) -> Tuple[List[dict], dict, dict]:
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    new_schedule[idx]["start"] = new_start
    new_schedule[idx]["end"] = new_end
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


# ---------------------------------------------------------------------------
# CHANGE_DAY
# ---------------------------------------------------------------------------

def apply_change_day(
    schedule: List[dict], target: ClassTarget, new_day: str
) -> Tuple[List[dict], dict, dict]:
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    new_schedule[idx]["day"] = new_day
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


# ---------------------------------------------------------------------------
# REPLACE_CLASS
# ---------------------------------------------------------------------------

def apply_replace_class(
    schedule: List[dict], target: ClassTarget, new_spec: ClassSpec
) -> Tuple[List[dict], dict, dict]:
    """Remove matching class and insert new spec."""
    idx = _find_entry(schedule, target)
    if idx is None:
        raise ValueError(f"No matching class found for: {target.model_dump(exclude_none=True)}")
    new_schedule = _clone(schedule)
    before = copy.deepcopy(new_schedule[idx])
    new_schedule[idx] = new_spec.to_schedule_dict()
    after = copy.deepcopy(new_schedule[idx])
    return new_schedule, before, after


# ---------------------------------------------------------------------------
# RESTORE_VERSION  (schedule already fetched by engine from DB)
# ---------------------------------------------------------------------------

def apply_restore_version(old_entries: List[dict]) -> List[dict]:
    """Return a deep copy of old version's entries as the new schedule."""
    return copy.deepcopy(old_entries)
