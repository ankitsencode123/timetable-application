"""Tests for Role-based access control."""
from __future__ import annotations

def test_admin_can_access_users(client, auth_headers):
    resp = client.get("/api/users", headers=auth_headers)
    assert resp.status_code == 200

def test_teacher_cannot_access_users(client, teacher_headers):
    resp = client.get("/api/users", headers=teacher_headers)
    assert resp.status_code == 403

def test_teacher_can_access_teachers(client, teacher_headers):
    resp = client.get("/api/teachers", headers=teacher_headers)
    assert resp.status_code == 200

def test_admin_can_create_user(client, auth_headers):
    resp = client.post("/api/users", json={
        "email": "newadmin@test.com",
        "password": "pass",
        "full_name": "New Admin",
        "role": "ADMIN"
    }, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "newadmin@test.com"

def test_teacher_cannot_create_user(client, teacher_headers):
    resp = client.post("/api/users", json={
        "email": "hax0r@test.com",
        "password": "pass",
        "full_name": "Hax",
        "role": "ADMIN"
    }, headers=teacher_headers)
    assert resp.status_code == 403
