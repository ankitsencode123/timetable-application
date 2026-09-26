"""
Fresh database seeder — drops all data and re-seeds teachers + timetable.
Run this ONCE on first deployment or to reset the database.

Usage:
    python seed_timetable.py
"""
from app.main import app  # ensure all models are loaded
from app.core.database import SessionLocal, engine
from app.models.base import Base
from app.models.teacher import Teacher
from app.models.user import User, RoleEnum
from app.models.timetable import TimetableVersion
from app.services.auth_service import create_user
from app.services.seed_timetable import seed_default_timetable_if_needed, seed_catalog_if_needed
import secrets

# === Teacher Registry ===
# Internal faculty (permanent staff)
INTERNAL_TEACHERS = [
    {"short_name": "SCh", "full_name": "Dr. Sumit Chakraborty",     "subjects_csv": "Discrete Mathematics"},
    {"short_name": "NC",  "full_name": "Dr. Nabendu Chaki",          "subjects_csv": "Software Engineering,SE Lab,Advance DBMS,ADBMS Lab"},
    {"short_name": "RKP", "full_name": "Dr. Rajat Kumar Pal",        "subjects_csv": "VLSI Design,Problem Solving Techniques"},
    {"short_name": "SKS", "full_name": "Dr. Sanjit Kumar Setua",     "subjects_csv": "Compiler Design,CD Lab,Deep Learning"},
    {"short_name": "SC",  "full_name": "Dr. Sankhayan Choudhury",    "subjects_csv": "DBMS,DBMS Lab,Wireless & Mobile Computing"},
    {"short_name": "RD",  "full_name": "Dr. Rajib Kumar Das",        "subjects_csv": "Microprocessor & Microcontroller,Introduction to Data Mining,IDM Lab,Digital Logic & Microprocessor Lab"},
    {"short_name": "PB",  "full_name": "Dr. Pritha Banerjee",        "subjects_csv": "Data Structure,Data Structure Lab,Advanced Algorithms,AA Lab"},
    {"short_name": "SK",  "full_name": "Dr. Sunirmal Khatua",        "subjects_csv": "Digital Logic,Computer Networks,CN Lab,Wireless & Mobile Computing,Digital Logic & Microprocessor Lab"},
]

# External / Guest faculty
EXTERNAL_TEACHERS = [
    {"short_name": "PBn", "full_name": "Dr. Priya Banerjee",         "subjects_csv": "Environmental Science"},
    {"short_name": "AD",  "full_name": "Dr. Amartya Dutta",          "subjects_csv": "AI & Machine Learning,AIML Lab"},
    {"short_name": "SN",  "full_name": "Dr. Somen Nandy",            "subjects_csv": "Algorithmic Graph Theory,AGT Lab"},
    {"short_name": "GM",  "full_name": "Dr. Gautam Mahapatra",       "subjects_csv": "Computer Graphics,CG Lab"},
    {"short_name": "AB",  "full_name": "Dr. Ansuman Banerjee",       "subjects_csv": "Artificial Intelligence & Machine Learning,AIML Lab"},
    {"short_name": "TD",  "full_name": "Dr. Tathagata Das",          "subjects_csv": "Environment Systems & Internet of Things,ES&IoT Lab"},
    {"short_name": "DK",  "full_name": "Dr. Dhiman Karmakar",        "subjects_csv": "Image Processing & Pattern Recognition"},
    {"short_name": "AG",  "full_name": "Dr. Anupam Ghosh",           "subjects_csv": "Artificial Intelligence,AI Lab"},
    {"short_name": "SGC", "full_name": "Dr. Shruti Gan Choudhuri",   "subjects_csv": "Mathematical Foundations of Computer Science"},
    {"short_name": "SR",  "full_name": "Dr. Samir Roy",              "subjects_csv": "Soft Computing,Soft Computing Lab"},
    {"short_name": "SD",  "full_name": "Dr. Susmita Das",            "subjects_csv": "English for Research Paper Writing"},
    {"short_name": "DC",  "full_name": "Dr. Debesh Choudhury",       "subjects_csv": "Research Methodology"},
    {"short_name": "RS",  "full_name": "Dr. Rituparna Sinha",        "subjects_csv": "Bioinformatics"},
    {"short_name": "TG",  "full_name": "Dr. Tathagata Ghosh",        "subjects_csv": "ES&IoT Lab"},
]

def seed_all():
    db = SessionLocal()
    try:
        print("=== Seeding teachers ===")
        for t_data in INTERNAL_TEACHERS:
            _upsert_teacher(db, t_data, is_internal=True)
        for t_data in EXTERNAL_TEACHERS:
            _upsert_teacher(db, t_data, is_internal=False)

        print("=== Seeding catalog ===")
        seed_catalog_if_needed(db)

        print("=== Seeding timetable ===")
        admin = db.query(User).filter(User.role == RoleEnum.ADMIN).first()
        if admin:
            seed_default_timetable_if_needed(db, admin.id)
        else:
            print("WARNING: No admin user found. Timetable not seeded. Create admin first.")

        print("=== All done! ===")
    finally:
        db.close()


def _upsert_teacher(db, t_data, is_internal: bool):
    short_name = t_data["short_name"]
    full_name  = t_data["full_name"]
    existing = db.query(Teacher).filter(Teacher.short_name == short_name).first()
    if existing:
        existing.subjects_csv = t_data.get("subjects_csv", "")
        existing.is_internal  = is_internal
        db.commit()
        print(f"Updated teacher: {short_name} ({full_name})")
        return

    email = f"{short_name.lower()}@college.edu"
    user_obj = db.query(User).filter(User.email == email).first()
    if not user_obj:
        user_obj = create_user(
            db, email=email,
            password=secrets.token_urlsafe(8),
            full_name=full_name,
            role=RoleEnum.TEACHER,
        )

    teacher = Teacher(
        user_id=user_obj.id,
        short_name=short_name,
        full_name=full_name,
        subjects_csv=t_data.get("subjects_csv", ""),
        is_internal=is_internal,
    )
    db.add(teacher)
    db.commit()
    print(f"Added {'internal' if is_internal else 'external'} teacher: {short_name} ({full_name})")


if __name__ == "__main__":
    seed_all()
