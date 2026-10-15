"""Tests for /api/actions/smart-schedule/weekly endpoint."""
from __future__ import annotations

import pytest
from app.models.timetable import TimetableVersion
from app.models.timetable_entry import TimetableEntry
from app.models.user import RoleEnum
from app.services.auth_service import create_user
from app.actions.concurrency import ensure_concurrency_schema
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_db
from app.models.base import Base


@pytest.fixture(scope="function")
def isolated_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    ensure_concurrency_schema(engine)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    admin = create_user(db, "admin@test.com", "pass", "Admin User", RoleEnum.ADMIN)

    version = TimetableVersion(
        created_by=admin.id,
        change_summary="Base Test Version",
    )
    db.add(version)
    db.flush()

    entries = [
        TimetableEntry(
            version_id=version.id,
            day="Monday",
            start_time="10:00",
            end_time="12:00",
            program="B.Tech",
            semester="3rd",
            subject_code="ds",
            subject_name="Data Structure",
            teacher="SKS",
            entry_type="Theory",
            room="R#205",
        ),
        TimetableEntry(
            version_id=version.id,
            day="Tuesday",
            start_time="10:00",
            end_time="17:30",
            program="B.Tech",
            semester="3rd",
            subject_code="dm",
            subject_name="Discrete Mathematics",
            teacher="RKP",
            entry_type="Theory",
            room="R#205",
        ),
    ]
    db.add_all(entries)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "pass"})
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        yield client, headers, version, db

    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_weekly_smart_schedule_validation_error(client, auth_headers):
    """Invalid weekly_hours should return HTTP 422."""
    payload = {
        "program": "B.Tech",
        "semester": "3rd",
        "subject_code": "ds",
        "subject_name": "Data Structure",
        "teacher": "SKS",
        "entry_type": "Theory",
        "weekly_hours": -1,
        "selected_days": ["Monday", "Wednesday"],
    }
    resp = client.post(
        "/api/actions/smart-schedule/weekly",
        headers=auth_headers,
        json=payload,
    )
    assert resp.status_code == 422
    assert "weekly_hours must be a positive multiple of 0.5" in resp.json()["detail"]


def test_weekly_smart_schedule_unknown_day_error(client, auth_headers):
    """Unknown selected day should return HTTP 422."""
    payload = {
        "program": "B.Tech",
        "semester": "3rd",
        "subject_code": "ds",
        "subject_name": "Data Structure",
        "teacher": "SKS",
        "entry_type": "Theory",
        "weekly_hours": 4,
        "selected_days": ["Funday"],
    }
    resp = client.post(
        "/api/actions/smart-schedule/weekly",
        headers=auth_headers,
        json=payload,
    )
    assert resp.status_code == 422
    assert "Unknown or non-working day" in resp.json()["detail"]


def test_weekly_smart_schedule_success(isolated_env):
    """Requesting 4h over Wednesday and Thursday should return exact proposals."""
    client, auth_headers, base_timetable, _ = isolated_env
    payload = {
        "program": "B.Tech",
        "semester": "3rd",
        "subject_code": "ds",
        "subject_name": "Data Structure",
        "teacher": "SKS",
        "entry_type": "Theory",
        "weekly_hours": 4.0,
        "selected_days": ["Wednesday", "Thursday"],
        "day_rule": "exactly",
        "day_count": 2,
    }
    resp = client.post(
        "/api/actions/smart-schedule/weekly",
        headers=auth_headers,
        json=payload,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["status"] in ("exact", "substituted")
    assert len(data["proposals"]) > 0
    p0 = data["proposals"][0]
    assert len(p0["sessions"]) == 2
    assert len(p0["actions_to_apply"]) >= 2
    for act in p0["actions_to_apply"]:
        assert act["action"] in ("ADD_CLASS", "MOVE_CLASS", "REMOVE_CLASS")


def test_weekly_smart_schedule_execution_integration(isolated_env):
    """Proposals from weekly smart schedule can be applied atomically via /actions/execute."""
    client, auth_headers, base_timetable, _ = isolated_env
    payload = {
        "program": "B.Tech",
        "semester": "3rd",
        "subject_code": "ds",
        "subject_name": "Data Structure",
        "teacher": "SKS",
        "entry_type": "Theory",
        "weekly_hours": 4.0,
        "selected_days": ["Wednesday", "Thursday"],
        "day_rule": "exactly",
        "day_count": 2,
    }
    resp = client.post(
        "/api/actions/smart-schedule/weekly",
        headers=auth_headers,
        json=payload,
    )
    assert resp.status_code == 200
    proposals = resp.json()["proposals"]
    assert len(proposals) > 0

    actions_to_apply = proposals[0]["actions_to_apply"]

    exec_resp = client.post(
        "/api/actions/execute",
        headers=auth_headers,
        json={
            "actions": actions_to_apply,
            "version_id": base_timetable.id,
            "partial_ok": False,
        },
    )
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["success"] is True
    assert exec_data["new_version_id"] is not None

