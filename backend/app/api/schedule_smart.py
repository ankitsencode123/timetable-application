"""Smart Auto-Schedule endpoint.

Given a class spec without a time slot, find the best conflict-free
time + room automatically and return it as a proposed ADD_CLASS action.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_teacher_or_admin
from app.models.user import User
from app.models.timetable import TimetableVersion
from app.models.busy_slot import TeacherBusySlot
from app.actions.suggestions import (
    suggest_free_slots_for_teacher,
    suggest_free_rooms,
    _count_new_violations,
    _simulate_move,
)
from app.actions.engine import ActionEngine
from app.actions.types import parse_action

router = APIRouter()


class SmartScheduleRequest(BaseModel):
    program: str
    semester: str
    subject_code: str
    subject_name: str
    teacher: str
    entry_type: str = "Theory"           # "Theory" | "Practical"
    room: Optional[str] = None           # leave blank to auto-assign
    preferred_day: Optional[str] = None  # optional day preference
    auto_execute: bool = False           # if True, immediately book the slot


class SmartScheduleResponse(BaseModel):
    found: bool
    proposed_action: Optional[dict] = None
    day: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    room: Optional[str] = None
    message: str = ""
    new_version_id: Optional[int] = None


@router.post("", response_model=SmartScheduleResponse)
def smart_schedule(
    req: SmartScheduleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    # ── Load current schedule ─────────────────────────────────────────────
    v = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
    schedule: List[dict] = [e.to_dict() for e in v.entries] if v else []

    # ── Early Validation: Ensure Subject exists for this Program/Sem ────────
    import app.scheduler.constraints as C
    prog_raw = req.program.strip().rstrip('.')
    sem_raw = req.semester.strip()
    subj_code = req.subject_code.strip().lower()
    allowed = C.PROGRAMME_SUBJECT_MAP.get((prog_raw, sem_raw), set())
    if not allowed or (subj_code not in allowed and subj_code[:-2] not in allowed):
        if not (subj_code in ("m", "mm") and ("m" in allowed or "mm" in allowed)):
            return SmartScheduleResponse(
                found=False,
                message=(
                    f"Subject '{req.subject_code}' / '{req.subject_name}' is not recognized "
                    f"for {req.program} {req.semester}. Please add it to the Catalog first."
                )
            )

    # ── Collect permanent busy days for teacher ───────────────────────────
    busy_records = db.query(TeacherBusySlot).filter(
        TeacherBusySlot.teacher_short_name == req.teacher,
    ).all()
    today = date.today()
    busy_days: set[str] = set()
    for b in busy_records:
        if b.scope == "permanent" and b.day_of_week:
            busy_days.add(b.day_of_week)
        elif b.scope == "temporary" and b.specific_date == today:
            # block temporary dates that fall on today (or could check day name)
            pass

    # ── Find free slots for the teacher ──────────────────────────────────
    duration = 180 if req.entry_type == "Practical" else 120
    free_slots = suggest_free_slots_for_teacher(schedule, req.teacher, duration, busy_days=busy_days)

    # Honor preferred day filter
    if req.preferred_day:
        preferred = [s for s in free_slots if s["day"] == req.preferred_day]
        if preferred:
            free_slots = preferred

    # ── For each candidate slot, find a conflict-free room ───────────────
    need_lab = req.entry_type == "Practical"
    best_slot = None
    best_room = None

    for slot in free_slots:
        # Build candidate entry for simulation
        candidate_entry = {
            "day": slot["day"], "start": slot["start"], "end": slot["end"],
            "program": req.program, "semester": req.semester,
            "subject_code": req.subject_code, "subject_name": req.subject_name,
            "teacher": req.teacher, "type": req.entry_type,
            "room": req.room or "",
        }
        # Try preferred room first, then auto-assign
        rooms_to_try = ([req.room] if req.room else []) + suggest_free_rooms(
            schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab
        )
        for room in rooms_to_try:
            trial_entry = {**candidate_entry, "room": room}
            candidate_schedule = schedule + [trial_entry]
            sim_vio = _count_new_violations(schedule, candidate_schedule)
            if sim_vio > 0:
                from app.scheduler.validator import validate_schedule
                vv = validate_schedule(candidate_schedule)
                new_v = [v for v in vv if v.get("a") == trial_entry or v.get("b") == trial_entry or v.get("entry") == trial_entry]
                print(f"DEBUG VIOLATION Slot {slot['day']} {slot['start']}-{slot['end']} Room {room} -> {new_v}")
                
            if sim_vio == 0:
                best_slot = slot
                best_room = room
                break
        if best_slot:
            break

    if not best_slot or not best_room:
        return SmartScheduleResponse(
            found=False,
            message=(
                f"No conflict-free slot found for {req.teacher} "
                f"({'any day' if not req.preferred_day else req.preferred_day}). "
                f"Consider adjusting busy days or constraints."
            ),
        )

    proposed = {
        "action": "ADD_CLASS",
        "spec": {
            "program": req.program,
            "semester": req.semester,
            "day": best_slot["day"],
            "start_time": best_slot["start"],
            "end_time": best_slot["end"],
            "subject_code": req.subject_code,
            "subject_name": req.subject_name,
            "teacher": req.teacher,
            "entry_type": req.entry_type,
            "room": best_room,
        },
    }

    if not req.auto_execute:
        return SmartScheduleResponse(
            found=True,
            proposed_action=proposed,
            day=best_slot["day"],
            start=best_slot["start"],
            end=best_slot["end"],
            room=best_room,
            message=(
                f"Best slot: {best_slot['day']} {best_slot['start']}–{best_slot['end']} "
                f"in {best_room}"
            ),
        )

    # ── Auto-execute ──────────────────────────────────────────────────────
    try:
        action = parse_action(proposed)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid action payload: {e}")

    engine = ActionEngine(db=db, user=user)
    result = engine.execute([action])

    if not result.success:
        errors = " | ".join(r.error for r in result.results if not r.success)
        return SmartScheduleResponse(
            found=True,
            proposed_action=proposed,
            day=best_slot["day"],
            start=best_slot["start"],
            end=best_slot["end"],
            room=best_room,
            message=f"Slot found but booking failed: {errors}",
        )

    return SmartScheduleResponse(
        found=True,
        proposed_action=proposed,
        day=best_slot["day"],
        start=best_slot["start"],
        end=best_slot["end"],
        room=best_room,
        new_version_id=result.new_version_id,
        message=f"Booked! {best_slot['day']} {best_slot['start']}–{best_slot['end']} in {best_room}. Version #{result.new_version_id}.",
    )
