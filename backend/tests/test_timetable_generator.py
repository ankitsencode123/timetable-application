"""Tests for the Automated Timetable Generator endpoints."""
import pytest
from app.scheduler.timetable_generator import build_demo_board


def test_generator_presets(client):
    res = client.get("/api/timetable/generator/presets")
    assert res.status_code == 200
    data = res.json()
    assert "demo" in data
    assert "catalog" in data
    assert len(data["demo"]["semesters"]) == 4
    assert len(data["demo"]["rooms"]) == 8
    assert len(data["demo"]["teachers"]) == 14
    assert len(data["demo"]["subjects"]) == 23


def test_generator_validate(client):
    demo = build_demo_board().to_dict()
    res = client.post("/api/timetable/generator/validate", json={"board": demo})
    assert res.status_code == 200
    data = res.json()
    assert "valid" in data
    assert "issues" in data


def test_generator_run(client):
    demo = build_demo_board().to_dict()
    demo["config"]["time_limit_s"] = 1.0
    res = client.post("/api/timetable/generator/generate", json={"board": demo, "strict": False})
    assert res.status_code == 200
    data = res.json()
    assert "classes" in data
    assert len(data["classes"]) > 0
    assert "stats" in data
    assert "preferences" in data
    assert "violations" in data
    assert len(data["violations"]) == 0


def test_generator_save_draft(client, auth_headers):
    demo = build_demo_board().to_dict()
    demo["config"]["time_limit_s"] = 1.0
    gen_res = client.post("/api/timetable/generator/generate", json={"board": demo, "strict": False})
    classes = gen_res.json()["classes"]

    save_res = client.post(
        "/api/timetable/generator/save-draft",
        json={"classes": classes, "change_summary": "Test Auto Generation"},
        headers=auth_headers,
    )
    assert save_res.status_code == 200
    save_data = save_res.json()
    assert "version_id" in save_data
    assert save_data["entries_count"] == len(classes)
    assert save_data["status"] == "DRAFT"

    # Verify that teacher short forms are stored, NOT I01..I09
    v_res = client.get(f"/api/versions/{save_data['version_id']}", headers=auth_headers)
    assert v_res.status_code == 200
    v_entries = v_res.json()["entries"]
    for e in v_entries:
        assert not e["teacher"].startswith("I0"), f"Found raw teacher ID {e['teacher']} in entry!"
        assert not e["teacher"].startswith("I1"), f"Found raw teacher ID {e['teacher']} in entry!"


def test_generator_extract_files_and_sync(client):
    sub_csv = "Subject Code,Subject Name,Semester,Internal Teacher ID,External Teacher ID,Class Type,Sessions Per Week\nCS901,Quantum Computing,B.Tech 3rd,I99,,Theory,3\n"
    tch_csv = "Teacher ID,Name,Pref 1 Day,Pref 1 Time,Pref 1 Facility\nI99,Dr. Richard Feynman,Mon,10-12:00,blackboard\n"

    res = client.post(
        "/api/timetable/generator/extract-files",
        json={"subjects_file_content": sub_csv, "teachers_file_content": tch_csv, "auto_sync_catalog": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["teachers"]) >= 1
    assert len(data["subjects"]) >= 1
    assert "created_accounts" in data
    # Verify account was created with email and password
    feynman_acc = next((a for a in data["created_accounts"] if "Feynman" in a["full_name"]), None)
    assert feynman_acc is not None
    assert feynman_acc["short_name"] == "RF"
    assert "@college.edu" in feynman_acc["email"]
    assert len(feynman_acc["password"]) >= 6


def test_generator_faculty_and_subject_endpoints(client):
    # Add faculty
    fac_res = client.post("/api/timetable/generator/faculty", json={"name": "Dr. Marie Curie", "is_internal": True})
    assert fac_res.status_code == 200
    fac_data = fac_res.json()
    assert fac_data["short_name"] == "MC"
    assert "@college.edu" in fac_data["email"]
    assert len(fac_data["password"]) >= 6

    # Add subject
    sub_res = client.post(
        "/api/timetable/generator/subject",
        json={"code": "RAD101", "name": "Radioactivity", "program": "B.Tech", "semester": "5th", "class_type": "Theory", "teacher_short_name": "MC"},
    )
    assert sub_res.status_code == 200
    assert sub_res.json()["code"] == "rad101"

