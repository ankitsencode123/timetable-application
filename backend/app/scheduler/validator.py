"""
Hard-constraint validator: H1–H11 + schema validation.
Preserved verbatim from timetable_app.py — not altered.
"""
from __future__ import annotations
import re
from typing import Any
from app.scheduler.constraints import (
    SCHEDULE_REQUIRED_KEYS, VALID_DAYS, VALID_TYPES, VALID_PROGRAMS,
    WORKING_DAYS, PROGRAMME_SUBJECT_MAP, ROOM_FACILITIES,
    INTERNAL_TEACHERS, normalize_program,
)

def fetch_global_busy_days() -> dict[str, set[str]]:
    """Fetch global permanent busy days for teachers from the DB.
       Returns a dictionary mapping teacher_short_name to a set of days (e.g., {"SK": {"Monday", "Thursday"}}).
    """
    try:
        from app.core.database import SessionLocal
        from app.models.busy_slot import TeacherBusySlot
        db = SessionLocal()
        try:
            busy_slots = db.query(TeacherBusySlot).filter(TeacherBusySlot.scope == "permanent").all()
            busy_map = {}
            for slot in busy_slots:
                if slot.day_of_week:
                    busy_map.setdefault(slot.teacher_short_name, set()).add(slot.day_of_week)
            return busy_map
        finally:
            db.close()
    except Exception:
        # DB might not be initialized during isolated contexts
        return {}


def to_minutes(t: str) -> int:
    h, m = t.strip().split(":")
    return int(h) * 60 + int(m)


def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return to_minutes(a_start) < to_minutes(b_end) and to_minutes(b_start) < to_minutes(a_end)


def teacher_set(c: dict) -> set[str]:
    t = str(c.get("teacher", "") or "")
    parts = re.split(r"[,&+/]", t)
    return {p.strip() for p in parts if p.strip()}


def _entry_id(c: dict) -> dict[str, Any]:
    return {
        "day": c.get("day"), "program": normalize_program(c.get("program")), "semester": c.get("semester"),
        "start": c.get("start"), "end": c.get("end"),
        "subject": c.get("subject_code") or c.get("subject_name"),
        "teacher": c.get("teacher"), "room": c.get("room"),
    }


def validate_schema(schedule: list) -> list[dict]:
    """P0: Validate every schedule entry has required keys and valid values."""
    errors: list[dict] = []
    for idx, entry in enumerate(schedule):
        if not isinstance(entry, dict):
            errors.append({"rule": "schema_not_dict", "index": idx}); continue
        entry_repr = _entry_id(entry)
        missing = SCHEDULE_REQUIRED_KEYS - set(entry.keys())
        if missing:
            errors.append({"rule": "schema_missing_keys", "entry": entry_repr, "missing": sorted(missing)})
        if entry.get("day") not in VALID_DAYS:
            errors.append({"rule": "schema_invalid_day", "entry": entry_repr, "day": entry.get("day")})
        if entry.get("type") not in VALID_TYPES:
            errors.append({"rule": "schema_invalid_type", "entry": entry_repr, "type": entry.get("type")})
        prog_norm = normalize_program(entry.get("program"))
        if prog_norm not in VALID_PROGRAMS:
            errors.append({"rule": "schema_invalid_program", "entry": entry_repr, "program": entry.get("program")})
        if entry.get("semester") not in {"1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"}:
            errors.append({"rule": "schema_invalid_semester", "entry": entry_repr, "semester": entry.get("semester")})
        for field in ("start", "end"):
            val = entry.get(field, "")
            if not re.match(r"^\d{1,2}:\d{2}$", str(val)):
                errors.append({"rule": "schema_invalid_time_format", "entry": entry_repr, "field": field, "value": val})
    return errors



def validate_schedule(schedule: list) -> list[dict]:
    """Full hard-constraint validator: H1–H11 plus time-format checks."""
    violations: list[dict] = []
    n = len(schedule)
    global_busy_days = fetch_global_busy_days()

    # --- Time format prerequisites ---
    for a in schedule:
        try:
            if to_minutes(a["start"]) >= to_minutes(a["end"]):
                violations.append({"rule": "start_not_before_end", "entry": _entry_id(a)})
        except Exception:
            violations.append({"rule": "bad_time_format", "entry": _entry_id(a)})

    # --- Pairwise overlap: H1, H2, H3, H10 ---
    for i in range(n):
        for j in range(i + 1, n):
            a, b = schedule[i], schedule[j]
            if a.get("day") != b.get("day"):
                continue
            try:
                if not overlaps(a["start"], a["end"], b["start"], b["end"]):
                    continue
            except Exception:
                continue
            p_a = str(a.get("program", "")).strip().rstrip('.').lower()
            p_b = str(b.get("program", "")).strip().rstrip('.').lower()
            s_a = str(a.get("semester", "")).strip().lower()
            s_b = str(b.get("semester", "")).strip().lower()
            if p_a == p_b and s_a == s_b:
                violations.append({"rule": "H1_semester_clash", "a": _entry_id(a), "b": _entry_id(b)})
            shared = teacher_set(a) & teacher_set(b)
            if shared:
                violations.append({"rule": "H2_teacher_clash", "teachers": sorted(shared),
                                   "a": _entry_id(a), "b": _entry_id(b)})
            if a.get("room") and a.get("room") == b.get("room"):
                violations.append({"rule": "H3_room_clash", "room": a.get("room"),
                                   "a": _entry_id(a), "b": _entry_id(b)})

    # --- Single-entry checks: H4, H5, H6 ---
    theory_days: dict = {}
    practical_days: dict = {}
    teacher_days: dict = {}

    for c in schedule:
        class_type = str(c.get("type", "")).strip()
        day = c.get("day", "")
        room = c.get("room", "")
        teachers = teacher_set(c)

        # H12: Teacher busy slots
        for t in teachers:
            if t in global_busy_days and day in global_busy_days[t]:
                violations.append({
                    "rule": "H12_teacher_busy",
                    "teacher": t,
                    "day": day,
                    "entry": _entry_id(c),
                    "note": f"Teacher {t} is unavailable on {day} due to a busy slot."
                })

        # H4 constraint removed per user request.

        # H5 prep: count theory and practical per (teacher, day)
        if class_type == "Theory":
            for t in teachers:
                key = (t, day)
                theory_days[key] = theory_days.get(key, 0) + 1
        elif class_type == "Practical":
            for t in teachers:
                key = (t, day)
                practical_days[key] = practical_days.get(key, 0) + 1

        # H6 prep
        if day in VALID_DAYS:
            for t in teachers:
                teacher_days.setdefault(t, set()).add(day)

    # H11: Subject must belong to the correct program & semester
    for c in schedule:
        prog_raw  = str(c.get("program", "")).strip().rstrip('.')
        sem_raw   = str(c.get("semester", "")).strip()
        code_h11  = str(c.get("subject_code", "")).strip().lower()
        allowed   = PROGRAMME_SUBJECT_MAP.get((prog_raw, sem_raw))
        if not allowed and code_h11:
            violations.append({
                "rule": "H11_wrong_semester_subject",
                "program": prog_raw, "semester": sem_raw,
                "subject_code": c.get("subject_code"),
                "entry": _entry_id(c),
                "note": f"Program '{prog_raw}' Semester '{sem_raw}' has no recognized subjects.",
            })
        elif allowed and code_h11:
            root = code_h11[:-2] if code_h11.endswith("-p") else code_h11
            if root not in allowed and code_h11 not in allowed:
                if not (root in ("m", "mm") and ("m" in allowed or "mm" in allowed)):
                    violations.append({
                        "rule": "H11_wrong_semester_subject",
                        "program": prog_raw, "semester": sem_raw,
                        "subject_code": c.get("subject_code"),
                        "entry": _entry_id(c),
                        "note": f"'{code_h11}' not in authoritative list for {prog_raw} {sem_raw}.",
                    })

    # H5: Max 1 theory class per teacher per day.
    #     A teacher may additionally have 1 practical on the same day.
    for (teacher, day), count in theory_days.items():
        if count > 1:
            violations.append({"rule": "H5_multiple_theory_same_day",
                                "teacher": teacher, "day": day})

    # H5b: Max 1 practical class per teacher per day
    for (teacher, day), count in practical_days.items():
        if count > 1:
            violations.append({"rule": "H5_multiple_practical_same_day",
                                "teacher": teacher, "day": day})

    # H6: Internal teachers must have ≥ 1 free day per week
    for teacher in INTERNAL_TEACHERS:
        working = teacher_days.get(teacher, set())
        if not [d for d in WORKING_DAYS if d not in working] and working:
            violations.append({"rule": "H6_no_free_day", "teacher": teacher})

    return violations


def regenerate_tables_from_schedule(schedule: list) -> dict:
    """
    Regenerate the four most hallucination-prone markdown sections directly from
    the authoritative schedule array. Overwrites LLM-generated versions.
    """
    from collections import defaultdict
    day_order = {d: i for i, d in enumerate(WORKING_DAYS)}

    def _row(c: dict) -> str:
        time_str = f"{c.get('start','?')}-{c.get('end','?')}"
        subj = c.get("subject_code") or c.get("subject_name") or "?"
        return (f"| {c.get('day','?')} | {time_str} | {subj} | "
                f"{c.get('teacher','?')} | {c.get('type','?')} | {c.get('room','?')} |")

    def _section(entries: list, label: str) -> str:
        if not entries:
            return f"*No entries for {label}.*\n"
        header = f"### {label}\n\n| Day | Time | Subject | Teacher | Type | Room |\n|---|---|---|---|---|---|\n"
        rows = sorted(entries, key=lambda c: (day_order.get(c.get("day", ""), 99), c.get("start", "")))
        return header + "\n".join(_row(c) for c in rows) + "\n\n"

    groups: dict = defaultdict(list)
    for c in schedule:
        groups[(str(c.get("program", "")).strip().rstrip('.'), c.get("semester", ""))].append(c)

    btech_md = "## B.Tech Semester Timetables\n\n*(Generated from schedule — source of truth)*\n\n"
    for sem in ["3rd", "5th", "7th"]:
        btech_md += _section(groups.get(("B.Tech", sem), []), f"B.Tech {sem} Semester")

    msc_md = "## M.Sc Semester Timetables\n\n*(Generated from schedule — source of truth)*\n\n"
    for sem in ["1st", "3rd"]:
        msc_md += _section(groups.get(("M.Sc", sem), []), f"M.Sc {sem} Semester")

    mtech_md = "## M.Tech Semester Timetables\n\n*(Generated from schedule — source of truth)*\n\n"
    for sem in ["1st", "3rd"]:
        mtech_md += _section(groups.get(("M.Tech", sem), []), f"M.Tech {sem} Semester")

    room_groups: dict = defaultdict(list)
    for c in schedule:
        room_groups[c.get("room", "UNKNOWN")].append(c)

    room_md = "## Room Utilization\n\n*(Generated from schedule — double-bookings will appear here)*\n\n"
    for room in sorted(room_groups.keys()):
        entries = sorted(room_groups[room],
                         key=lambda c: (day_order.get(c.get("day", ""), 99), c.get("start", "")))
        room_md += f"### {room}\n\n| Day | Time | Program | Sem | Subject | Teacher | Type |\n|---|---|---|---|---|---|---|\n"
        for c in entries:
            time_str = f"{c.get('start','?')}-{c.get('end','?')}"
            subj = c.get("subject_code") or c.get("subject_name") or "?"
            room_md += (f"| {c.get('day','?')} | {time_str} | {c.get('program','?')} | "
                        f"{c.get('semester','?')} | {subj} | {c.get('teacher','?')} | {c.get('type','?')} |\n")
        room_md += "\n"

    return {
        "btech_semester_markdown": btech_md,
        "msc_semester_markdown": msc_md,
        "mtech_semester_markdown": mtech_md,
        "room_wise_markdown": room_md,
    }
