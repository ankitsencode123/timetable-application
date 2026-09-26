"""
Chat service — public chat queries the published timetable using LLM.
Teacher chat delegates to the ActionParser with live schedule context.
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy.orm import Session

from app.models.user import User


def _build_schedule_context(db: Session, version_id: int | None = None) -> str:
    """Build a compact schedule context string for the LLM."""
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


def handle_public_chat(message: str, db: Session | None = None) -> dict:
    """Read-only factual/conversational chat for unauthenticated users.
    
    Queries the published timetable and uses Groq to answer questions.
    Falls back to a structured text search if LLM is unavailable.
    """
    try:
        from app.scheduler.data import (
            EXISTING_TIMETABLE_MD, FACULTY_MASTER_MD, ROOM_EXAMPLE_MD
        )
        from app.scheduler.llm import call_groq

        # Also fetch live DB entries if available
        live_context = ""
        if db:
            try:
                from app.services.timetable_service import get_published_version
                pub = get_published_version(db)
                if pub and pub.entries:
                    rows = []
                    for e in pub.entries:
                        rows.append(
                            f"| {e.day} | {e.program} | {e.semester} | "
                            f"{e.start_time}-{e.end_time} | {e.teacher}({e.subject_name}):{e.room} |"
                        )
                    live_context = (
                        "LIVE PUBLISHED TIMETABLE:\n"
                        "| Day | Program | Semester | Time | Subject/Teacher/Room |\n"
                        "|---|---|---|---|---|\n"
                        + "\n".join(rows)
                    )
            except Exception:
                pass

        timetable_context = live_context or EXISTING_TIMETABLE_MD

        system_prompt = f"""You are a helpful timetable assistant for a university.
Answer ONLY from the timetable data provided below. Be concise and precise.
If you cannot find the information, say so clearly.

TIMETABLE:
{timetable_context}

FACULTY:
{FACULTY_MASTER_MD}

ROOMS:
{ROOM_EXAMPLE_MD}

Rules:
- Only answer questions about the timetable (schedules, rooms, teachers, subjects, programs, semesters, days, times).
- Do NOT make up any information not in the data.
- When referencing teacher short names, use the faculty master to resolve full names if helpful.
- Format answers clearly, use bullet points or tables when listing multiple classes.
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        content, _ = call_groq(messages, expect_json=False)

        return {
            "message": content.strip(),
            "schedule_updated": False,
            "response_data": {},
            "parsed_actions": [],
        }

    except Exception as e:
        logger.warning(f"Public chat LLM error: {e}")
        return {
            "message": (
                "I couldn't answer that right now. "
                "Please try again, or check the timetable grid directly."
            ),
            "schedule_updated": False,
            "response_data": {},
            "parsed_actions": [],
        }


def handle_teacher_chat(user: User, message: str, db: Session | None = None) -> dict:
    """
    Authenticated chat for teachers.
    Attempts to parse the message into structured actions via ActionParser.
    Passes the live schedule context so the LLM correctly identifies targets.
    Returns parsed actions so the UI can show an interpret-then-confirm flow.
    Actions are NOT auto-executed here — the user must confirm via /actions/execute.
    """
    try:
        from app.actions.parser import ActionParser
        parser = ActionParser()

        # Build schedule context for grounded target resolution
        schedule_ctx = _build_schedule_context(db) if db else ""

        parsed = parser.parse(message, schedule_context=schedule_ctx)
        actions_out = [a.model_dump() for a in parsed]

        # Build rich interpretation listing action details
        interpretation_lines = []
        for i, a in enumerate(parsed, 1):
            action_name = str(a.action).replace("_", " ").title()
            target = getattr(a, "target", None)
            detail = ""
            if target:
                t = target.model_dump(exclude_none=True)
                parts = [
                    t.get("day", ""), t.get("start_time", ""), f"[{t.get('subject_code', '')}]",
                    t.get("teacher", ""), t.get("program", ""),
                ]
                detail = " ".join(p for p in parts if p)
            interpretation_lines.append(
                f"  {i}. {action_name}: {detail}" if detail else f"  {i}. {action_name}"
            )

        interpretation = "\n".join(interpretation_lines)
        return {
            "message": (
                f"I understood {len(parsed)} action(s):\n{interpretation}\n\n"
                f"Review and confirm to apply these changes."
            ),
            "schedule_updated": False,
            "response_data": {},
            "parsed_actions": actions_out,
        }
    except Exception as e:
        logger.warning(f"Teacher chat parse error: {e}")
        return {
            "message": (
                f"Sorry, I couldn't process that: {e}\n\n"
                f"Try rephrasing, or use the action buttons for precise control."
            ),
            "schedule_updated": False,
            "response_data": {},
            "parsed_actions": [],
        }
