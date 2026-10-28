"""
Chat service — public chat queries the published timetable using LLM.
Teacher chat delegates to the ActionParser with live schedule context.
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy.orm import Session

from app.models.user import User

from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    tz = pytz.timezone("Asia/Kolkata")

def _build_schedule_context(db: Session, version_id: int | None = None) -> str:
    """Build a compact schedule context string for the LLM using the cache layer."""
    try:
        from app.services.cache_layer import build_schedule_context_cached
        return build_schedule_context_cached(db, version_id)
    except Exception:
        pass
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


def _parse_timetable_entries(timetable_text: str) -> list[dict]:
    entries = []
    lines = [l.strip() for l in timetable_text.splitlines() if l.strip().startswith('|') and not l.strip().startswith('|---') and 'Program' not in l]
    for line in lines:
        parts = [p.strip() for p in line.split('|')[1:-1]]
        if len(parts) >= 10:
            day, prog, sem, start, end, code, subj, teacher, room, etype = parts[:10]
            entries.append({
                "day": day, "program": prog, "semester": sem, "time": f"{start}-{end}",
                "teacher": teacher, "subject": subj or code, "is_practical": etype.lower() == "practical" or "-p" in (subj + code).lower()
            })
        elif len(parts) >= 5:
            day, prog, sem, time_slot, sfr = parts[0], parts[1], parts[2], parts[3], parts[4]
            teacher_subj = sfr.split(':')[0]
            if '(' in teacher_subj:
                t = teacher_subj[:teacher_subj.find('(')].strip()
                subj = teacher_subj[teacher_subj.find('(')+1:teacher_subj.rfind(')')].strip()
            else:
                t = teacher_subj
                subj = ''
            is_practical = '-P' in subj or ':P' in subj or 'practical' in subj.lower()
            entries.append({
                "day": day, "program": prog, "semester": sem, "time": time_slot,
                "teacher": t, "subject": subj, "is_practical": is_practical
            })
    return entries


def _check_teachers_more_than_one_theory(timetable_text: str) -> str:
    entries = _parse_timetable_entries(timetable_text)
    days_data: dict[str, dict[str, list[dict]]] = {}
    for e in entries:
        if not e["is_practical"] and e["teacher"]:
            days_data.setdefault(e["day"], {}).setdefault(e["teacher"], []).append(e)

    violations = []
    day_order = {"Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4, "Friday": 5, "Saturday": 6}
    sorted_days = sorted(days_data.keys(), key=lambda d: day_order.get(d, 99))
    for day in sorted_days:
        for teacher in sorted(days_data[day].keys()):
            classes = days_data[day][teacher]
            if len(classes) > 1:
                violations.append((day, teacher, classes))

    if not violations:
        return "I checked the timetable: **No teacher** takes more than one theory class in a single day."

    out = ["Yes, here are the teachers who take more than one theory class in a single day:\n"]
    for day, teacher, classes in violations:
        out.append(f"• **{teacher}** on **{day}** ({len(classes)} theory classes):")
        for c in classes:
            out.append(f"   - {c['subject']} ({c['time']}, {c['program']} {c['semester']})")
    out.append("\n_Note: Under Rule H5, each teacher is supposed to take at most 1 theory class per day across all programmes._")
    return "\n".join(out)


def _check_internal_teacher_free_days(timetable_text: str, internal_teachers: set[str]) -> str:
    entries = _parse_timetable_entries(timetable_text)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    teacher_days: dict[str, set[str]] = {t: set() for t in internal_teachers}
    for e in entries:
        t = e["teacher"]
        if t in teacher_days and e["day"] in weekdays:
            teacher_days[t].add(e["day"])

    out = ["**Free Day Status for Internal Teachers (Mon-Fri):**\n"]
    for t in sorted(internal_teachers):
        busy = teacher_days.get(t, set())
        free = [d for d in weekdays if d not in busy]
        if free:
            out.append(f"• **{t}**: Free on {', '.join(free)}")
        else:
            out.append(f"• **{t}**: ⚠️ No free teaching day (teaches on all 5 days)")
    return "\n".join(out)


def handle_public_chat(message: str, db: Session | None = None) -> dict:
    """Read-only factual/conversational chat for unauthenticated users.
    
    Queries the published timetable and uses Groq to answer questions.
    Falls back to a structured text search if LLM is unavailable.
    """
    try:
        from app.calendar_chat import try_handle_calendar_question
        r = try_handle_calendar_question(message, db)
        if r is not None:
            return r

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

        from app.scheduler.constraints import INTERNAL_TEACHERS
        internal_teachers_str = ", ".join(sorted(INTERNAL_TEACHERS))

        timetable_context = live_context or EXISTING_TIMETABLE_MD
        
        current_date = datetime.now(tz).strftime('%A, %B %d, %Y')

        system_prompt = f"""You are a helpful timetable assistant for a university.
Answer ONLY from the timetable data provided below. Be concise, precise, and analytical.
If you cannot find the information, say so clearly.

CURRENT DATE:
Today is {current_date}. Use this to understand questions about "today" or "tomorrow".

TIMETABLE:
{timetable_context}

FACULTY:
{FACULTY_MASTER_MD}
Note: The following short names correspond to INTERNAL teachers: {internal_teachers_str}. Any others are visiting/external.

ROOMS:
{ROOM_EXAMPLE_MD}

Timetable Structure & Rules:
- Theory vs Practical:
  * Practical sessions have '-P' or ':P' in the subject code or name (e.g. DL-P, AIML-P, AA-P, CG-P, ADBMS-P, CN-P, AGT-P, SC-P) or are explicitly labeled Practical.
  * All other classes are Theory classes.
- Rule H5 (Max 1 theory class per day per teacher):
  * Teachers are supposed to take at most 1 theory class per day across all programmes.
  * When asked if any teacher takes more than one theory class in a single day, check every day and every teacher across all programmes.
  * List all teacher(s) who take more than 1 theory class on any day, stating the Day, Teacher, Theory subjects, times, and programs. If no teacher takes >1 theory class per day, state that clearly.
- Rule H6 (One free day for internal teachers):
  * Internal teachers must have at least one teaching day (Mon-Fri) with no classes scheduled.
- General Rules:
  * Only answer questions about the timetable (schedules, rooms, teachers, subjects, programs, semesters, days, times, rules).
  * Do NOT make up any information not in the data.
  * When referencing teacher short names, use the faculty master to resolve full names if helpful.
  * Format answers clearly, use bullet points or tables when listing classes.
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        try:
            content, _ = call_groq(messages, expect_json=False)
            return {
                "message": content.strip(),
                "schedule_updated": False,
                "response_data": {},
                "parsed_actions": [],
            }
        except Exception as llm_err:
            logger.warning(f"Public chat LLM call failed: {llm_err}")
            # Factual fallback for analytical checks
            lowered = message.lower()
            if any(k in lowered for k in ["theory class", "more than one", "single day", "more than 1", "theory"]):
                fb = _check_teachers_more_than_one_theory(timetable_context)
                if fb:
                    return {
                        "message": fb,
                        "schedule_updated": False,
                        "response_data": {},
                        "parsed_actions": [],
                    }
            if any(k in lowered for k in ["free day", "one free day", "day off"]):
                fb = _check_internal_teacher_free_days(timetable_context, INTERNAL_TEACHERS)
                if fb:
                    return {
                        "message": fb,
                        "schedule_updated": False,
                        "response_data": {},
                        "parsed_actions": [],
                    }
            raise llm_err

    except Exception as e:
        logger.warning(f"Public chat error: {e}")
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
        from app.calendar_chat import try_handle_calendar_question
        r = try_handle_calendar_question(message, db, user=user)
        if r is not None:
            return r

        from app.actions.parser import ActionParser
        parser = ActionParser()

        # Build schedule context for grounded target resolution
        schedule_ctx = _build_schedule_context(db) if db else ""

        parsed = parser.parse(message, schedule_context=schedule_ctx)
        actions_out = [a.model_dump() for a in parsed]

        # Build rich interpretation listing action details with conversational descriptions
        interpretation_lines = []
        for i, a in enumerate(parsed, 1):
            action_name = str(a.action).replace("_", " ").title()
            target = getattr(a, "target", None)
            detail_parts = []
            if target:
                t = target.model_dump(exclude_none=True)
                subj = t.get("subject_name") or t.get("subject_code", "")
                if subj:
                    detail_parts.append(f"**{subj}**")
                if t.get("day"):
                    if t.get("start_time"):
                        detail_parts.append(f"{t['day']} {t['start_time']}\u2013{t.get('end_time', '?')}")
                    else:
                        detail_parts.append(t["day"])
                if t.get("teacher"):
                    detail_parts.append(f"teacher: {t['teacher']}")
                if t.get("program"):
                    detail_parts.append(f"{t['program']} {t.get('semester', '')}".strip())

            new_day = getattr(a, "new_day", None)
            new_start = getattr(a, "new_start_time", None)
            new_end = getattr(a, "new_end_time", None)
            new_teacher = getattr(a, "new_teacher", None)
            new_room = getattr(a, "new_room", None)
            dest_parts = []
            if new_day:
                dest_parts.append(new_day)
            if new_start:
                dest_parts.append(f"{new_start}\u2013{new_end or '?'}")
            if new_teacher:
                dest_parts.append(f"teacher \u2192 {new_teacher}")
            if new_room:
                dest_parts.append(f"room \u2192 {new_room}")

            spec = getattr(a, "spec", None)
            if spec:
                s = spec.model_dump(exclude_none=True)
                subj = s.get("subject_name") or s.get("subject_code", "")
                if subj:
                    detail_parts.append(f"**{subj}**")
                if s.get("program"):
                    detail_parts.append(f"{s['program']} {s.get('semester', '')}".strip())
                if s.get("day"):
                    ts = f"{s['day']} {s.get('start_time', '')}\u2013{s.get('end_time', '')}".strip()
                    detail_parts.append(ts)
                if s.get("teacher"):
                    detail_parts.append(f"teacher: {s['teacher']}")

            detail = ", ".join(p for p in detail_parts if p)
            dest = " \u2192 " + ", ".join(p for p in dest_parts if p) if dest_parts else ""
            interpretation_lines.append(
                f"  {i}. **{action_name}**: {detail}{dest}" if detail else f"  {i}. **{action_name}**"
            )

        count = len(parsed)
        if count == 0:
            intro = "I couldn't identify any specific actions. Please try rephrasing."
        elif count == 1:
            intro = "I found **1 action** in your request:"
        else:
            intro = f"I found **{count} actions** in your request:"

        interpretation = intro + "\n" + "\n".join(interpretation_lines)
        return {
            "message": (
                f"{interpretation}\n\n"
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
