"""HTTP integration tests for the /actions endpoints."""
from __future__ import annotations
import json
from unittest.mock import patch


# ---------------------------------------------------------------------------
# POST /api/actions/parse
# ---------------------------------------------------------------------------

def test_parse_endpoint(client, auth_headers, db):
    with patch("app.actions.parser.ActionParser.parse") as mock_parse:
        from app.actions.types import CancelClassAction, ClassTarget
        mock_parse.return_value = [CancelClassAction(target=ClassTarget(day="Monday"))]
        
        resp = client.post(
            "/api/actions/parse",
            headers=auth_headers,
            json={"text": "Cancel monday class"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action_count"] == 1
        assert data["parsed_actions"][0]["action"] == "CANCEL_CLASS"


# ---------------------------------------------------------------------------
# POST /api/actions/chat
# ---------------------------------------------------------------------------

def test_chat_execute_false(client, auth_headers):
    """If execute=False, it should just return the parsed preview without running the engine."""
    with patch("app.actions.parser.ActionParser.parse") as mock_parse:
        from app.actions.types import CancelClassAction, ClassTarget
        mock_parse.return_value = [CancelClassAction(target=ClassTarget(day="Monday"))]
        
        resp = client.post(
            "/api/actions/chat",
            headers=auth_headers,
            json={"text": "Cancel monday class", "execute": False}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["executed"] is False
        assert data["execution_result"] is None
        assert data["parsed_actions"][0]["action"] == "CANCEL_CLASS"


def test_chat_execute_true(client, auth_headers):
    """If execute=True, it should parse AND run the engine."""
    with patch("app.actions.parser.ActionParser.parse") as mock_parse, \
         patch("app.actions.engine.ActionEngine.execute") as mock_exec:
        
        from app.actions.types import CancelClassAction, ClassTarget
        from app.actions.engine import EngineResult, ActionResult
        
        mock_parse.return_value = [CancelClassAction(target=ClassTarget(day="Monday"))]
        mock_exec.return_value = EngineResult(
            success=True,
            results=[ActionResult(action_type="CANCEL_CLASS", success=True)],
            new_version_id=99,
        )
        
        resp = client.post(
            "/api/actions/chat",
            headers=auth_headers,
            json={"text": "Cancel monday class", "execute": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["executed"] is True
        assert data["execution_result"]["success"] is True
        assert data["execution_result"]["new_version_id"] == 99


# ---------------------------------------------------------------------------
# Teacher Chat router integration (GET /api/teacher/chat)
# ---------------------------------------------------------------------------

def test_teacher_chat_router_integration(client, teacher_headers):
    """Verify the main chat endpoint now uses the parser and returns parsed_actions."""
    with patch("app.actions.parser.ActionParser.parse") as mock_parse:
        from app.actions.types import CancelClassAction, ClassTarget
        mock_parse.return_value = [CancelClassAction(target=ClassTarget(day="Friday"))]
        
        resp = client.post(
            "/api/teacher/chat",
            headers=teacher_headers,
            json={"message": "Cancel my Friday classes"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "found **1 action**" in data["message"].lower()
        assert len(data["parsed_actions"]) == 1
        assert data["parsed_actions"][0]["action"] == "CANCEL_CLASS"
