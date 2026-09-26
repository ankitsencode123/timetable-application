"""Teacher profile endpoints."""
from __future__ import annotations
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_admin
from app.models.user import User
from app.models.teacher import Teacher
from app.schemas.user import TeacherOut, TeacherCreate

router = APIRouter()


@router.get("", response_model=List[TeacherOut])
def list_teachers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all teacher profiles. Accessible to authenticated users."""
    return db.query(Teacher).all()


@router.post("", response_model=TeacherOut)
def create_teacher(
    req: TeacherCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Create a new teacher profile (ADMIN only)."""
    exists = db.query(Teacher).filter(
        (Teacher.user_id == req.user_id) | (Teacher.short_name == req.short_name)
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="Teacher or short_name already exists")
    
    t = Teacher(
        user_id=req.user_id,
        short_name=req.short_name,
        full_name=req.full_name,
        subjects_csv=req.subjects_csv,
        is_internal=req.is_internal
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t
