"""Migrate any legacy raw teacher IDs (I01..I19, E01..E04) in timetable_entries to clean teacher short forms."""
import os
import re
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.services.teacher_catalog_service import resolve_entry_teacher_short_name

def migrate():
    load_dotenv('backend/.env')
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("No DATABASE_URL found.")
        return

    engine = create_engine(db_url)
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, subject_name, subject_code, teacher FROM timetable_entries WHERE teacher ~ '^[IE][0-9]+'")).fetchall()
        print(f"Found {len(rows)} entries with raw teacher IDs in Supabase.")
        count = 0
        for row in rows:
            eid, sub_name, sub_code, raw_t = row
            clean_short = resolve_entry_teacher_short_name(
                teacher_val=raw_t,
                subject_name=sub_name or "",
                subject_code=sub_code or "",
            )
            if clean_short and clean_short != raw_t:
                conn.execute(
                    text("UPDATE timetable_entries SET teacher = :clean WHERE id = :id"),
                    {"clean": clean_short, "id": eid}
                )
                count += 1
        print(f"Updated {count} entries in Supabase to teacher short forms.")

if __name__ == '__main__':
    migrate()
