"""Tests for public endpoints."""
from __future__ import annotations
import json
from unittest.mock import patch

def test_public_timetable_hidden_when_draft(client, auth_headers):
    # Gen draft
    with patch("app.services.scheduler_service.call_groq") as mock_call:
        mock_call.return_value = (json.dumps({"schedule": []}), "mock")
        resp = client.post("/api/timetable/generate", json={"change_summary": "T"}, headers=auth_headers)
        assert resp.status_code == 200
        
    # Unauthenticated user tries to view
    pub_resp = client.get("/api/public/timetable")
    # Should be 404 because status == DRAFT, not PUBLISHED
    assert pub_resp.status_code == 404
