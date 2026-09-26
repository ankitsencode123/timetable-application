"""Pydantic I/O schemas for the Actions API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.actions.types import ActionType


class ClassTargetSchema(BaseModel):
    program:      Optional[str] = None
    semester:     Optional[str] = None
    day:          Optional[str] = None
    start_time:   Optional[str] = None
    end_time:     Optional[str] = None
    subject_code: Optional[str] = None
    subject_name: Optional[str] = None
    teacher:      Optional[str] = None
    room:         Optional[str] = None
    entry_type:   Optional[str] = None


class ClassSpecSchema(BaseModel):
    program:      str
    semester:     str
    day:          str
    start_time:   str
    end_time:     str
    subject_code: str
    subject_name: str
    teacher:      str
    entry_type:   str
    room:         str


class ActionResultSchema(BaseModel):
    """Result of a single action within a multi-action request."""
    action_type:          str
    success:              bool
    change_log:           str = ""
    error:                str = ""
    violated_constraint:  Optional[Dict[str, Any]] = None
    suggestions:          Optional[Dict[str, Any]] = None
    before:               Optional[Any] = None
    after:                Optional[Any] = None


class ActionExecuteRequest(BaseModel):
    """
    Execute a list of pre-parsed actions (button path or post-confirmation chat path).
    version_id=None → operate on the most recent version.
    partial_ok=True  → apply successful sub-actions even if some fail.
    """
    version_id:  Optional[int] = None
    actions:     List[Dict[str, Any]]   # serialised ParsedAction dicts
    # Independent actions are evaluated and committed separately by default.
    # Callers that require all-or-nothing behavior can explicitly pass False.
    partial_ok:  bool = True


class ActionExecuteResponse(BaseModel):
    success:          bool
    results:          List[ActionResultSchema]
    new_version_id:   Optional[int] = None
    violations:       List[Dict[str, Any]] = []
    schema_errors:    List[Dict[str, Any]] = []
    change_log:       str = ""
    partial_applied:  bool = False


class ActionParseRequest(BaseModel):
    """NLP text to parse into action list (preview only — no DB write)."""
    text: str


class ActionParseResponse(BaseModel):
    """Preview of parsed actions. No schedule changes made."""
    parsed_actions:  List[Dict[str, Any]]
    action_count:    int
    interpretation:  str = ""


class ActionChatRequest(BaseModel):
    """
    Combined NLP parse + optional execute in one call.
    If execute=True, the engine runs immediately after parsing.
    If execute=False, only the parsed actions are returned for UI confirmation.
    """
    text:        str
    version_id:  Optional[int] = None
    execute:     bool = False
    partial_ok:  bool = False


class ActionChatResponse(BaseModel):
    parsed_actions:    List[Dict[str, Any]]
    action_count:      int
    interpretation:    str = ""
    executed:          bool = False
    execution_result:  Optional[ActionExecuteResponse] = None
