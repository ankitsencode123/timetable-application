"""Chat request/response schemas."""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    subject_teacher_allocation: str = ""
    teacher_preferences: str = ""
    room_information: str = ""


class ChatResponse(BaseModel):
    version_id: Optional[int] = None
    message: str
    schedule_updated: bool
    response_data: dict
    parsed_actions: List[Dict[str, Any]] = []

