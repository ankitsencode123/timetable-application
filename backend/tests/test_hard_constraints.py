"""Unit tests verifying that hard constraints can never be bypassed or suggested."""
import pytest
from app.actions.suggestions import _is_fully_valid, _is_physically_valid, suggest_alternatives
from app.actions.types import parse_action
from app.actions import executor
from app.scheduler.validator import validate_schedule


def _entry(day, start, end, code, teacher, room, prog="M.Tech", sem="1st"):
    return {
        "day": day,
        "program": prog,
        "semester": sem,
        "start": start,
        "end": end,
        "subject_code": code,
        "subject_name": code,
        "teacher": teacher,
        "type": "Theory",
        "room": room,
    }


def test_is_physically_valid_rejects_semester_clash():
    base = [
        _entry("Wednesday", "14:30", "17:30", "PE-1:SC-P", "SR", "R#208"),
    ]
    # Adding overlapping class in same semester
    cand = [
        _entry("Wednesday", "14:30", "17:30", "PE-1:SC-P", "SR", "R#208"),
        _entry("Wednesday", "14:00", "17:00", "aa", "PB", "R#303"),
    ]
    assert not _is_physically_valid(base, cand)
    assert not _is_fully_valid(base, cand)


def test_is_physically_valid_rejects_teacher_clash():
    base = [
        _entry("Wednesday", "10:00", "12:00", "DS", "PB", "R#205", prog="B.Tech", sem="3rd"),
    ]
    cand = [
        _entry("Wednesday", "10:00", "12:00", "DS", "PB", "R#205", prog="B.Tech", sem="3rd"),
        _entry("Wednesday", "11:00", "13:00", "AA", "PB", "R#209", prog="M.Tech", sem="1st"),
    ]
    assert not _is_physically_valid(base, cand)
    assert not _is_fully_valid(base, cand)


def test_is_physically_valid_rejects_room_clash():
    base = [
        _entry("Wednesday", "14:30", "17:30", "PE-1:SC-P", "SR", "R#208", prog="M.Tech", sem="1st"),
    ]
    cand = [
        _entry("Wednesday", "14:30", "17:30", "PE-1:SC-P", "SR", "R#208", prog="M.Tech", sem="1st"),
        _entry("Wednesday", "14:30", "15:30", "CG-P", "GM", "R#208", prog="M.Sc", sem="1st"),
    ]
    assert not _is_physically_valid(base, cand)
    assert not _is_fully_valid(base, cand)


def test_suggestions_never_return_hard_clashes():
    # Base schedule with no free slots in Wednesday afternoon for M.Tech 1st
    base = [
        _entry("Wednesday", "14:30", "17:30", "PE-1:SC-P", "SR", "R#208"),
    ]
    clashing_entry = _entry("Wednesday", "14:00", "17:00", "aa", "PB", "R#303")
    failed_schedule = [base[0], clashing_entry]

    violation = {
        "rule": "H1_semester_clash",
        "a": clashing_entry,
        "b": base[0],
    }

    sugg = suggest_alternatives(
        base,
        "MOVE_CLASS",
        violation,
        teacher="PB",
        orig_target={
            "day": "Wednesday",
            "start_time": "14:00",
            "end_time": "17:00",
            "program": "M.Tech",
            "semester": "1st",
            "subject_code": "aa",
            "teacher": "PB",
        },
        mutated_entry=clashing_entry,
    )

    # Every rich suggestion returned MUST be conflict-free regarding hard rules
    for item in sugg.get("rich_suggestions", []):
        act = parse_action(item["action"])
        candidate, _, _ = executor.apply_move_class(
            failed_schedule,
            act.target,
            act.new_day,
            act.new_start_time,
            act.new_end_time,
            act.new_room,
        )
        cand_v = validate_schedule(candidate)
        assert not any(v.get("rule") in {"H1_semester_clash", "H2_teacher_clash", "H3_room_clash", "H12_teacher_busy"} for v in cand_v)
