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


def test_combined_l1_and_semantic_cache_pipeline(client, db, admin_user, auth_headers):
    """Combined L1 (cache_layer) + L2 (semantic_cache) handles exact hits, paraphrases, slot guards, feedback, and invalidation."""
    from app.services import semantic_cache as sc

    version = TimetableVersion(status=VersionStatus.PUBLISHED, change_summary="SemCache Test", created_by=admin_user.id)
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
    orig_llm = sc.ENGINE.llm_fn
    sc.set_llm_function(lambda state, msg, now: (calls.append(msg) or f"RKS teaches CS301 in room 204 ({len(calls)})"))
    try:
        with patch("app.services.semantic_cache._calendar_sync", return_value=None):
            # 1) Cold miss -> calls LLM once, populates L2 and binds L1
            r1 = client.post("/api/public/chat", json={"message": "Who teaches CS301 on Monday?"})
            assert r1.status_code == 200
            assert r1.headers.get("x-cache") == "MISS"
            entry_id = r1.headers.get("x-cache-entry")
            assert entry_id
            assert len(calls) == 1

            # 2) Identical question -> served via L1 alias validated against L2 (zero LLM calls)
            l1_hits_before = sc.L1_CACHE.hits
            r2 = client.post("/api/public/chat", json={"message": "Who teaches CS301 on Monday?"})
            assert r2.status_code == 200
            assert r2.headers.get("x-cache") == "HIT-EXACT"
            assert sc.L1_CACHE.hits > l1_hits_before
            assert len(calls) == 1

            # 3) Paraphrase with same hard slots -> L2 semantic hit (zero LLM calls)
            r3 = client.post("/api/public/chat", json={"message": "Which faculty takes CS301 class on Monday?"})
            assert r3.status_code == 200
            assert r3.headers.get("x-cache") in ("HIT-EXACT", "HIT-SEMANTIC")
            assert r3.json()["message"] == r1.json()["message"]
            assert len(calls) == 1

            # 4) Different hard slot (Tuesday instead of Monday) -> MUST NOT match Monday's answer
            r4 = client.post("/api/public/chat", json={"message": "Who teaches CS301 on Tuesday?"})
            assert r4.status_code == 200
            assert r4.headers.get("x-cache") == "MISS"
            assert len(calls) == 2

            # 5) Admin inspect & unified stats
            insp = client.get(
                "/api/admin/semcache/inspect",
                params={"q": "Which professor conducts CS301 on Monday?"},
                headers=auth_headers,
            )
            assert insp.status_code == 200
            assert insp.json()["decision"] in ("HIT-EXACT", "HIT-SEMANTIC")

            stats = client.get("/api/admin/cache/stats", headers=auth_headers).json()
            assert "semantic" in stats
            assert "l1_cache" in stats["semantic"]

            # 6) Downvote eviction removes entry from L2 and clears L1 alias
            client.post("/api/public/chat/feedback", json={"entry_id": entry_id, "helpful": False}, headers={"X-Client-Id": "u1"})
            fb2 = client.post("/api/public/chat/feedback", json={"entry_id": entry_id, "helpful": False}, headers={"X-Client-Id": "u2"})
            assert fb2.status_code == 200
            assert fb2.json()["evicted"] is True

            # Re-asking after eviction must miss
            r5 = client.post("/api/public/chat", json={"message": "Who teaches CS301 on Monday?"})
            assert r5.headers.get("x-cache") == "MISS"
            assert len(calls) == 3
    finally:
        sc.set_llm_function(orig_llm)

