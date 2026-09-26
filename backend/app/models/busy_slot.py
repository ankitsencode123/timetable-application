"""Faculty busy slot model — marks days/dates when a teacher is unavailable."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TeacherBusySlot(Base):
    __tablename__ = "teacher_busy_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    teacher_short_name: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # "permanent" = blocks every week on day_of_week
    # "temporary" = blocks a specific calendar date
    scope: Mapped[str] = mapped_column(String(20), nullable=False)  # "permanent" | "temporary"
    day_of_week: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)   # "Monday"…"Saturday"
    specific_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
