import datetime as dt
from unittest.mock import MagicMock
from app.calendar_system import Ctx, _resolve_day
from app.models.busy_slot import TeacherBusySlot

def test_resolve_day_moves_busy_teacher_classes_to_cancelled():
    target_date = dt.date(2026, 10, 14) # Wednesday
    mock_db = MagicMock()
    
    # Base entry on Wednesday
    base_entry = {
        "day": "Wednesday",
        "program": "M.Sc.",
        "semester": "1st",
        "start": "10:00",
        "end": "12:00",
        "subject_code": "ES101",
        "subject_name": "ES & IoT",
        "teacher": "TK",
        "type": "Theory",
        "room": "101",
    }
    
    # Create Ctx with mock entries and a temporary busy slot for TK on 2026-10-14
    slot = TeacherBusySlot(
        teacher_short_name="TK",
        scope="temporary",
        specific_date=target_date,
        reason="Medical leave",
    )
    
    ctx = Ctx(
        db=mock_db,
        windows=[],
        overrides={},
        published_id=1,
        busy_slots={target_date: [slot]},
        entries_cache={1: [base_entry]},
        status_cache={1: "PUBLISHED"},
    )
    
    res = _resolve_day(ctx, target_date)
    
    assert res["has_overrides"] is True
    assert len(res["entries"]) == 0
    assert len(res["cancelled"]) == 1
    cancelled_item = res["cancelled"][0]
    assert cancelled_item["teacher"] == "TK"
    assert cancelled_item["cal_cancelled_by"] == "teacher_busy"
    assert "Teacher TK unavailable: Medical leave" in cancelled_item["cal_note"]
    assert any("Teacher TK is unavailable" in n for n in res["notices"])
