"""Actions API — unified endpoint for button and NLP-driven timetable mutations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_teacher_or_admin
from app.models.user import User
from app.schemas.actions import (
    ActionExecuteRequest, ActionExecuteResponse, ActionResultSchema,
    ActionParseRequest, ActionParseResponse,
    ActionChatRequest, ActionChatResponse,
)
from app.actions.engine import ActionEngine
from app.actions.parser import ActionParser
from app.actions.types import parse_action
from app.services.email_service import notify_teachers_of_changes

router = APIRouter()
_parser = ActionParser()


# ---------------------------------------------------------------------------
# Helper: build live schedule context string for LLM
# ---------------------------------------------------------------------------

def _build_schedule_context(db: Session, version_id: int | None) -> str:
    """Build a compact schedule context string from the current draft version."""
    try:
        from app.models.timetable import TimetableVersion
        from app.services import timetable_service
        if version_id:
            version = timetable_service.get_version(db, version_id)
        else:
            version = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
        if not version or not version.entries:
            return ""
        rows = []
        for e in version.entries:
            rows.append(
                f"| {e.day} | {e.program} | {e.semester} | "
                f"{e.start_time} | {e.end_time} | {e.subject_code} | "
                f"{e.subject_name} | {e.teacher} | {e.room} | {e.entry_type} |"
            )
        header = (
            "| Day | Program | Semester | Start Time | End Time | Code | Subject | Teacher | Room | Type |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n"
        )
        return header + "\n".join(rows)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# POST /actions/parse  — NLP → actions (preview only, no DB write)
# ---------------------------------------------------------------------------

@router.post("/parse", response_model=ActionParseResponse)
def parse_actions(
    req: ActionParseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    """
    Convert free-text command into a list of parsed actions.
    No schedule changes are made — this is a preview / confirmation step.
    """
    schedule_ctx = _build_schedule_context(db, None)
    try:
        parsed = _parser.parse(req.text, schedule_context=schedule_ctx)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"NLP parse failed: {e}")

    actions_out = [a.model_dump() for a in parsed]
    interpretation = _build_interpretation(parsed)
    return ActionParseResponse(
        parsed_actions=actions_out,
        action_count=len(actions_out),
        interpretation=interpretation,
    )


# ---------------------------------------------------------------------------
# POST /actions/execute  — execute pre-parsed or button-submitted actions
# ---------------------------------------------------------------------------

@router.post("/execute", response_model=ActionExecuteResponse)
def execute_actions(
    req: ActionExecuteRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    """
    Execute a list of action dicts transactionally.
    Actions can come from the button UI (structured) or from the /parse preview.
    """
    try:
        parsed = [parse_action(a) for a in req.actions]
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid action payload: {e}")

    engine = ActionEngine(
        db=db,
        user=user,
        version_id=req.version_id,
        partial_ok=req.partial_ok,
        skip_suggestions=req.skip_suggestions,
    )

    result = engine.execute(parsed)

    # Dispath email notification in the background
    if result.success or result.partial_applied:
        background_tasks.add_task(notify_teachers_of_changes, result, db)

    return ActionExecuteResponse(
        success=result.success,
        results=[_action_result_to_schema(r) for r in result.results],
        new_version_id=result.new_version_id,
        violations=result.violations,
        schema_errors=result.schema_errors,
        change_log=result.change_log,
        partial_applied=result.partial_applied,
    )


# ---------------------------------------------------------------------------
# POST /actions/chat  — NLP parse + optional execute in one call
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ActionChatResponse)
def chat_execute(
    req: ActionChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    """
    Combined NLP parse and optional execute.

    - If execute=False (default): returns parsed actions for UI confirmation.
    - If execute=True: parses and immediately executes (trusted/power-user mode).

    The same H1-H11 validation always runs regardless of execute flag.
    """
    # Load live schedule context so LLM can resolve ambiguous targets
    schedule_ctx = _build_schedule_context(db, req.version_id)

    try:
        parsed = _parser.parse(req.text, schedule_context=schedule_ctx)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"NLP parse failed: {e}")

    actions_out = [a.model_dump() for a in parsed]
    interpretation = _build_interpretation(parsed)

    if not req.execute:
        return ActionChatResponse(
            parsed_actions=actions_out,
            action_count=len(actions_out),
            interpretation=interpretation,
            executed=False,
        )

    # Execute
    engine = ActionEngine(
        db=db,
        user=user,
        version_id=req.version_id,
        partial_ok=True, # Allow multiple chat intents to proceed independently
    )
    result = engine.execute(parsed)

    # Dispatch email notification in the background
    if result.success or result.partial_applied:
        background_tasks.add_task(notify_teachers_of_changes, result, db)

    # Build rich execution result
    exec_response = ActionExecuteResponse(
        success=result.success,
        results=[_action_result_to_schema(r) for r in result.results],
        new_version_id=result.new_version_id,
        violations=result.violations,
        schema_errors=result.schema_errors,
        change_log=result.change_log,
        partial_applied=result.partial_applied,
    )

    # Build feedback message
    if result.success:
        per_action_lines = []
        for r in result.results:
            per_action_lines.append(f"  ✓ {r.action_type}")
        execution_summary = (
            f"✓ All {len(result.results)} action(s) applied. New version #{result.new_version_id} created.\n"
            + "\n".join(per_action_lines)
        )
    else:
        per_action_lines = []
        for r in result.results:
            if r.success:
                per_action_lines.append(f"  ✓ {r.action_type}")
            else:
                error_detail = r.error or "Unknown error"
                if r.violated_constraint:
                    vc = r.violated_constraint
                    error_detail += f" [{vc.get('rule', 'constraint')} — {vc.get('message', '')}]"
                per_action_lines.append(f"  ✗ {r.action_type}: {error_detail}")
        success_count = sum(1 for r in result.results if r.success)
        fail_count = len(result.results) - success_count
        execution_summary = (
            f"⚠ {success_count} action(s) applied, {fail_count} failed:\n"
            + "\n".join(per_action_lines)
        )

    return ActionChatResponse(
        parsed_actions=actions_out,
        action_count=len(actions_out),
        interpretation=interpretation + "\n\n" + execution_summary,
        executed=True,
        execution_result=exec_response,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _action_result_to_schema(r) -> ActionResultSchema:
    return ActionResultSchema(
        action_type=str(r.action_type),
        success=r.success,
        change_log=r.change_log or "",
        error=r.error or "",
        violated_constraint=r.violated_constraint,
        suggestions=r.suggestions,
        before=r.before,
        after=r.after,
    )


def _build_interpretation(parsed: list) -> str:
    """Build a rich human-readable description of parsed actions."""
    if not parsed:
        return "No actions parsed."

    count = len(parsed)
    header = (
        f"Interpreted **{count} action{'s' if count > 1 else ''}** in your request:"
    )

    lines = []
    for i, a in enumerate(parsed, 1):
        action_name = str(a.action).replace("_", " ").title()
        detail_parts = []

        # Extract target info
        target = getattr(a, "target", None)
        if target:
            t = target.model_dump(exclude_none=True)
            if "subject_code" in t or "subject_name" in t:
                subj = t.get("subject_name") or t.get("subject_code", "")
                detail_parts.append(f"**{subj}**")
            if "day" in t:
                if "start_time" in t:
                    detail_parts.append(f"{t['day']} {t['start_time']}–{t.get('end_time', '?')}")
                else:
                    detail_parts.append(t["day"])
            if "teacher" in t:
                detail_parts.append(f"teacher: {t['teacher']}")
            if "program" in t:
                detail_parts.append(f"{t['program']} {t.get('semester', '')}")

        # Extract destination info (for MOVE, CHANGE_TIME, etc.)
        new_day = getattr(a, "new_day", None)
        new_start = getattr(a, "new_start_time", None)
        new_end = getattr(a, "new_end_time", None)
        new_teacher = getattr(a, "new_teacher", None)
        new_room = getattr(a, "new_room", None)

        dest_parts = []
        if new_day:
            dest_parts.append(new_day)
        if new_start:
            dest_parts.append(f"{new_start}–{new_end or '?'}")
        if new_teacher:
            dest_parts.append(f"teacher → {new_teacher}")
        if new_room:
            dest_parts.append(f"room → {new_room}")

        # ADD_CLASS / REPLACE_CLASS spec
        spec = getattr(a, "spec", None) or getattr(a, "new_spec", None)
        if spec:
            s = spec.model_dump(exclude_none=True)
            subj = s.get("subject_name") or s.get("subject_code", "")
            if subj:
                detail_parts.append(f"**{subj}**")
            detail_parts += [
                s.get("program", ""), s.get("semester", ""),
                s.get("day", ""),
                f"{s.get('start_time', '')}–{s.get('end_time', '')}" if s.get("start_time") else "",
                f"teacher: {s.get('teacher', '')}" if s.get("teacher") else "",
            ]

        detail = ", ".join(p for p in detail_parts if p)
        dest = " → " + ", ".join(p for p in dest_parts if p) if dest_parts else ""
        lines.append(f"  {i}. **{action_name}**: {detail}{dest}" if detail else f"  {i}. **{action_name}**")

    return header + "\n" + "\n".join(lines)
