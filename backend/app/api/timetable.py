"""Timetable generation and validation."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import require_teacher_or_admin
from app.models.user import User
from app.schemas.timetable import GenerateRequest, GenerateResponse, ValidateResponse, VersionOut
from app.services import scheduler_service, validation_service, timetable_service

router = APIRouter()


@router.post("/generate", response_model=GenerateResponse)
def generate_timetable(
    req: GenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin)
):
    """Generate a new draft timetable via LLM and local validation loop."""
    try:
        data = scheduler_service.generate_and_save(req, user, db)
        # Inject fields required by GenerateResponse but produced by the service under different keys
        data.setdefault("status", "DRAFT")
        data.setdefault("violations", data.get("validator_violations", []))
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate/{version_id}", response_model=ValidateResponse)
def validate_timetable(
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin)
):
    """Force run H1-H11 validator on an existing saved DRAFT."""
    try:
        v = validation_service.validate_version(db, version_id)
        import json
        result = json.loads(v.validation_result_json) if v.validation_result_json else {}
        return {
            "version_id": v.id,
            "status": v.status,
            "violations": result.get("violations", []),
            "schema_errors": result.get("schema_errors", []),
            "violation_count": result.get("violation_count", 0)
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/draft", response_model=VersionOut)
def get_current_draft(
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin)
):
    """Get the most recently created version."""
    v = db.query(timetable_service.TimetableVersion).order_by(timetable_service.TimetableVersion.id.desc()).first()
    if not v:
        raise HTTPException(status_code=404, detail="No drafts exist")
    return v
