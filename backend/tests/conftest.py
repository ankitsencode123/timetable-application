"""pytest fixtures for fastapi and database testing."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import get_db
from app.models.base import Base
from app.services.auth_service import create_user
from app.models.user import RoleEnum

# In-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, pool_pre_ping=True
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session")
def db_engine():
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def db(db_engine):
    """Provides a fresh database session for each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db):
    """Test client bypassing normal db dependency to use the test db."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
            
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user(db):
    return create_user(db, "admin@test.com", "pass", "Admin User", RoleEnum.ADMIN)


@pytest.fixture
def teacher_user(db):
    return create_user(db, "teacher@test.com", "pass", "Teacher User", RoleEnum.TEACHER)


@pytest.fixture
def auth_headers(client, admin_user):
    resp = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "pass"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def teacher_headers(client, teacher_user):
    resp = client.post("/api/auth/login", json={"email": "teacher@test.com", "password": "pass"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
