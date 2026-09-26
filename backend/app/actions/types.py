"""
Normalized action model for the Timetable Action Engine.

All action types share this module.  Button-driven UI and NLP-driven chat
both produce the same Pydantic objects — there is NO separate logic path.
"""
from __future__ import annotations

import enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Action type enum
# ---------------------------------------------------------------------------

class ActionType(str, enum.Enum):
    ADD_CLASS           = "ADD_CLASS"
    REMOVE_CLASS        = "REMOVE_CLASS"
    CANCEL_CLASS        = "CANCEL_CLASS"
    EXTEND_CLASS        = "EXTEND_CLASS"
    SHORTEN_CLASS       = "SHORTEN_CLASS"
    MOVE_CLASS          = "MOVE_CLASS"
    SWAP_CLASSES        = "SWAP_CLASSES"
    INTERCHANGE_CLASSES = "INTERCHANGE_CLASSES"
    CHANGE_TEACHER      = "CHANGE_TEACHER"
    CHANGE_ROOM         = "CHANGE_ROOM"
    CHANGE_TIME         = "CHANGE_TIME"
    CHANGE_DAY          = "CHANGE_DAY"
    REPLACE_CLASS       = "REPLACE_CLASS"
    GENERATE_TIMETABLE  = "GENERATE_TIMETABLE"
    OPTIMIZE_TIMETABLE  = "OPTIMIZE_TIMETABLE"
    VALIDATE_TIMETABLE  = "VALIDATE_TIMETABLE"
    RESTORE_VERSION     = "RESTORE_VERSION"
    # Catalog management
    ADD_TEACHER         = "ADD_TEACHER"
    ADD_SUBJECT         = "ADD_SUBJECT"
    ADD_PROGRAM         = "ADD_PROGRAM"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

class ClassTarget(BaseModel):
    """Identifies an existing class slot enough to find it in the schedule."""
    program: Optional[str] = None        # "B.Tech" | "M.Tech" | "M.Sc"
    semester: Optional[str] = None       # "3rd" | "5th" | ...
    day: Optional[str] = None            # "Monday" … "Saturday"
    start_time: Optional[str] = None     # "HH:MM"
    end_time: Optional[str] = None       # "HH:MM"
    subject_code: Optional[str] = None   # canonical code, e.g. "cn"
    subject_name: Optional[str] = None   # free-text fallback
    teacher: Optional[str] = None        # short name, e.g. "SK"
    room: Optional[str] = None           # e.g. "R#207B"
    entry_type: Optional[str] = None     # "Theory" | "Practical"


class ClassSpec(BaseModel):
    """Full specification for creating or replacing a class slot."""
    program: str
    semester: str
    day: str
    start_time: str
    end_time: str
    subject_code: str
    subject_name: str
    teacher: str
    entry_type: str    # "Theory" | "Practical"
    room: str

    def to_schedule_dict(self) -> dict:
        return {
            "day":          self.day,
            "program":      self.program,
            "semester":     self.semester,
            "start":        self.start_time,
            "end":          self.end_time,
            "subject_code": self.subject_code,
            "subject_name": self.subject_name,
            "teacher":      self.teacher,
            "type":         self.entry_type,
            "room":         self.room,
        }


# ---------------------------------------------------------------------------
# Per-action payload models (discriminated union via `action` literal field)
# ---------------------------------------------------------------------------

class AddClassAction(BaseModel):
    action: Literal[ActionType.ADD_CLASS] = ActionType.ADD_CLASS
    spec: ClassSpec


class RemoveClassAction(BaseModel):
    action: Literal[ActionType.REMOVE_CLASS] = ActionType.REMOVE_CLASS
    target: ClassTarget


class CancelClassAction(BaseModel):
    action: Literal[ActionType.CANCEL_CLASS] = ActionType.CANCEL_CLASS
    target: ClassTarget


class ExtendClassAction(BaseModel):
    action: Literal[ActionType.EXTEND_CLASS] = ActionType.EXTEND_CLASS
    target: ClassTarget
    new_end_time: str   # "HH:MM"


class ShortenClassAction(BaseModel):
    action: Literal[ActionType.SHORTEN_CLASS] = ActionType.SHORTEN_CLASS
    target: ClassTarget
    new_end_time: str   # "HH:MM" — must be before current end


class MoveClassAction(BaseModel):
    action: Literal[ActionType.MOVE_CLASS] = ActionType.MOVE_CLASS
    target: ClassTarget
    new_day: Optional[str] = None
    new_start_time: Optional[str] = None
    new_end_time: Optional[str] = None
    new_room: Optional[str] = None


class SwapClassesAction(BaseModel):
    """Swap only the time slot (day + start + end) between two classes."""
    action: Literal[ActionType.SWAP_CLASSES] = ActionType.SWAP_CLASSES
    target_a: ClassTarget
    target_b: ClassTarget


class InterchangeClassesAction(BaseModel):
    """Fully swap all fields (subject, teacher, room, time) between two classes."""
    action: Literal[ActionType.INTERCHANGE_CLASSES] = ActionType.INTERCHANGE_CLASSES
    target_a: ClassTarget
    target_b: ClassTarget


class ChangeTeacherAction(BaseModel):
    action: Literal[ActionType.CHANGE_TEACHER] = ActionType.CHANGE_TEACHER
    target: ClassTarget
    new_teacher: str

    @model_validator(mode='before')
    @classmethod
    def check_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if 'teacher' in data and 'new_teacher' not in data:
                data['new_teacher'] = data.pop('teacher')
        return data


class ChangeRoomAction(BaseModel):
    action: Literal[ActionType.CHANGE_ROOM] = ActionType.CHANGE_ROOM
    target: ClassTarget
    new_room: str

    @model_validator(mode='before')
    @classmethod
    def check_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if 'room' in data and 'new_room' not in data:
                data['new_room'] = data.pop('room')
        return data


class ChangeTimeAction(BaseModel):
    action: Literal[ActionType.CHANGE_TIME] = ActionType.CHANGE_TIME
    target: ClassTarget
    new_start_time: str
    new_end_time: str

    @model_validator(mode='before')
    @classmethod
    def check_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if 'start_time' in data and 'new_start_time' not in data:
                data['new_start_time'] = data.pop('start_time')
            if 'end_time' in data and 'new_end_time' not in data:
                data['new_end_time'] = data.pop('end_time')
        return data


class ChangeDayAction(BaseModel):
    action: Literal[ActionType.CHANGE_DAY] = ActionType.CHANGE_DAY
    target: ClassTarget
    new_day: str

    @model_validator(mode='before')
    @classmethod
    def check_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if 'day' in data and 'new_day' not in data:
                data['new_day'] = data.pop('day')
        return data


class ReplaceClassAction(BaseModel):
    """Remove matching class and insert new spec in its place."""
    action: Literal[ActionType.REPLACE_CLASS] = ActionType.REPLACE_CLASS
    target: ClassTarget
    new_spec: ClassSpec


class GenerateTimetableAction(BaseModel):
    action: Literal[ActionType.GENERATE_TIMETABLE] = ActionType.GENERATE_TIMETABLE
    extra_notes: str = ""
    subject_teacher_allocation: str = ""
    teacher_preferences: str = ""
    room_information: str = ""
    change_summary: str = "Generated via action engine"


class OptimizeTimetableAction(BaseModel):
    action: Literal[ActionType.OPTIMIZE_TIMETABLE] = ActionType.OPTIMIZE_TIMETABLE
    version_id: Optional[int] = None
    change_summary: str = "Optimized via action engine"


class ValidateTimetableAction(BaseModel):
    action: Literal[ActionType.VALIDATE_TIMETABLE] = ActionType.VALIDATE_TIMETABLE
    version_id: Optional[int] = None   # None = use latest draft


class RestoreVersionAction(BaseModel):
    action: Literal[ActionType.RESTORE_VERSION] = ActionType.RESTORE_VERSION
    version_id: int
    change_summary: str = "Restored from previous version"


# ---------------------------------------------------------------------------
# Discriminated union — single ParsedAction type accepted everywhere
# ---------------------------------------------------------------------------

ParsedAction = Union[
    AddClassAction,
    RemoveClassAction,
    CancelClassAction,
    ExtendClassAction,
    ShortenClassAction,
    MoveClassAction,
    SwapClassesAction,
    InterchangeClassesAction,
    ChangeTeacherAction,
    ChangeRoomAction,
    ChangeTimeAction,
    ChangeDayAction,
    ReplaceClassAction,
    GenerateTimetableAction,
    OptimizeTimetableAction,
    ValidateTimetableAction,
    RestoreVersionAction,
    "AddTeacherAction",
    "AddSubjectAction",
    "AddProgramAction",
]


class AddTeacherAction(BaseModel):
    action: Literal[ActionType.ADD_TEACHER] = ActionType.ADD_TEACHER
    short_name: str
    full_name: str
    subjects_csv: str = ""
    is_internal: bool = True
    email: Optional[str] = None


class AddSubjectAction(BaseModel):
    action: Literal[ActionType.ADD_SUBJECT] = ActionType.ADD_SUBJECT
    code: str
    name: str
    program: str
    semester: str
    entry_type: str = "Theory"
    weekly_hours: int = 2


class AddProgramAction(BaseModel):
    action: Literal[ActionType.ADD_PROGRAM] = ActionType.ADD_PROGRAM
    name: str
    semesters_count: int = 8
    description: str = ""


# rebuild union with concrete classes
ParsedAction = Union[
    AddClassAction,
    RemoveClassAction,
    CancelClassAction,
    ExtendClassAction,
    ShortenClassAction,
    MoveClassAction,
    SwapClassesAction,
    InterchangeClassesAction,
    ChangeTeacherAction,
    ChangeRoomAction,
    ChangeTimeAction,
    ChangeDayAction,
    ReplaceClassAction,
    GenerateTimetableAction,
    OptimizeTimetableAction,
    ValidateTimetableAction,
    RestoreVersionAction,
    AddTeacherAction,
    AddSubjectAction,
    AddProgramAction,
]


def parse_action(data: Dict[str, Any]) -> ParsedAction:
    """Deserialize a dict into the correct action model based on 'action' key."""
    action_type = data.get("action")
    _map = {
        ActionType.ADD_CLASS:           AddClassAction,
        ActionType.REMOVE_CLASS:        RemoveClassAction,
        ActionType.CANCEL_CLASS:        CancelClassAction,
        ActionType.EXTEND_CLASS:        ExtendClassAction,
        ActionType.SHORTEN_CLASS:       ShortenClassAction,
        ActionType.MOVE_CLASS:          MoveClassAction,
        ActionType.SWAP_CLASSES:        SwapClassesAction,
        ActionType.INTERCHANGE_CLASSES: InterchangeClassesAction,
        ActionType.CHANGE_TEACHER:      ChangeTeacherAction,
        ActionType.CHANGE_ROOM:         ChangeRoomAction,
        ActionType.CHANGE_TIME:         ChangeTimeAction,
        ActionType.CHANGE_DAY:          ChangeDayAction,
        ActionType.REPLACE_CLASS:       ReplaceClassAction,
        ActionType.GENERATE_TIMETABLE:  GenerateTimetableAction,
        ActionType.OPTIMIZE_TIMETABLE:  OptimizeTimetableAction,
        ActionType.VALIDATE_TIMETABLE:  ValidateTimetableAction,
        ActionType.RESTORE_VERSION:     RestoreVersionAction,
        ActionType.ADD_TEACHER:         AddTeacherAction,
        ActionType.ADD_SUBJECT:         AddSubjectAction,
        ActionType.ADD_PROGRAM:         AddProgramAction,
    }
    model_cls = _map.get(action_type)
    if model_cls is None:
        raise ValueError(f"Unknown action type: {action_type!r}")
    return model_cls.model_validate(data)
