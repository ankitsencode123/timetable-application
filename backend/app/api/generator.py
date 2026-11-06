"""API endpoints for the Automated Timetable Generator (CSP + Repair + Simulated Annealing)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import require_teacher_or_admin
from app.models.user import User
from app.models.timetable import VersionStatus
from app.services.timetable_service import create_draft
from app.services.audit_service import log as audit_log
from app.services.cache_layer import invalidate_timetable_data
from app.scheduler.timetable_generator import (
    SchedulingBoard,
    SolverConfig,
    build_demo_board,
    build_board_from_db,
    InfeasibleInputError,
)

router = APIRouter()

DAY_MAP = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


def _normalize_day(d: str) -> str:
    key = str(d).strip().lower()[:3]
    return DAY_MAP.get(key, str(d).strip())


def _format_semester(sem_id: str) -> str:
    parts = str(sem_id).split("-")
    raw_num = parts[-1] if len(parts) > 1 else sem_id
    digits = "".join(filter(str.isdigit, raw_num))
    if not digits:
        return sem_id
    n = int(digits)
    suffix = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class ValidateBoardRequest(BaseModel):
    board: Dict[str, Any]


class GenerateBoardRequest(BaseModel):
    board: Dict[str, Any]
    strict: bool = False


class SaveDraftRequest(BaseModel):
    classes: List[Dict[str, Any]]
    change_summary: str = "Automated Timetable Generation"
    publish_immediately: bool = False


class ExtractFilesRequest(BaseModel):
    subjects_file_content: Optional[str] = None
    teachers_file_content: Optional[str] = None
    auto_sync_catalog: bool = True


SUBJECTS_TEMPLATE_CSV = """Subject Code,Subject Name,Semester,Internal Teacher ID,External Teacher ID,Class Type,Sessions Per Week,Facility
CS301,Data Structures,B.Tech 3rd,I01,E01,Theory,3,none
CS302,Database Systems,B.Tech 3rd,I02,E02,Theory,3,none
MA301,Discrete Mathematics,B.Tech 3rd,I03,,Theory,3,blackboard
CS351,Data Structures Lab,B.Tech 3rd,I01,,Practical,1,none
CS601,Advanced Algorithms,M.Tech 1st,I01,E01,Theory,3,none
CS651,Algorithms Lab,M.Tech 1st,I03,,Practical,2,none
MS201,Linear Algebra,M.Sc. 2nd,I04,E02,Theory,3,blackboard
MS251,Computing Lab,M.Sc. 2nd,I04,,Practical,1,none
"""

TEACHERS_TEMPLATE_CSV = """Teacher ID,Name,Pref 1 Day,Pref 1 Time,Pref 1 Facility,Pref 2 Day,Pref 2 Time,Pref 2 Facility,Pref 3 Day,Pref 3 Time,Pref 3 Facility
I01,Dr. A. Rao,Mon,10-12:00,blackboard and projector,Wed,12-2,projector,Fri,10-12:00,black board
I02,Dr. B. Iyer,Tue,10-12:00,black board,Thu,2:30-4:30,blackboard and projector,Mon,12-2,projector
I03,Prof. C. Das,Mon,10-12:00,black board,Tue,12-2,black board,Thu,10-12:00,black board
I04,Dr. D. Menon,Wed,10-12:00,projector,Thu,12-2,both,Fri,2:30-4:30,projector
E01,Mr. K. Patel (ext),Tue,10-12:00,projector,Wed,10-12:00,projector,Thu,10-12:00,both
E02,Ms. L. Gupta (ext),Tue,12-2,both,Thu,12-2,both,Wed,12-2,both
"""


@router.get("/templates")
def get_templates():
    """Return downloadable/viewable CSV template content for subject allocations and teacher preferences."""
    return {
        "subjects_template_csv": SUBJECTS_TEMPLATE_CSV,
        "teachers_template_csv": TEACHERS_TEMPLATE_CSV,
    }


@router.post("/extract-files")
def extract_files(req: ExtractFilesRequest, db: Session = Depends(get_db)):
    """Extract semesters, subjects, and teachers from uploaded CSV files and automatically
    sync them to the catalog, generating user accounts (email & password) for faculty."""
    from app.scheduler.file_parser import parse_subjects_file, parse_teachers_file
    from app.services.teacher_catalog_service import (
        sync_extracted_files_to_catalog,
        derive_teacher_short_form,
    )

    parsed_teachers_map: Dict[str, Dict[str, Any]] = {}
    if req.teachers_file_content:
        for t in parse_teachers_file(req.teachers_file_content):
            parsed_teachers_map[t["id"]] = t

    parsed_sub_data = {"semesters": [], "subjects": [], "teachers": []}
    if req.subjects_file_content:
        parsed_sub_data = parse_subjects_file(req.subjects_file_content)

    # Merge teachers: teachers from file take precedence, supplement with discovered ones
    final_teachers_map = dict(parsed_teachers_map)
    for t in parsed_sub_data.get("teachers", []):
        tid = t["id"]
        if tid not in final_teachers_map:
            final_teachers_map[tid] = t

    semesters = parsed_sub_data.get("semesters", [])
    subjects = parsed_sub_data.get("subjects", [])
    teachers = list(final_teachers_map.values())

    created_accounts: List[Dict[str, Any]] = []
    sync_summary = {}

    if req.auto_sync_catalog:
        try:
            sync_res = sync_extracted_files_to_catalog(
                db=db,
                semesters=semesters,
                subjects=subjects,
                teachers=teachers,
            )
            created_accounts = sync_res.get("created_accounts", [])
            sync_summary = {
                "synced_teachers_count": sync_res.get("synced_teachers_count", 0),
                "synced_subjects_count": sync_res.get("synced_subjects_count", 0),
                "new_accounts_created": len(created_accounts),
            }
            # Assign short forms to extracted teachers
            t_map = sync_res.get("teacher_id_to_short", {})
            for t in teachers:
                tid = t.get("id") or ""
                t["short_name"] = t_map.get(tid) or derive_teacher_short_form(t.get("name", ""), tid)
        except Exception as e:
            sync_summary = {"error": str(e)}

    return {
        "semesters": semesters,
        "subjects": subjects,
        "teachers": teachers,
        "created_accounts": created_accounts,
        "sync_summary": sync_summary,
        "summary": {
            "semesters_count": len(semesters),
            "subjects_count": len(subjects),
            "teachers_count": len(teachers),
        },
    }


@router.get("/presets")
def get_presets(db: Session = Depends(get_db)):
    """Return default boards: the university benchmark demo and DB catalog board."""
    demo_board = build_demo_board()
    try:
        catalog_board = build_board_from_db(db)
    except Exception:
        catalog_board = demo_board

    return {
        "demo": demo_board.to_dict(),
        "catalog": catalog_board.to_dict(),
    }


@router.post("/validate")
def validate_board(req: ValidateBoardRequest):
    """Validate a SchedulingBoard configuration before running the solver."""
    try:
        board = SchedulingBoard.from_dict(req.board)
        issues = board.validate()
        has_error = any(i.level == "error" for i in issues)
        return {
            "valid": not has_error,
            "issues": [{"level": i.level, "message": i.message} for i in issues],
        }
    except Exception as e:
        return {
            "valid": False,
            "issues": [{"level": "error", "message": str(e)}],
        }


class CreateFacultyRequest(BaseModel):
    name: str
    id: Optional[str] = None
    is_internal: bool = True
    designation: Optional[str] = None
    initial_subject: Optional[str] = None


class CreateSubjectRequest(BaseModel):
    code: str
    name: str
    program: str = "B.Tech"
    semester: str = "1st"
    class_type: str = "Theory"
    weekly_hours: int = 2
    teacher_short_name: Optional[str] = None


@router.post("/generate")
def run_generator(req: GenerateBoardRequest):
    """Run the 4-phase CSP + Repair + Simulated Annealing timetable generator."""
    try:
        board = SchedulingBoard.from_dict(req.board)
        result = board.generate(strict=req.strict)
        return result.to_dict()
    except InfeasibleInputError as e:
        raise HTTPException(status_code=422, detail=f"Infeasible scheduling input: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")


@router.post("/faculty")
def create_generator_faculty(req: CreateFacultyRequest, db: Session = Depends(get_db)):
    """Create a faculty member in the catalog, auto-generating their user login account (email & password)."""
    import re
    from app.services.teacher_catalog_service import ensure_teacher_account

    t_obj, u_obj, is_new, email, plain_pw = ensure_teacher_account(
        db=db,
        full_name=req.name,
        short_name=req.id if req.id and not re.match(r"^[IE]\d+$", req.id, re.IGNORECASE) else None,
        is_internal=req.is_internal,
        designation=req.designation,
        initial_subject=req.initial_subject,
    )
    return {
        "id": t_obj.short_name,
        "short_name": t_obj.short_name,
        "name": t_obj.full_name,
        "full_name": t_obj.full_name,
        "internal": t_obj.is_internal,
        "is_internal": t_obj.is_internal,
        "email": email,
        "auto_email": email,
        "password": plain_pw,
        "auto_password": plain_pw,
        "is_new": is_new,
        "message": f"Faculty member '{t_obj.full_name}' ({t_obj.short_name}) registered. Login: {email}",
    }


@router.post("/subject")
def create_generator_subject(req: CreateSubjectRequest, db: Session = Depends(get_db)):
    """Add a subject to the corresponding semester catalog, linking to the assigned teacher."""
    from app.services.teacher_catalog_service import ensure_subject_catalog

    subj, created = ensure_subject_catalog(
        db=db,
        code=req.code,
        name=req.name,
        program=req.program,
        semester=req.semester,
        entry_type=req.class_type,
        weekly_hours=req.weekly_hours,
        teacher_short_name=req.teacher_short_name,
    )
    return {
        "code": subj.code,
        "name": subj.name,
        "program": subj.program,
        "semester": subj.semester,
        "entry_type": subj.entry_type,
        "weekly_hours": subj.weekly_hours,
        "created": created,
        "message": f"Subject '{subj.name}' ({subj.code}) added to {subj.program} {subj.semester}.",
    }


@router.post("/save-draft")
def save_generated_draft(
    req: SaveDraftRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    """Persist generated classes into a new timetable version in the database,
    automatically ensuring teacher accounts exist, subjects are registered in
    the corresponding semester, and teacher short forms (NOT I01, I02...) are saved."""
    import re
    from app.services.teacher_catalog_service import (
        resolve_entry_teacher_short_name,
        derive_teacher_short_form,
        ensure_teacher_account,
        ensure_subject_catalog,
    )

    if not req.classes:
        raise HTTPException(status_code=400, detail="Cannot save an empty timetable.")

    entries: List[Dict[str, Any]] = []
    created_accounts: List[Dict[str, Any]] = []

    for c in req.classes:
        sem_raw = c.get("semester_id") or c.get("semester") or "1"
        semester_formatted = _format_semester(str(sem_raw))
        day_formatted = _normalize_day(c.get("day") or "Monday")
        prog_formatted = c.get("programme") or c.get("program") or "B.Tech"

        t_name = str(c.get("teacher_name") or "").strip()
        t_id = str(c.get("teacher_id") or c.get("teacher") or "").strip()
        s_name = str(c.get("subject_name") or "").strip()
        s_code = str(c.get("subject_code") or "").strip()
        c_type = str(c.get("class_type") or c.get("type") or "Theory").capitalize()

        # 1. Resolve clean teacher short form (NEVER save I01, I02.. I19, E02!)
        short_form = resolve_entry_teacher_short_name(
            teacher_val=t_id or t_name,
            subject_name=s_name,
            subject_code=s_code,
            db=db,
        )
        if not short_form or re.match(r"^[IE]\d+$", short_form, re.IGNORECASE):
            short_form = derive_teacher_short_form(t_name, fallback_id=t_id)

        # 2. Automatically ensure teacher exists in catalog & has User account
        if t_name and t_name != t_id:
            try:
                t_obj, u_obj, is_new, email, plain_pw = ensure_teacher_account(
                    db=db,
                    full_name=t_name,
                    short_name=short_form,
                    initial_subject=s_code,
                )
                short_form = t_obj.short_name
                if is_new:
                    created_accounts.append({
                        "short_name": short_form,
                        "full_name": t_obj.full_name,
                        "email": email,
                        "password": plain_pw,
                    })
            except Exception:
                pass

        # 3. Automatically ensure subject exists in semester catalog
        if s_code:
            try:
                ensure_subject_catalog(
                    db=db,
                    code=s_code,
                    name=s_name or s_code,
                    program=prog_formatted,
                    semester=semester_formatted,
                    entry_type=c_type,
                    teacher_short_name=short_form,
                )
            except Exception:
                pass

        entries.append({
            "day": day_formatted,
            "program": prog_formatted,
            "semester": semester_formatted,
            "start": c.get("start") or c.get("start_time") or "10:00",
            "end": c.get("end") or c.get("end_time") or "12:00",
            "subject_code": s_code,
            "subject_name": s_name or s_code,
            "teacher": short_form,
            "type": c_type,
            "room": c.get("room") or "101",
        })

    try:
        version = create_draft(
            db=db,
            user=user,
            entries=entries,
            change_summary=req.change_summary,
            llm_output={"generated_by": "Automated Timetable Generator (CSP + SA)", "total_classes": len(entries)},
        )

        if req.publish_immediately:
            version.status = VersionStatus.PUBLISHED
            version.published_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(version)

        audit_log(
            db=db,
            action="GENERATE_AUTOMATED_TIMETABLE",
            user_id=user.id,
            version_id=version.id,
            params={
                "classes_count": len(entries),
                "published": req.publish_immediately,
                "change_summary": req.change_summary,
            },
        )

        try:
            invalidate_timetable_data("automated_generator_save")
        except Exception:
            pass

        return {
            "version_id": version.id,
            "status": version.status.value,
            "entries_count": len(version.entries),
            "created_accounts": created_accounts,
            "message": f"Successfully saved {len(entries)} classes as Version #{version.id} ({version.status.value}). Teacher short forms recorded.",
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save generated timetable: {e}")
