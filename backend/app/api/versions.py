"""Version history and publication management."""
from __future__ import annotations
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import require_admin, require_teacher_or_admin
from app.models.user import User
from app.schemas.timetable import VersionOut, VersionDetail
from app.services import timetable_service, publication_service, audit_service

router = APIRouter()


@router.get("", response_model=List[VersionOut])
def list_versions(
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin)
):
    """List all timetable versions (history)."""
    return timetable_service.list_versions(db)


@router.get("/{version_id}", response_model=VersionDetail)
def get_version(
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin)
):
    """Get specific version detailing classes and LLM outputs."""
    v = timetable_service.get_version(db, version_id)
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    
    import json
    return {
        "id": v.id,
        "created_by": v.created_by,
        "parent_version_id": v.parent_version_id,
        "status": v.status,
        "change_summary": v.change_summary,
        "created_at": v.created_at,
        "published_at": v.published_at,
        "archived_at": v.archived_at,
        "entries": [e.to_dict() for e in v.entries],
        "validation_result": json.loads(v.validation_result_json) if v.validation_result_json else {},
        "llm_output": json.loads(v.llm_output_json) if v.llm_output_json else {},
    }


@router.post("/{version_id}/publish", response_model=VersionOut)
def publish_version(
    version_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Publish a validated version (ADMIN only)."""
    try:
        v = publication_service.publish_version(db, version_id)
        audit_service.log(db, action="PUBLISH", user_id=admin.id, version_id=v.id)
        return v
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{version_id}/unpublish")
def unpublish(
    version_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Unpublish the currently published version (ADMIN only)."""
    publication_service.unpublish_all(db)
    audit_service.log(db, action="UNPUBLISH_ALL", user_id=admin.id)
    return {"message": "Timetable unpublished successfully"}


@router.post("/{version_id}/rollback")
def rollback_version(
    version_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Rollback to a previously published/archived version (ADMIN only)."""
    # Just an alias to publish for now. Validation checks in publish_version guard it nicely if we tweak statuses.
    raise HTTPException(status_code=501, detail="Rollback logic to be implemented")
