"""Service tying together LLM generation, validation, and persistence."""
from __future__ import annotations
import json
from sqlalchemy.orm import Session
from loguru import logger

from app.models.user import User
from app.schemas.timetable import GenerateRequest
from app.scheduler.prompts import build_user_content, SYSTEM_PROMPT
from app.scheduler.llm import call_groq, extract_json
from app.scheduler.validator import validate_schedule, validate_schema, regenerate_tables_from_schedule
from app.services.timetable_service import create_draft
from app.services.audit_service import log as audit_log


def generate_and_save(req: GenerateRequest, user: User, db: Session) -> dict:
    """End-to-end generation, exactly matching the old endpoint logic."""
    user_content = build_user_content(
        extra_notes=req.extra_notes,
        subject_teacher_allocation=req.subject_teacher_allocation,
        teacher_preferences=req.teacher_preferences,
        room_information=req.room_information,
    )
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]
    
    # Pass 1
    raw, used_model = call_groq(messages)
    data = extract_json(raw)
    
    schedule = data.get("schedule", [])
    if not isinstance(schedule, list):
        schedule = []
    
    schema_errors = validate_schema(schedule)
    violations = validate_schedule(schedule)
    
    # Self-correction loop
    attempts = 0
    RETRYABLE = {"H1_semester_clash", "H2_teacher_clash", "H3_room_clash",
                 "H4_room_not_lab", "H5_multiple_theory_same_day", "H9_wrong_practical_duration",
                 "start_not_before_end", "bad_time_format", "H7_unallocated_subject", "H11_wrong_semester_subject"}
    
    retryable = [v for v in violations if v.get("rule") in RETRYABLE]
    
    while retryable and attempts < 2:
        fix_msg = (
            "An automated checker found these hard-constraint violations in your schedule: "
            + json.dumps(retryable)[:6000]
            + ". Return the complete corrected JSON object again, same schema, with every violation resolved and nothing else changed unless necessary. Respond with only the JSON object."
        )
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": fix_msg})
        try:
            raw, used_model = call_groq(messages)
            data = extract_json(raw)
            schedule = data.get("schedule", [])
            if not isinstance(schedule, list): schedule = []
            schema_errors = validate_schema(schedule)
            violations = validate_schedule(schedule)
            retryable = [v for v in violations if v.get("rule") in RETRYABLE]
        except Exception:
            break
        attempts += 1

    # Regenerate MD tables safely
    regen = regenerate_tables_from_schedule(schedule)
    for k, v in regen.items():
        data[k] = v
        
    data["schedule"] = schedule
    data["validator_violations"] = violations
    data["schema_errors"] = schema_errors
    data["validator_attempts"] = attempts
    data["model_used"] = used_model
    
    if not schedule:
        data["warning"] = "Model returned no schedule. Check assumptions/validation."
    elif schema_errors:
        data["warning"] = f"{len(schema_errors)} schema errors."

    # Save cleanly as DRAFT version
    version = create_draft(
        db=db,
        user=user,
        entries=schedule,
        change_summary=req.change_summary,
        llm_output=data
    )
    
    # Audit log the generation request
    audit_log(
        db,
        action="GENERATE_TIMETABLE",
        user_id=user.id,
        version_id=version.id,
        params=req.model_dump(),
        after=schedule
    )
    
    data["version_id"] = version.id
    return data
