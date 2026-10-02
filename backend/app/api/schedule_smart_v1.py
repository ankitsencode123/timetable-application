"""Smart Auto-Schedule endpoint.

Given a class spec without a time slot, find the best conflict-free
time + room automatically and return it as a proposed ADD_CLASS action.
"""
from __future__ import annotations

import copy
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
    _simulate_remove,
    _fingerprint,
)
from app.actions.engine import ActionEngine
from app.actions.types import parse_action
from app.scheduler.validator import validate_schedule, to_minutes
import app.scheduler.constraints as C

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
    auto_execute: bool = False           # if True, immediately book the FIRST slot (legacy)


class NormalProposal(BaseModel):
    day: str
    start: str
    end: str
    room: str
    proposed_action: dict
    constraint_issue: Optional[str] = None  # If present, this is an imperfect slot


class SmartScheduleResponse(BaseModel):
    found: bool
    is_perfect_match: bool = True
    proposals: List[NormalProposal] = []
    message: str = ""
    # Legacy fields (will map to the first proposal for backwards compatibility)
    proposed_action: Optional[dict] = None
    day: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    room: Optional[str] = None
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
    base_v = validate_schedule(schedule)
    base_fps = { _fingerprint(x) for x in base_v }

    # ── Early Validation: Ensure Subject exists for this Program/Sem ────────
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
            pass

    # ── Find free slots for the teacher ──────────────────────────────────
    duration = 180 if req.entry_type == "Practical" else 120
    all_free_slots = suggest_free_slots_for_teacher(schedule, req.teacher, duration, busy_days=busy_days)

    # Preferred-day: search that day FIRST, fall back to all days if needed
    preferred_day_only_slots = (
        [s for s in all_free_slots if s["day"] == req.preferred_day]
        if req.preferred_day else []
    )
    free_slots = preferred_day_only_slots or all_free_slots
    expanded_to_all_days = bool(req.preferred_day and not preferred_day_only_slots)

    need_lab = req.entry_type == "Practical"
    
    perfect_options: List[NormalProposal] = []
    imperfect_options: List[NormalProposal] = []

    # --- Pass 1: search within free_slots (may be restricted to preferred day) ---
    for slot in free_slots:
        if len(perfect_options) >= 3:
            break
            
        candidate_entry = {
            "day": slot["day"], "start": slot["start"], "end": slot["end"],
            "program": req.program, "semester": req.semester,
            "subject_code": req.subject_code, "subject_name": req.subject_name,
            "teacher": req.teacher, "type": req.entry_type,
            "room": req.room or "",
        }
        rooms_to_try = ([req.room] if req.room else []) + suggest_free_rooms(
            schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab
        )
        
        # Try the provided room or the first available room to avoid span
        if rooms_to_try:
            room = rooms_to_try[0]
            trial_entry = {**candidate_entry, "room": room}
            candidate_schedule = schedule + [trial_entry]
            
            cand_v = validate_schedule(candidate_schedule)
            new_violations = [item for item in cand_v if _fingerprint(item) not in base_fps]
            
            proposed = {
                "action": "ADD_CLASS",
                "spec": {
                    "program": req.program, "semester": req.semester,
                    "day": slot["day"], "start_time": slot["start"], "end_time": slot["end"],
                    "subject_code": req.subject_code, "subject_name": req.subject_name,
                    "teacher": req.teacher, "entry_type": req.entry_type,
                    "room": room,
                },
            }
            
            if len(new_violations) == 0:
                perfect_options.append(NormalProposal(
                    day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                    proposed_action=proposed
                ))
            else:
                # Check for hard physical conflicts. If H1, H2, H3 exist, this slot is physically impossible.
                hard_rules = {"H1_semester_clash", "H2_teacher_clash", "H3_room_clash", "H12_teacher_busy"}
                if not any(v.get("rule") in hard_rules for v in new_violations):
                    issue_msgs = []
                    for v in new_violations:
                        rule = v.get("rule", "")
                        note = v.get("note")
                        if note:
                            issue_msgs.append(note)
                        elif rule == "H5_multiple_theory_same_day":
                            issue_msgs.append("Exceeds 1 theory class per day for teacher.")
                        elif rule == "H5_multiple_practical_same_day":
                            issue_msgs.append("Exceeds 1 practical class per day for teacher.")
                        elif rule == "H6_no_free_day":
                            issue_msgs.append("Teacher would have no free days.")
                        elif rule == "H11_wrong_semester_subject":
                            issue_msgs.append("Subject is not officially listed for this semester.")
                        else:
                            issue_msgs.append(f"Soft constraint violation: {rule}")
                            
                    imperfect_options.append(NormalProposal(
                        day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                        proposed_action=proposed,
                        constraint_issue=" | ".join(issue_msgs)
                    ))

    final_proposals = perfect_options if perfect_options else imperfect_options[:3]
    is_perfect = len(perfect_options) > 0

    # --- Pass 2: if preferred-day search found nothing, expand to all days ---
    if not final_proposals and preferred_day_only_slots and all_free_slots:
        # Re-run search over all remaining days (excluding preferred, already tried)
        other_slots = [s for s in all_free_slots if s["day"] != req.preferred_day]
        perfect_options2: List[NormalProposal] = []
        imperfect_options2: List[NormalProposal] = []
        for slot in other_slots:
            if len(perfect_options2) >= 3:
                break
            candidate_entry = {
                "day": slot["day"], "start": slot["start"], "end": slot["end"],
                "program": req.program, "semester": req.semester,
                "subject_code": req.subject_code, "subject_name": req.subject_name,
                "teacher": req.teacher, "type": req.entry_type,
                "room": req.room or "",
            }
            rooms_to_try = ([req.room] if req.room else []) + suggest_free_rooms(
                schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab
            )
            if rooms_to_try:
                room = rooms_to_try[0]
                trial_entry = {**candidate_entry, "room": room}
                candidate_schedule = schedule + [trial_entry]
                cand_v = validate_schedule(candidate_schedule)
                new_violations = [item for item in cand_v if _fingerprint(item) not in base_fps]
                proposed = {
                    "action": "ADD_CLASS",
                    "spec": {
                        "program": req.program, "semester": req.semester,
                        "day": slot["day"], "start_time": slot["start"], "end_time": slot["end"],
                        "subject_code": req.subject_code, "subject_name": req.subject_name,
                        "teacher": req.teacher, "entry_type": req.entry_type,
                        "room": room,
                    },
                }
                fallback_note = f"Preferred day ({req.preferred_day}) unavailable — expanded search."
                if len(new_violations) == 0:
                    perfect_options2.append(NormalProposal(
                        day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                        proposed_action=proposed,
                        constraint_issue=fallback_note,
                    ))
                else:
                    hard_rules = {"H1_semester_clash", "H2_teacher_clash", "H3_room_clash", "H12_teacher_busy"}
                    if not any(v.get("rule") in hard_rules for v in new_violations):
                        imperfect_options2.append(NormalProposal(
                            day=slot["day"], start=slot["start"], end=slot["end"], room=room,
                            proposed_action=proposed,
                            constraint_issue=fallback_note,
                        ))
        final_proposals = perfect_options2 if perfect_options2 else imperfect_options2[:3]
        is_perfect = False  # Expanded fallback — not a perfect preferred-day match

    if not final_proposals and expanded_to_all_days:
        # All-days search: teacher fully booked everywhere
        return SmartScheduleResponse(
            found=False,
            message=(
                f"No slots found for {req.teacher} on {req.preferred_day} or any other day. "
                f"Teacher may be fully booked or blocked by hard constraints."
            ),
        )

    if not final_proposals:
        return SmartScheduleResponse(
            found=False,
            message=(
                f"No slots found for {req.teacher} "
                f"({'any day' if not req.preferred_day else req.preferred_day}). "
                f"Teacher may be fully booked or blocked by hard constraints."
            ),
        )

    # Legacy auto-execute support
    if req.auto_execute and is_perfect:
        first_prop = final_proposals[0].proposed_action
        try:
            action = parse_action(first_prop)
            engine = ActionEngine(db=db, user=user)
            result = engine.execute([action])
            if result.success:
                return SmartScheduleResponse(
                    found=True, is_perfect_match=True, proposals=final_proposals,
                    proposed_action=first_prop, day=final_proposals[0].day,
                    start=final_proposals[0].start, end=final_proposals[0].end, room=final_proposals[0].room,
                    new_version_id=result.new_version_id,
                    message=f"Booked! {final_proposals[0].day} {final_proposals[0].start}–{final_proposals[0].end} in {final_proposals[0].room}. Version #{result.new_version_id}.",
                )
        except Exception:
            pass

    return SmartScheduleResponse(
        found=True,
        is_perfect_match=is_perfect,
        proposals=final_proposals,
        proposed_action=final_proposals[0].proposed_action,
        day=final_proposals[0].day, start=final_proposals[0].start, end=final_proposals[0].end, room=final_proposals[0].room,
        message="Found options" if is_perfect else "Found closest feasible options (with constraint warnings)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Advanced Smart Schedule
# ─────────────────────────────────────────────────────────────────────────────

class AdvancedSmartScheduleRequest(BaseModel):
    program: str
    semester: str
    subject_code: str
    subject_name: str
    teacher: str
    entry_type: str = "Theory"
    room: Optional[str] = None
    preferred_day: Optional[str] = None


class ProposedModification(BaseModel):
    description: str
    modification_type: str            # "none" | "move" | "cancel"
    affected_class: dict
    new_slot: Optional[dict] = None
    priority_slot: dict
    actions_to_apply: List[dict]


class AdvancedSmartScheduleResponse(BaseModel):
    found: bool
    proposals: List[ProposedModification] = []
    message: str = ""


def _find_best_slot_for_new_class(
    schedule: List[dict],
    program: str,
    semester: str,
    subject_code: str,
    subject_name: str,
    teacher: str,
    entry_type: str,
    room: Optional[str],
    busy_days: set,
    preferred_day: Optional[str] = None,
) -> Optional[dict]:
    """Find the best slot+room for a new class given the current schedule (must have 0 violations).
    
    If preferred_day is set, slots on that day are tried first before expanding to all days.
    """
    need_lab = (entry_type == "Practical")
    
    try:
        from app.scheduler.fast_suggester import find_valid_slots
        entry = {
            "day": preferred_day,
            "program": program, "semester": semester,
            "subject_code": subject_code, "subject_name": subject_name,
            "teacher": teacher, "type": entry_type,
            "room": room or "",
        }
        res = find_valid_slots(schedule, entry, None, need_lab=need_lab, orig_target=None, 
                               simulate_as_add=True, limit=1, allow_cascade=False)
        if res:
            return {"day": res[0]["day"], "start": res[0]["start"], "end": res[0]["end"], "room": res[0].get("room", "")}
        return None
    except Exception:
        pass

    # Fallback to brute force
    duration = 180 if need_lab else 120
    free_slots = suggest_free_slots_for_teacher(schedule, teacher, duration, busy_days=busy_days)

    # Re-order: preferred day first, then everything else
    if preferred_day:
        preferred_slots = [s for s in free_slots if s["day"] == preferred_day]
        other_slots = [s for s in free_slots if s["day"] != preferred_day]
        ordered_slots = preferred_slots + other_slots
    else:
        ordered_slots = free_slots

    for slot in ordered_slots:
        candidate_entry = {
            "day": slot["day"], "start": slot["start"], "end": slot["end"],
            "program": program, "semester": semester,
            "subject_code": subject_code, "subject_name": subject_name,
            "teacher": teacher, "type": entry_type,
            "room": room or "",
        }
        rooms_to_try = ([room] if room else []) + suggest_free_rooms(
            schedule, slot["day"], slot["start"], slot["end"], need_lab=need_lab
        )
        for r in rooms_to_try:
            trial = {**candidate_entry, "room": r}
            if _count_new_violations(schedule, schedule + [trial]) == 0:
                return {"day": slot["day"], "start": slot["start"], "end": slot["end"], "room": r}
    return None


@router.post("/advanced", response_model=AdvancedSmartScheduleResponse)
def advanced_smart_schedule(
    req: AdvancedSmartScheduleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    """
    Advanced Smart Schedule — finds modifications strictly within the same
    program+semester to make room for a priority class, OR simply returns the
    free slot if no modification is necessary.

    Returns proposals without applying any changes.
    The caller must explicitly execute the actions_to_apply after user confirmation.
    """
    # ── Load schedule ────────────────────────────────────────────────────
    v = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
    schedule: List[dict] = [e.to_dict() for e in v.entries] if v else []

    # ── Validate subject is in catalog ───────────────────────────────────
    prog_raw = req.program.strip().rstrip('.')
    sem_raw = req.semester.strip()
    subj_code = req.subject_code.strip().lower()
    allowed = C.PROGRAMME_SUBJECT_MAP.get((prog_raw, sem_raw), set())
    if allowed and subj_code not in allowed and subj_code[:-2] not in allowed:
        if not (subj_code in ("m", "mm") and ("m" in allowed or "mm" in allowed)):
            return AdvancedSmartScheduleResponse(
                found=False,
                message=(
                    f"Subject '{req.subject_code}' is not recognized for "
                    f"{req.program} {req.semester}. Add it to the Catalog first."
                )
            )

    # ── Collect teacher busy days ────────────────────────────────────────
    busy_records = db.query(TeacherBusySlot).filter(
        TeacherBusySlot.teacher_short_name == req.teacher,
    ).all()
    busy_days: set = set()
    for b in busy_records:
        if b.scope == "permanent" and b.day_of_week:
            busy_days.add(b.day_of_week)

    proposals: List[ProposedModification] = []

    # ── Step 1: Normal scheduling (no modifications required) ──────────────
    # Preferred day: try the preferred day first; if slot found there → great.
    # If not, the helper will automatically fall through to other days.
    normal_slot = _find_best_slot_for_new_class(
        schedule, req.program, req.semester,
        req.subject_code, req.subject_name,
        req.teacher, req.entry_type, req.room, busy_days,
        preferred_day=req.preferred_day,
    )
    if normal_slot:
        proposed_action = {
            "action": "ADD_CLASS",
            "spec": {
                "program": req.program, "semester": req.semester,
                "day": normal_slot["day"],
                "start_time": normal_slot["start"], "end_time": normal_slot["end"],
                "subject_code": req.subject_code, "subject_name": req.subject_name,
                "teacher": req.teacher, "entry_type": req.entry_type,
                "room": normal_slot["room"],
            },
        }
        proposals.append(ProposedModification(
            description=(
                f"No modifications needed. Best free slot available: "
                f"{normal_slot['day']} {normal_slot['start']}–{normal_slot['end']} "
                f"in {normal_slot['room']}."
            ),
            modification_type="none",
            affected_class={},
            new_slot=normal_slot,
            priority_slot=normal_slot,
            actions_to_apply=[proposed_action],
        ))

    # ── Step 2: Find same-program/semester classes to modify ─────────────
    same_group = [
        e for e in schedule
        if (C.normalize_program(e.get("program", "")) == C.normalize_program(req.program))
        and (e.get("semester", "").strip() == sem_raw)
    ]

    for candidate in same_group:
        if len(proposals) >= 5:
            break

        cand_teacher = candidate.get("teacher", "")
        cand_subj = candidate.get("subject_name") or candidate.get("subject_code", "")
        cand_day = candidate.get("day", "")
        cand_start = candidate.get("start", "")
        cand_end = candidate.get("end", "")

        # Prevent "Cancel Class X" to "Add Class X" (circular self-replacements)
        if cand_subj.lower() == req.subject_code.lower() or cand_subj.lower() == req.subject_name.lower():
            continue

        # ── Strategy A: Move the candidate class to another free slot ────
        cand_duration = to_minutes(cand_end) - to_minutes(cand_start) if cand_start and cand_end else 120
        # Check their busy days quickly
        c_busy_records = db.query(TeacherBusySlot).filter(TeacherBusySlot.teacher_short_name == cand_teacher).all()
        c_busy_days = {b.day_of_week for b in c_busy_records if b.scope == "permanent" and b.day_of_week}
        
        cand_free_slots = suggest_free_slots_for_teacher(schedule, cand_teacher, cand_duration, busy_days=c_busy_days)

        moved = False
        for move_slot in cand_free_slots:
            if move_slot["day"] == cand_day and move_slot["start"] == cand_start:
                continue

            moved_schedule = _simulate_move(
                schedule, candidate,
                move_slot["day"], move_slot["start"], move_slot["end"],
            )
            if _count_new_violations(schedule, moved_schedule) > 0:
                continue

            freed_slot = _find_best_slot_for_new_class(
                moved_schedule, req.program, req.semester,
                req.subject_code, req.subject_name,
                req.teacher, req.entry_type, req.room, busy_days,
                preferred_day=req.preferred_day,
            )
            
            # Prevent proposing the exact same end-result as "no modification needed"
            if freed_slot and normal_slot and freed_slot["day"] == normal_slot["day"] and freed_slot["start"] == normal_slot["start"]:
                continue
                
            if not freed_slot:
                continue
                
            # Prevent proposing a move if the freed_slot was ALREADY perfectly valid in the original schedule.
            trial = {
                "day": freed_slot["day"], "start": freed_slot["start"], "end": freed_slot["end"],
                "program": req.program, "semester": req.semester,
                "subject_code": req.subject_code, "subject_name": req.subject_name,
                "teacher": req.teacher, "type": req.entry_type, "room": freed_slot["room"],
            }
            if _count_new_violations(schedule, schedule + [trial]) == 0:
                continue

            move_rooms = suggest_free_rooms(moved_schedule, move_slot["day"], move_slot["start"], move_slot["end"])
            move_room = candidate.get("room", "") or (move_rooms[0] if move_rooms else "")

            proposals.append(ProposedModification(
                description=(
                    f"Move '{cand_subj}' ({cand_day} {cand_start}–{cand_end}) → "
                    f"{move_slot['day']} {move_slot['start']}–{move_slot['end']}, "
                    f"then place '{req.subject_name}' on "
                    f"{freed_slot['day']} {freed_slot['start']}–{freed_slot['end']} in {freed_slot['room']}."
                ),
                modification_type="move",
                affected_class={
                    "subject": cand_subj,
                    "day": cand_day, "start": cand_start, "end": cand_end,
                    "teacher": cand_teacher,
                    "program": candidate.get("program"), "semester": candidate.get("semester"),
                },
                new_slot={"day": move_slot["day"], "start": move_slot["start"], "end": move_slot["end"], "room": move_room},
                priority_slot=freed_slot,
                actions_to_apply=[
                    {
                        "action": "MOVE_CLASS",
                        "target": {
                            "day": cand_day, "start_time": cand_start, "end_time": cand_end,
                            "program": candidate.get("program"), "semester": candidate.get("semester"),
                            "subject_code": candidate.get("subject_code") or candidate.get("subject"),
                        },
                        "new_day": move_slot["day"],
                        "new_start_time": move_slot["start"],
                        "new_end_time": move_slot["end"],
                        **({"new_room": move_room} if move_room else {}),
                    },
                    {
                        "action": "ADD_CLASS",
                        "spec": {
                            "program": req.program, "semester": req.semester,
                            "day": freed_slot["day"],
                            "start_time": freed_slot["start"], "end_time": freed_slot["end"],
                            "subject_code": req.subject_code, "subject_name": req.subject_name,
                            "teacher": req.teacher, "entry_type": req.entry_type,
                            "room": freed_slot["room"],
                        },
                    },
                ],
            ))
            moved = True
            break

        if moved or len(proposals) >= 5:
            continue

        # ── Strategy B: Cancel the candidate class (last resort) ─────────
        cancelled_schedule = _simulate_remove(schedule, candidate)
        if _count_new_violations(schedule, cancelled_schedule) > 0:
            continue

        freed_slot = _find_best_slot_for_new_class(
            cancelled_schedule, req.program, req.semester,
            req.subject_code, req.subject_name,
            req.teacher, req.entry_type, req.room, busy_days,
            preferred_day=req.preferred_day,
        )
        if not freed_slot:
            continue
            
        trial_cancel = {
            "day": freed_slot["day"], "start": freed_slot["start"], "end": freed_slot["end"],
            "program": req.program, "semester": req.semester,
            "subject_code": req.subject_code, "subject_name": req.subject_name,
            "teacher": req.teacher, "type": req.entry_type, "room": freed_slot["room"],
        }
        if _count_new_violations(schedule, schedule + [trial_cancel]) == 0:
            continue

        proposals.append(ProposedModification(
            description=(
                f"Cancel '{cand_subj}' ({cand_day} {cand_start}–{cand_end}), "
                f"then place '{req.subject_name}' on "
                f"{freed_slot['day']} {freed_slot['start']}–{freed_slot['end']} in {freed_slot['room']}."
            ),
            modification_type="cancel",
            affected_class={
                "subject": cand_subj,
                "day": cand_day, "start": cand_start, "end": cand_end,
                "teacher": cand_teacher,
                "program": candidate.get("program"), "semester": candidate.get("semester"),
            },
            new_slot=None,
            priority_slot=freed_slot,
            actions_to_apply=[
                {
                    "action": "REMOVE_CLASS",
                    "target": {
                        "day": cand_day, "start_time": cand_start, "end_time": cand_end,
                        "program": candidate.get("program"), "semester": candidate.get("semester"),
                        "subject_code": candidate.get("subject_code") or candidate.get("subject"),
                    },
                },
                {
                    "action": "ADD_CLASS",
                    "spec": {
                        "program": req.program, "semester": req.semester,
                        "day": freed_slot["day"],
                        "start_time": freed_slot["start"], "end_time": freed_slot["end"],
                        "subject_code": req.subject_code, "subject_name": req.subject_name,
                        "teacher": req.teacher, "entry_type": req.entry_type,
                        "room": freed_slot["room"],
                    },
                },
            ],
        ))

    if not proposals:
        return AdvancedSmartScheduleResponse(
            found=False,
            message=(
                f"Could not find any valid modifications within "
                f"{req.program} {req.semester} to accommodate '{req.subject_name}'. "
            ),
        )

    return AdvancedSmartScheduleResponse(
        found=True,
        proposals=proposals,
        message=f"Found {len(proposals)} proposal(s).",
    )
