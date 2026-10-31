"""Publication service — handles state transitions for publishing timetables."""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from app.models.timetable import TimetableVersion, VersionStatus


def publish_version(db: Session, version_id: int) -> TimetableVersion:
    target = db.query(TimetableVersion).filter(TimetableVersion.id == version_id).first()
    if not target:
        raise ValueError("Version not found")
    if target.status not in (VersionStatus.VALIDATED, VersionStatus.DRAFT, VersionStatus.ARCHIVED):
        raise ValueError("Only DRAFT, VALIDATED, or ARCHIVED versions can be published")
    
    # Refuse to publish if any hard constraints are violated
    from app.scheduler.validator import validate_schedule, validate_schema
    schedule = [
        {
            "day": e.day,
            "program": e.program,
            "semester": e.semester,
            "start": e.start_time,
            "end": e.end_time,
            "subject_code": e.subject_code,
            "subject_name": e.subject_name,
            "teacher": e.teacher,
            "type": e.entry_type,
            "room": e.room,
        }
        for e in target.entries
    ]
    HARD_RULES = {
        "H1_semester_clash",
        "H2_teacher_clash",
        "H3_room_clash",
        "H12_teacher_busy",
        "start_not_before_end",
        "bad_time_format",
    }
    violations = validate_schedule(schedule) + validate_schema(schedule)
    hard_violations = [v for v in violations if v.get("rule") in HARD_RULES]
    if hard_violations:
        details = "; ".join(
            f"{v.get('rule')}: {v.get('message', v.get('detail', 'Constraint violation'))}"
            for v in hard_violations[:3]
        )
        raise ValueError(f"Cannot publish timetable with hard constraint violations: {details}")

    # Archive the currently published version(s)
    current_pubs = db.query(TimetableVersion).filter(TimetableVersion.status == VersionStatus.PUBLISHED).all()
    now_utc = datetime.now(timezone.utc)
    for p in current_pubs:
        p.status = VersionStatus.ARCHIVED
        p.archived_at = now_utc

    # Publish the target
    target.status = VersionStatus.PUBLISHED
    target.published_at = now_utc
    db.commit()
    db.refresh(target)
    return target


def unpublish_all(db: Session) -> None:
    current_pubs = db.query(TimetableVersion).filter(TimetableVersion.status == VersionStatus.PUBLISHED).all()
    now_utc = datetime.now(timezone.utc)
    for p in current_pubs:
        p.status = VersionStatus.ARCHIVED
        p.archived_at = now_utc
    db.commit()
