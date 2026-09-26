"""Subject catalog model."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    program: Mapped[str] = mapped_column(String(50), nullable=False)   # "B.Tech", "M.Tech", "M.Sc"
    semester: Mapped[str] = mapped_column(String(10), nullable=False)  # "1st", "3rd", …
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False, default="Theory")  # "Theory" | "Practical" | "Both"
    weekly_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
