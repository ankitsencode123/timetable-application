from app.actions.suggestions import _is_fully_valid, suggest_alternatives
from app.actions import executor
from app.actions.types import parse_action
from app.scheduler.validator import validate_schedule


def _entry(day, start, end, code, teacher, room, subject_name=None):
    return {
        "day": day,
        "program": "M.Tech",
        "semester": "1st",
        "start": start,
        "end": end,
        "subject_code": code,
        "subject_name": subject_name or code,
        "teacher": teacher,
        "type": "Theory",
        "room": room,
    }


def test_move_suggestions_search_and_validate_all_windows():
    schedule = [
        _entry("Wednesday", "14:30", "16:30", "mfcs", "SK", "R#207B", "MFCS"),
        _entry("Monday", "14:30", "16:30", "wmc", "SK", "R#208", "Wireless and Mobile Computing"),
    ]
    failed_schedule = [dict(schedule[1], day="Wednesday"), schedule[0]]
    violation = next(
        item for item in validate_schedule(failed_schedule)
        if item["rule"] == "H1_semester_clash"
    )

    suggestions = suggest_alternatives(
        schedule,
        "MOVE_CLASS",
        violation,
        teacher="SK",
        orig_target={
            "day": "Monday",
            "start_time": "14:30",
            "end_time": "16:30",
            "program": "M.Tech",
            "semester": "1st",
            "subject_code": "wmc",
        },
        mutated_entry=failed_schedule[0],
    )

    assert suggestions["rich_suggestions"]
    assert len(suggestions["rich_suggestions"]) == 3
    for item in suggestions["rich_suggestions"]:
        action = parse_action(item["action"])
        candidate, _, _ = executor.apply_move_class(
            schedule,
            action.target,
            action.new_day,
            action.new_start_time,
            action.new_end_time,
            action.new_room,
        )
        assert _is_fully_valid(schedule, candidate)
        assert not (
            action.new_day == "Wednesday"
            and action.new_start_time == "14:30"
        )
        assert "M.Tech 1st" in item["title"]
        assert item["status"] == "Conflict-free and validated"


def test_add_class_suggestions_for_h5_violation():
    schedule = [
        _entry("Thursday", "12:00", "14:00", "algo", "SC", "R#205", "Algorithms"),
    ]
    added_entry = {
        "day": "Thursday",
        "start": "10:00",
        "end": "12:00",
        "program": "B.Tech",
        "semester": "5th",
        "subject_code": "dbms",
        "subject_name": "DBMS",
        "teacher": "SC",
        "type": "Theory",
        "room": "R#207B",
    }
    violation = {
        "rule": "H5_multiple_theory_same_day",
        "teacher": "SC",
        "day": "Thursday",
    }
    suggestions = suggest_alternatives(
        schedule,
        "ADD_CLASS",
        violation,
        teacher="SC",
        mutated_entry=added_entry,
    )

    assert suggestions["rich_suggestions"]
    assert len(suggestions["rich_suggestions"]) > 0
    for item in suggestions["rich_suggestions"]:
        act = item["action"]
        assert act["action"] == "ADD_CLASS"
        spec = act["spec"]
        assert spec["program"] == "B.Tech"
        assert spec["semester"] == "5th"
        assert spec["subject_code"] == "dbms"
        assert spec["teacher"] == "SC"
        assert spec["day"] != "Thursday"
        assert spec["room"]
        assert "Schedule" in item["title"]

