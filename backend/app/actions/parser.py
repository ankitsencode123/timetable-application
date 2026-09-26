"""
NLP → Action List parser.

Converts free-text teacher commands into a list of ParsedAction objects
using the Groq LLM, then validates/grounds the output against authoritative
data (TEACHER_SUBJECTS, ROOM_FACILITIES, PROGRAMME_SUBJECT_MAP).

LLM provides intent. Backend validation is the final authority.
"""
from __future__ import annotations

import json
import re
from typing import List

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

_ACTION_SCHEMA = """
Supported action types:
  ADD_CLASS           - add a new class
  REMOVE_CLASS        - permanently remove a class slot
  CANCEL_CLASS        - cancel/remove a class slot
  EXTEND_CLASS        - extend the end time of a class
  SHORTEN_CLASS       - shorten the end time of a class
  MOVE_CLASS          - move to a different day/time
  SWAP_CLASSES        - swap day+time between two classes
  INTERCHANGE_CLASSES - fully interchange all fields between two classes
  CHANGE_TEACHER      - replace teacher on a class
  CHANGE_ROOM         - replace room on a class
  CHANGE_TIME         - change start+end on a class
  CHANGE_DAY          - change day on a class
  REPLACE_CLASS       - replace one class entirely with a new spec
  GENERATE_TIMETABLE  - generate new timetable from scratch
  OPTIMIZE_TIMETABLE  - optimise existing timetable
  VALIDATE_TIMETABLE  - run validation on current timetable
  RESTORE_VERSION     - restore a specific previous version
  ADD_TEACHER         - add a new teacher to the catalog; fields: short_name, full_name, subjects_csv (comma-separated subject codes), is_internal (bool), email (optional)
  ADD_SUBJECT         - add a new subject to the catalog; fields: code, name, program, semester, entry_type ("Theory"|"Practical"|"Both"), weekly_hours
  ADD_PROGRAM         - add a new academic program/course; fields: name, semesters_count, description

ClassTarget fields (use only what is needed to identify the class):
  program, semester, day, start_time, end_time,
  subject_code, subject_name, teacher, room, entry_type

ClassSpec fields (all required for ADD_CLASS / REPLACE_CLASS):
  program, semester, day, start_time, end_time,
  subject_code, subject_name, teacher, entry_type ("Theory"|"Practical"), room

Time format: "HH:MM" (24h).  
Days: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday.  
Programs: B.Tech, M.Tech, M.Sc.

Return a JSON array. Each element is one action object with field "action"
equal to one of the types above, plus the relevant target/spec fields.

Example for "swap Monday and Tuesday classes of M.Tech 1st semester":
[
  {
    "action": "SWAP_CLASSES",
    "target_a": {"program": "M.Tech", "semester": "1st", "day": "Monday"},
    "target_b": {"program": "M.Tech", "semester": "1st", "day": "Tuesday"}
  }
]

IMPORTANT:
- Return ONLY the JSON array. No prose, no markdown fences.
- Use exact canonical short teacher names (e.g. SK, SKS, PB, PBn).
- Use lowercase subject codes (e.g. "cn", "dbms-p", "cn-p").
- Do not invent rooms not in: R#205, R#207A, R#207B, R#208, R#209, R#303, R#403.
"""

SYSTEM_PROMPT = (
    "You are a timetable assistant. "
    "Parse the user's natural language command into a JSON array of action objects. "
    "IF THE USER ENTERS MULTIPLE INSTRUCTIONS, YOU MUST RETURN MULTIPLE ACTION OBJECTS IN THE ARRAY. "
    "Do not miss any requested changes. "
    + _ACTION_SCHEMA
)


# ---------------------------------------------------------------------------
# Entity grounding helpers
# ---------------------------------------------------------------------------

def _ground_teacher(name: str) -> str:
    """Fuzzy-ground a teacher identifier against the authoritative short-name set."""
    if name in INTERNAL_TEACHERS:
        return name
    # Case-insensitive match for known internal teachers
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
                # Just preserve as-is; engine checks ROOM_FACILITIES
                grounded[k] = v
            elif k in ("day", "new_day") and v:
                # Title-case
                grounded[k] = v.strip().title()
            elif k == "program" and v:
                # Normalise program string
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
        Raises ValueError if parsing or validation fails after self-repair.

        schedule_context: optional string with live schedule rows to inject into
        the system prompt so the LLM can ground targets against real data.
        """
        system_with_context = SYSTEM_PROMPT
        if schedule_context:
            system_with_context = (
                SYSTEM_PROMPT
                + "\n\nCURRENT LIVE SCHEDULE (use this to resolve targets precisely):\n"
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

    def _parse_and_validate(self, raw: str) -> List[ParsedAction] | None:
        """Try to parse raw text into a list of ParsedAction. Returns None on failure."""
        try:
            data = self._extract_json_array(raw)
        except Exception as e:
            logger.warning(f"ActionParser JSON extraction failed: {e}")
            return None

        if not isinstance(data, list):
            return None

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
        """Extract a JSON array from raw LLM text.
        
        Handles both:
        - A JSON array: [{...}, {...}]
        - A single JSON object: {...}  (wraps it in a list automatically)
        """
        t = text.strip()
        # Strip markdown fences
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t).strip()
        if t.endswith("```"):
            t = t[:-3].strip()

        # Try array first: find [ ... ]
        start = t.find("[")
        obj_start = t.find("{")

        # If there's an array and it comes before any object, parse as array
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
                        return [obj]  # wrap single action in list
                except json.JSONDecodeError:
                    candidate2 = re.sub(r",\s*([}\]])", r"\1", candidate)
                    obj = json.loads(candidate2)
                    if isinstance(obj, dict):
                        return [obj]

        raise ValueError("No JSON array or object found in LLM output")
