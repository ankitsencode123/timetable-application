"""Tests for zero-cost cache layer (app.services.cache_layer)."""
import json
from unittest.mock import patch

from app.models.timetable import TimetableVersion, VersionStatus
from app.models.timetable_entry import TimetableEntry


def test_cache_stats_admin(client, auth_headers):
    """Admin can query cache stats."""
    resp = client.get("/api/admin/cache/stats", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "uptime_seconds" in data
    assert "metrics" in data
    assert "config" in data


def test_cache_purge_admin(client, auth_headers):
    """Admin can purge cache."""
    resp = client.post("/api/admin/cache/purge", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json().get("purged") is True


def test_public_timetable_cached_with_etag_and_304(client, db, admin_user):
    """Published timetable returns ETag and 304 Not Modified when header matches."""
    # Create published timetable
    version = TimetableVersion(status=VersionStatus.PUBLISHED, change_summary="Test Published", created_by=admin_user.id)
    db.add(version)
    db.flush()

    entry = TimetableEntry(
        version_id=version.id,
        day="Monday",
        start_time="10:00",
        end_time="11:00",
        program="B.Tech CS",
        semester="1st",
        subject_code="CS101",
        subject_name="Intro to CS",
        teacher="Dr. Smith",
        room="101",
        entry_type="Theory",
    )
    db.add(entry)
    db.commit()

    # First request -> 200 OK + ETag
    resp1 = client.get("/api/public/timetable")
    assert resp1.status_code == 200
    etag = resp1.headers.get("etag")
    assert etag is not None
    data = resp1.json()
    assert len(data) == 1
    assert data[0]["subject_code"] == "CS101"

    # Second request with If-None-Match -> 304 Not Modified
    resp2 = client.get("/api/public/timetable", headers={"If-None-Match": etag})
    assert resp2.status_code == 304

    # Query filtered slices
    resp_prog = client.get("/api/public/timetable/program/btech%20cs/1st")
    assert resp_prog.status_code == 200
    assert len(resp_prog.json()) == 1

    resp_teach = client.get("/api/public/timetable/teacher/dr.%20smith")
    assert resp_teach.status_code == 200
    assert len(resp_teach.json()) == 1

    resp_meta = client.get("/api/public/meta")
    assert resp_meta.status_code == 200
    assert resp_meta.json()["version_id"] == version.id
