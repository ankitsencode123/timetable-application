"""Catalog API — manage teachers, subjects, and programs."""
from __future__ import annotations

import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import get_current_user, require_admin
from app.models.user import User, RoleEnum
from app.models.teacher import Teacher
from app.models.subject import Subject
from app.models.program import Program
from app.services.auth_service import create_user
from app.scheduler import constraints as C

router = APIRouter()


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class TeacherIn(BaseModel):
    short_name: str
    full_name: str
    subjects_csv: str = ""
    is_internal: bool = True
    email: Optional[str] = None          # auto-generated if not provided
    password: Optional[str] = None       # auto-generated if not provided

class TeacherOut(BaseModel):
    id: int
    short_name: str
    full_name: str
    subjects_csv: str
    is_internal: bool
    user_id: int
    auto_email: Optional[str] = None
    auto_password: Optional[str] = None

    class Config:
        from_attributes = True

class TeacherPatch(BaseModel):
    full_name: Optional[str] = None
    subjects_csv: Optional[str] = None
    is_internal: Optional[bool] = None


class SubjectIn(BaseModel):
    code: str
    name: str
    program: str
    semester: str
    entry_type: str = "Theory"
    weekly_hours: int = 2

class SubjectOut(BaseModel):
    id: int
    code: str
    name: str
    program: str
    semester: str
    entry_type: str
    weekly_hours: int

    class Config:
        from_attributes = True


class ProgramIn(BaseModel):
    name: str
    semesters_count: int = 8
    description: str = ""

class ProgramOut(BaseModel):
    id: int
    name: str
    semesters_count: int
    description: str
    is_active: bool

    class Config:
        from_attributes = True

class BundleSubjectIn(BaseModel):
    code: str
    name: str
    semester: str
    entry_type: str = "Theory"
    weekly_hours: int = 2
    teacher_short_name: str
    teacher_full_name: str

class BundleIn(BaseModel):
    program_name: str
    semesters: int = 8
    description: str = ""
    subjects: List[BundleSubjectIn]

# ── Teachers ─────────────────────────────────────────────────────────────────

@router.get("/teachers", response_model=List[TeacherOut])
def list_teachers(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(Teacher).all()


@router.post("/teachers", response_model=TeacherOut, status_code=status.HTTP_201_CREATED)
def create_teacher(
    req: TeacherIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    exists = db.query(Teacher).filter(Teacher.short_name == req.short_name).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"Teacher '{req.short_name}' already exists.")

    # Auto-generate email and password if not provided
    email = req.email or f"{req.short_name.lower()}@college.edu"
    password = req.password or secrets.token_urlsafe(8)

    # Check if user with this email already exists
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        user_obj = existing_user
        auto_password = None
    else:
        user_obj = create_user(db, email=email, password=password,
                               full_name=req.full_name, role=RoleEnum.TEACHER)
        auto_password = password

    teacher = Teacher(
        user_id=user_obj.id,
        short_name=req.short_name,
        full_name=req.full_name,
        subjects_csv=req.subjects_csv,
        is_internal=req.is_internal,
    )
    db.add(teacher)
    db.commit()
    db.refresh(teacher)

    # Rebuild in-memory constraint caches
    C.reload_catalog(db)

    out = TeacherOut.model_validate(teacher)
    out.auto_email = email
    out.auto_password = auto_password
    return out


@router.patch("/teachers/{teacher_id}", response_model=TeacherOut)
def update_teacher(
    teacher_id: int,
    req: TeacherPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    t = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Teacher not found.")
    if req.full_name is not None:
        t.full_name = req.full_name
    if req.subjects_csv is not None:
        t.subjects_csv = req.subjects_csv
    if req.is_internal is not None:
        t.is_internal = req.is_internal
    db.commit()
    db.refresh(t)
    C.reload_catalog(db)
    return t


# ── Subjects ─────────────────────────────────────────────────────────────────

@router.get("/subjects", response_model=List[SubjectOut])
def list_subjects(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(Subject).all()


@router.post("/subjects", response_model=SubjectOut, status_code=status.HTTP_201_CREATED)
def create_subject(
    req: SubjectIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    code_lower = req.code.strip().lower()
    exists = db.query(Subject).filter(
        Subject.code == code_lower, 
        Subject.program == req.program
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"Subject with code '{req.code}' already exists in program '{req.program}'.")

    s = Subject(
        code=code_lower,
        name=req.name,
        program=req.program,
        semester=req.semester,
        entry_type=req.entry_type,
        weekly_hours=req.weekly_hours,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    C.reload_catalog(db)
    return s


@router.delete("/subjects/{subject_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subject(
    subject_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    s = db.query(Subject).filter(Subject.id == subject_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Subject not found.")
    db.delete(s)
    db.commit()
    C.reload_catalog(db)


# ── Programs ─────────────────────────────────────────────────────────────────

@router.get("/programs", response_model=List[ProgramOut])
def list_programs(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(Program).all()


@router.post("/programs", response_model=ProgramOut, status_code=status.HTTP_201_CREATED)
def create_program(
    req: ProgramIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    exists = db.query(Program).filter(Program.name == req.name).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"Program '{req.name}' already exists.")
    p = Program(name=req.name, semesters_count=req.semesters_count, description=req.description)
    db.add(p)
    db.commit()
    db.refresh(p)
    C.reload_catalog(db)
    return p


# ── Bundle Setup ─────────────────────────────────────────────────────────────

from sqlalchemy import func

@router.post("/bundle", status_code=status.HTTP_201_CREATED)
def create_bundle(
    req: BundleIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Atomically create a program, its subjects, and ensure teachers exist."""
    # 1. Create or get Program
    prog = db.query(Program).filter(Program.name == req.program_name).first()
    if not prog:
        prog = Program(name=req.program_name, semesters_count=req.semesters, description=req.description)
        db.add(prog)

    # 2. Process subjects and teachers
    for s_in in req.subjects:
        code_lower = s_in.code.strip().lower()
        search_short = s_in.teacher_short_name.strip()
        # Ensure teacher exists (case-insensitive check)
        t = db.query(Teacher).filter(func.lower(Teacher.short_name) == search_short.lower()).first()
        if not t:
            # Create user account for new teacher
            email = f"{s_in.teacher_short_name.lower()}@college.edu"
            password = secrets.token_urlsafe(8)
            user_obj = db.query(User).filter(User.email == email).first()
            if not user_obj:
                user_obj = create_user(db, email=email, password=password, full_name=s_in.teacher_full_name, role=RoleEnum.TEACHER)
            t = Teacher(
                user_id=user_obj.id,
                short_name=s_in.teacher_short_name,
                full_name=s_in.teacher_full_name,
                subjects_csv=code_lower,
                is_internal=True
            )
            db.add(t)
        else:
            if s_in.teacher_full_name and s_in.teacher_full_name.strip().lower() != t.full_name.strip().lower():
                raise HTTPException(status_code=400, detail=f"Short name '{s_in.teacher_short_name}' is already taken by '{t.full_name}'. Please use a different short name.")
            
            # Append subject to existing teacher
            if t.subjects_csv:
                codes = [c.strip() for c in t.subjects_csv.split(',')]
                if code_lower not in codes:
                    t.subjects_csv += f",{code_lower}"
            else:
                t.subjects_csv = code_lower
        
        # Ensure subject code is not taken by another subject in this program
        subj = db.query(Subject).filter(
            Subject.code == code_lower, 
            Subject.program == req.program_name
        ).first()
        
        if subj:
            # If it already exists in the same semester, it could be an idempotent update or we just skip redefining
            # but if it exists in a different semester, that's definitely problematic.
            # To be safe, if a subject already exists, just make sure we don't accidentally create a duplicate.
            # We can update it or ignore. Let's ensure it has the right semester.
            if subj.semester != s_in.semester:
                raise HTTPException(status_code=400, detail=f"Subject with code '{s_in.code}' already exists in program '{req.program_name}' in semester '{subj.semester}'.")
        else:
            subj = Subject(
                code=code_lower,
                name=s_in.name,
                program=req.program_name,
                semester=s_in.semester,
                entry_type=s_in.entry_type,
                weekly_hours=s_in.weekly_hours
            )
            db.add(subj)

    db.commit()
    C.reload_catalog(db)
    return {"status": "success", "message": f"Bundle for {req.program_name} created."}

