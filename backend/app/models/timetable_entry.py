"""Individual schedule row stored per timetable version."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TimetableEntry(Base):
    __tablename__ = "timetable_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("timetable_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    day: Mapped[str] = mapped_column(String(20), nullable=False)
    program: Mapped[str] = mapped_column(String(50), nullable=False)
    semester: Mapped[str] = mapped_column(String(10), nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "Theory" | "Practical"
    room: Mapped[str] = mapped_column(String(50), nullable=False)

    version = relationship("TimetableVersion", back_populates="entries")

    __table_args__ = (
        Index("ix_entry_version_program_sem", "version_id", "program", "semester"),
        Index("ix_entry_version_teacher", "version_id", "teacher"),
        Index("ix_entry_version_room", "version_id", "room"),
    )

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "program": self.program,
            "semester": self.semester,
            "start": self.start_time,
            "end": self.end_time,
            "subject_code": self.subject_code,
            "subject_name": self.subject_name,
            "teacher": self.teacher,
            "type": self.entry_type,
            "room": self.room,
        }
