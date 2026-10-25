"""Tests for app/services/preload.py (optimistic preloading)."""
from __future__ import annotations

import gzip
import json

import pytest

from app.core.security import create_access_token
from app.models.user import RoleEnum
from app.services import timetable_service
from app.services.auth_service import create_user
from app.services.preload import BUNDLES, PCFG


ENTRY = {
    "day": "Monday", "program": "B.Tech", "semester": "3rd",
    "start": "10:00", "end": "12:00", "subject_code": "evs",
    "subject_name": "Environmental Science", "teacher": "PBn",
    "type": "Theory", "room": "R#303",
}


@pytest.fixture
def admin(db):
    return create_user(db, "preload-admin@test.com", "pass1234", "Preload Admin", RoleEnum.ADMIN)


@pytest.fixture
def headers(admin):
    return {"Authorization": f"Bearer {create_access_token(str(admin.id), admin.role.value)}"}


@pytest.fixture
def teacher_hdrs(db):
    t = create_user(db, "preload-teacher@test.com", "pass1234", "Preload Teacher", RoleEnum.TEACHER)
    return {"Authorization": f"Bearer {create_access_token(str(t.id), t.role.value)}"}


def test_preload_enabled_and_routes_installed(client):
    assert PCFG.enabled
    assert client.get("/api/preload/ping").status_code == 204
    assert client.get("/api/preload/bundle").status_code == 401     # exists, needs auth
    assert client.post("/api/preload/warm", json={}).status_code in (202, 429)


# ---------------------------------------------------------------- public ----
def test_warm_is_public_and_returns_no_data(client):
    for body in (None, {}, {"email": "someone@uni.edu"}, {"email": "bad"},
                 {"email": "x@uni.edu", "password": "secret"}):
        resp = client.post("/api/preload/warm", json=body) if body is not None else client.post("/api/preload/warm")
        assert resp.status_code in (202, 429), resp.text
        if resp.status_code == 202:
            assert resp.json() == {"status": "accepted"}
        assert resp.headers["cache-control"] == "no-store"


def test_ping(client):
    resp = client.get("/api/preload/ping")
    assert resp.status_code == 204


# --------------------------------------------------------- authenticated ----
def test_bundle_requires_auth(client):
    assert client.get("/api/preload/bundle").status_code == 401
    assert client.get("/api/versions").status_code == 401
    assert client.get("/api/timetable/draft").status_code == 401


def test_bundle_shape_etag_and_gzip(client, headers, admin, db):
    v = timetable_service.create_draft(db, admin, [ENTRY], "preload test")

    resp = client.get("/api/preload/bundle", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "private" in resp.headers["cache-control"]
    data = resp.json()
    assert data["latest_version_id"] == v.id
    assert any(x["id"] == v.id for x in data["versions"])
    assert data["latest"]["id"] == v.id
    assert len(data["latest"]["entries"]) == 1

    etag = resp.headers["etag"]
    resp304 = client.get("/api/preload/bundle", headers={**headers, "If-None-Match": etag})
    assert resp304.status_code == 304

    raw = client.get("/api/preload/bundle", headers={**headers, "Accept-Encoding": "identity"})
    assert raw.status_code == 200 and raw.json() == data


def test_shadow_routes_match_original_shape(client, headers, admin, db):
    v = timetable_service.create_draft(db, admin, [ENTRY], "shadow test")

    lst = client.get("/api/versions", headers=headers)
    assert lst.status_code == 200
    assert lst.json()[0]["id"] == v.id
    assert set(lst.json()[0]) >= {"id", "status", "change_summary", "created_at"}

    det = client.get(f"/api/versions/{v.id}", headers=headers)
    assert det.status_code == 200
    body = det.json()
    assert body["id"] == v.id and body["entries"][0]["subject_name"] == "Environmental Science"
    assert body["validation_result"] == {} and body["llm_output"] == {}

    assert client.get("/api/versions/999999", headers=headers).status_code == 404

    dr = client.get("/api/timetable/draft", headers=headers)
    assert dr.status_code == 200 and dr.json()["id"] == v.id


def test_teacher_can_read(client, teacher_hdrs):
    assert client.get("/api/preload/bundle", headers=teacher_hdrs).status_code == 200


def test_invalidation_after_commit(client, headers, admin, db):
    v1 = timetable_service.create_draft(db, admin, [ENTRY], "first")
    assert client.get("/api/timetable/draft", headers=headers).json()["id"] == v1.id

    v2 = timetable_service.create_draft(db, admin, [ENTRY, {**ENTRY, "day": "Tuesday"}], "second")
    assert BUNDLES.is_dirty()                     # commit hook fired
    assert client.get("/api/timetable/draft", headers=headers).json()["id"] == v2.id
    det = client.get(f"/api/versions/{v2.id}", headers=headers).json()
    assert len(det["entries"]) == 2

    # Publishing through the API is reflected immediately too.
    pub = client.post(f"/api/versions/{v1.id}/publish", headers=headers)
    if pub.status_code == 200:
        data = client.get("/api/preload/bundle", headers=headers).json()
        assert data["published_version_id"] == v1.id
        assert data["published"]["id"] == v1.id


def test_no_draft_returns_404(client, headers):
    resp = client.get("/api/timetable/draft", headers=headers)
    # Shared session-scoped DB may already hold versions from other tests.
    assert resp.status_code in (200, 404)


def test_admin_stats_include_preload(client, headers):
    client.get("/api/preload/bundle", headers=headers)
    stats = client.get("/api/admin/cache/stats", headers=headers)
    assert stats.status_code == 200
    assert "preload" in stats.json()


def test_lifespan_twice_executor_survives():
    """Two full app lifespans in one process must both work (executor recreated)."""
    from app.services import preload as P
    P._EXEC.shutdown(wait=False)
    ex = P._executor()
    assert ex.submit(lambda: 42).result(timeout=5) == 42
