"""Core timetable CRUD operations."""
from __future__ import annotations
import json
from typing import Optional, List
from sqlalchemy.orm import Session, joinedload

from app.models.timetable import TimetableVersion, VersionStatus
from app.models.timetable_entry import TimetableEntry
from app.models.user import User


def create_draft(
    db: Session,
    user: User,
    entries: List[dict],
    change_summary: str,
    parent_id: Optional[int] = None,
    llm_output: Optional[dict] = None,
) -> TimetableVersion:
    version = TimetableVersion(
        created_by=user.id,
        parent_version_id=parent_id,
        status=VersionStatus.DRAFT,
        change_summary=change_summary,
        llm_output_json=json.dumps(llm_output) if llm_output else None,
    )
    db.add(version)
    db.flush()

    for row in entries:
        en = TimetableEntry(
            version_id=version.id,
            day=row.get("day", ""),
            program=row.get("program", ""),
            semester=row.get("semester", ""),
            start_time=row.get("start", ""),
            end_time=row.get("end", ""),
            subject_code=row.get("subject_code") or row.get("subject_name", ""),
            subject_name=row.get("subject_name", ""),
            teacher=row.get("teacher", ""),
            entry_type=row.get("type", ""),
            room=row.get("room", ""),
        )
        db.add(en)
    
    db.commit()
    db.refresh(version)
    # Invalidate the fast suggester's in-memory index so the next suggestion
    # request rebuilds it from the new schedule.
    try:
        from app.scheduler.fast_suggester import invalidate_caches
        invalidate_caches()
    except Exception:
        pass
    return version


def get_version(db: Session, version_id: int) -> Optional[TimetableVersion]:
    return db.query(TimetableVersion).options(joinedload(TimetableVersion.entries)).filter(TimetableVersion.id == version_id).first()


def get_published_version(db: Session) -> Optional[TimetableVersion]:
    return db.query(TimetableVersion).options(joinedload(TimetableVersion.entries)).filter(TimetableVersion.status == VersionStatus.PUBLISHED).first()


def list_versions(db: Session) -> List[TimetableVersion]:
    return db.query(TimetableVersion).order_by(TimetableVersion.created_at.desc()).all()


def update_version_validation(db: Session, version_id: int, violations: list, schema_errors: list) -> TimetableVersion:
    v = get_version(db, version_id)
    if not v:
        raise ValueError("Version not found")
    
    result = {
        "violations": violations,
        "schema_errors": schema_errors,
        "violation_count": len(violations)
    }
    v.validation_result_json = json.dumps(result)
    
    # If perfect, mark as VALIDATED. Otherwise remains DRAFT.
    if len(violations) == 0 and len(schema_errors) == 0:
        v.status = VersionStatus.VALIDATED
    
    db.commit()
    db.refresh(v)
    return v
