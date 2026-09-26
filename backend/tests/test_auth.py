"""Tests for the complete auth and account system."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import get_db
from app.models.base import Base
from app.models.user import User, RoleEnum
from app.core.security import hash_password

# ── In-memory test database setup ─────────────────────────────────────────────

TEST_DB_URL = "sqlite://"

engine = create_engine(
    TEST_DB_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    s = TestingSessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_user(db):
    user = User(
        email="admin@test.com",
        hashed_password=hash_password("adminpass123"),
        full_name="Admin User",
        role=RoleEnum.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def teacher_user(db):
    user = User(
        email="teacher@test.com",
        hashed_password=hash_password("teachpass123"),
        full_name="Teacher User",
        role=RoleEnum.TEACHER,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_as(client, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def bearer(client, email: str, password: str) -> dict:
    """Return headers dict with Authorization and X-CSRF-Token after successful login."""
    data = login_as(client, email, password).json()
    return {
        "Authorization": f"Bearer {data['access_token']}",
        "X-CSRF-Token": data.get("csrf_token", "")
    }


# ── Login tests ────────────────────────────────────────────────────────────────

class TestLogin:
    def test_login_success(self, client, admin_user):
        r = login_as(client, "admin@test.com", "adminpass123")
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "csrf_token" in data
        assert data["user"]["email"] == "admin@test.com"
        assert data["user"]["role"] == "ADMIN"

    def test_login_wrong_password(self, client, admin_user):
        r = login_as(client, "admin@test.com", "wrongpass")
        assert r.status_code == 401

    def test_login_nonexistent_email(self, client):
        r = login_as(client, "nobody@test.com", "anypass")
        assert r.status_code == 401
        # Must not reveal whether email exists
        assert "Incorrect email or password" in r.json()["detail"]

    def test_login_sets_httponly_cookies(self, client, admin_user):
        r = login_as(client, "admin@test.com", "adminpass123")
        assert r.status_code == 200
        cookies = r.headers.get("set-cookie", "")
        assert "access_token" in cookies
        assert "refresh_token" in cookies
        assert "HttpOnly" in cookies

    def test_login_disabled_account(self, client, db, teacher_user):
        teacher_user.is_active = False
        db.commit()
        r = login_as(client, "teacher@test.com", "teachpass123")
        assert r.status_code == 403
        assert "disabled" in r.json()["detail"].lower()

    def test_login_updates_last_login(self, client, db, admin_user):
        assert admin_user.last_login is None
        login_as(client, "admin@test.com", "adminpass123")
        db.refresh(admin_user)
        assert admin_user.last_login is not None

    def test_login_resets_failed_attempts(self, client, db, admin_user):
        admin_user.failed_attempts = 3
        db.commit()
        login_as(client, "admin@test.com", "adminpass123")
        db.refresh(admin_user)
        assert admin_user.failed_attempts == 0


# ── Account lockout ────────────────────────────────────────────────────────────

class TestAccountLockout:
    def test_failed_attempts_increment(self, client, db, teacher_user):
        for _ in range(3):
            login_as(client, "teacher@test.com", "wrong")
        db.refresh(teacher_user)
        assert teacher_user.failed_attempts == 3

    def test_account_locks_after_max_attempts(self, client, db, teacher_user):
        from app.core.config import get_settings
        max_attempts = get_settings().LOGIN_MAX_ATTEMPTS
        for _ in range(max_attempts):
            login_as(client, "teacher@test.com", "wrong")
        db.refresh(teacher_user)
        assert teacher_user.locked_until is not None
        r = login_as(client, "teacher@test.com", "teachpass123")
        assert r.status_code == 423

    def test_unlock_via_admin_enable(self, client, db, admin_user, teacher_user):
        from app.core.config import get_settings
        for _ in range(get_settings().LOGIN_MAX_ATTEMPTS):
            login_as(client, "teacher@test.com", "wrong")
        headers = bearer(client, "admin@test.com", "adminpass123")
        r = client.post(f"/api/users/{teacher_user.id}/enable", headers=headers)
        assert r.status_code == 200
        # Teacher can log in now
        r2 = login_as(client, "teacher@test.com", "teachpass123")
        assert r2.status_code == 200


# ── Token / Me ─────────────────────────────────────────────────────────────────

class TestTokenAndMe:
    def test_get_me_with_bearer(self, client, admin_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.get("/api/auth/me", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["email"] == "admin@test.com"

    def test_get_me_requires_auth(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_refresh_rotation_via_cookie(self, client, admin_user):
        client2 = TestClient(app, raise_server_exceptions=False)
        client2.post("/api/auth/login", json={"email": "admin@test.com", "password": "adminpass123"})
        r = client2.post("/api/auth/refresh")
        assert r.status_code == 200
        assert "access_token" in r.json()


# ── Logout ─────────────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_revokes_refresh_token(self, client, db, admin_user):
        from app.models.refresh_token import RefreshToken
        client2 = TestClient(app, raise_server_exceptions=False)
        r = client2.post("/api/auth/login", json={"email": "admin@test.com", "password": "adminpass123"})
        csrf = r.json().get("csrf_token", "")
        active_before = db.query(RefreshToken).filter(RefreshToken.revoked == False).count()
        assert active_before >= 1
        client2.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
        db.expire_all()
        active_after = db.query(RefreshToken).filter(RefreshToken.revoked == False).count()
        assert active_after == 0

    def test_logout_clears_cookies(self, client, admin_user):
        client2 = TestClient(app, raise_server_exceptions=False)
        r = client2.post("/api/auth/login", json={"email": "admin@test.com", "password": "adminpass123"})
        csrf = r.json().get("csrf_token", "")
        r2 = client2.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
        assert r2.status_code == 200


# ── Password change ────────────────────────────────────────────────────────────

class TestPasswordChange:
    def test_change_password_success(self, client, teacher_user):
        hdrs = bearer(client, "teacher@test.com", "teachpass123")
        r = client.post(
            "/api/auth/change-password",
            json={"old_password": "teachpass123", "new_password": "newpassword456"},
            headers=hdrs,
        )
        assert r.status_code == 200
        # Old password fails
        assert login_as(client, "teacher@test.com", "teachpass123").status_code == 401
        # New password works
        assert login_as(client, "teacher@test.com", "newpassword456").status_code == 200

    def test_change_password_wrong_old(self, client, teacher_user):
        hdrs = bearer(client, "teacher@test.com", "teachpass123")
        r = client.post(
            "/api/auth/change-password",
            json={"old_password": "wrongold", "new_password": "newpassword456"},
            headers=hdrs,
        )
        assert r.status_code == 400

    def test_change_password_too_short(self, client, teacher_user):
        hdrs = bearer(client, "teacher@test.com", "teachpass123")
        r = client.post(
            "/api/auth/change-password",
            json={"old_password": "teachpass123", "new_password": "short"},
            headers=hdrs,
        )
        assert r.status_code == 422


# ── Admin user management ──────────────────────────────────────────────────────

class TestAdminUserManagement:
    def test_list_users_admin_only(self, client, admin_user, teacher_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.get("/api/users", headers=hdrs)
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_list_users_teacher_forbidden(self, client, admin_user, teacher_user):
        hdrs = bearer(client, "teacher@test.com", "teachpass123")
        r = client.get("/api/users", headers=hdrs)
        assert r.status_code == 403

    def test_create_user(self, client, admin_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.post(
            "/api/users",
            json={"email": "new@test.com", "password": "newpass456", "full_name": "New Teacher", "role": "TEACHER"},
            headers=hdrs,
        )
        assert r.status_code == 201
        assert r.json()["email"] == "new@test.com"

    def test_create_duplicate_email(self, client, admin_user, teacher_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.post(
            "/api/users",
            json={"email": "teacher@test.com", "password": "pass456789", "full_name": "Dup"},
            headers=hdrs,
        )
        assert r.status_code == 400

    def test_disable_user(self, client, db, admin_user, teacher_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.post(f"/api/users/{teacher_user.id}/disable", headers=hdrs)
        assert r.status_code == 200
        db.refresh(teacher_user)
        assert teacher_user.is_active == False
        assert login_as(client, "teacher@test.com", "teachpass123").status_code == 403

    def test_enable_user(self, client, db, admin_user, teacher_user):
        teacher_user.is_active = False
        db.commit()
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.post(f"/api/users/{teacher_user.id}/enable", headers=hdrs)
        assert r.status_code == 200
        db.refresh(teacher_user)
        assert teacher_user.is_active == True

    def test_admin_reset_password(self, client, admin_user, teacher_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.post(
            f"/api/users/{teacher_user.id}/reset-password",
            json={"new_password": "resetpass456"},
            headers=hdrs,
        )
        assert r.status_code == 200
        assert login_as(client, "teacher@test.com", "teachpass123").status_code == 401
        assert login_as(client, "teacher@test.com", "resetpass456").status_code == 200

    def test_cannot_disable_self(self, client, admin_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.post(f"/api/users/{admin_user.id}/disable", headers=hdrs)
        assert r.status_code == 400

    def test_update_user_name(self, client, admin_user, teacher_user):
        hdrs = bearer(client, "admin@test.com", "adminpass123")
        r = client.patch(
            f"/api/users/{teacher_user.id}",
            json={"full_name": "Updated Name"},
            headers=hdrs,
        )
        assert r.status_code == 200
        assert r.json()["full_name"] == "Updated Name"


# ── Role guards ────────────────────────────────────────────────────────────────

class TestRoleGuards:
    def test_teacher_cannot_access_admin_routes(self, client, db, teacher_user):
        hdrs = bearer(client, "teacher@test.com", "teachpass123")
        r = client.get("/api/users", headers=hdrs)
        assert r.status_code == 403

    def test_unauthenticated_cannot_access_protected(self, client):
        for endpoint in ["/api/auth/me", "/api/users", "/api/timetable/draft"]:
            r = client.get(endpoint)
            assert r.status_code in (401, 403)


# ── Profile self-service ───────────────────────────────────────────────────────

class TestProfile:
    def test_get_my_profile(self, client, teacher_user):
        hdrs = bearer(client, "teacher@test.com", "teachpass123")
        r = client.get("/api/profile/me", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["email"] == "teacher@test.com"

    def test_update_my_name(self, client, teacher_user):
        hdrs = bearer(client, "teacher@test.com", "teachpass123")
        r = client.patch("/api/profile/me", json={"full_name": "New Name"}, headers=hdrs)
        assert r.status_code == 200
        assert r.json()["full_name"] == "New Name"
