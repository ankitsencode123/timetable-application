"""Publication service — handles state transitions for publishing timetables."""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from app.models.timetable import TimetableVersion, VersionStatus


def publish_version(db: Session, version_id: int) -> TimetableVersion:
    target = db.query(TimetableVersion).filter(TimetableVersion.id == version_id).first()
    if not target:
        raise ValueError("Version not found")
    if target.status != VersionStatus.VALIDATED:
        raise ValueError("Only VALIDATED drafts can be published")
    
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
