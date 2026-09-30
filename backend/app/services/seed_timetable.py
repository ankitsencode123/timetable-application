"""Seed the default timetable from authoritative EXISTING_TIMETABLE_MD data."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.timetable import TimetableVersion, VersionStatus
from app.models.timetable_entry import TimetableEntry

log = logging.getLogger(__name__)

# Subject-to-type mapping: subjects ending with -P are Practicals
def _entry_type(subject_code: str) -> str:
    s = subject_code.strip().upper()
    if s.endswith("-P") or s.endswith("P)") or "-P:" in s:
        return "Practical"
    return "Theory"


def _parse_timetable_md(md: str) -> list[dict]:
    """Parse EXISTING_TIMETABLE_MD markdown table into list of entry dicts."""
    entries = []
    seen_header = False
    for raw_line in md.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        # Skip header rows
        if cells[0].lower() in ("day", "---", ""):
            seen_header = True
            continue
        if not seen_header:
            continue
        if cells[0].startswith("---") or cells[1].startswith("---"):
            continue

        day, program, semester, time_range, info = cells[0], cells[1], cells[2], cells[3], cells[4]
        program = program.replace("B.Tech.", "B.Tech").replace("M.Tech.", "M.Tech").replace("M.Sc.", "M.Sc")

        # Parse time range HH:MM-HH:MM
        m = re.match(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", time_range.strip())
        if not m:
            continue
        start_time, end_time = m.group(1), m.group(2)

        # Parse info: "TEACHER(SUBJECT):ROOM" or "TEACHER+(SUBJECT):ROOM"
        # Handle edge cases like "SR+(PE-1:SC)" with colons in subject
        # Format: TEACHER_CODE(SUBJECT_CODE):ROOM
        mi = re.match(r"([A-Za-z+]+)\((.+)\):(.+)", info.strip())
        if not mi:
            continue
        teacher = mi.group(1).rstrip("+").strip()
        subject_code = mi.group(2).strip()
        room = mi.group(3).strip()

        entries.append({
            "day": day,
            "program": program,
            "semester": semester,
            "start": start_time,
            "end": end_time,
            "subject_code": subject_code,
            "subject_name": subject_code,
            "teacher": teacher,
            "type": _entry_type(subject_code),
            "room": room,
        })
    return entries


def seed_default_timetable_if_needed(db: Session, admin_user_id: int) -> None:
    """Seed the canonical timetable as a PUBLISHED version on first boot.
    
    Only runs if there are zero versions in the database.
    """
    existing = db.query(TimetableVersion).first()
    if existing:
        return  # Data already seeded or user has their own data

    from app.scheduler.data import EXISTING_TIMETABLE_MD

    log.info("Seeding default timetable from EXISTING_TIMETABLE_MD …")
    entries_data = _parse_timetable_md(EXISTING_TIMETABLE_MD)
    log.info("  Parsed %d timetable entries", len(entries_data))

    version = TimetableVersion(
        created_by=admin_user_id,
        status=VersionStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc),
        change_summary="Initial default timetable (seeded on first boot)",
    )
    db.add(version)
    db.flush()

    for row in entries_data:
        en = TimetableEntry(
            version_id=version.id,
            day=row["day"],
            program=row["program"],
            semester=row["semester"],
            start_time=row["start"],
            end_time=row["end"],
            subject_code=row["subject_code"],
            subject_name=row["subject_name"],
            teacher=row["teacher"],
            entry_type=row["type"],
            room=row["room"],
        )
        db.add(en)

    db.commit()
    log.info("Default timetable seeded as Version #%d (PUBLISHED)", version.id)

def seed_catalog_if_needed(db: Session) -> None:
    """Seed default B.Tech, M.Tech, M.Sc programs and subjects into the database."""
    from app.models.program import Program
    from app.models.subject import Subject
    from app.scheduler.constraints import VALID_PROGRAMS, PROGRAMME_SUBJECT_MAP
    
    if db.query(Program).first():
        return
        
    for p_name in VALID_PROGRAMS:
        prog = Program(name=p_name, semesters_count=8, description=f"Default {p_name} Program")
        db.add(prog)
    db.commit()

    for (p_name, sem), subjects in PROGRAMME_SUBJECT_MAP.items():
        grouped = set()
        for code in subjects:
            if code.endswith("-p"):
                continue # handled with theory
            entry_type = "Theory"
            if f"{code}-p" in subjects:
                entry_type = "Both"
            if code not in grouped:
                s = Subject(
                    code=code,
                    name=code.upper(),
                    program=p_name,
                    semester=sem,
                    entry_type=entry_type,
                    weekly_hours=2,
                )
                db.add(s)
                grouped.add(code)
                
        # Also add independent practicals if they have no theory
        for code in subjects:
            if code.endswith("-p"):
                base = code[:-2]
                if base not in grouped:
                    s = Subject(
                        code=code,
                        name=code.upper(),
                        program=p_name,
                        semester=sem,
                        entry_type="Practical",
                        weekly_hours=2,
                    )
                    db.add(s)
    db.commit()
    log.info("Default catalog seeded.")
