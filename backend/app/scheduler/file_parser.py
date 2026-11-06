"""Robust file parsers for extracting timetable entities from user-uploaded CSV / TSV / Text files."""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple


def _normalize_header(h: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(h).strip().lower())


def _parse_slot_id(val: Any) -> int:
    """Map flexible time representations (1, 10-12, 10:00-12:00, 12-2, 2:30-4:30, etc.) to slot IDs 1, 2, 3."""
    s = str(val).strip().lower().replace(" ", "")
    if not s:
        return 1
    if s in ("1", "slot1", "s1"):
        return 1
    if s in ("2", "slot2", "s2"):
        return 2
    if s in ("3", "slot3", "s3"):
        return 3
    if s in ("4", "slot4", "s4"):
        return 4
    if s in ("5", "slot5", "s5"):
        return 5

    # Match time strings
    if any(k in s for k in ("10-12", "10:00-12:00", "10:00", "10to12", "slot1")):
        return 1
    if any(k in s for k in ("12-2", "12-14", "12:00-14:00", "12:00", "12to2", "slot2")):
        return 2
    if any(k in s for k in ("2:30-4:30", "14:30-16:30", "14:30", "2:30", "2.30", "slot3")):
        return 3
    if any(k in s for k in ("8-10", "08:00-10:00", "8:00", "08:00", "slot4")):
        return 4
    if any(k in s for k in ("4:30-6:30", "16:30-18:30", "16:30", "4:30", "slot5")):
        return 5

    # If first digit matches 1, 2, 3
    m = re.search(r'\b([1-5])\b', s)
    if m:
        return int(m.group(1))

    return 1


def _normalize_facility(val: Any) -> str:
    s = str(val).strip().lower()
    if not s or s in ("none", "any", "no"):
        return "none"
    s_clean = s.replace("&", "and").replace("+", "and").replace("-", " ")
    if "black" in s_clean and "proj" in s_clean:
        return "both"
    if "both" in s_clean:
        return "both"
    if "proj" in s_clean:
        return "projector"
    if "black" in s_clean or "board" in s_clean or "bb" in s_clean:
        return "blackboard"
    return "none"


def _normalize_day(val: Any) -> str:
    s = str(val).strip()
    if not s:
        return "Mon"
    key = s[:3].capitalize()
    day_map = {
        "Mon": "Mon", "Tue": "Tue", "Wed": "Wed", "Thu": "Thu", "Fri": "Fri", "Sat": "Sat",
        "Sun": "Sun",
    }
    return day_map.get(key, "Mon")


def _extract_semester_info(val: Any) -> Tuple[str, str, int]:
    """Given 'BT-3', 'B.Tech 3rd', 'B.Tech Sem 3', 'M.Tech 1st', etc., return (semester_id, programme, number)."""
    s = str(val).strip()
    if not s:
        return "SEM-1", "B.Tech", 1

    # Check for direct ID like BT-3, MT-1, MSC-2
    m_direct = re.match(r'^([a-zA-Z]+)[-_ ]?(\d+)$', s)
    if m_direct:
        prefix = m_direct.group(1).upper()
        num = int(m_direct.group(2))
        prog_map = {"BT": "B.Tech", "MT": "M.Tech", "MSC": "M.Sc.", "MS": "M.Sc.", "BSC": "B.Sc."}
        prog = prog_map.get(prefix, f"{prefix}")
        return f"{prefix}-{num}", prog, num

    # Search for number
    m_num = re.search(r'(\d+)', s)
    num = int(m_num.group(1)) if m_num else 1

    s_lower = s.lower().replace(" ", "").replace(".", "")
    if "btech" in s_lower or "bt" in s_lower:
        return f"BT-{num}", "B.Tech", num
    if "mtech" in s_lower or "mt" in s_lower:
        return f"MT-{num}", "M.Tech", num
    if "msc" in s_lower or "ms" in s_lower:
        return f"MSC-{num}", "M.Sc.", num
    if "bsc" in s_lower:
        return f"BSC-{num}", "B.Sc.", num

    return f"S-{num}", s, num


def _read_rows(file_content: str) -> List[List[str]]:
    """Tolerantly parse CSV, TSV, pipe-delimited table, or semicolon-delimited rows."""
    text = file_content.strip()
    if not text:
        return []

    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return []

    # Detect delimiter: comma, tab, pipe, semicolon
    first_line = lines[0]
    if "\t" in first_line:
        delimiter = "\t"
    elif "|" in first_line:
        delimiter = "|"
    elif ";" in first_line and "," not in first_line:
        delimiter = ";"
    else:
        delimiter = ","

    rows = []
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    for r in reader:
        cleaned = [c.strip() for c in r if c.strip() != ""]
        if cleaned:
            # If line is Markdown separator |---|---| ignore it
            if all(re.match(r'^-+$', col) for col in cleaned):
                continue
            rows.append([c.strip() for c in r])
    return rows


def parse_subjects_file(file_content: str) -> Dict[str, Any]:
    """Parse subject allocations file containing subject code, name, semester, teacher ID (internal and external)."""
    rows = _read_rows(file_content)
    if not rows:
        return {"semesters": [], "subjects": [], "teachers": []}

    header = [_normalize_header(c) for c in rows[0]]
    has_header = any(k in "".join(header) for k in ("subject", "code", "semester", "sem", "teacher", "faculty"))

    data_rows = rows[1:] if has_header else rows
    if has_header:
        col_map = {h: i for i, h in enumerate(header)}
    else:
        col_map = {}

    def get_val(row: List[str], keys: Tuple[str, ...], default_idx: int) -> str:
        for k in keys:
            norm_k = _normalize_header(k)
            for h, idx in col_map.items():
                if norm_k in h and idx < len(row):
                    return row[idx].strip()
        if 0 <= default_idx < len(row):
            return row[default_idx].strip()
        return ""

    semesters_map: Dict[str, Dict[str, Any]] = {}
    subjects: List[Dict[str, Any]] = []
    discovered_teachers: Set[str] = set()

    for row in data_rows:
        if not any(row):
            continue

        code = get_val(row, ("subjectcode", "code", "subjectid", "subject", "paper"), 0).strip().upper()
        if not code:
            continue

        name = get_val(row, ("subjectname", "name", "title", "subject"), 1).strip() or code
        sem_raw = get_val(row, ("semesterid", "semester", "sem", "class", "programsemester"), 2).strip() or "BT-3"

        sem_id, prog, sem_num = _extract_semester_info(sem_raw)
        if sem_id not in semesters_map:
            semesters_map[sem_id] = {
                "id": sem_id,
                "programme": prog,
                "number": sem_num,
                "name": f"{prog} Sem {sem_num}",
                "strength": None,
            }

        internal_teacher = get_val(row, ("internalteacherid", "internalteacher", "teacherid", "teacher", "internal", "faculty"), 3).strip()
        external_teacher = get_val(row, ("externalteacherid", "externalteacher", "external", "visiting"), 4).strip() or None

        if internal_teacher:
            discovered_teachers.add(internal_teacher)
        if external_teacher:
            discovered_teachers.add(external_teacher)

        # Type detection
        raw_type = get_val(row, ("classtype", "type", "entrytype"), 5).lower()
        is_lab = any(k in raw_type for k in ("p", "practical", "lab")) or code.endswith("-P") or "lab" in name.lower()
        class_type = "practical" if is_lab else "theory"

        # Sessions
        raw_sessions = get_val(row, ("sessionsperweek", "sessions", "weeklyhours", "hours", "classes"), 6)
        if raw_sessions:
            try:
                sessions = float(raw_sessions)
            except ValueError:
                sessions = 1.0 if is_lab else 3.0
        else:
            sessions = 1.0 if is_lab else 3.0

        # External sessions
        raw_ext_sessions = get_val(row, ("externalsessions", "extsessions"), 7)
        if raw_ext_sessions:
            try:
                ext_sessions = float(raw_ext_sessions)
            except ValueError:
                ext_sessions = 1.0 if external_teacher else 0.0
        else:
            ext_sessions = 1.0 if external_teacher else 0.0

        # Facility
        raw_fac = get_val(row, ("facility", "requirement", "equipment", "roomfacility"), 8)
        facility = _normalize_facility(raw_fac)

        subjects.append({
            "code": code,
            "name": name,
            "semester_id": sem_id,
            "class_type": class_type,
            "internal_teacher_id": internal_teacher or None,
            "external_teacher_id": external_teacher or None,
            "sessions_per_week": sessions,
            "external_sessions": ext_sessions if external_teacher else None,
            "facility": facility,
        })

    return {
        "semesters": list(semesters_map.values()),
        "subjects": subjects,
        "teachers": [{"id": tid, "name": tid, "internal": not tid.upper().startswith("E"), "preferences": [], "unavailable": [], "unavailable_days": []} for tid in sorted(discovered_teachers)],
    }


def parse_teachers_file(file_content: str) -> List[Dict[str, Any]]:
    """Parse teacher profile file containing Teacher ID, Name, and up to 3 preferred time slots
    (day, time, black board, projector, blackboard and projector).
    Slots: 10-12:00, 12-2, 2:30-4:30.
    """
    rows = _read_rows(file_content)
    if not rows:
        return []

    header = [_normalize_header(c) for c in rows[0]]
    has_header = any(k in "".join(header) for k in ("teacher", "id", "name", "pref", "slot", "facility", "day"))

    data_rows = rows[1:] if has_header else rows
    if has_header:
        col_map = {h: i for i, h in enumerate(header)}
    else:
        col_map = {}

    def get_val(row: List[str], keys: Tuple[str, ...], default_idx: int) -> str:
        for k in keys:
            norm_k = _normalize_header(k)
            for h, idx in col_map.items():
                if norm_k in h and idx < len(row):
                    return row[idx].strip()
        if 0 <= default_idx < len(row):
            return row[default_idx].strip()
        return ""

    teachers: List[Dict[str, Any]] = []

    for row in data_rows:
        if not any(row):
            continue

        tid = get_val(row, ("teacherid", "id", "shortname", "code"), 0).strip()
        if not tid:
            continue

        name = get_val(row, ("teachername", "name", "fullname", "professor"), 1).strip() or tid

        # Check internal/external
        raw_role = get_val(row, ("role", "type", "internal", "status"), -1).lower()
        if raw_role:
            is_internal = not ("ext" in raw_role or "visit" in raw_role or raw_role == "false" or raw_role == "0")
        else:
            is_internal = not (tid.upper().startswith("E") or "(EXT)" in name.upper() or "(VISIT" in name.upper())

        preferences = []

        # Parse preferences:
        # Check if individual pref columns exist: pref1_day, pref1_time, pref1_facility etc.
        for pref_idx in (1, 2, 3):
            p_day = get_val(row, (f"pref{pref_idx}day", f"p{pref_idx}day", f"day{pref_idx}"), -1)
            p_time = get_val(row, (f"pref{pref_idx}time", f"pref{pref_idx}slot", f"p{pref_idx}slot", f"p{pref_idx}time", f"time{pref_idx}", f"slot{pref_idx}"), -1)
            p_fac = get_val(row, (f"pref{pref_idx}facility", f"p{pref_idx}facility", f"facility{pref_idx}"), -1)

            if p_day and (p_time or p_fac):
                preferences.append({
                    "day": _normalize_day(p_day),
                    "slot": _parse_slot_id(p_time),
                    "facility": _normalize_facility(p_fac),
                })
            else:
                # Check for single preference string e.g. "Mon 10-12 projector"
                single_p = get_val(row, (f"pref{pref_idx}", f"preference{pref_idx}", f"p{pref_idx}"), -1)
                if single_p:
                    parsed_p = _parse_preference_string(single_p)
                    if parsed_p:
                        preferences.append(parsed_p)

        # If no preferences found in named columns, try positional parsing from columns 2..
        if not preferences and len(row) > 2:
            idx = 2
            while idx < len(row) and len(preferences) < 3:
                val = row[idx].strip()
                if not val:
                    idx += 1
                    continue
                # If column has 3 items in itself e.g. "Mon, 1, both" or "Wed 12-2 projector"
                parsed_p = _parse_preference_string(val)
                if parsed_p:
                    preferences.append(parsed_p)
                    idx += 1
                elif idx + 2 < len(row):
                    # 3 consecutive columns: day, time/slot, facility
                    p_day = _normalize_day(row[idx])
                    p_slot = _parse_slot_id(row[idx + 1])
                    p_fac = _normalize_facility(row[idx + 2])
                    preferences.append({"day": p_day, "slot": p_slot, "facility": p_fac})
                    idx += 3
                else:
                    idx += 1

        # Check unavailable days
        unavail_raw = get_val(row, ("unavailable", "unavailabledays", "busy", "busydays", "freeday"), -1)
        unavail_days = []
        if unavail_raw:
            for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat"):
                if d.lower() in unavail_raw.lower():
                    unavail_days.append(d)

        teachers.append({
            "id": tid,
            "name": name,
            "internal": is_internal,
            "preferences": preferences,
            "unavailable": [],
            "unavailable_days": unavail_days,
        })

    return teachers


def _parse_preference_string(s: str) -> Optional[Dict[str, Any]]:
    """Parse a string like 'Mon 10-12:00 blackboard and projector' or 'Wed, 2, projector'."""
    text = str(s).strip()
    if not text or text.lower() in ("none", "-", "n/a"):
        return None

    # Detect day
    day = "Mon"
    for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"):
        if re.search(r'\b' + d + r'\b', text, re.IGNORECASE):
            day = _normalize_day(d)
            break

    # Detect slot
    slot = _parse_slot_id(text)

    # Detect facility
    facility = _normalize_facility(text)

    return {"day": day, "slot": slot, "facility": facility}
