"""TimetableVersion model — every generated/modified timetable is a version."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class VersionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class TimetableVersion(Base):
    __tablename__ = "timetable_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    parent_version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("timetable_versions.id"), nullable=True
    )
    status: Mapped[VersionStatus] = mapped_column(
        Enum(VersionStatus), nullable=False, default=VersionStatus.DRAFT, index=True
    )
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON-encoded validation result from the last validate run
    validation_result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Model-generated markdown sections stored for retrieval
    llm_output_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    creator = relationship("User", back_populates="versions")
    entries = relationship("TimetableEntry", back_populates="version",
                           cascade="all, delete-orphan", lazy="selectin")
    audit_logs = relationship("AuditLog", back_populates="version", lazy="dynamic")
    parent = relationship("TimetableVersion", remote_side=[id], foreign_keys=[parent_version_id])
