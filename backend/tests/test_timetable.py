"""Tests for timetable versioning, publishing, and generation endpoint shells."""
from __future__ import annotations
import json
from unittest.mock import patch
from app.models.timetable import VersionStatus

def test_generate_draft_mocked(client, auth_headers, db):
    # We mock out the actual Groq call for deterministic testing
    with patch("app.services.scheduler_service.call_groq") as mock_call:
        mock_call.return_value = (json.dumps({
            "schedule": [
                {
                    "day": "Monday",
                    "program": "B.Tech",
                    "semester": "3rd",
                    "start": "10:00",
                    "end": "12:00",
                    "subject_code": "evs",
                    "subject_name": "Environmental Science",
                    "teacher": "PBn",
                    "type": "Theory",
                    "room": "R#303"
                }
            ],
            "btech_semester_markdown": "test md",
            "msc_semester_markdown": "test md",
            "mtech_semester_markdown": "test md",
            "room_wise_markdown": "test md",
        }), "mock_model")

        resp = client.post("/api/timetable/generate", json={
            "change_summary": "Test run"
        }, headers=auth_headers)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["version_id"] > 0
        assert data["status"] == "DRAFT"
        assert len(data["schedule"]) == 1

        # Check DB
        resp2 = client.get("/api/timetable/draft", headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["id"] == data["version_id"]


def test_publish_workflow(client, auth_headers):
    # Create draft — mock both Groq call and validator so a partial schedule passes validation
    with patch("app.services.scheduler_service.call_groq") as mock_call, \
         patch("app.services.validation_service.validate_schedule", return_value=[]) as _mock_val:
        mock_call.return_value = (json.dumps({
            "schedule": [
                {
                    "day": "Monday",
                    "program": "B.Tech",
                    "semester": "3rd",
                    "start": "10:00",
                    "end": "12:00",
                    "subject_code": "evs",
                    "subject_name": "Environmental Science",
                    "teacher": "PBn",
                    "type": "Theory",
                    "room": "R#303"
                }
            ]
        }), "mock_model")
        gen_resp = client.post("/api/timetable/generate", json={"change_summary": "T"}, headers=auth_headers)
        assert gen_resp.status_code == 200, gen_resp.text
        version_id = gen_resp.json()["version_id"]

    # Try to publish before validation (should fail)
    resp_pub_fail = client.post(f"/api/versions/{version_id}/publish", headers=auth_headers)
    assert resp_pub_fail.status_code == 400

    # Validate draft — with validator patched to report zero violations, status → VALIDATED
    with patch("app.services.validation_service.validate_schedule", return_value=[]):
        val_resp = client.post(f"/api/timetable/validate/{version_id}", headers=auth_headers)
    assert val_resp.status_code == 200
    assert val_resp.json()["status"] == "VALIDATED"
    assert val_resp.json()["violation_count"] == 0

    # Publish validated (should succeed)
    resp_pub = client.post(f"/api/versions/{version_id}/publish", headers=auth_headers)
    assert resp_pub.status_code == 200
    assert resp_pub.json()["status"] == "PUBLISHED"

    # Verify public visibility
    pub_resp = client.get("/api/public/timetable")
    assert pub_resp.status_code == 200
    assert len(pub_resp.json()) == 1

    # Unpublish
    unp_resp = client.post(f"/api/versions/{version_id}/unpublish", headers=auth_headers)
    assert unp_resp.status_code == 200

    # Verify public hidden
    pub_resp_2 = client.get("/api/public/timetable")
    assert pub_resp_2.status_code == 404

