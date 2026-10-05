"""
NLP → Action List parser.

Converts free-text teacher commands into a list of ParsedAction objects
using an LLM, then validates/grounds the output against authoritative
data (TEACHER_SUBJECTS, ROOM_FACILITIES, PROGRAMME_SUBJECT_MAP).

LLM provides intent. Backend validation is the final authority.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from loguru import logger

from app.actions.types import ParsedAction, parse_action
from app.scheduler.llm import call_groq
from app.scheduler.constraints import (
    ROOM_FACILITIES, PROGRAMME_SUBJECT_MAP,
    VALID_DAYS, VALID_PROGRAMS, INTERNAL_TEACHERS
)

# ---------------------------------------------------------------------------
# Parser system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an intelligent timetable assistant. Your ONLY job is to parse a natural language command into a JSON array of action objects.

=== CRITICAL RULES (READ EVERY ONE) ===

RULE 1 — ALWAYS RETURN A JSON ARRAY.
Multiple requested changes MUST produce multiple objects in the same array. Never stop at one action if the request implies more.

RULE 2 — INFER ALL IMPLIED ACTIONS.
When the user says something like:
  "Add subject X, professor is Y, schedule classes on Tuesday and Friday"
You MUST emit ALL of the following:
  • ADD_SUBJECT (to register the subject in the catalog)
  • ADD_TEACHER (if the teacher sounds like a new person not in the system)
  • ADD_CLASS once for EACH day mentioned

RULE 3 — USE REASONABLE DEFAULTS (never leave required fields blank).
  • No time given for a class → use 10:00-11:00 for Theory, 10:00-12:00 for Practical.
  • No room given → use R#205 for Theory, R#207A for Practical/Lab.
  • No entry_type → default to "Theory".
  • No program for ADD_SUBJECT → use "B.Tech" and ask for semester via CLARIFY.

RULE 4 — AMBIGUITY: emit CLARIFY only when critical fields are truly unknown.
Critical fields: program + semester when scheduling/adding classes.
Optional fields (room, time, duration): use defaults, do NOT ask.
If you must ask, emit ONE object: {"action": "CLARIFY", "question": "...your question..."}

RULE 5 — ORDER OF ACTIONS.
Catalog actions first (ADD_SUBJECT, ADD_TEACHER), then schedule actions (ADD_CLASS etc.).

=== MULTI-ACTION EXAMPLE ===
User: "Add System Design as new subject code sd, professor Rahul Das (rd), schedule classes tuesday and friday for btech 3rd semester"

Correct response:
[
  {"action": "ADD_SUBJECT", "code": "sd", "name": "System Design", "program": "B.Tech", "semester": "3rd", "entry_type": "Theory", "weekly_hours": 2},
  {"action": "ADD_TEACHER", "short_name": "rd", "full_name": "Rahul Das", "subjects_csv": "sd", "is_internal": true},
  {"action": "ADD_CLASS", "spec": {"program": "B.Tech", "semester": "3rd", "day": "Tuesday", "start_time": "10:00", "end_time": "11:00", "subject_code": "sd", "subject_name": "System Design", "teacher": "rd", "entry_type": "Theory", "room": "R#205"}},
  {"action": "ADD_CLASS", "spec": {"program": "B.Tech", "semester": "3rd", "day": "Friday", "start_time": "10:00", "end_time": "11:00", "subject_code": "sd", "subject_name": "System Design", "teacher": "rd", "entry_type": "Theory", "room": "R#205"}}
]

=== CLARIFY EXAMPLE (semester unknown) ===
User: "Add System Design as new subject, professor Rahul Das, schedule on tuesday and friday"

Correct response:
[
  {"action": "CLARIFY", "question": "Which program and semester should the System Design classes be scheduled for? e.g. B.Tech 3rd semester"}
]

=== ACTION TYPES ===
  ADD_CLASS           - add a new class slot
  REMOVE_CLASS        - permanently remove a class slot
  CANCEL_CLASS        - cancel a class slot
  EXTEND_CLASS        - extend end time of a class
  SHORTEN_CLASS       - shorten end time of a class
  MOVE_CLASS          - move to a different day/time
  SWAP_CLASSES        - swap day+time between two classes
  INTERCHANGE_CLASSES - fully swap all fields between two classes
  CHANGE_TEACHER      - replace teacher on a class
  CHANGE_ROOM         - replace room on a class
  CHANGE_TIME         - change start+end on a class
  CHANGE_DAY          - change day on a class
  REPLACE_CLASS       - replace one class entirely with a new spec
  GENERATE_TIMETABLE  - generate new timetable from scratch
  OPTIMIZE_TIMETABLE  - optimise existing timetable
  VALIDATE_TIMETABLE  - run validation on current timetable
  RESTORE_VERSION     - restore a specific previous version
  ADD_TEACHER         - fields: short_name, full_name, subjects_csv, is_internal (bool), email (optional)
  ADD_SUBJECT         - fields: code, name, program, semester, entry_type ("Theory"|"Practical"|"Both"), weekly_hours
  ADD_PROGRAM         - fields: name, semesters_count, description
  ADD_TEACHER_BUSY    - fields: teacher_short_name, scope ("permanent"|"temporary"), day_of_week (if permanent), specific_date "YYYY-MM-DD" (if temporary), reason
  CLARIFY             - fields: question (ask user exactly what you need)

ClassTarget fields: program, semester, day, start_time, end_time, subject_code, subject_name, teacher, room, entry_type
ClassSpec fields (ALL required for ADD_CLASS): program, semester, day, start_time, end_time, subject_code, subject_name, teacher, entry_type, room

Time: "HH:MM" 24h. Days: Monday..Saturday. Programs: B.Tech, M.Tech, M.Sc.
Valid rooms: R#205, R#207A, R#207B, R#208, R#209, R#303, R#403.
Use lowercase subject codes: "cn", "dbms-p", "sd".

Return ONLY a valid JSON array. No prose, no markdown, no explanation.
"""


# ---------------------------------------------------------------------------
# Clarification response
# ---------------------------------------------------------------------------

class ClarifyNeeded(Exception):
    """Raised when the LLM signals it needs more info from the user before acting."""
    def __init__(self, question: str):
        self.question = question
        super().__init__(question)


# ---------------------------------------------------------------------------
# Entity grounding helpers
# ---------------------------------------------------------------------------

def _ground_teacher(name: str) -> str:
    """Fuzzy-ground a teacher identifier against the authoritative short-name set."""
    if name in INTERNAL_TEACHERS:
        return name
    name_l = name.strip().lower()
    for canonical in INTERNAL_TEACHERS:
        if canonical.lower() == name_l:
            return canonical
    return name  # return as-is for external faculty; engine validator will catch issues


def _ground_subject_code(code: str) -> str:
    """Lowercase the code to match canonical set."""
    return code.strip().lower()


def _ground_action_dict(d: dict) -> dict:
    """Recursively ground known string fields in an action dict."""
    grounded = {}
    for k, v in d.items():
        if isinstance(v, dict):
            grounded[k] = _ground_action_dict(v)
        elif isinstance(v, str):
            if k == "teacher" or k == "new_teacher":
                grounded[k] = _ground_teacher(v)
            elif k in ("subject_code",):
                grounded[k] = _ground_subject_code(v)
            elif k == "room" or k == "new_room":
                grounded[k] = v
            elif k in ("day", "new_day") and v:
                grounded[k] = v.strip().title()
            elif k == "program" and v:
                mapping = {"btech": "B.Tech", "mtech": "M.Tech", "msc": "M.Sc",
                           "b.tech": "B.Tech", "m.tech": "M.Tech", "m.sc": "M.Sc"}
                grounded[k] = mapping.get(v.lower(), v)
            else:
                grounded[k] = v
        else:
            grounded[k] = v
    return grounded


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class ActionParser:
    """Convert natural-language text → list[ParsedAction]."""

    MAX_REPAIR_ATTEMPTS = 1

    def parse(self, text: str, schedule_context: str = "") -> List[ParsedAction]:
        """
        Parse 'text' into a list of ParsedAction objects.
        Raises ClarifyNeeded if the LLM requests clarification.
        Raises ValueError if parsing or validation fails after self-repair.
        """
        system_with_context = SYSTEM_PROMPT
        if schedule_context:
            system_with_context = (
                SYSTEM_PROMPT
                + "\n\nCURRENT LIVE SCHEDULE (use this to resolve targets precisely and pick non-conflicting time slots):\n"
                + schedule_context
                + "\n\nIMPORTANT: When identifying a class to modify, use the exact day, "
                "start_time, end_time, subject_code, and teacher from the schedule above "
                "to fill target fields accurately. Never guess.\n"
            )

        messages = [
            {"role": "system", "content": system_with_context},
            {"role": "user",   "content": text},
        ]

        raw, model = call_groq(messages)
        logger.debug(f"ActionParser LLM ({model}) raw: {raw[:500]}")

        actions = self._parse_and_validate(raw)
        if actions is not None:
            return actions

        # Self-repair attempt
        repair_prompt = (
            f"Your previous response could not be parsed as a JSON array of action objects.\n"
            f"Response was: {raw[:800]}\n"
            f"Please return ONLY a valid JSON array conforming to the action schema. "
            f"No prose, no markdown fences."
        )
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user",      "content": repair_prompt})
        raw2, _ = call_groq(messages)
        logger.debug(f"ActionParser repair raw: {raw2[:500]}")

        actions = self._parse_and_validate(raw2)
        if actions is not None:
            return actions

        raise ValueError(
            f"Failed to parse NLP command into valid actions after {self.MAX_REPAIR_ATTEMPTS + 1} "
            f"attempts. Last LLM output: {raw2[:400]}"
        )

    def _parse_and_validate(self, raw: str) -> Optional[List[ParsedAction]]:
        """Try to parse raw text into a list of ParsedAction. Returns None on failure.
        Raises ClarifyNeeded if the LLM returned a CLARIFY action."""
        try:
            data = self._extract_json_array(raw)
        except Exception as e:
            logger.warning(f"ActionParser JSON extraction failed: {e}")
            return None

        if not isinstance(data, list):
            return None

        # Check for CLARIFY action first
        for item in data:
            if isinstance(item, dict) and item.get("action") == "CLARIFY":
                raise ClarifyNeeded(item.get("question", "Please provide more details."))

        parsed: List[ParsedAction] = []
        for item in data:
            if not isinstance(item, dict):
                return None
            try:
                grounded = _ground_action_dict(item)
                action = parse_action(grounded)
                parsed.append(action)
            except Exception as e:
                logger.warning(f"ActionParser Pydantic validation failed for item {item}: {e}")
                return None

        return parsed if parsed else None

    @staticmethod
    def _extract_json_array(text: str) -> list:
        """Extract a JSON array from raw LLM text."""
        t = text.strip()
        # Strip markdown fences
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t).strip()
        if t.endswith("```"):
            t = t[:-3].strip()

        # Try array first: find [ ... ]
        start = t.find("[")
        obj_start = t.find("{")

        if start != -1 and (obj_start == -1 or start < obj_start):
            end = t.rfind("]")
            if end > start:
                candidate = t[start : end + 1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, list):
                        return result
                except json.JSONDecodeError:
                    candidate2 = re.sub(r",\s*([}\]])", r"\1", candidate)
                    result = json.loads(candidate2)
                    if isinstance(result, list):
                        return result

        # Fall back: try to parse as a single object and wrap in list
        if obj_start != -1:
            end = t.rfind("}")
            if end > obj_start:
                candidate = t[obj_start : end + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return [obj]
                except json.JSONDecodeError:
                    candidate2 = re.sub(r",\s*([}\]])", r"\1", candidate)
                    obj = json.loads(candidate2)
                    if isinstance(obj, dict):
                        return [obj]

        raise ValueError("No JSON array or object found in LLM output")
