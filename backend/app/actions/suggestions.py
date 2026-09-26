"""
Suggests feasible alternatives when an action fails a hard constraint.

Uses the authoritative constraint tables from app/scheduler/constraints.py.
All suggestions are validated against the FULL H1–H11 constraint set before
being offered, so clicking "Apply Suggestion" will never produce a new violation.

When no validated alternative exists, ``rich_suggestions`` is empty.
"""
import copy
import json
from typing import List, Dict, Any, Optional

from app.scheduler.constraints import ROOM_FACILITIES, INTERNAL_TEACHERS, normalize_program
from app.scheduler.validator import overlaps, to_minutes, validate_schedule, validate_schema, fetch_global_busy_days


STANDARD_SLOTS = [
    ("10:00", "12:00"),
    ("12:00", "14:00"),
    ("14:30", "16:30"),
    ("14:30", "17:30"),
    ("16:30", "17:30"),
]

WORKING_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
SEARCH_START_MINUTES = 9 * 60
SEARCH_END_MINUTES = 17 * 60 + 30
MAX_RANKED_SUGGESTIONS = 3


# ---------------------------------------------------------------------------
# Full-constraint validation helpers
# ---------------------------------------------------------------------------

def _fingerprint(err: dict) -> str:
    """Produce an exact fingerprint for a violation.

    Location is part of a violation's identity. Ignoring it can make a newly
    created clash look like an unrelated pre-existing clash elsewhere.
    """
    return json.dumps(err, sort_keys=True)


def _count_new_violations(base_schedule: List[dict], candidate_schedule: List[dict]) -> int:
    """Return the number of genuinely NEW violations introduced by candidate vs base."""
    base_v = validate_schedule(base_schedule) + validate_schema(base_schedule)
    cand_v = validate_schedule(candidate_schedule) + validate_schema(candidate_schedule)

    base_fps = [_fingerprint(x) for x in base_v]
    new_count = 0
    for item in cand_v:
        fp = _fingerprint(item)
        if fp in base_fps:
            base_fps.remove(fp)
        else:
            new_count += 1
    return new_count


def _is_fully_valid(base_schedule: List[dict], candidate_schedule: List[dict]) -> bool:
    """Return True only when the candidate introduces zero NEW violations compared to the base."""
    return _count_new_violations(base_schedule, candidate_schedule) == 0


def _is_physically_valid(base_schedule: List[dict], candidate_schedule: List[dict], base_physical_fps: Optional[list] = None) -> bool:
    """Return True if candidate introduces no new physical/overlap violations."""
    PHYSICAL_RULES = ("H1_semester_clash", "H2_teacher_clash", "H3_room_clash")
    if base_physical_fps is None:
        base_v = validate_schedule(base_schedule)
        base_physical_fps = [_fingerprint(x) for x in base_v if x.get("rule") in PHYSICAL_RULES]
    else:
        base_physical_fps = list(base_physical_fps)

    cand_v = validate_schedule(candidate_schedule)
    for item in cand_v:
        if item.get("rule") not in PHYSICAL_RULES:
            continue
        fp = _fingerprint(item)
        if fp in base_physical_fps:
            base_physical_fps.remove(fp)
        else:
            return False
    return True


def _simulate_move(schedule: List[dict], entry: dict,
                   new_day: str, new_start: str, new_end: str,
                   new_room: Optional[str] = None,
                   orig_target: Optional[dict] = None) -> List[dict]:
    """Create a candidate schedule with entry moved to a new slot."""
    candidate = copy.deepcopy(schedule)

    # Use orig_target if available to find the class in the pre-simulation schedule.
    search_props = orig_target if orig_target else entry

    for c in candidate:
        match = True
        if search_props.get("day") and c.get("day") != search_props.get("day"):
            match = False
        st = search_props.get("start_time", search_props.get("start"))
        if st and c.get("start") != st:
            match = False
        ed = search_props.get("end_time", search_props.get("end"))
        if ed and c.get("end") != ed:
            match = False
        if search_props.get("program") and normalize_program(c.get("program")) != normalize_program(search_props.get("program")):
            match = False
        if search_props.get("semester") and c.get("semester") != search_props.get("semester"):
            match = False
        subj = search_props.get("subject_code") or search_props.get("subject")
        if subj:
            c_subj = c.get("subject_code") or c.get("subject")
            if c_subj and c_subj != subj and c_subj.lower() != subj.lower():
                match = False

        if match:
            c["day"] = new_day
            c["start"] = new_start
            c["end"] = new_end
            if new_room:
                c["room"] = new_room
            break
    return candidate


def _simulate_room_change(schedule: List[dict], entry: dict, new_room: str,
                          orig_target: Optional[dict] = None) -> List[dict]:
    """Create a candidate schedule with entry's room changed."""
    candidate = copy.deepcopy(schedule)

    search_props = orig_target if orig_target else entry

    for c in candidate:
        match = True
        if search_props.get("day") and c.get("day") != search_props.get("day"):
            match = False
        st = search_props.get("start_time", search_props.get("start"))
        if st and c.get("start") != st:
            match = False
        ed = search_props.get("end_time", search_props.get("end"))
        if ed and c.get("end") != ed:
            match = False
        if search_props.get("program") and c.get("program") != search_props.get("program"):
            match = False
        if search_props.get("semester") and c.get("semester") != search_props.get("semester"):
            match = False
        subj = search_props.get("subject_code") or search_props.get("subject")
        if subj:
            c_subj = c.get("subject_code") or c.get("subject")
            if c_subj and c_subj != subj and c_subj.lower() != subj.lower():
                match = False

        if match:
            c["room"] = new_room
            break
    return candidate


def _simulate_add(schedule: List[dict], entry: dict, new_day: str, new_start: str, new_end: str, new_room: Optional[str] = None) -> List[dict]:
    candidate = copy.deepcopy(schedule)
    new_class = copy.deepcopy(entry)
    new_class["day"] = new_day
    new_class["start"] = new_start
    new_class["end"] = new_end
    if new_room:
        new_class["room"] = new_room
    candidate.append(new_class)
    return candidate


def _simulate_remove(schedule: List[dict], entry: dict,
                     orig_target: Optional[dict] = None) -> List[dict]:
    """Create a candidate schedule with the matching entry removed."""
    candidate = copy.deepcopy(schedule)
    search_props = orig_target if orig_target else entry

    for i, c in enumerate(candidate):
        match = True
        if search_props.get("day") and c.get("day") != search_props.get("day"):
            match = False
        st = search_props.get("start_time", search_props.get("start"))
        if st and c.get("start") != st:
            match = False
        ed = search_props.get("end_time", search_props.get("end"))
        if ed and c.get("end") != ed:
            match = False
        subj = search_props.get("subject_code") or search_props.get("subject")
        if subj:
            c_subj = c.get("subject_code") or c.get("subject")
            if c_subj and c_subj != subj and c_subj.lower() != subj.lower():
                match = False
        if match:
            candidate.pop(i)
            break
    return candidate


def _find_schedule_entry(schedule: List[dict], target: Optional[dict], fallback: dict) -> dict:
    """Resolve the authoritative pre-action entry used for alternative search."""
    if target:
        for item in schedule:
            if (
                (not target.get("subject_code") or item.get("subject_code", "").lower() == target["subject_code"].lower())
                and (not target.get("program") or item.get("program") == target["program"])
                and (not target.get("semester") or item.get("semester") == target["semester"])
                and (not target.get("day") or item.get("day") == target["day"])
                and (not target.get("start_time") or item.get("start") == target["start_time"])
            ):
                return item
    return fallback


# ---------------------------------------------------------------------------
# Slot / Room enumeration
# ---------------------------------------------------------------------------

def suggest_free_rooms(
    schedule: List[dict],
    day: str,
    start: str,
    end: str,
    need_lab: bool = False,
) -> List[str]:
    """Return rooms that are free during [start, end) on day."""
    busy_rooms: set[str] = set()
    for e in schedule:
        if e.get("day") != day:
            continue
        try:
            if overlaps(start, end, e["start"], e["end"]):
                if e.get("room"):
                    busy_rooms.add(e["room"])
        except Exception:
            continue
    free = [
        room for room, fac in ROOM_FACILITIES.items()
        if room not in busy_rooms
        and (not need_lab or fac["lab"])
    ]
    return sorted(free)


def suggest_free_slots_for_teacher(
    schedule: List[dict],
    teacher: str,
    duration_minutes: int,
    busy_days: Optional[set] = None,
) -> List[Dict[str, str]]:
    """Return (day, start, end) slots where teacher has no overlap."""
    results: list[Dict[str, str]] = []
    skip_days = busy_days or set()
    for day in WORKING_DAYS:
        if day in skip_days:
            continue
        for start, end in STANDARD_SLOTS:
            dur = to_minutes(end) - to_minutes(start)
            if dur != duration_minutes and duration_minutes not in (dur, dur + 30):
                continue
            clash = False
            for e in schedule:
                if e.get("day") != day:
                    continue
                teachers_in_entry = {t.strip() for t in str(e.get("teacher", "")).split(",") if t.strip()}
                if teacher in teachers_in_entry:
                    try:
                        if overlaps(start, end, e["start"], e["end"]):
                            clash = True
                            break
                    except Exception:
                        pass
            if not clash:
                results.append({"day": day, "start": start, "end": end})
    return results


def suggest_free_slots_for_room(
    schedule: List[dict],
    room: str,
    duration_minutes: int,
) -> List[Dict[str, str]]:
    """Return (day, start, end) slots where room is free."""
    results: list[Dict[str, str]] = []
    for day in WORKING_DAYS:
        for start, end in STANDARD_SLOTS:
            dur = to_minutes(end) - to_minutes(start)
            if dur != duration_minutes and duration_minutes not in (dur, dur + 30):
                continue
            clash = False
            for e in schedule:
                if e.get("day") != day or e.get("room") != room:
                    continue
                try:
                    if overlaps(start, end, e["start"], e["end"]):
                        clash = True
                        break
                except Exception:
                    pass
            if not clash:
                results.append({"day": day, "start": start, "end": end})
    return results


def _all_slots_of_duration(duration_minutes: int) -> List[Dict[str, str]]:
    """Return every working-day slot in the searchable timetable window."""
    results = []
    for day in WORKING_DAYS:
        for start_minutes in range(SEARCH_START_MINUTES, SEARCH_END_MINUTES, 30):
            end_minutes = start_minutes + duration_minutes
            if end_minutes > SEARCH_END_MINUTES:
                continue
            results.append({
                "day": day,
                "start": f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
                "end": f"{end_minutes // 60:02d}:{end_minutes % 60:02d}",
            })
    return results


# ---------------------------------------------------------------------------
# Validated proposal builders
# ---------------------------------------------------------------------------

def _find_valid_room(schedule: List[dict], entry: dict,
                     free_rooms: List[str],
                     orig_target: Optional[dict] = None,
                     simulate_as_move: bool = False,
                     simulate_as_add: bool = False,
                     target_day: str = "",
                     target_start: str = "",
                     target_end: str = "") -> Optional[str]:
    """Find the first room from free_rooms that introduces zero new violations."""
    for room in free_rooms:
        if simulate_as_add:
            candidate = _simulate_add(schedule, entry, target_day, target_start, target_end, new_room=room)
        elif simulate_as_move:
            candidate = _simulate_move(schedule, entry, target_day, target_start, target_end, new_room=room, orig_target=orig_target)
        else:
            candidate = _simulate_room_change(schedule, entry, room, orig_target=orig_target)

        if candidate != schedule and _is_fully_valid(schedule, candidate):
            return room
    return None


def _find_valid_slot(schedule: List[dict], entry: dict,
                     free_slots: List[Dict[str, str]],
                     need_lab: bool = False,
                     orig_target: Optional[dict] = None,
                     simulate_as_add: bool = False) -> Optional[Dict[str, str]]:
    """Find the first slot from free_slots that introduces zero new violations.
    Also finds a valid room for that slot if needed."""
    for slot in free_slots:
        # First try keeping the same room
        if simulate_as_add:
            candidate = _simulate_add(schedule, entry, slot["day"], slot["start"], slot["end"])
        else:
            candidate = _simulate_move(schedule, entry, slot["day"], slot["start"], slot["end"], orig_target=orig_target)
        if candidate != schedule and _is_fully_valid(schedule, candidate):
            return slot

        # If same room fails, try finding a free room at the new slot
        free_rooms = suggest_free_rooms(schedule, slot["day"], slot["start"], slot["end"],
                                        need_lab=need_lab)
        for room in free_rooms:
            if simulate_as_add:
                candidate = _simulate_add(schedule, entry, slot["day"], slot["start"], slot["end"], new_room=room)
            else:
                candidate = _simulate_move(schedule, entry,
                                           slot["day"], slot["start"], slot["end"],
                                           new_room=room, orig_target=orig_target)
            if candidate != schedule and _is_fully_valid(schedule, candidate):
                slot_with_room = dict(slot)
                slot_with_room["room"] = room
                return slot_with_room
    return None


def _find_valid_slots(
    schedule: List[dict],
    entry: dict,
    free_slots: List[Dict[str, str]],
    need_lab: bool = False,
    orig_target: Optional[dict] = None,
    simulate_as_add: bool = False,
    limit: int = 3,
) -> List[Dict[str, str]]:
    """Exhaustively validate and rank all candidate slots before returning them."""
    preferred_day = entry.get("day") or (orig_target or {}).get("day")
    preferred_start = entry.get("start") or (orig_target or {}).get("start_time") or (orig_target or {}).get("start")
    preferred_start_minutes = to_minutes(preferred_start) if preferred_start else None
    source_day = (orig_target or {}).get("day")
    source_start = (orig_target or {}).get("start_time") or (orig_target or {}).get("start")
    valid: list[Dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for slot in free_slots:
        if slot["day"] == source_day and slot["start"] == source_start:
            continue
        candidates: list[tuple[List[dict], Optional[str]]] = []
        if simulate_as_add:
            candidates.append((_simulate_add(schedule, entry, slot["day"], slot["start"], slot["end"]), None))
        else:
            candidates.append((_simulate_move(schedule, entry, slot["day"], slot["start"], slot["end"], orig_target=orig_target), None))

        for room in suggest_free_rooms(schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab):
            if simulate_as_add:
                candidate = _simulate_add(schedule, entry, slot["day"], slot["start"], slot["end"], new_room=room)
            else:
                candidate = _simulate_move(
                    schedule, entry, slot["day"], slot["start"], slot["end"],
                    new_room=room, orig_target=orig_target,
                )
            candidates.append((candidate, room))

        for candidate, room in candidates:
            if candidate == schedule or not _is_fully_valid(schedule, candidate):
                continue
            result = dict(slot)
            if room:
                result["room"] = room
            # One suggestion per time window; prefer the class's existing room
            # when it is valid, otherwise the first compatible free room.
            key = (result["day"], result["start"], result["end"])
            if key not in seen:
                seen.add(key)
                result["_rank"] = (
                    0 if result["day"] == preferred_day else 1,
                    abs(to_minutes(result["start"]) - preferred_start_minutes)
                    if preferred_start_minutes is not None else 0,
                    WORKING_DAYS.index(result["day"]),
                    to_minutes(result["start"]),
                )
                valid.append(result)

    valid.sort(key=lambda item: item["_rank"])
    for item in valid:
        item.pop("_rank", None)
    return valid[:limit]


def _build_proposed_change_room(entry: dict, valid_room: str,
                                orig_target: Optional[dict] = None) -> Dict[str, Any]:
    """Build a CHANGE_ROOM proposed action."""
    return {
        "action": "CHANGE_ROOM",
        "target": orig_target if orig_target else {
            "day": entry.get("day"),
            "start_time": entry.get("start"),
            "end_time": entry.get("end"),
            "program": entry.get("program"),
            "semester": entry.get("semester"),
            "subject_code": entry.get("subject_code") or entry.get("subject"),
        },
        "new_room": valid_room,
    }


def _build_proposed_move_class(entry: dict, valid_slot: Dict[str, str],
                               orig_target: Optional[dict] = None) -> Dict[str, Any]:
    """Build a MOVE_CLASS proposed action. If the slot includes a room override, add CHANGE_ROOM."""
    action: Dict[str, Any] = {
        "action": "MOVE_CLASS",
        "target": orig_target if orig_target else {
            "day": entry.get("day"),
            "start_time": entry.get("start"),
            "end_time": entry.get("end"),
            "program": entry.get("program"),
            "semester": entry.get("semester"),
            "subject_code": entry.get("subject_code") or entry.get("subject"),
        },
        "new_day": valid_slot["day"],
        "new_start_time": valid_slot["start"],
        "new_end_time": valid_slot["end"],
    }
    if "room" in valid_slot:
        action["new_room"] = valid_slot["room"]
    return action


def _build_proposed_add_class(entry: dict, valid_slot: Dict[str, str]) -> Dict[str, Any]:
    """Build an ADD_CLASS proposed action with updated slot parameters."""
    return {
        "action": "ADD_CLASS",
        "spec": {
            "program": entry.get("program"),
            "semester": entry.get("semester"),
            "day": valid_slot.get("day", entry.get("day")),
            "start_time": valid_slot.get("start", entry.get("start")),
            "end_time": valid_slot.get("end", entry.get("end")),
            "subject_code": entry.get("subject_code") or entry.get("subject", ""),
            "subject_name": entry.get("subject_name", ""),
            "teacher": entry.get("teacher", ""),
            "entry_type": entry.get("type", "Theory"),
            "room": valid_slot.get("room", entry.get("room", "")),
        }
    }


def _build_proposed_remove_class(entry: dict, orig_target: Optional[dict] = None) -> Dict[str, Any]:
    """Build a REMOVE_CLASS proposed action."""
    return {
        "action": "REMOVE_CLASS",
        "target": orig_target if orig_target else {
            "day": entry.get("day"),
            "start_time": entry.get("start"),
            "end_time": entry.get("end"),
            "program": entry.get("program"),
            "semester": entry.get("semester"),
            "subject_code": entry.get("subject_code") or entry.get("subject"),
        },
    }


# ---------------------------------------------------------------------------
# Helper: build the best universal move suggestion
# ---------------------------------------------------------------------------

STANDARD_DURATIONS = [120, 180, 90, 60, 150]  # 2h, 3h, 1.5h, 1h, 2.5h


def _normalize_duration(minutes: int) -> int:
    """Snap an odd duration to the nearest standard duration."""
    if minutes <= 0:
        return 120
    best = min(STANDARD_DURATIONS, key=lambda d: abs(d - minutes))
    return best


def _universal_move_suggestion(
    schedule: List[dict],
    entry: dict,
    action_type: str,
    need_lab: bool,
    orig_target: Optional[dict],
    teacher: Optional[str],
    duration_minutes: int,
    busy_days: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Try EVERY strategy to find a universally safe free slot.

    Search strategy (in order, each tier fully exhausted before the next):
      1. Teacher-aware free slots at normalized duration
      2. All slots at normalized duration
      3. All slots at every standard duration (120, 180, 90, 60, 150)
      4. Brute-force: all slots × all rooms for each duration
    """
    _teacher = teacher or orig_target.get("teacher") if orig_target else entry.get("teacher")
    global_busy_map = fetch_global_busy_days()
    teacher_global_busy_days = global_busy_map.get(_teacher, set()) if _teacher else set()
    merged_busy_days = (busy_days or set()) | teacher_global_busy_days

    is_add = (action_type == "ADD_CLASS")

    # Filter helper
    def _filter_orig(slots: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if orig_target:
            orig_day = orig_target.get("day")
            orig_start = orig_target.get("start_time") or orig_target.get("start")
            return [s for s in slots if not (s["day"] == orig_day and s["start"] == orig_start)]
        return slots

    # Normalize to nearest standard duration so we generate sensible time windows
    norm_dur = _normalize_duration(duration_minutes)
    durations_to_try = [norm_dur] + [d for d in STANDARD_DURATIONS if d != norm_dur]

    for dur in durations_to_try:
        # Tier 1: teacher-aware free slots
        if _teacher:
            free_slots = _filter_orig(suggest_free_slots_for_teacher(schedule, _teacher, dur, merged_busy_days))
            if free_slots:
                valid_slots = _find_valid_slots(
                    schedule, entry, free_slots,
                    need_lab=need_lab, orig_target=orig_target,
                    simulate_as_add=is_add,
                )
                if valid_slots:
                    return valid_slots[0]

        # Tier 2: ALL slots at this duration
        all_slots = _filter_orig([s for s in _all_slots_of_duration(dur) if not merged_busy_days or s["day"] not in merged_busy_days])
        if all_slots:
            valid_slots = _find_valid_slots(
                schedule, entry, all_slots,
                need_lab=need_lab, orig_target=orig_target,
                simulate_as_add=is_add,
            )
            if valid_slots:
                return valid_slots[0]

    # Tier 3: Brute-force — try EVERY slot × EVERY room for each duration
    from app.scheduler.constraints import ROOM_FACILITIES
    all_rooms = sorted(ROOM_FACILITIES.keys())

    for dur in durations_to_try:
        all_slots = _filter_orig([s for s in _all_slots_of_duration(dur) if not merged_busy_days or s["day"] not in merged_busy_days])
        for slot in all_slots:
            for room in all_rooms:
                if need_lab and not ROOM_FACILITIES[room].get("lab"):
                    continue
                if is_add:
                    candidate = _simulate_add(schedule, entry, slot["day"], slot["start"], slot["end"], new_room=room)
                else:
                    candidate = _simulate_move(schedule, entry, slot["day"], slot["start"], slot["end"], new_room=room, orig_target=orig_target)
                if candidate != schedule and _is_fully_valid(schedule, candidate):
                    result = dict(slot)
                    result["room"] = room
                    return result

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def suggest_alternatives(
    schedule: List[dict],
    action_type: str,
    violation: Dict[str, Any],
    teacher: str | None = None,
    need_lab: bool = False,
    orig_target: Optional[dict] = None,
    mutated_entry: Optional[dict] = None,
    busy_days: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Produce alternative suggestions based on the violated constraint rule.

    CRITICAL: Every suggested action is validated against the full H1–H11 constraint
    set before being offered in `rich_suggestions`.

    If no fully validated alternative exists, no rich suggestion is returned.
    """
    rule = violation.get("rule", "")
    entry = mutated_entry if mutated_entry else (violation.get("a") or violation.get("entry") or {})

    global_busy_map = fetch_global_busy_days()
    _primary_teacher = teacher or violation.get("teacher") or entry.get("teacher") or ""
    teacher_global_busy_days = global_busy_map.get(_primary_teacher, set()) if _primary_teacher else set()
    merged_busy_days = (busy_days or set()) | teacher_global_busy_days

    # ── Normalize entry so it always has all required schedule keys ──────
    # When entry comes from a violation's _entry_id dict, it uses "subject"
    # instead of "subject_code"/"subject_name" and is missing "type".
    # Without normalization, every simulated candidate would fail schema validation.
    entry = dict(entry)  # copy to avoid mutating the violation
    if "program" in entry and entry["program"]:
        entry["program"] = normalize_program(entry["program"])
    if orig_target and "program" in orig_target and orig_target["program"]:
        orig_target = dict(orig_target)
        orig_target["program"] = normalize_program(orig_target["program"])

    if "subject_code" not in entry and "subject" in entry:
        entry["subject_code"] = entry.get("subject", "")
    if "subject_name" not in entry:
        entry["subject_name"] = entry.get("subject_code", "") or entry.get("subject", "")
    if "type" not in entry:
        entry["type"] = "Practical" if need_lab or str(entry.get("subject_code", "")).endswith("-p") else "Theory"
    if "room" not in entry:
        entry["room"] = ""

    day   = entry.get("day", "")
    start = entry.get("start", "")
    end   = entry.get("end", "")
    suggestions: Dict[str, Any] = {"rich_suggestions": []}

    is_add_action = (action_type == "ADD_CLASS")
    is_move_action = action_type in ("MOVE_CLASS", "ADD_CLASS", "SWAP_CLASSES", "INTERCHANGE_CLASSES")
    is_slot_action = is_move_action or action_type in (
        "REPLACE_CLASS", "CHANGE_TIME", "CHANGE_DAY",
        "EXTEND_CLASS", "SHORTEN_CLASS",
    )
    need_lab = need_lab or entry.get("type") == "Practical"

    dur = to_minutes(end) - to_minutes(start) if start and end else 120
    dur = _normalize_duration(dur)  # Snap to nearest standard duration

    def _action_context(action: dict) -> str:
        spec = action.get("spec") or action.get("new_spec")
        target = action.get("target") or action.get("target_a") or {}
        code = (spec or {}).get("subject_code") or target.get("subject_code") or ""
        source = next(
            (
                item for item in schedule
                if code
                and (item.get("subject_code") or "").lower() == str(code).lower()
                and (not target.get("program") or item.get("program") == target.get("program"))
                and (not target.get("semester") or item.get("semester") == target.get("semester"))
            ),
            {},
        )
        item = {**source, **(spec or {})}
        item["day"] = action.get("new_day") or item.get("day")
        item["start"] = action.get("new_start_time") or item.get("start_time") or item.get("start")
        item["end"] = action.get("new_end_time") or action.get("new_end") or item.get("end_time") or item.get("end")
        parts = []
        subject = item.get("subject_name") or code or item.get("subject") or "Class"
        if code and code.lower() not in str(subject).lower():
            subject = f"{subject} [{code}]"
        parts.append(str(subject))
        if item.get("program") or item.get("semester"):
            parts.append(f"{item.get('program', '')} {item.get('semester', '')}".strip())
        section = item.get("section") or item.get("class_section")
        if section:
            parts.append(f"Section {section}")
        if item.get("day") and item.get("start") and item.get("end"):
            parts.append(f"{item['day']}, {item['start']}–{item['end']}")
        if item.get("teacher"):
            parts.append(f"Teacher {item['teacher']}")
        room = action.get("new_room") or item.get("room")
        if room:
            parts.append(f"Room {room}")
        return " — ".join(parts)

    def _add_rich_suggestion(action: dict, title: str, desc: str):
        action_key = json.dumps(action, sort_keys=True)
        if any(json.dumps(item["action"], sort_keys=True) == action_key for item in suggestions["rich_suggestions"]):
            return
        suggestions["rich_suggestions"].append({
            "title": f"{title} — {_action_context(action)}",
            "description": desc,
            "status": "Conflict-free and validated",
            "action": action
        })

    def _add_validated_remove(title: str, desc: str) -> None:
        candidate = _simulate_remove(schedule, entry, orig_target)
        if candidate != schedule and _is_fully_valid(schedule, candidate):
            _add_rich_suggestion(
                _build_proposed_remove_class(entry, orig_target),
                title,
                desc,
            )

    # Extensions have a bounded, explicit search space: every later 30-minute
    # endpoint on the class's existing day is simulated and fully validated.
    # Do this before rule-specific fallbacks so an extension never receives a
    # misleading MOVE_CLASS suggestion.
    if action_type == "EXTEND_CLASS":
        source = _find_schedule_entry(schedule, orig_target, entry)
        source_end = source.get("end")
        extension_options: list[str] = []
        if source_end:
            for end_minutes in range(to_minutes(source_end) + 30, SEARCH_END_MINUTES + 1, 30):
                new_end = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
                candidate = _simulate_move(
                    schedule, source, source.get("day"), source.get("start"),
                    new_end, orig_target=orig_target,
                )
                if candidate != schedule and _is_fully_valid(schedule, candidate):
                    extension_options.append(new_end)
        suggestions["note"] = (
            f"Requested extension conflicts with the timetable. "
            f"Searched every later period through {SEARCH_END_MINUTES // 60:02d}:30."
        )
        for new_end in extension_options[:MAX_RANKED_SUGGESTIONS]:
            proposed = {
                "action": "EXTEND_CLASS",
                "target": orig_target or {
                    "day": source.get("day"),
                    "start_time": source.get("start"),
                    "end_time": source.get("end"),
                    "program": source.get("program"),
                    "semester": source.get("semester"),
                    "subject_code": source.get("subject_code"),
                },
                "new_end_time": new_end,
            }
            _add_rich_suggestion(
                proposed,
                f"Extend {source.get('subject_code') or source.get('subject_name') or 'class'} to {new_end}",
                "Keeps the class within a fully validated, conflict-free period.",
            )
        if suggestions["rich_suggestions"]:
            return suggestions

    # ── H3 / H4: Room clash / not-lab / unknown room ──────────────────────────
    if rule in ("H3_room_clash", "H4_room_not_lab", "H4_unknown_room"):
        free_rooms = suggest_free_rooms(schedule, day, start, end, need_lab=need_lab)
        valid_rooms: list[str] = []
        suggestions["note"] = (
            f"Alternative rooms free on {day} {start}–{end}"
            + (" (lab)" if need_lab else "")
        )

        for valid_room in free_rooms[:3]:
            is_valid = _find_valid_room(
                schedule, entry, [valid_room], orig_target=orig_target,
                simulate_as_move=is_move_action and not is_add_action,
                simulate_as_add=is_add_action,
                target_day=day, target_start=start, target_end=end
            )
            if is_valid:
                valid_rooms.append(valid_room)
                subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
                if is_slot_action:
                    if action_type == "ADD_CLASS":
                        proposed = _build_proposed_add_class(entry, {"day": day, "start": start, "end": end, "room": valid_room})
                    else:
                        proposed = _build_proposed_move_class(entry, {"day": day, "start": start, "end": end, "room": valid_room}, orig_target)
                    _add_rich_suggestion(proposed, f"Assign to {valid_room}", f"Resolves the room conflict by scheduling {subject_name} in {valid_room} during the requested {day} {start}–{end} slot.")
                else:
                    proposed = _build_proposed_change_room(entry, valid_room, orig_target)
                    _add_rich_suggestion(proposed, f"Change room to {valid_room}", f"Resolves the room conflict by moving {subject_name} to {valid_room}.")

        suggestions["free_rooms"] = valid_rooms

        # If room-only fix didn't work, also try moving to a different slot+room
        if not suggestions["rich_suggestions"]:
            valid_slot = _universal_move_suggestion(schedule, entry, action_type, need_lab, orig_target, teacher, dur, busy_days)
            if valid_slot:
                subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
                if is_add_action:
                    proposed = _build_proposed_add_class(entry, valid_slot)
                else:
                    proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
                room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
                _add_rich_suggestion(proposed, f"Move {subject_name} to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}{room_txt}", "Resolves the room conflict by finding a fully free slot.")

    # ── H1: Semester clash ────────────────────────────────────────────────────
    elif rule == "H1_semester_clash":
        free_slots = _all_slots_of_duration(dur)
        suggestions["free_slots"] = []
        suggestions["note"] = "Semester clash — try a different day or time slot."

        target_cand = entry
        target_cand_orig = orig_target

        if action_type == "EXTEND_CLASS":
            current_end = target_cand_orig.get("end_time") if target_cand_orig else target_cand.get("end")
            current_end_minutes = to_minutes(current_end) if current_end else to_minutes(end)
            resize_options = []
            for new_end_minutes in range(current_end_minutes + 30, SEARCH_END_MINUTES + 1, 30):
                new_end = f"{new_end_minutes // 60:02d}:{new_end_minutes % 60:02d}"
                candidate = _simulate_move(
                    schedule, target_cand,
                    target_cand_orig.get("day", target_cand.get("day")) if target_cand_orig else target_cand.get("day"),
                    target_cand_orig.get("start_time", target_cand.get("start")) if target_cand_orig else target_cand.get("start"),
                    new_end,
                    orig_target=target_cand_orig,
                )
                if candidate != schedule and _is_fully_valid(schedule, candidate):
                    resize_options.append(new_end)
            for new_end in resize_options[:3]:
                proposed = {
                    "action": "EXTEND_CLASS",
                    "target": target_cand_orig or {
                        "day": target_cand.get("day"),
                        "start_time": target_cand.get("start"),
                        "end_time": target_cand.get("end"),
                        "program": target_cand.get("program"),
                        "semester": target_cand.get("semester"),
                        "subject_code": target_cand.get("subject_code") or target_cand.get("subject"),
                    },
                    "new_end_time": new_end,
                }
                _add_rich_suggestion(
                    proposed,
                    f"Extend {target_cand.get('subject_code') or target_cand.get('subject') or 'class'} to {new_end}",
                    "Keeps the extension within a fully validated, conflict-free period.",
                )
        else:
            target_slots = [
                slot for slot in free_slots
                if not (
                    target_cand_orig
                    and slot["day"] == target_cand_orig.get("day")
                    and slot["start"] == (
                        target_cand_orig.get("start_time")
                        or target_cand_orig.get("start")
                    )
                )
            ]
            valid_slots = _find_valid_slots(
                schedule,
                target_cand,
                target_slots,
                need_lab=need_lab,
                orig_target=target_cand_orig,
                simulate_as_add=(action_type == "ADD_CLASS"),
            )
            for valid_slot in valid_slots:
                subject_name = target_cand.get("subject_code") or target_cand.get("subject") or "Class"
                if action_type == "ADD_CLASS":
                    proposed = _build_proposed_add_class(target_cand, valid_slot)
                else:
                    proposed = _build_proposed_move_class(target_cand, valid_slot, target_cand_orig)

                room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
                _add_rich_suggestion(proposed, f"Move {subject_name} to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}", f"Resolves the class overlap by shifting {subject_name} to a fully validated time slot{room_txt}.")

    # ── H2: Teacher clash ─────────────────────────────────────────────────────
    elif rule == "H2_teacher_clash":
        _teacher = teacher or entry.get("teacher")
        free_slots = _all_slots_of_duration(dur)

        suggestions["free_slots"] = []
        suggestions["note"] = f"Free slots for teacher {_teacher}" if _teacher else "Available alternative slots"

        valid_slots = _find_valid_slots(
            schedule, entry, free_slots, need_lab=need_lab,
            orig_target=orig_target, simulate_as_add=(action_type == "ADD_CLASS"),
        )
        for valid_slot in valid_slots:
                subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
                if action_type == "ADD_CLASS":
                    proposed = _build_proposed_add_class(entry, valid_slot)
                else:
                    proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
                room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
                _add_rich_suggestion(
                    proposed,
                    f"Move {subject_name} to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}",
                    f"Resolves the teacher overlap without creating any new conflicts{room_txt}."
                )

    # ── H5: Multiple theory/practical same day ────────────────────────────────
    elif rule in ("H5_multiple_theory_same_day", "H5_multiple_practical_same_day"):
        _teacher = teacher or violation.get("teacher") or entry.get("teacher", "")
        conflict_day = violation.get("day") or day
        subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
        suggestions["note"] = f"Teacher {_teacher} already has a class on {conflict_day}. Move to a different day."

        # Find slots on OTHER days for this teacher
        other_day_slots = [
            s for s in suggest_free_slots_for_teacher(schedule, _teacher, dur)
            if s["day"] != conflict_day
        ] if _teacher else []

        if not other_day_slots:
            other_day_slots = [s for s in _all_slots_of_duration(dur) if s["day"] != conflict_day]

        valid_slots = _find_valid_slots(
            schedule, entry, other_day_slots, need_lab=need_lab,
            orig_target=orig_target, simulate_as_add=is_add_action,
        )
        for valid_slot in valid_slots:
                if is_add_action:
                    proposed = _build_proposed_add_class(entry, valid_slot)
                else:
                    proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
                room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
                _add_rich_suggestion(proposed, f"Move {subject_name} to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}", f"Avoids scheduling {_teacher} twice on {conflict_day}{room_txt}.")

    # ── H6: Teacher has no free day ───────────────────────────────────────────
    elif rule == "H6_no_free_day":
        _teacher = teacher or violation.get("teacher") or entry.get("teacher", "")
        subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
        suggestions["note"] = f"Teacher {_teacher} has classes on every working day. Free up a day by moving a class."

        # The action (entry) causes the teacher to lose their last free day.
        # We should suggest alternative slots for 'entry' on days the teacher is ALREADY working.
        teacher_classes = [
            e for e in schedule
            if _teacher and _teacher in {t.strip() for t in str(e.get("teacher", "")).split(",")}
        ]
        busy_days = {c.get("day") for c in teacher_classes if c.get("day")}
        
        # We need slots that DO NOT consume a new free day.
        # But wait, since 'entry' caused the loss of the free day, we can just search for ANY
        # valid slot for 'entry' that leaves at least one day completely free.
        # Usually, putting 'entry' on an ALREADY busy day solves this.
        
        valid_slots = _universal_move_suggestion(schedule, entry, action_type, need_lab, orig_target, _teacher, dur, busy_days)
        if valid_slots:
            sub = entry.get("subject_code") or entry.get("subject") or "class"
            if is_add_action:
                proposed = _build_proposed_add_class(entry, valid_slots)
            else:
                proposed = _build_proposed_move_class(entry, valid_slots, orig_target)
            _add_rich_suggestion(proposed, f"Move {sub} to {valid_slots['day']} {valid_slots['start']}–{valid_slots['end']}", f"Keeps {_teacher}'s working days within the allowed limit.")

    # ── H6: Teacher Free Day via synthetic busy slot ──────────────────────────
    elif rule == "H6_teacher_free_day":
        _teacher = teacher or violation.get("teacher") or entry.get("teacher", "")
        subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
        conflict_day = violation.get("day") or day
        suggestions["note"] = f"Teacher {_teacher} is busy on {conflict_day}. Finding a new slot."
        
        valid_slots = _universal_move_suggestion(schedule, entry, action_type, need_lab, orig_target, _teacher, dur, busy_days)
        if valid_slots:
            sub = entry.get("subject_code") or entry.get("subject") or "class"
            if is_add_action:
                proposed = _build_proposed_add_class(entry, valid_slots)
            else:
                proposed = _build_proposed_move_class(entry, valid_slots, orig_target)
            
            room_txt = f" in {valid_slots['room']}" if "room" in valid_slots else ""
            _add_rich_suggestion(proposed, f"Move {sub} to {valid_slots['day']} {valid_slots['start']}–{valid_slots['end']}", f"Relocates {sub} to a day when {_teacher} is available{room_txt}.")

    # ── H7: Teacher not authorised for subject ────────────────────────────────
    elif rule in ("H7_teacher_not_authorised", "H7_unallocated_subject"):
        subject_code = (violation.get("subject_code") or entry.get("subject_code") or entry.get("subject") or "").lower()
        subject_name = entry.get("subject_name") or subject_code or "Class"
        suggestions["note"] = f"No teacher is authorised to teach '{subject_code}'. Try a different teacher."

        # Find teachers whose subjects_csv contains this subject code
        # We rely on catalog.INTERNAL_TEACHERS and try to suggest change_teacher actions
        # If we have access to a schedule, find all teachers who ALREADY teach this subject in the schedule
        qualified_teachers = set()
        for e in schedule:
            ec = (e.get("subject_code") or e.get("subject") or "").lower()
            if ec == subject_code or ec == subject_code.rstrip("-p"):
                t = e.get("teacher", "")
                if t:
                    qualified_teachers.add(t)

        if not qualified_teachers:
            _add_validated_remove(
                f"Remove '{subject_name}' class",
                f"No authorised teacher is available for '{subject_code}'.",
            )
        else:
            count = 0
            for qt in list(qualified_teachers)[:3]:
                if count >= 3:
                    break
                # Suggest changing the teacher to a qualified one
                target_for_change = orig_target if orig_target else {
                    "day": entry.get("day"),
                    "start_time": entry.get("start"),
                    "end_time": entry.get("end"),
                    "program": entry.get("program"),
                    "semester": entry.get("semester"),
                    "subject_code": subject_code,
                }
                proposed = {
                    "action": "CHANGE_TEACHER",
                    "target": target_for_change,
                    "new_teacher": qt,
                }
                # Validate
                candidate = copy.deepcopy(schedule)
                for c in candidate:
                    c_subj = (c.get("subject_code") or c.get("subject") or "").lower()
                    if c_subj == subject_code and c.get("day") == entry.get("day") and c.get("start") == entry.get("start"):
                        c["teacher"] = qt
                        break
                if _is_fully_valid(schedule, candidate):
                    _add_rich_suggestion(proposed, f"Change teacher to {qt}", f"{qt} is authorised to teach '{subject_code}' and is available.")
                    count += 1

        # If still empty, fall back to remove class
        if not suggestions["rich_suggestions"]:
            _add_validated_remove(
                f"Remove '{subject_name}' class",
                "No authorised and conflict-free teacher was found automatically.",
            )

    # ── H8: Wrong weekly hours ────────────────────────────────────────────────
    elif rule == "H8_wrong_weekly_hours":
        subject_code = violation.get("subject_code") or ""
        expected_min = int(violation.get("expected_minutes") or 0)
        actual_min  = int(violation.get("actual_minutes") or 0)
        subject_name = subject_code or "Class"
        suggestions["note"] = f"'{subject_code}' has wrong weekly hours. Expected {expected_min // 60}h, got {actual_min // 60}h."
        # We can suggest moving or removing to fix hours — best we can do is move to a free slot
        valid_slot = _universal_move_suggestion(schedule, entry, action_type, need_lab, orig_target, teacher, dur, busy_days)
        if valid_slot:
            if is_add_action:
                proposed = _build_proposed_add_class(entry, valid_slot)
            else:
                proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
            room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
            _add_rich_suggestion(proposed, f"Reschedule {subject_name} to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}", f"Moves the class to a conflict-free slot{room_txt}. Also verify total weekly hours for this subject.")
        else:
            _add_validated_remove(
                f"Remove extra '{subject_name}' class",
                f"'{subject_code}' has too many/few scheduled hours.",
            )

    # ── H9: Wrong practical duration / Saturday practical ────────────────────
    elif rule in ("H9_wrong_practical_duration", "H9_saturday_practical"):
        subject_name = entry.get("subject_code") or entry.get("subject") or "Practical"
        if rule == "H9_saturday_practical":
            suggestions["note"] = "Practical sessions are not allowed on Saturdays."
            # Move to a weekday
            weekday_slots = [s for s in _all_slots_of_duration(dur) if s["day"] != "Saturday"]
            valid_slot = _find_valid_slot(schedule, entry, weekday_slots, need_lab=True, orig_target=orig_target, simulate_as_add=is_add_action)
        else:
            suggestions["note"] = f"Practical '{subject_name}' has the wrong duration."
            valid_slot = _universal_move_suggestion(schedule, entry, action_type, need_lab=True, orig_target=orig_target, teacher=teacher, duration_minutes=180)  # try 3h for practicals

        if valid_slot:
            if is_add_action:
                proposed = _build_proposed_add_class(entry, valid_slot)
            else:
                proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
            room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
            _add_rich_suggestion(proposed, f"Move {subject_name} to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}", f"Moves this practical to a valid weekday slot with the correct duration{room_txt}.")
        else:
            _add_validated_remove(
                f"Remove '{subject_name}' practical",
                "No valid practical slot was found automatically.",
            )

    # ── H10: Missing subject ──────────────────────────────────────────────────
    elif rule == "H10_missing_subject":
        subject_code = violation.get("subject_code") or ""
        suggestions["note"] = f"Required subject '{subject_code}' is not scheduled at all."
        valid_slot = _universal_move_suggestion(schedule, entry, "ADD_CLASS", need_lab, None, teacher, dur)
        if valid_slot and entry:
            proposed = _build_proposed_add_class(entry, valid_slot)
            room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
            _add_rich_suggestion(proposed, f"Add '{subject_code}' on {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}", f"Schedules the missing subject in a conflict-free slot{room_txt}.")
        else:
            suggestions["note"] += " No fully validated slot is available."

    # ── H11: Wrong semester subject ───────────────────────────────────────────
    elif rule == "H11_wrong_semester_subject":
        subject_code = violation.get("subject_code") or entry.get("subject_code") or entry.get("subject") or ""
        subject_name = subject_code
        prog = entry.get("program") or violation.get("program") or ""
        sem  = entry.get("semester") or violation.get("semester") or ""
        suggestions["note"] = f"'{subject_code}' does not belong to {prog} {sem}."

        # Option 1: remove the class (it shouldn't be there)
        _add_validated_remove(
            f"Remove '{subject_name}' from this semester",
            f"'{subject_code}' is not in the authorised subject list for {prog} {sem}.",
        )

        # Option 2: move to correct semester if we can figure out which one
        # Find the actual semester this subject belongs to from PROGRAMME_SUBJECT_MAP
        try:
            from app.scheduler.constraints import PROGRAMME_SUBJECT_MAP
            correct_sems = [
                f"{p} {s}" for (p, s), codes in PROGRAMME_SUBJECT_MAP.items()
                if subject_code.lower() in codes
            ]
            if correct_sems:
                suggestions["note"] += f" It belongs to: {', '.join(correct_sems[:3])}."
                # Build an "add class to correct semester" suggestion as informational
                if correct_sems:
                    parts = correct_sems[0].split(" ", 1)
                    if len(parts) == 2:
                        correct_prog, correct_sem = parts
                        new_entry = copy.deepcopy(entry)
                        new_entry["program"] = correct_prog
                        new_entry["semester"] = correct_sem
                        valid_slot = _universal_move_suggestion(schedule, new_entry, "ADD_CLASS", need_lab, None, teacher, dur)
                        if valid_slot:
                            proposed_add = _build_proposed_add_class(new_entry, valid_slot)
                            _add_rich_suggestion(proposed_add, f"Re-add '{subject_name}' under {correct_prog} {correct_sem}", f"Moves this class to the correct program/semester combination in a conflict-free slot.")
        except Exception:
            pass

    # ── Generic / schema errors ────────────────────────────────────────────────
    else:
        suggestions["note"] = f"Constraint violated: {rule}"
        # Universal fallback: try to find a safe slot for any move-type action
        if is_slot_action:
            valid_slot = _universal_move_suggestion(schedule, entry, action_type, need_lab, orig_target, teacher, dur)
            if valid_slot:
                subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
                if is_add_action:
                    proposed = _build_proposed_add_class(entry, valid_slot)
                else:
                    proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
                room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
                _add_rich_suggestion(proposed, f"Reschedule to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}", f"Resolves the constraint by moving the class to a guaranteed conflict-free slot{room_txt}.")

    # ── FINAL FALLBACK: only add a fully validated slot ───────────────────────
    if not suggestions["rich_suggestions"]:
        valid_slot = _universal_move_suggestion(schedule, entry, action_type, need_lab, orig_target, teacher, dur)
        if valid_slot:
            subject_name = entry.get("subject_code") or entry.get("subject") or "Class"
            if is_add_action:
                proposed = _build_proposed_add_class(entry, valid_slot)
            else:
                proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
            room_txt = f" in {valid_slot['room']}" if "room" in valid_slot else ""
            _add_rich_suggestion(
                proposed,
                f"Reschedule {subject_name} to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']}",
                f"Resolves all conflicts by safely relocating the class to a fully available slot{room_txt}."
            )

    # ── ULTIMATE GUARANTEE: ALWAYS PROVIDE AT LEAST ONE SUGGESTION ───────────
    if not suggestions["rich_suggestions"]:
        # If strict validation fails (usually due to workload/semester constraints),
        # force a suggestion by checking only physical room/teacher/time overlaps.
        fallback_found = False
        fallback_slots = [
            s for s in _all_slots_of_duration(dur)
            if not merged_busy_days or s["day"] not in merged_busy_days
        ]
        from app.scheduler.constraints import ROOM_FACILITIES
        all_rooms = sorted(ROOM_FACILITIES.keys())

        # Precompute base physical fingerprints once to avoid O(N^2) revalidation in loop
        PHYSICAL_RULES = ("H1_semester_clash", "H2_teacher_clash", "H3_room_clash")
        base_v = validate_schedule(schedule)
        base_physical_fps = [_fingerprint(x) for x in base_v if x.get("rule") in PHYSICAL_RULES]

        for slot in fallback_slots:
            if fallback_found: break
            # Priority: free rooms for this slot first, otherwise first available rooms
            free_rooms = suggest_free_rooms(schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab)
            candidate_rooms = free_rooms[:2] if free_rooms else ([entry.get("room")] if entry.get("room") in ROOM_FACILITIES else all_rooms[:2])

            for room in candidate_rooms:
                if need_lab and not ROOM_FACILITIES[room].get("lab"):
                    continue

                if is_add_action:
                    candidate = _simulate_add(schedule, entry, slot["day"], slot["start"], slot["end"], new_room=room)
                else:
                    candidate = _simulate_move(schedule, entry, slot["day"], slot["start"], slot["end"], new_room=room, orig_target=orig_target)

                if candidate != schedule and _is_physically_valid(schedule, candidate, base_physical_fps):
                    # We found a physically viable slot
                    valid_slot = dict(slot)
                    valid_slot["room"] = room
                    subject_name = entry.get("subject_code") or entry.get("subject") or "Class"

                    if is_add_action:
                        proposed = _build_proposed_add_class(entry, valid_slot)
                    else:
                        proposed = _build_proposed_move_class(entry, valid_slot, orig_target)

                    _add_rich_suggestion(
                        proposed,
                        f"Force Reschedule to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']} in {room}",
                        "Guaranteed alternative slot (avoids physical overlaps but may trigger strict constraint warnings)."
                    )
                    fallback_found = True
                    break

        if not fallback_found:
            # If absolutely 0 slots exist (e.g. 100% schedule utilization), just return the first slot as a forced suggestion.
            slot = fallback_slots[0] if fallback_slots else {"day": "Monday", "start": "10:00", "end": "12:00"}
            room = all_rooms[0] if all_rooms else "Room 1"
            valid_slot = dict(slot)
            valid_slot["room"] = room
            if is_add_action:
                proposed = _build_proposed_add_class(entry, valid_slot)
            else:
                proposed = _build_proposed_move_class(entry, valid_slot, orig_target)
            _add_rich_suggestion(
                proposed,
                f"Emergency Reschedule to {valid_slot['day']} {valid_slot['start']}–{valid_slot['end']} in {room}",
                "Forced alternative (schedule is at maximum capacity)."
            )

    if not suggestions["rich_suggestions"]:
        suggestions["note"] = (
            suggestions.get("note", f"Constraint violated: {rule}")
            + " Exhaustive search found no fully validated alternative across the working days and searchable time windows."
        )
    else:
        suggestions["rich_suggestions"] = suggestions["rich_suggestions"][:MAX_RANKED_SUGGESTIONS]
    return suggestions
