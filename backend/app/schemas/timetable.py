"""Pydantic schemas for timetable versioning and entries."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from app.models.timetable import VersionStatus


class ScheduleEntry(BaseModel):
    """Single class row — mirrors the LLM JSON output schema."""
    day: str
    program: str
    semester: str
    start: str
    end: str
    subject_code: str
    subject_name: str
    teacher: str
    type: str
    room: str


class GenerateRequest(BaseModel):
    subject_teacher_allocation: str = ""
    teacher_preferences: str = ""
    room_information: str = ""
    extra_notes: str = ""
    change_summary: str = "LLM-generated schedule"


class ValidateResponse(BaseModel):
    version_id: int
    status: VersionStatus
    violations: List[Dict[str, Any]]
    schema_errors: List[Dict[str, Any]]
    violation_count: int


class VersionOut(BaseModel):
    id: int
    created_by: int
    parent_version_id: Optional[int]
    status: VersionStatus
    change_summary: str
    created_at: datetime
    published_at: Optional[datetime]
    archived_at: Optional[datetime]
    violation_count: Optional[int] = None

    model_config = {"from_attributes": True}


class VersionDetail(VersionOut):
    entries: List[ScheduleEntry]
    validation_result: Optional[Dict[str, Any]] = None
    llm_output: Optional[Dict[str, Any]] = None


class GenerateResponse(BaseModel):
    version_id: int
    status: VersionStatus
    schedule: List[ScheduleEntry]
    violations: List[Dict[str, Any]]
    schema_errors: List[Dict[str, Any]]
    model_used: str
    validator_attempts: int
    warning: Optional[str] = None
    # LLM markdown sections
    data_validation_markdown: str = ""
    assumptions_markdown: str = ""
    btech_semester_markdown: str = ""
    msc_semester_markdown: str = ""
    mtech_semester_markdown: str = ""
    faculty_wise_markdown: str = ""
    room_wise_markdown: str = ""
    workload_markdown: str = ""
    preference_markdown: str = ""
    free_day_markdown: str = ""
    validation_report_markdown: str = ""
    change_log_markdown: str = ""
    infeasibility_markdown: str = ""
    quality_score_markdown: str = ""
