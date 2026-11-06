"""Teacher and Subject Catalog Management Service.

Provides automatic account generation, short-form derivation, and catalog
synchronization for faculty members and course subjects imported via CSV
or created using interactive UI buttons.
"""
from __future__ import annotations

import re
import secrets
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User, RoleEnum
from app.models.teacher import Teacher
from app.models.subject import Subject
from app.models.program import Program
from app.services.auth_service import create_user
from app.services.preload import invalidate_preload
from app.scheduler import constraints as C

# Standard demo / benchmark faculty ID to short form mapping
DEMO_ID_MAP: Dict[str, str] = {
    "I01": "AR",
    "I02": "BI",
    "I03": "CD",
    "I04": "DM",
    "I05": "EK",
    "I06": "FN",
    "I07": "GS",
    "I08": "HJ",
    "I09": "IR",
    "I10": "JV",
    "I11": "GM",
    "I12": "AD",
    "I13": "TD",
    "I14": "DK",
    "I15": "AG",
    "I16": "SGC",
    "I17": "SR",
    "I18": "SD",
    "I19": "DC",
    "I21": "RKP",
    "E01": "KP",
    "E02": "LG",
    "E03": "MS",
    "E04": "NB",
}

# Known subject to faculty short form mapping for standard curriculum
KNOWN_SUBJECT_MAP: Dict[str, str] = {
    "research methodology": "DC",
    "discrete mathematics": "SCh",
    "software engineering": "NC",
    "compiler design": "SKS",
    "deep learning": "SKS",
    "digital logic": "SK",
    "dbms": "SC",
    "database systems": "SC",
    "algorithmic graph theory": "SN",
    "mathematical foundations of computer science": "SGC",
    "microprocessor & microcontroller": "RD",
    "microprocessor": "RD",
    "introduction to data mining": "RD",
    "ai & machine learning": "AD",
    "artificial intelligence": "AG",
    "ai lab": "AG",
    "aiml lab": "AD",
    "cg lab": "GM",
    "computer graphics": "GM",
    "environment systems & internet of things": "TD",
    "environmental science": "PBn",
    "soft computing lab": "SR",
    "soft computing": "SR",
    "adbms lab": "NC",
    "advance dbms": "NC",
    "wireless & mobile computing": "SC",
    "external practical training": "EPT",
    "data structure": "PB",
    "data structures": "PB",
    "data structure lab": "PB",
    "advanced algorithms": "PB",
    "computer networks": "SK",
    "cn lab": "SK",
    "cloud computing": "JV",
    "linear algebra": "DM",
    "statistics": "EK",
    "numerical methods": "FN",
    "optimisation": "HJ",
    "problem solving techniques": "RKP",
    "vlsi design": "RKP",
    "english for research paper writing": "SD",
    "image processing & pattern recognition": "DK",
}


def derive_teacher_short_form(
    full_name: str,
    fallback_id: str = "",
    existing_shorts: Optional[Set[str]] = None,
) -> str:
    """Derive standard academic initials / short form from a faculty member's full name.
    
    Examples:
        'Dr. Debesh Choudhury' -> 'DC'
        'Dr. Sanjit Kumar Setua' -> 'SKS'
        'Dr. A. Rao' -> 'AR'
        'Prof. C. Das' -> 'CD'
        'Mr. K. Patel (ext)' -> 'KP'
        'Albert Einstein' -> 'AE'
    """
    cleaned_fallback = str(fallback_id).strip()
    is_opaque_id = bool(re.match(r"^[IE]\d+$", cleaned_fallback, re.IGNORECASE))

    # If full name is blank, check fallback
    if not full_name or not full_name.strip():
        if cleaned_fallback and not is_opaque_id:
            return cleaned_fallback.upper()
        if cleaned_fallback.upper() in DEMO_ID_MAP:
            return DEMO_ID_MAP[cleaned_fallback.upper()]
        return cleaned_fallback.upper() if cleaned_fallback else "TCH"

    # Clean name: remove parentheticals like (ext), (visiting), etc.
    name = re.sub(r"\(.*?\)", "", full_name).strip()
    # Remove academic/honorific titles
    titles = r"\b(dr|prof|professor|mr|ms|mrs|er|shri|smt|guest|faculty)\b\.?"
    name = re.sub(titles, "", name, flags=re.IGNORECASE).strip()

    # Split into word tokens
    parts = [p.strip() for p in re.split(r"[\s._\-]+", name) if p.strip()]

    if not parts:
        if cleaned_fallback and not is_opaque_id:
            return cleaned_fallback.upper()
        if cleaned_fallback.upper() in DEMO_ID_MAP:
            return DEMO_ID_MAP[cleaned_fallback.upper()]
        return "TCH"

    if len(parts) == 1:
        base = parts[0][:3].upper()
    else:
        # Take the uppercase first letter of each word
        base = "".join(p[0].upper() for p in parts)

    # Collision avoidance against known existing short names
    if existing_shorts is not None and base in existing_shorts:
        # Try two letters from the surname e.g. SCh for Sumit Chakraborty
        if len(parts) >= 2 and len(parts[-1]) >= 2:
            alt = parts[0][0].upper() + parts[-1][:2].capitalize()
            if alt not in existing_shorts:
                return alt
        # Suffix with number e.g. DC2
        idx = 2
        while f"{base}{idx}" in existing_shorts:
            idx += 1
        return f"{base}{idx}"

    return base


def ensure_teacher_account(
    db: Session,
    full_name: str,
    short_name: Optional[str] = None,
    is_internal: bool = True,
    designation: Optional[str] = None,
    initial_subject: Optional[str] = None,
) -> Tuple[Teacher, User, bool, str, str]:
    """Ensure a Teacher profile and active User login account exist in the database.
    
    If teacher does not exist:
      - Automatically derives a short_name (if not provided).
      - Automatically generates institutional email (e.g. dc@college.edu).
      - Automatically generates a secure password.
      - Creates User record with role TEACHER.
      - Creates Teacher profile record linked to User.
    
    Returns:
        (teacher_obj, user_obj, is_newly_created, email, plain_password)
    """
    full_name_clean = full_name.strip()
    if not full_name_clean:
        raise ValueError("Teacher full name cannot be blank.")

    # 1. Search existing teacher by short_name or full_name (case-insensitive)
    t_query = db.query(Teacher)
    existing_t: Optional[Teacher] = None

    if short_name and short_name.strip():
        req_short = short_name.strip()
        existing_t = t_query.filter(func.lower(Teacher.short_name) == req_short.lower()).first()

    if not existing_t:
        existing_t = t_query.filter(func.lower(Teacher.full_name) == full_name_clean.lower()).first()

    if existing_t:
        # Teacher already exists — link subject if provided
        if initial_subject and initial_subject.strip():
            sub_code = initial_subject.strip().lower()
            current_codes = [c.strip() for c in existing_t.subjects_csv.split(",") if c.strip()]
            if sub_code not in current_codes:
                current_codes.append(sub_code)
                existing_t.subjects_csv = ",".join(current_codes)
                db.commit()
                db.refresh(existing_t)
                C.reload_catalog(db)

        user_obj = db.query(User).filter(User.id == existing_t.user_id).first()
        email = user_obj.email if user_obj else f"{existing_t.short_name.lower()}@college.edu"
        return existing_t, user_obj, False, email, ""

    # 2. Derive unique short name
    all_existing_shorts = {t.short_name.upper() for t in db.query(Teacher.short_name).all() if t.short_name}
    derived_short = short_name.strip().upper() if short_name and short_name.strip() and not re.match(r"^[IE]\d+$", short_name.strip(), re.IGNORECASE) else ""
    if not derived_short or derived_short in all_existing_shorts:
        derived_short = derive_teacher_short_form(full_name_clean, fallback_id=short_name or "", existing_shorts=all_existing_shorts)

    # 3. Generate email & secure password
    base_email = f"{derived_short.lower()}@college.edu"
    email = base_email
    counter = 1
    while db.query(User).filter(User.email == email).first():
        email = f"{derived_short.lower()}{counter}@college.edu"
        counter += 1

    # Generate readable yet secure password
    plain_password = f"{derived_short.capitalize()}@{secrets.token_hex(3)}"

    # 4. Create User account
    user_obj = create_user(
        db=db,
        email=email,
        password=plain_password,
        full_name=full_name_clean,
        role=RoleEnum.TEACHER,
    )

    # 5. Create Teacher profile
    clean_sub = initial_subject.strip().lower() if initial_subject else ""
    teacher_obj = Teacher(
        user_id=user_obj.id,
        short_name=derived_short,
        full_name=full_name_clean,
        subjects_csv=clean_sub,
        is_internal=is_internal,
        designation=designation or ("Faculty" if is_internal else "Guest Faculty"),
    )
    db.add(teacher_obj)
    db.commit()
    db.refresh(teacher_obj)

    # Invalidate cache and preload
    C.reload_catalog(db)
    invalidate_preload("create_teacher_account")

    return teacher_obj, user_obj, True, email, plain_password


def ensure_subject_catalog(
    db: Session,
    code: str,
    name: str,
    program: str,
    semester: str,
    entry_type: str = "Theory",
    weekly_hours: int = 2,
    teacher_short_name: Optional[str] = None,
) -> Tuple[Subject, bool]:
    """Ensure a Subject is registered under the specified Program and Semester.
    
    If the subject exists, updates or links the teacher.
    If the subject does not exist, registers it in the catalog.
    """
    code_clean = code.strip().lower()
    name_clean = name.strip() or code.strip().upper()
    prog_clean = (program or "B.Tech").strip()
    sem_clean = (semester or "1st").strip()
    # Normalize semester string e.g. "3" -> "3rd"
    if sem_clean.isdigit():
        n = int(sem_clean)
        suffix = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        sem_clean = f"{n}{suffix}"

    # 1. Ensure Program exists
    prog_obj = db.query(Program).filter(func.lower(Program.name) == prog_clean.lower()).first()
    if not prog_obj:
        prog_obj = Program(name=prog_clean, semesters_count=8, description=f"{prog_clean} Degree Programme")
        db.add(prog_obj)
        db.commit()
        db.refresh(prog_obj)

    # 2. Check Subject in this program
    subj_obj = db.query(Subject).filter(
        Subject.code == code_clean,
        func.lower(Subject.program) == prog_clean.lower(),
    ).first()

    created = False
    if not subj_obj:
        subj_obj = Subject(
            code=code_clean,
            name=name_clean,
            program=prog_obj.name,
            semester=sem_clean,
            entry_type=entry_type,
            weekly_hours=weekly_hours,
        )
        db.add(subj_obj)
        db.commit()
        db.refresh(subj_obj)
        created = True
    else:
        # Update semester if not set
        if subj_obj.semester != sem_clean:
            subj_obj.semester = sem_clean
            db.commit()

    # 3. Link to teacher if provided
    if teacher_short_name and teacher_short_name.strip():
        t_short = teacher_short_name.strip()
        t_obj = db.query(Teacher).filter(func.lower(Teacher.short_name) == t_short.lower()).first()
        if t_obj:
            codes = [c.strip() for c in t_obj.subjects_csv.split(",") if c.strip()]
            if code_clean not in codes:
                codes.append(code_clean)
                t_obj.subjects_csv = ",".join(codes)
                db.commit()

    C.reload_catalog(db)
    return subj_obj, created


def sync_extracted_files_to_catalog(
    db: Session,
    semesters: List[Dict[str, Any]],
    subjects: List[Dict[str, Any]],
    teachers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Atomically ingest parsed CSV files into the system catalog.
    
    1. Registers all teachers with derived short names and auto-generated accounts (email + password).
    2. Registers all subjects under their respective programs and semesters.
    3. Associates subjects with teachers in `subjects_csv`.
    4. Returns credentials report of all newly created accounts.
    """
    created_accounts: List[Dict[str, Any]] = []
    teacher_id_to_short: Dict[str, str] = {}

    # Map teachers from teachers list
    for t_data in teachers:
        raw_id = str(t_data.get("id") or "").strip()
        raw_name = str(t_data.get("name") or "").strip()
        is_int = bool(t_data.get("internal", True))

        # If name is missing or identical to ID, check fallback
        if not raw_name or raw_name == raw_id:
            raw_name = DEMO_ID_MAP.get(raw_id.upper(), raw_id)

        try:
            t_obj, u_obj, is_new, email, password = ensure_teacher_account(
                db=db,
                full_name=raw_name,
                short_name=raw_id if not re.match(r"^[IE]\d+$", raw_id, re.IGNORECASE) else None,
                is_internal=is_int,
            )
            teacher_id_to_short[raw_id] = t_obj.short_name
            if is_new:
                created_accounts.append({
                    "teacher_id": raw_id,
                    "short_name": t_obj.short_name,
                    "full_name": t_obj.full_name,
                    "email": email,
                    "password": password,
                    "is_internal": t_obj.is_internal,
                })
        except Exception:
            pass

    # Map semesters to program names
    sem_id_to_info: Dict[str, Tuple[str, str]] = {}
    for sem in semesters:
        sid = sem.get("id") or "SEM-1"
        prog = sem.get("programme") or "B.Tech"
        num = sem.get("number") or 1
        suffix = "th" if 11 <= num <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(num % 10, "th")
        sem_id_to_info[sid] = (prog, f"{num}{suffix}")

    # Process subjects
    synced_subjects_count = 0
    for s_data in subjects:
        code = str(s_data.get("code") or "").strip()
        name = str(s_data.get("name") or code).strip()
        sem_id = str(s_data.get("semester_id") or "BT-1").strip()
        prog, sem_str = sem_id_to_info.get(sem_id, ("B.Tech", "1st"))
        class_type = str(s_data.get("class_type") or "theory").capitalize()
        hours = int(float(s_data.get("sessions_per_week") or 2) * 2)  # session to hours approx

        # Teacher short name
        int_tid = str(s_data.get("internal_teacher_id") or "").strip()
        ext_tid = str(s_data.get("external_teacher_id") or "").strip()
        t_short = teacher_id_to_short.get(int_tid) or teacher_id_to_short.get(ext_tid)

        if not t_short and int_tid:
            # Try finding known mapping
            t_short = DEMO_ID_MAP.get(int_tid.upper())

        try:
            _, created = ensure_subject_catalog(
                db=db,
                code=code,
                name=name,
                program=prog,
                semester=sem_str,
                entry_type=class_type,
                weekly_hours=max(hours, 2),
                teacher_short_name=t_short,
            )
            synced_subjects_count += 1
        except Exception:
            pass

    C.reload_catalog(db)
    invalidate_preload("catalog_files_synced")

    return {
        "created_accounts": created_accounts,
        "synced_subjects_count": synced_subjects_count,
        "synced_teachers_count": len(teacher_id_to_short),
        "teacher_id_to_short": teacher_id_to_short,
    }


def resolve_entry_teacher_short_name(
    teacher_val: str,
    subject_name: str = "",
    subject_code: str = "",
    db: Optional[Session] = None,
) -> str:
    """Resolve any teacher string (e.g. 'I19', 'I01', 'Dr. Debesh Choudhury') into a clean short form.
    
    Guarantees that raw IDs like I01, I02... I19, E02 are NEVER returned.
    """
    t_val = str(teacher_val or "").strip()
    is_opaque = bool(re.match(r"^[IE]\d+$", t_val, re.IGNORECASE))

    # If it's already a clean short form e.g. "SK", "NC", "SKS", "RD", "DC"
    if t_val and not is_opaque and len(t_val) <= 5 and t_val.isupper():
        return t_val

    # 1. Check if subject has an authoritative known teacher short form
    sub_key = (subject_name or "").strip().lower()
    if sub_key in KNOWN_SUBJECT_MAP:
        return KNOWN_SUBJECT_MAP[sub_key]

    # 2. Check if DB has a matching teacher
    if db is not None:
        try:
            if not is_opaque and t_val:
                db_t = db.query(Teacher).filter(
                    (func.lower(Teacher.short_name) == t_val.lower()) |
                    (func.lower(Teacher.full_name) == t_val.lower())
                ).first()
                if db_t:
                    return db_t.short_name

            # Check if subject code matches a teacher's subjects_csv
            if subject_code:
                s_code = subject_code.strip().lower()
                all_t = db.query(Teacher).all()
                for t in all_t:
                    codes = [c.strip() for c in t.subjects_csv.split(",") if c.strip()]
                    if s_code in codes:
                        return t.short_name
        except Exception:
            pass

    # 3. Check demo mapping for I01..I19, E01..E04
    if is_opaque and t_val.upper() in DEMO_ID_MAP:
        return DEMO_ID_MAP[t_val.upper()]

    # 4. If full name is provided, derive initials
    if t_val and not is_opaque:
        return derive_teacher_short_form(t_val)

    return t_val or "TCH"
