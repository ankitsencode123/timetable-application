"""Faculty busy slots API — mark teachers as unavailable on certain days/dates.
When a permanent busy slot is created, the system automatically detects
affected existing classes and tries to reschedule them.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.secure_auth import get_current_user, require_admin
from app.models.user import User
from app.models.busy_slot import TeacherBusySlot

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────────────

class BusySlotIn(BaseModel):
    teacher_short_name: str
    scope: str                          # "permanent" | "temporary"
    day_of_week: Optional[str] = None   # required for permanent
    specific_date: Optional[date] = None  # required for temporary
    reason: Optional[str] = None


class BusySlotOut(BaseModel):
    id: int
    teacher_short_name: str
    scope: str
    day_of_week: Optional[str]
    specific_date: Optional[date]
    reason: Optional[str]

    class Config:
        from_attributes = True


class MoveInfo(BaseModel):
    subject_code: str
    subject_name: str
    from_day: str
    from_start: str
    from_end: str
    to_day: str
    to_start: str
    to_end: str
    room: str


class CancelledInfo(BaseModel):
    subject_code: str
    subject_name: str
    day: str
    start: str
    end: str
    reason: str = "No valid conflict-free slot was found"


class BusySlotCreateResponse(BaseModel):
    slot: BusySlotOut
    auto_rescheduled: List[MoveInfo] = []
    cancelled_classes: List[CancelledInfo] = []
    message: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _entry_conflicts_busy(entry: dict, busy_day: Optional[str]) -> bool:
    """Return True if entry is on the busy day."""
    if not busy_day:
        return False
    return entry.get("day", "").lower() == busy_day.lower()


def _reschedule_affected_classes(
    db: Session,
    teacher: str,
    conflict_day: Optional[str],
) -> Dict[str, Any]:
    """
    Attempt to auto-reschedule all classes of `teacher` on `conflict_day`.
    Returns a dict with 'auto_rescheduled' and 'cancelled_classes' lists.
    """
    from app.services.timetable_service import get_published_version, list_versions, create_draft, get_version
    from app.scheduler.validator import validate_schedule
    from app.actions.suggestions import suggest_alternatives
    from app.models.user import User as UserModel, RoleEnum

    # Get the admin user for version creation
    admin_user = db.query(UserModel).filter(UserModel.role == RoleEnum.ADMIN).first()
    if not admin_user:
        admin_user = db.query(UserModel).first()
    if not admin_user:
        return {"auto_rescheduled": [], "cancelled_classes": []}

    # Load current schedule (latest draft/version)
    versions = list_versions(db)
    if versions:
        version = get_version(db, versions[0].id)
    else:
        version = None
        
    if not version or not getattr(version, 'entries', None):
        return {"auto_rescheduled": [], "cancelled_classes": []}

    schedule = [
        {
            "day": e.day, "program": e.program, "semester": e.semester,
            "start": e.start_time, "end": e.end_time,
            "subject_code": e.subject_code, "subject_name": e.subject_name,
            "teacher": e.teacher, "type": e.entry_type, "room": e.room,
        }
        for e in version.entries
    ]
    
    # Find affected entries
    affected = [e for e in schedule if e.get("teacher") == teacher and _entry_conflicts_busy(e, conflict_day)]
    if not affected:
        return {"auto_rescheduled": [], "cancelled_classes": []}

    auto_rescheduled: List[dict] = []
    cancelled_classes: List[dict] = []
    current_schedule = copy.deepcopy(schedule)

    for entry in affected:
        # Build a synthetic violation to pass into suggest_alternatives
        violation = {
            "rule": "H6_teacher_free_day",
            "a": entry,
            "entry": entry,
            "teacher": teacher,
            "day": conflict_day,
        }
        sugg = suggest_alternatives(
            schedule=current_schedule,
            action_type="MOVE_CLASS",
            violation=violation,
            teacher=teacher,
            need_lab=(entry.get("type") == "Practical"),
            orig_target={
                "day": entry["day"],
                "start_time": entry["start"],
                "end_time": entry["end"],
                "program": entry["program"],
                "semester": entry["semester"],
                "subject_code": entry["subject_code"],
                "teacher": entry["teacher"],
            },
            busy_days={conflict_day} if conflict_day else None,
        )

        rich = sugg.get("rich_suggestions", [])
        if rich:
            best = rich[0]
            action = best.get("action", {})
            new_day = action.get("new_day", entry["day"])
            new_start = action.get("new_start_time", entry["start"])
            new_end = action.get("new_end_time", entry["end"])
            new_room = action.get("new_room", entry.get("room", ""))

            # Apply the move to current_schedule for the next iteration
            for i, e in enumerate(current_schedule):
                if (e.get("day") == entry["day"] and
                        e.get("start") == entry["start"] and
                        e.get("subject_code") == entry["subject_code"] and
                        e.get("teacher") == teacher):
                    current_schedule[i] = {**e, "day": new_day, "start": new_start, "end": new_end, "room": new_room}
                    break

            auto_rescheduled.append({
                "subject_code": entry["subject_code"],
                "subject_name": entry.get("subject_name", ""),
                "from_day": entry["day"],
                "from_start": entry["start"],
                "from_end": entry["end"],
                "to_day": new_day,
                "to_start": new_start,
                "to_end": new_end,
                "room": new_room,
            })
        else:
            cancelled_classes.append({
                "subject_code": entry["subject_code"],
                "subject_name": entry.get("subject_name", ""),
                "day": entry["day"],
                "start": entry["start"],
                "end": entry["end"],
                "reason": "No valid conflict-free slot was found. Please reschedule manually.",
            })

    # Persist the updated schedule as a new draft
    if auto_rescheduled:
        new_version_summary = (
            f"Auto-rescheduled: teacher {teacher} marked busy on {conflict_day}. "
            f"{len(auto_rescheduled)} class(es) moved."
        )
        create_draft(
            db=db,
            user=admin_user,
            entries=current_schedule,
            change_summary=new_version_summary,
            parent_id=version.id,
        )

    return {"auto_rescheduled": auto_rescheduled, "cancelled_classes": cancelled_classes}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[BusySlotOut])
def list_busy_slots(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(TeacherBusySlot).all()


@router.post("", response_model=BusySlotCreateResponse, status_code=status.HTTP_201_CREATED)
def create_busy_slot(
    req: BusySlotIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if req.scope == "permanent" and not req.day_of_week:
        raise HTTPException(status_code=400, detail="day_of_week is required for permanent busy slots.")
    if req.scope == "temporary" and not req.specific_date:
        raise HTTPException(status_code=400, detail="specific_date is required for temporary busy slots.")

    slot = TeacherBusySlot(
        teacher_short_name=req.teacher_short_name,
        scope=req.scope,
        day_of_week=req.day_of_week,
        specific_date=req.specific_date,
        reason=req.reason,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)

    # Auto-reschedule affected classes for permanent slots (day-based)
    auto_result: Dict[str, Any] = {"auto_rescheduled": [], "cancelled_classes": []}
    if req.scope == "permanent" and req.day_of_week:
        try:
            auto_result = _reschedule_affected_classes(
                db=db,
                teacher=req.teacher_short_name,
                conflict_day=req.day_of_week,
            )
        except Exception as e:
            # Non-fatal: log but don't block the response
            import traceback
            print(f"[busy_slots] auto-reschedule failed: {e}\n{traceback.format_exc()}")

    rescheduled_count = len(auto_result["auto_rescheduled"])
    cancelled_count = len(auto_result["cancelled_classes"])
    if rescheduled_count or cancelled_count:
        msg = (
            f"Busy slot created. "
            f"{rescheduled_count} class(es) auto-rescheduled"
            + (f", {cancelled_count} could not be moved (see cancelled_classes)." if cancelled_count else ".")
        )
    else:
        msg = "Busy slot created. No existing classes were affected."

    return BusySlotCreateResponse(
        slot=BusySlotOut.model_validate(slot),
        auto_rescheduled=[MoveInfo(**x) for x in auto_result["auto_rescheduled"]],
        cancelled_classes=[CancelledInfo(**x) for x in auto_result["cancelled_classes"]],
        message=msg,
    )


@router.delete("/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_busy_slot(
    slot_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    slot = db.query(TeacherBusySlot).filter(TeacherBusySlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Busy slot not found.")
    db.delete(slot)
    db.commit()
