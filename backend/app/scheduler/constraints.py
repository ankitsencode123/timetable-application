"""
Authoritative constraints, parsed structures, and subject catalogues.

All data is derived from scheduler/data.py strings — never invented.
Preserved verbatim from the original timetable_app.py.
"""
from __future__ import annotations
import re
from app.scheduler.data import FACULTY_MASTER_MD, ROOM_EXAMPLE_MD

def normalize_program(p: str | None) -> str:
    if not p:
        return ""
    s = str(p).strip()
    while s.endswith("."):
        s = s[:-1].strip()
    s_lower = s.lower().replace(" ", "")
    if s_lower in ("b.tech", "btech"):
        return "B.Tech"
    if s_lower in ("m.tech", "mtech"):
        return "M.Tech"
    if s_lower in ("m.sc", "msc"):
        return "M.Sc"
    return s

SCHEDULE_REQUIRED_KEYS = {
    "day", "program", "semester", "start", "end",
    "subject_code", "subject_name", "teacher", "type", "room"
}
VALID_DAYS    = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}
VALID_TYPES   = {"Theory", "Practical"}
VALID_PROGRAMS = {"B.Tech", "M.Tech", "M.Sc"}
WORKING_DAYS  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# ── H11: Authoritative per-semester subject catalogue ────────────────────────
PROGRAMME_SUBJECT_MAP: dict = {
    # ── B.Tech. Semester III (PCC CS-301/302/303/304, MC CS-308) ──────────────
    ("B.Tech", "3rd"): {
        "dm",               # Discrete Mathematics           PCC CS-301
        "ds", "ds-p",       # Data Structure + Lab           PCC CS-302
        "dl", "dl-p",       # Digital Logic + DL&MP Lab      PCC CS-303
        "mm", "m",          # Microprocessor & Microcontroller  PCC CS-304
        "pst",              # Problem Solving Techniques
        "evs",              # Environmental Science          MC CS-308
    },
    # ── B.Tech. Semester V (PCC CS-501/502/503/504) ───────────────────────────
    ("B.Tech", "5th"): {
        "cn", "cn-p",       # Computer Networks + Lab        PCC CS-501
        "dbms", "dbms-p",   # Database Mgmt Systems + Lab    PCC CS-502
        "se", "se-p",       # Software Engineering + Lab     PCC CS-503
        "aiml", "aiml-p",   # AI & Machine Learning + Lab    PCC CS-504
    },
    # ── B.Tech. Semester VII (CSEL7X1/7X2) ───────────────────────────────────
    ("B.Tech", "7th"): {
        "cd", "cd-p",       # Compiler Design + Lab          CSEL7X1
        "agt", "agt-p",     # Algorithmic Graph Theory + Lab CSEL7X2
        "idm", "idm-p",     # Introduction to Data Mining + Lab
    },
    # ── M.Sc. Semester I (CSE101/102/103/104/105) ─────────────────────────────
    ("M.Sc", "1st"): {
        "cd",               # Compiler Design                CSE101
        "cg", "cg-p",       # Computer Graphics + Lab        CSE102
        "adbms", "adbms-p", # Advance Database Mgmt + Lab    CSE103
        "aiml", "aiml-p",   # AI & Machine Learning + Lab    CSE104
        "es&iot", "es&iot-p",  # Embedded Systems & IoT + Lab  CSE105
    },
    # ── M.Sc. Semester III (CSMC301/304) ─────────────────────────────────────
    ("M.Sc", "3rd"): {
        "ippr", "ippr-p",   # Image Processing & Pattern Recognition + Lab
        "ai", "ai-p",       # Artificial Intelligence + Lab
    },
    # ── M.Tech. Semester I (CSE901/902/903, CSA90202, CSA901) ────────────────
    ("M.Tech", "1st"): {
        "aa", "aa-p",                           # Advanced Algorithms (PB)
        "mfcs",                                  # Mathematical Foundations  CSE901
        "pe-1:sc", "pe-1:sc-p", "sc", "sc-p",   # Soft Computing + Lab  CSE902
        "wmc",                                   # Wireless & Mobile Computing  CSE903
        "erpw",                                  # English for Research Paper Writing
        "rm",                                    # Research Methodology  CSA901
    },
    # ── M.Tech. Semester III ─────────────────────────────────────────────────
    ("M.Tech", "3rd"): {
        "bioinformatics",   # Bioinformatics
        "vlsi",             # VLSI Design
        "deeplearning",     # Deep Learning
    },
}

# ── H4: Room facilities (parsed from ROOM_EXAMPLE_MD) ─────────────────────────
def _parse_room_facilities() -> dict:
    rooms = {}
    for line in ROOM_EXAMPLE_MD.strip().splitlines():
        if not line.startswith("|") or "Room" in line or "---" in line or "EXAMPLE" in line:
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) < 4:
            continue
        rooms[parts[0]] = {
            "blackboard": parts[1].lower() == "yes",
            "projector":  parts[2].lower() == "yes",
            "lab":        parts[3].lower() == "yes",
        }
    return rooms

ROOM_FACILITIES: dict = _parse_room_facilities()

# ── H6: Authoritative internal faculty list ───────────────────────────────────
# These are the 7 permanent/full-time internal faculty.
# Short codes must match the teacher codes used in the timetable entries.
# External/visiting faculty are NOT in this set and do not require a free day.
INTERNAL_TEACHERS: set = {
    "SKS",   # Sanjit Kumar Setua
    "RKP",   # Rajat Kumar Pal
    "SCh",   # Sankhayan Choudhury
    "NC",    # Nabendu Chaki
    "RD",    # Rajib Kumar Das
    "PB",    # Pritha Banerjee
    "SK",    # Sunirmal Khatua
}



# ── H7: Subject-code alias map for fuzzy teacher-subject matching ──────────────
SUBJECT_CODE_ALIASES: dict[str, list[str]] = {
    "evs": ["environmental"], "dl": ["digital logic"], "dl-p": ["digital logic"],
    "ds": ["data structure"], "ds-p": ["data structure"],
    "mm": ["microprocessor"], "m": ["microprocessor"],
    "dm": ["discrete mathematics"],
    "aiml": ["machine learning", "artificial intelligence"],
    "aiml-p": ["machine learning", "artificial intelligence"],
    "agt": ["algorithmic graph"], "agt-p": ["algorithmic graph"],
    "idm": ["data mining"], "idm-p": ["data mining"],
    "cd": ["compiler design"], "cd-p": ["compiler design"],
    "cg": ["computer graphics"], "cg-p": ["computer graphics"],
    "dbms": ["database", "dbms"], "dbms-p": ["database", "dbms"],
    "cn": ["computer networks"], "cn-p": ["computer networks"],
    "se": ["software engineering"], "se-p": ["software engineering"],
    "wmc": ["wireless", "mobile computing"],
    "es&iot": ["internet of things", "environment"],
    "es&iot-p": ["internet of things", "environment"],
    "adbms": ["advance dbms", "advanced"], "adbms-p": ["advance dbms", "advanced"],
    "aa": ["algorithm"], "aa-p": ["algorithm"],
    "mfcs": ["mathematical foundations", "mathematical"],
    "erpw": ["research paper writing", "english for research"],
    "rm": ["research methodology"],
    "ippr": ["image processing", "pattern recognition"],
    "ippr-p": ["image processing", "pattern recognition"],
    "ai": ["artificial intelligence"], "ai-p": ["artificial intelligence"],
    "vlsi": ["vlsi"], "pst": ["problem solving"],
    "pe-1:sc": ["soft computing"], "pe-1:sc-p": ["soft computing"],
    "sc": ["soft computing"], "sc-p": ["soft computing"],
    "deeplearning": ["deep learning"], "bioinformatics": ["bioinformatics"],
}


# ── Dynamic catalog reload ────────────────────────────────────────────────────
def reload_catalog(db=None) -> None:
    """Merge DB subjects/teachers into in-memory constraint maps.
    Safe to call with db=None (no-op for import-time safety).
    """
    global PROGRAMME_SUBJECT_MAP, INTERNAL_TEACHERS, VALID_PROGRAMS  # noqa: PLW0603

    if db is None:
        return

    try:
        from app.models.subject import Subject
        from app.models.teacher import Teacher
        from app.models.program import Program

        for subj in db.query(Subject).all():
            prog_raw = str(subj.program).strip().rstrip('.')
            key = (prog_raw, subj.semester)
            PROGRAMME_SUBJECT_MAP.setdefault(key, set())
            code = subj.code.strip().lower()
            PROGRAMME_SUBJECT_MAP[key].add(code)
            if not code.endswith("-p") and subj.entry_type in ("Practical", "Both"):
                PROGRAMME_SUBJECT_MAP[key].add(f"{code}-p")

        for prog in db.query(Program).filter_by(is_active=True).all():
            VALID_PROGRAMS.add(prog.name)

        for t in db.query(Teacher).filter_by(is_internal=True).all():
            INTERNAL_TEACHERS.add(t.short_name)

    except Exception:
        pass
