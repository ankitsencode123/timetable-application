"""Publicly accessible read-only timetable queries."""
from __future__ import annotations
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.timetable import ScheduleEntry
from app.services import timetable_service

router = APIRouter()


@router.get("/timetable", response_model=List[ScheduleEntry])
def get_public_timetable(db: Session = Depends(get_db)):
    """Return the raw JSON schedule arrays for the active PUBLISHED version."""
    v = timetable_service.get_published_version(db)
    if not v:
        raise HTTPException(status_code=404, detail="No published timetable available")
    return [e.to_dict() for e in v.entries]


@router.get("/timetable/program/{program}/{semester}", response_model=List[ScheduleEntry])
def get_public_timetable_by_program(program: str, semester: str, db: Session = Depends(get_db)):
    v = timetable_service.get_published_version(db)
    if not v:
        raise HTTPException(status_code=404, detail="No published timetable available")
    
    # Simple Python filtering since arrays are small. DB filtering could also be used here via relationships.
    program_norm = program.lower().replace(".", "")
    sem_norm = semester.lower()
    
    res = [
        e.to_dict() for e in v.entries 
        if e.program.lower().replace(".", "") == program_norm and e.semester.lower() == sem_norm
    ]
    return res


@router.get("/timetable/teacher/{code}", response_model=List[ScheduleEntry])
def get_public_timetable_by_teacher(code: str, db: Session = Depends(get_db)):
    v = timetable_service.get_published_version(db)
    if not v:
        raise HTTPException(status_code=404, detail="No published timetable available")
    
    code_norm = code.lower()
    return [e.to_dict() for e in v.entries if e.teacher.lower() == code_norm]


@router.get("/meta")
def get_public_meta(db: Session = Depends(get_db)):
    """Return metadata about the current published version (for frontend headers)."""
    v = timetable_service.get_published_version(db)
    if not v:
        raise HTTPException(status_code=404, detail="No published timetable available")
    return {
        "version_id": v.id,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "change_summary": v.change_summary
    }
