"""Tests for the hard constraints engine."""
from __future__ import annotations
from app.scheduler.validator import validate_schedule

def test_h1_semester_clash():
    schedule = [
        {"day": "Monday", "program": "B.Tech", "semester": "3rd", "start": "10:00", "end": "12:00", 
         "subject_code": "dl", "teacher": "SK", "type": "Theory", "room": "R#208"},
        {"day": "Monday", "program": "B.Tech", "semester": "3rd", "start": "11:00", "end": "13:00", 
         "subject_code": "dm", "teacher": "SCh", "type": "Theory", "room": "R#303"}
    ]
    violations = validate_schedule(schedule)
    h1 = [v for v in violations if v["rule"] == "H1_semester_clash"]
    assert len(h1) == 1

def test_h2_teacher_clash():
    schedule = [
        {"day": "Monday", "program": "B.Tech", "semester": "3rd", "start": "10:00", "end": "12:00", 
         "subject_code": "dl", "teacher": "SK", "type": "Theory", "room": "R#208"},
        {"day": "Monday", "program": "B.Tech", "semester": "5th", "start": "11:00", "end": "13:00", 
         "subject_code": "cn", "teacher": "SK", "type": "Theory", "room": "R#303"}
    ]
    violations = validate_schedule(schedule)
    h2 = [v for v in violations if v["rule"] == "H2_teacher_clash"]
    assert len(h2) == 1

def test_h3_room_clash():
    schedule = [
        {"day": "Monday", "program": "B.Tech", "semester": "3rd", "start": "10:00", "end": "12:00", 
         "subject_code": "dl", "teacher": "SK", "type": "Theory", "room": "R#208"},
        {"day": "Monday", "program": "B.Tech", "semester": "5th", "start": "11:00", "end": "13:00", 
         "subject_code": "dbms", "teacher": "SC", "type": "Theory", "room": "R#208"}
    ]
    violations = validate_schedule(schedule)
    h3 = [v for v in violations if v["rule"] == "H3_room_clash"]
    assert len(h3) == 1

def test_h11_wrong_semester_subject():
    schedule = [
         # B.Tech 3rd shouldn't have IDM (Intro to Data Mining - B.Tech 7th)
        {"day": "Monday", "program": "B.Tech", "semester": "3rd", "start": "10:00", "end": "12:00", 
         "subject_code": "idm", "teacher": "RD", "type": "Theory", "room": "R#208"}
    ]
    violations = validate_schedule(schedule)
    h11 = [v for v in violations if v["rule"] == "H11_wrong_semester_subject"]
    assert len(h11) == 1
