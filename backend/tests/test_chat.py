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
        assert "action" in resp.json()["message"].lower()

def test_public_chat_student_queries(client):
    queries = [
        "hey AI what classes do i have on this coming wednesay?",
        "hey AI what classes do i have on this coming wednesay? I want all the classes",
        "hey AI what classes are there on this coming wednesay?",
    ]
    for q in queries:
        resp = client.post("/api/public/chat", json={"message": q})
        assert resp.status_code == 200
        msg = resp.json()["message"]
        # Ensure it resolves Wednesday and does not return "I can't tell who I/my is without logging in"
        assert "Wednesday" in msg
        assert "without logging in" not in msg.lower()

