"""Tests for chat api endpoints."""
from __future__ import annotations

def test_public_chat_accessible(client):
    resp = client.post("/api/public/chat", json={"message": "hello"})
    assert resp.status_code == 200
    assert "message" in resp.json()

def test_teacher_chat_protected(client):
    resp = client.post("/api/teacher/chat", json={"message": "hello"})
    assert resp.status_code == 401

def test_teacher_chat_accessible(client, auth_headers):
    from unittest.mock import patch
    with patch("app.actions.parser.ActionParser.parse") as mock_parse:
        from app.actions.types import CancelClassAction, ClassTarget
        mock_parse.return_value = [CancelClassAction(target=ClassTarget(day="Monday"))]
        resp = client.post(
            "/api/teacher/chat",
            json={"message": "remove monday class"},
            headers=auth_headers
        )
        assert resp.status_code == 200
        assert "understood" in resp.json()["message"].lower()
