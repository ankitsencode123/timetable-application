"""Validation wrapper service."""
from __future__ import annotations
from sqlalchemy.orm import Session

from app.scheduler.validator import validate_schedule, validate_schema
from app.services.timetable_service import get_version, update_version_validation
from app.models.timetable import TimetableVersion


def validate_version(db: Session, version_id: int) -> TimetableVersion:
    """Run constraints engine on a saved timetable version and update its validity status."""
    v = get_version(db, version_id)
    if not v:
        raise ValueError("Version not found")
        
    schedule = [e.to_dict() for e in v.entries]
    schema_errs = validate_schema(schedule)
    violations = validate_schedule(schedule)
    
    return update_version_validation(db, version_id, violations, schema_errs)
