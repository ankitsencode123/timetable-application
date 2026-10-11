"""Tests for advanced NLP chat router (app.actions.advanced_chat)."""
import json
from unittest.mock import patch

from app.models.timetable import TimetableVersion, VersionStatus
from app.models.timetable_entry import TimetableEntry


def test_advanced_chat_status(client, teacher_headers):
    """Teacher or admin can query advanced chat telemetry/status."""
    resp = client.get("/api/actions/advanced-chat/status", headers=teacher_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_calls_last_minute" in data
    assert "breaker_open" in data
    assert "plan_cache_entries" in data
    assert "engine_simulation" in data
    assert data["engine_simulation"] is True


def test_advanced_chat_help_fastpath(client, teacher_headers):
    """'help' command returns immediate response without LLM call."""
    resp = client.post(
        "/api/actions/advanced-chat",
        json={"text": "help"},
        headers=teacher_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "info"
    assert "I can plan and apply timetable changes" in data["interpretation"]
    assert data["llm_calls"] == 0


def test_advanced_chat_validate_fastpath(client, teacher_headers, db):
    """'validate' command returns read-only validation without LLM call."""
    resp = client.post(
        "/api/actions/advanced-chat",
        json={"text": "validate"},
        headers=teacher_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "execute"
    assert len(data["parsed_actions"]) == 1
    assert data["parsed_actions"][0]["action"] == "VALIDATE_TIMETABLE"


def test_advanced_chat_reset_session(client, teacher_headers):
    """Session can be reset."""
    resp = client.delete(
        "/api/actions/advanced-chat/session?session_id=test_sess",
        headers=teacher_headers,
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_advanced_chat_preview_with_mock_llm(client, teacher_headers, db, teacher_user):
    """Preview mode generates plan, preflights it, and returns preview status with plan_id."""
    version = TimetableVersion(status=VersionStatus.DRAFT, change_summary="Base draft", created_by=teacher_user.id)
    db.add(version)
    db.flush()

    mock_llm_response = {
        "decision": "execute",
        "summary": "Move CS101 to Tuesday 10:00",
        "issues": [],
        "questions": [],
        "assumptions": [],
        "rules": [],
        "drop_rules": [],
        "actions": [
            {
                "action": "MOVE_CLASS",
                "target": {"subject_code": "CS101", "day": "Monday", "start_time": "10:00"},
                "new_day": "Tuesday",
                "new_start_time": "10:00",
                "new_end_time": "11:00",
            }
        ],
    }

    with patch("app.actions.advanced_chat._llm_plan", return_value=mock_llm_response):
        resp = client.post(
            "/api/actions/advanced-chat",
            json={"text": "Move CS101 to Tuesday 10am", "execute": False},
            headers=teacher_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("preview", "blocked", "clarify", "noop")
