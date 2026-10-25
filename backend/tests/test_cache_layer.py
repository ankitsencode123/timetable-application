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


def test_public_chat_cache_layer_pipeline(client, db, admin_user, auth_headers):
    """cache_layer public chat handles caching exact questions and invalidation on purge."""
    version = TimetableVersion(status=VersionStatus.PUBLISHED, change_summary="Cache Test", created_by=admin_user.id)
    db.add(version)
    db.flush()
    db.add(
        TimetableEntry(
            version_id=version.id,
            day="Monday",
            start_time="10:00",
            end_time="11:00",
            program="BTech",
            semester="3rd",
            subject_code="CS301",
            subject_name="Computer Networks",
            teacher="RKS",
            room="204",
            entry_type="Theory",
        )
    )
    db.commit()

    calls = []

    def mock_llm(snap, message):
        calls.append(message)
        return "RKS teaches CS301 in room 204 on Monday."

    with patch("app.services.cache_layer._llm_sync", side_effect=mock_llm):
        # 1) First call -> MISS, calls LLM
        r1 = client.post("/api/public/chat", json={"message": "Who teaches CS301 on Monday?"})
        assert r1.status_code == 200
        assert r1.headers.get("x-cache") == "MISS"
        assert "RKS teaches CS301" in r1.json()["message"]
        assert len(calls) == 1

        # 2) Identical question -> HIT from cache, no new LLM call
        r2 = client.post("/api/public/chat", json={"message": "Who teaches CS301 on Monday?"})
        assert r2.status_code == 200
        assert r2.headers.get("x-cache") == "HIT"
        assert r2.json()["message"] == r1.json()["message"]
        assert len(calls) == 1

        # 3) Purge cache
        purge_resp = client.post("/api/admin/cache/purge", headers=auth_headers)
        assert purge_resp.status_code == 200

        # 4) Re-asking after purge -> MISS, calls LLM again
        r3 = client.post("/api/public/chat", json={"message": "Who teaches CS301 on Monday?"})
        assert r3.status_code == 200
        assert r3.headers.get("x-cache") == "MISS"
        assert len(calls) == 2


