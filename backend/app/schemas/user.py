"""Pydantic schemas for User and Teacher endpoints."""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr
from app.models.user import RoleEnum


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: RoleEnum = RoleEnum.TEACHER


class UserUpdateAdmin(BaseModel):
    """Admin-allowed update fields."""
    full_name: Optional[str] = None
    role: Optional[RoleEnum] = None


class UserUpdateSelf(BaseModel):
    """Self-service update fields (teacher cannot change own role)."""
    full_name: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: RoleEnum
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    failed_attempts: int = 0
    locked_until: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Teacher profile models ────────────────────────────────────────────────────

class TeacherOut(BaseModel):
    id: int
    user_id: int
    short_name: str
    full_name: str
    subjects_csv: str
    is_internal: bool

    model_config = {"from_attributes": True}


class TeacherCreate(BaseModel):
    user_id: int
    short_name: str
    full_name: str
    subjects_csv: str = ""
    is_internal: bool = True


class TeacherUpdate(BaseModel):
    subjects_csv: Optional[str] = None
    is_internal: Optional[bool] = None
