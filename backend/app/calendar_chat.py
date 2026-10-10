"""
calendar_chat.py  —  Date-aware timetable CHATBOT (single file, additive)
=========================================================================

Answers questions such as
    "What classes do I have on 4th of October?"
    "What classes do I have next month and what holidays do I have?"
    "Is 15 August a holiday?"          "Any cancelled classes this week?"
    "What's my next class?"            "B.Tech 3rd sem classes tomorrow morning"
    "Classes of Dr. Sharma from 12 to 16 Oct"      "When is Data Structures this week?"

DESIGN (why it is accurate AND cheap on a free-tier LLM)
--------------------------------------------------------
* The LLM NEVER writes the answer.  Every class / holiday / cancellation shown is
  read from calendar_system (_load_ctx + _resolve_day) — i.e. exactly what
  /calendar/day/{date} returns (base timetable + validity windows + day overrides).
  Nothing can be hallucinated because nothing is generated.
* Understanding the question = 2 layers
    1. Deterministic parser (regex, zero cost, zero latency): dates, ranges,
       relative words, weekdays, months, intents, program / semester / teacher /
       subject / time-of-day.  Handles the vast majority of questions.
    2. LLM fallback (only when the parser sees something it cannot interpret, or on
       the dedicated endpoint when nothing was recognised).  The LLM only emits a
       tiny JSON "query plan"; Python validates every field and does ALL date
       arithmetic.  Free-tier protection: hard timeout, global per-minute limiter,
       LRU cache.  If the LLM is down/rate-limited the bot still works.
* Every answer states the exact resolved dates (with weekday) so the user can verify,
  and says when it assumed something (e.g. the year).

WIRING (nothing existing is modified)
-------------------------------------
1) app/api/router.py
       from app.calendar_chat import router as calendar_chat_router
       api_router.include_router(calendar_chat_router)
   -> POST /api/calendar-chat/public            (no auth, read-only)
      POST /api/calendar-chat/teacher           (auth; "I / my" = the logged-in teacher)

2) OPTIONAL: let your existing chat boxes use it automatically (3 lines each).
   The hook returns None for anything that is not a calendar question, so the old
   behaviour is untouched.  In app/services/chat_service.py:

       # top of handle_public_chat(...)
       from app.calendar_chat import try_handle_calendar_question
       r = try_handle_calendar_question(message, db)
       if r is not None:
           return r

       # top of handle_teacher_chat(...)
       from app.calendar_chat import try_handle_calendar_question
       r = try_handle_calendar_question(message, db, user=user)
       if r is not None:
           return r

   (Imperative requests such as "cancel my class on Monday" are always left to your
    ActionParser — the hook returns None for them.)

ENV (all optional)
------------------
CALENDAR_CHAT_LLM=1|0               enable LLM fallback (default 1)
CALENDAR_CHAT_LLM_TIMEOUT=25        seconds
CALENDAR_CHAT_LLM_PER_MIN=12        global cap on LLM calls per minute (free tier)
CALENDAR_CHAT_TIME_FORMAT=12h|24h   default 12h
CALENDAR_CHAT_TEACHER_ALIASES='{"teacher@x.com": ["Dr. Anil Sharma"], "17": ["AS"]}'
        -> pin a login (email or user id) to the name used in the timetable
        (only needed if names in the User table differ from the timetable).

NOTE: this file imports a few "private" helpers from app.calendar_system
(_load_ctx, _resolve_day, _today, _pkey).  They are stable in that file; if you
rename them, adjust the import block below.
"""
from __future__ import annotations

import calendar as _pycal
import datetime as dt
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_user                                          ###
from app.core.database import get_db                                               ###
from app.models.user import User                                                   ###
from app.schemas.chat import ChatRequest, ChatResponse                             ###
from app.calendar_system import (                                                  ###
    CAL_TZ_NAME, MAX_DATE, META, MIN_DATE, SEMESTERS, WEEKDAYS,
    _load_ctx, _pkey, _resolve_day, _today,
)
from app.scheduler.constraints import PROGRAMME_SUBJECT_MAP
from app.scheduler.validator import teacher_set

log = logging.getLogger("calendar_chat")

# ═════════════════════════════════════════════════════════════════════════════
# 0. Configuration
# ═════════════════════════════════════════════════════════════════════════════
def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


LLM_ENABLED = _env_bool("CALENDAR_CHAT_LLM", True)
LLM_TIMEOUT_S = float(os.getenv("CALENDAR_CHAT_LLM_TIMEOUT", "25"))
LLM_MAX_PER_MIN = int(os.getenv("CALENDAR_CHAT_LLM_PER_MIN", "12"))
TIME_FORMAT = os.getenv("CALENDAR_CHAT_TIME_FORMAT", "12h").strip().lower()

MAX_MESSAGE_CHARS = 600
MAX_DETAIL_DAYS = 93            # schedule / changes sections
MAX_HOLIDAY_DAYS = 366          # holidays-only questions
DEFAULT_HOLIDAY_DAYS = 90
DEFAULT_CHANGES_DAYS = 30
DEFAULT_SUBJECT_DAYS = 14
NEXT_CLASS_LOOKAHEAD = 21
MAX_LISTED_ENTRIES = 120        # beyond this we show per-day counts instead of every class
MAX_SPANS = 12

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

HELP_TEXT = (
    "I can answer questions about the timetable calendar — classes, holidays, cancellations "
    "and changes — for any date or period. For example:\n"
    "- What classes do I have on 4th October?\n"
    "- Show my classes next month and the holidays\n"
    "- Is 15 August a holiday?\n"
    "- Any cancelled or rescheduled classes this week?\n"
    "- What's my next class?\n"
    "- B.Tech 3rd sem classes tomorrow morning\n"
    "- Classes of Dr. Sharma from 12 to 16 October"
)
ACTION_TEXT = (
    "That sounds like a request to change the timetable. This chat only answers questions "
    "about it — please use the schedule-actions chat to make changes."
)


class ParseError(Exception):
    """A user-facing problem with the question (invalid date, range too big, ...)."""


# ═════════════════════════════════════════════════════════════════════════════
# 1. Lexicon
# ═════════════════════════════════════════════════════════════════════════════
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_WD = {
    "monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15,
         "twenty": 20, "thirty": 30}
_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_WDX = "(?:" + "|".join(sorted(_WD, key=len, reverse=True)) + ")"
_WD_FULL = "(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_NUM = r"(?:\d{1,3}|" + "|".join(sorted(_NUMW, key=len, reverse=True)) + ")"
_ORD = r"(?:st|nd|rd|th)"


def _num(s: str) -> int:
    return int(s) if s.isdigit() else _NUMW[s]


# ═════════════════════════════════════════════════════════════════════════════
# 2. Date formatting + span resolution
# ═════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Span:
    start: dt.date
    end: dt.date

    def days(self) -> int:
        return (self.end - self.start).days + 1


def fmt_date(d: dt.date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTH_NAMES[d.month]} {d.year}"


def _fmt_short(d: dt.date, with_year: bool = False) -> str:
    s = f"{WEEKDAYS[d.weekday()][:3]}, {d.day} {MONTH_NAMES[d.month][:3]}"
    return f"{s} {d.year}" if with_year else s


def fmt_range(a: dt.date, b: dt.date) -> str:
    if a == b:
        return fmt_date(a)
    return f"{_fmt_short(a, a.year != b.year)} – {fmt_date(b)}"


def _spans_label(spans: list) -> str:
    return "; ".join(fmt_range(s.start, s.end) for s in spans)


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    m += 1
    return dt.date(y, m, min(d.day, _pycal.monthrange(y, m)[1]))


def _mkdate(y: int, m: int, d: int) -> dt.date:
    try:
        out = dt.date(y, m, d)
    except (ValueError, OverflowError):
        mn = MONTH_NAMES[m] if 1 <= m <= 12 else str(m)
        raise ParseError(f"“{d} {mn} {y}” is not a valid date.")
    if not (MIN_DATE <= out <= MAX_DATE):
        raise ParseError(f"Dates must be between {MIN_DATE.year} and {MAX_DATE.year}.")
    return out


def _note(notes: list, msg: str) -> None:
    if msg not in notes:
        notes.append(msg)


def _upcoming_weekday(today: dt.date, wd: int, modifier: Optional[str]) -> dt.date:
    if modifier == "next":
        return today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=7 + wd)
    if modifier == "last":
        delta = (today.weekday() - wd) % 7 or 7
        return today - dt.timedelta(days=delta)
    return today + dt.timedelta(days=(wd - today.weekday()) % 7)


_RANGEABLE = {"date", "month", "weekday", "relative_day", "week"}


def _resolve_spec(spec: dict, today: dt.date, notes: list) -> list:
    """Turn a validated date-spec into concrete Span objects (ALL date arithmetic lives here)."""
    k = spec["kind"]
    if k == "multi":
        return [s for sp in spec["specs"] for s in _resolve_spec(sp, today, notes)]

    if k == "date":
        y = spec.get("year")
        if y is None:
            y = today.year
            _note(notes, f"You didn't give a year, so I assumed {y}.")
        d = _mkdate(y, spec["month"], spec["day"])
        if spec.get("ambig"):
            _note(notes, f"I read “{spec['ambig']}” as day/month (DD/MM) → {fmt_date(d)}.")
        hint = spec.get("weekday_hint")
        if hint is not None and d.weekday() != hint:
            _note(notes, f"{fmt_date(d)} is a {WEEKDAYS[d.weekday()]}, not a {WEEKDAYS[hint]}.")
        return [Span(d, d)]

    if k == "relative_day":
        d = today + dt.timedelta(days=spec["offset"])
        _mkdate(d.year, d.month, d.day)
        return [Span(d, d)]

    if k == "weekday":
        d = _upcoming_weekday(today, spec["weekday"], spec.get("modifier"))
        return [Span(d, d)]

    if k in ("week", "weekend"):
        monday = today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=7 * spec.get("offset", 0))
        if k == "week":
            return [Span(monday, monday + dt.timedelta(days=6))]
        return [Span(monday + dt.timedelta(days=5), monday + dt.timedelta(days=6))]

    if k == "rest_of_week":
        return [Span(today, today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=6))]

    if k == "rest_of_month":
        return [Span(today, today.replace(day=_pycal.monthrange(today.year, today.month)[1]))]

    if k == "month":
        m, mod, y = spec.get("month"), spec.get("modifier"), spec.get("year")
        if m is None:
            first = _add_months(today.replace(day=1), int(spec.get("offset") or 0))
        else:
            if mod == "next":
                y = today.year if m > today.month else today.year + 1
            elif mod == "last":
                y = today.year if m < today.month else today.year - 1
            elif y is None:
                y = today.year
                if mod != "this":
                    _note(notes, f"You didn't give a year, so I assumed {y}.")
            first = _mkdate(y, m, 1)
        last = first.replace(day=_pycal.monthrange(first.year, first.month)[1])
        return [Span(first, last)]

    if k == "year":
        y = spec["year"]
        return [Span(_mkdate(y, 1, 1), _mkdate(y, 12, 31))]

    if k == "next_days":
        n = int(spec["n"])
        return [Span(today, today + dt.timedelta(days=n - 1))]

    if k == "next_months":
        n = int(spec["n"])
        return [Span(today, _add_months(today, n) - dt.timedelta(days=1))]

    if k == "range":
        a = _resolve_spec(spec["start"], today, notes)[0]
        b = _resolve_end(spec["end"], today, notes, a.start)
        if b.end < a.start:
            raise ParseError("The end of that period comes before its start — could you rephrase it?")
        return [Span(a.start, b.end)]

    raise ParseError("I couldn't understand that date.")


def _resolve_end(spec: dict, today: dt.date, notes: list, not_before: dt.date) -> Span:
    """End of a range: roll weekdays / yearless dates forward so 'Mon to Fri', '20 Dec to 5 Jan' work."""
    k = spec["kind"]
    if k == "weekday" and spec.get("modifier") is None:
        d = _upcoming_weekday(today, spec["weekday"], None)
        while d < not_before:
            d += dt.timedelta(days=7)
        return Span(d, d)
    if k == "date" and spec.get("year") is None:
        for y in (not_before.year, not_before.year + 1):
            d = _mkdate(y, spec["month"], spec["day"])
            if d >= not_before:
                _note(notes, f"You didn't give a year, so I assumed {not_before.year}"
                      + (f"/{y}" if y != not_before.year else "") + ".")
                return Span(d, d)
        raise ParseError("The end of that period comes before its start.")
    if k == "month" and spec.get("month") and spec.get("year") is None and not spec.get("modifier"):
        for y in (not_before.year, not_before.year + 1):
            first = _mkdate(y, spec["month"], 1)
            last = first.replace(day=_pycal.monthrange(y, spec["month"])[1])
            if last >= not_before:
                _note(notes, f"You didn't give a year, so I assumed {not_before.year}"
                      + (f"/{y}" if y != not_before.year else "") + ".")
                return Span(first, last)
    return _resolve_spec(spec, today, notes)[-1]


def _merge_spans(spans: list) -> list:
    out: list = []
    for s in sorted(spans, key=lambda x: (x.start, x.end)):
        if out and s.start <= out[-1].end + dt.timedelta(days=1):
            if s.end > out[-1].end:
                out[-1] = Span(out[-1].start, s.end)
        else:
            out.append(s)
    return out


def _dates_of(spans: list) -> list:
    out = []
    for s in spans:
        d = s.start
        while d <= s.end:
            out.append(d)
            d += dt.timedelta(days=1)
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 3. Deterministic date scanner (text -> date-specs)
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class Mention:
    start: int
    end: int
    spec: dict


_MOD_WEEK = {"this": 0, "current": 0, "next": 1, "coming": 1, "upcoming": 1, "following": 1,
             "last": -1, "previous": -1, "past": -1}


def _h_range_dm(m, t):
    d1, d2, mon, yr = m.group(1), m.group(2), m.group(3), m.group(4)
    month = _MONTHS[mon]
    year = int(yr) if yr else None
    return {"kind": "range", "start": {"kind": "date", "day": int(d1), "month": month, "year": year},
            "end": {"kind": "date", "day": int(d2), "month": month, "year": year}}


def _h_list_dm(m, t):
    nums = [int(x) for x in re.findall(r"\d{1,2}", m.group(1))]
    month = _MONTHS[m.group(2)]
    year = int(m.group(3)) if m.group(3) else None
    specs = [{"kind": "date", "day": n, "month": month, "year": year} for n in nums]
    if len(specs) == 2 and re.search(r"\bbetween\s*$", t[:m.start()]):
        return {"kind": "range", "start": specs[0], "end": specs[1]}
    return {"kind": "multi", "specs": specs}


def _h_iso(m, t):
    return {"kind": "date", "year": int(m.group(1)), "month": int(m.group(2)), "day": int(m.group(3))}


def _h_dmy_num(m, t):
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    year = int(y) if len(y) == 4 else 2000 + int(y)
    ambig = d <= 12 and mo <= 12 and d != mo
    if mo > 12 and d <= 12:
        d, mo = mo, d
    return {"kind": "date", "day": d, "month": mo, "year": year, "ambig": m.group(0) if ambig else None}


def _h_dm_slash(m, t):
    d, mo = int(m.group(1)), int(m.group(2))
    ambig = d <= 12 and mo <= 12 and d != mo
    if mo > 12 and d <= 12:
        d, mo = mo, d
    return {"kind": "date", "day": d, "month": mo, "year": None, "ambig": m.group(0) if ambig else None}


def _h_dmy_txt(m, t):
    year = int(m.group(3)) if m.group(3) else None
    if year is not None and not (2000 <= year <= 2100):
        year = None
    return {"kind": "date", "day": int(m.group(1)), "month": _MONTHS[m.group(2)], "year": year}


def _h_mdy_txt(m, t):
    year = int(m.group(3)) if m.group(3) else None
    return {"kind": "date", "day": int(m.group(2)), "month": _MONTHS[m.group(1)], "year": year}


def _h_rel(offset):
    return lambda m, t: {"kind": "relative_day", "offset": offset}


def _h_in_n(m, t):
    n, unit = _num(m.group(2)), m.group(3)
    n = n * 7 if unit.startswith("week") else n
    if m.group(1) == "within":
        return {"kind": "next_days", "n": max(1, n)}
    return {"kind": "relative_day", "offset": n}


def _h_ago(m, t):
    n = _num(m.group(1))
    return {"kind": "relative_day", "offset": -(n * 7 if m.group(2).startswith("week") else n)}


def _h_next_n(m, t):
    n, unit = _num(m.group(1)), m.group(2)
    if unit.startswith("month"):
        return {"kind": "next_months", "n": n}
    return {"kind": "next_days", "n": n * 7 if unit.startswith("week") else n}


def _h_rest(m, t):
    return {"kind": "rest_of_week" if m.group(1) == "week" else "rest_of_month"}


def _h_week(m, t):
    return {"kind": "week", "offset": _MOD_WEEK[m.group(1)]}


def _h_weekend(m, t):
    return {"kind": "weekend", "offset": _MOD_WEEK.get(m.group(1) or "this", 0)}


def _h_rel_month(m, t):
    return {"kind": "month", "month": None, "offset": _MOD_WEEK[m.group(1)]}


def _h_mod_monthname(m, t):
    if m.group(2) == "may":
        return None
    mod = {"this": "this", "next": "next", "coming": "next", "upcoming": "next",
           "last": "last", "previous": "last"}[m.group(1)]
    return {"kind": "month", "month": _MONTHS[m.group(2)], "year": None, "modifier": mod}


def _h_weekday(m, t):
    mod = {"this": "this", "next": "next", "last": "last", "previous": "last"}.get(m.group(1))
    return {"kind": "weekday", "weekday": _WD[m.group(2)], "modifier": mod}


def _h_month_only(m, t):
    name = m.group(1)
    if name == "may":
        before = t[:m.start()].rstrip()
        after = t[m.end():]
        ok_before = re.search(r"(?:\b(?:in|during|for|of|from|to|till|until|through|between|and|or|by)|month of)$", before)
        ok_after = re.match(r"\s*(?:month\b|,?\s*\d{4}\b)", after)
        if not (ok_before or ok_after):
            return None
    year = int(m.group(2)) if m.group(2) else None
    if year is not None and not (2000 <= year <= 2100):
        year = None
    return {"kind": "month", "month": _MONTHS[name], "year": year, "modifier": None}


def _h_year(m, t):
    return {"kind": "year", "year": int(m.group(1))}


_DATE_PATTERNS = [
    (re.compile(rf"(?<![\d/.])(\d{{1,2}}){_ORD}?\s*(?:-|to|till|until|through|thru)\s*(\d{{1,2}}){_ORD}?\s*(?:of\s+)?({_MONTH})\b\.?(?:\s*,?\s*(\d{{4}}))?(?!\d)"), _h_range_dm),
    (re.compile(rf"(?<![\d/.])((?:\d{{1,2}}{_ORD}?\s*(?:,|and|&)\s*)+\d{{1,2}}{_ORD}?)\s*(?:of\s+)?({_MONTH})\b\.?(?:\s*,?\s*(\d{{4}}))?(?!\d)"), _h_list_dm),
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), _h_iso),
    (re.compile(r"(?<![\d/.\-])(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?![\d/])"), _h_dmy_num),
    (re.compile(r"(?<![\d/.\-])(\d{1,2})/(\d{1,2})(?![\d/])"), _h_dm_slash),
    (re.compile(rf"(?<![\d/.])(\d{{1,2}}){_ORD}?\s*(?:of\s+)?({_MONTH})\b\.?(?:\s*,?\s*(\d{{4}}))?(?!\d)"), _h_dmy_txt),
    (re.compile(rf"\b({_MONTH})\b\.?\s*(\d{{1,2}}){_ORD}?(?!\d)(?:\s*,?\s*(\d{{4}}))?(?!\d)"), _h_mdy_txt),
    (re.compile(r"\bday\s+after\s+(?:tomorrow|tmrw|tmr)\b"), _h_rel(2)),
    (re.compile(r"\bday\s+before\s+yesterday\b"), _h_rel(-2)),
    (re.compile(rf"\b(in|after|within)\s+({_NUM})\s+(days?|weeks?)\b"), _h_in_n),
    (re.compile(rf"\b({_NUM})\s+(days?|weeks?)\s+ago\b"), _h_ago),
    (re.compile(rf"\b(?:next|coming|upcoming|following)\s+({_NUM})\s+(days?|weeks?|months?)\b"), _h_next_n),
    (re.compile(r"\b(?:rest|remainder)\s+of\s+(?:the\s+|this\s+)?(week|month)\b"), _h_rest),
    (re.compile(r"\b(?:tomorrow|tmrw|tmr|tommorow|tommorrow|tommowrow|tomara)\b"), _h_rel(1)),
    (re.compile(r"\byesterday\b"), _h_rel(-1)),
    (re.compile(r"\b(?:today|tonight)\b"), _h_rel(0)),
    (re.compile(r"\b(this|current|next|coming|upcoming|following|last|previous|past)\s+week\b"), _h_week),
    (re.compile(r"\b(?:(this|next|coming|upcoming|last|previous)\s+)?weekend\b"), _h_weekend),
    (re.compile(r"\b(this|current|next|coming|upcoming|following|last|previous|past)\s+month\b"), _h_rel_month),
    (re.compile(rf"\b(this|next|coming|upcoming|last|previous)\s+({_MONTH})\b"), _h_mod_monthname),
    (re.compile(rf"\b(?:(this|next|last|coming|upcoming|previous|following)\s+)?({_WDX})\b"), _h_weekday),
    (re.compile(rf"\b(?:month\s+of\s+)?({_MONTH})\b\.?(?:\s*,?\s*(\d{{4}})\b)?"), _h_month_only),
    (re.compile(r"(?<![\d/.\-:])(20\d{2})(?![\d/:])(?!\s*(?:-|to)\s*\d)"), _h_year),
]

_RANGE_GAP = re.compile(rf"\s*(?:to|till|until|through|thru|up\s*to|-)\s*(?:(?:the\s+)?{_WDX}\s*,?\s*(?:the\s+)?)?")
_LABEL_GAP = re.compile(r"\s*,?\s*(?:the\s+)?")


def _scan_dates(t: str) -> tuple:
    """Returns (mentions, text_with_consumed_parts_blanked)."""
    cur = t
    found: list = []
    for rx, handler in _DATE_PATTERNS:
        accepted = []
        for m in rx.finditer(cur):
            spec = handler(m, t)
            if spec is None:
                continue
            accepted.append(Mention(m.start(), m.end(), spec))
        for mm in accepted:
            cur = cur[:mm.start] + " " * (mm.end - mm.start) + cur[mm.end:]
        found.extend(accepted)
    found.sort(key=lambda x: x.start)

    # absorb "Monday, 5 October" style weekday labels into the date
    kept: list = []
    for i, m in enumerate(found):
        if m.spec["kind"] == "weekday" and m.spec.get("modifier") is None:
            nxt = found[i + 1] if i + 1 < len(found) else None
            prv = kept[-1] if kept else None
            if nxt and nxt.spec["kind"] == "date" and _LABEL_GAP.fullmatch(t[m.end:nxt.start]):
                nxt.spec["weekday_hint"] = m.spec["weekday"]
                continue
            if prv and prv.spec["kind"] == "date" and _LABEL_GAP.fullmatch(t[prv.end:m.start]):
                prv.spec["weekday_hint"] = m.spec["weekday"]
                continue
        kept.append(m)

    # merge "X to Y" / "between X and Y" into ranges
    out: list = []
    i = 0
    while i < len(kept):
        m = kept[i]
        if i + 1 < len(kept) and m.spec["kind"] in _RANGEABLE and kept[i + 1].spec["kind"] in _RANGEABLE:
            n = kept[i + 1]
            gap = t[m.end:n.start]
            if _RANGE_GAP.fullmatch(gap) or (re.fullmatch(r"\s*(?:and|&)\s*", gap)
                                             and re.search(r"\bbetween\s*$", t[:m.start])):
                out.append(Mention(m.start, n.end, {"kind": "range", "start": m.spec, "end": n.spec}))
                i += 2
                continue
        out.append(m)
        i += 1
    return out, cur


# temporal words the scanner cannot interpret -> trigger LLM fallback / ask the user
_RECUR_RE = re.compile(
    rf"\b(?:every|each)\s+(?:week|month|day|weekday|{_WDX})\b|\b{_WD_FULL}s\b")
_RESIDUAL_RE = re.compile(
    rf"\b{_WD_FULL}s\b"
    r"|\b(?:day|days|week|weeks|month|months)\s+(?:after|before)\b"
    r"|\b(?:this|next|last|previous|current|coming|upcoming|following)\s+(?:year|semester|term|quarter|fortnight|session|academic\s+year)\b"
    r"|\b(?:few|couple\s+of|several)\s+(?:days|weeks|months)\b"
    r"|\b(?:ago|from\s+now|later)\b"
    r"|\b(?:weekly|daily|monthly|fortnightly)\b"
    rf"|\b(?:this|next|last|previous|coming|upcoming|following)\s+(?:{_WDX})\b"
)


def _residual(t_full: str, t_left: str) -> list:
    out = [m.group(0) for m in _RECUR_RE.finditer(t_full)]
    tmp = _NEXT_CLASS_RE.sub(" ", t_left)
    out += [m.group(0) for m in _RESIDUAL_RE.finditer(tmp)]
    return list(dict.fromkeys(out))


# ═════════════════════════════════════════════════════════════════════════════
# 4. Intent detection
# ═════════════════════════════════════════════════════════════════════════════
_HOLIDAY_PATS = [re.compile(p) for p in (
    r"\bholidays?\b", r"\bdays?\s+off\b", r"\boff\s+days?\b", r"\bvacations?\b",
    r"\bno\s+class(?:es)?\b", r"\bclass(?:es)?\s+(?:are\s+|is\s+|will\s+be\s+)?off\b",
    r"\b(?:college|university|campus|school|institute)\s+(?:is\s+|will\s+be\s+)?(?:closed|off)\b",
    r"\bclosed\b",
)]
_CHANGES_RE = re.compile(
    r"\b(?:cancel(?:l?ed|l?ations?)|called\s+off|re-?scheduled|postponed|preponed|"
    r"extra\s+class(?:es)?|added\s+class(?:es)?|substitut\w*|swapped|changes?|changed|modified|shifted|moved)\b")
_NEXT_CLASS_RE = re.compile(
    r"\b(?:next|upcoming|coming)\s+(?:class(?:es)?|lectures?|labs?|periods?|sessions?|practicals?)\b"
    r"|\bwhat(?:'s|s|\s+is)\s+next\b|\bwhat\s+next\b")
_SCHEDULE_RE = re.compile(
    r"\b(?:class(?:es)?|lectures?|labs?|practicals?|sessions?|timetable|time\s*table|schedule[ds]?|"
    r"routine|teaching|periods?|what\s+do\s+i\s+have|what\s+have\s+i\s+got|what(?:'s|s)\s+on|"
    r"what\s+am\s+i\s+(?:teaching|doing)|am\s+i\s+(?:free|busy)|free|busy)\b")
_SCHEDULE_STRONG_RE = re.compile(
    r"\b(?:timetable|time\s*table|schedule|routine|what\s+do\s+i\s+have|what\s+have\s+i\s+got|what(?:'s|s)\s+on)\b")
_IMPLICIT_RE = re.compile(
    r"\b(?:do\s+i\s+have|am\s+i|what\s+am\s+i|what(?:'s|s|\s+is)\s+(?:happening|scheduled|planned|going\s+on)|"
    r"anything\s+(?:on|scheduled|planned)|what\s+about|how\s+about)\b")
_FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|i've|me|my|mine|myself)\b")
_PLURAL_NEXT_RE = re.compile(r"\b(?:next|upcoming|coming)\s+(?:classes|lectures|labs|periods|sessions|practicals)\b")

_ACTION_RE = re.compile(
    r"^\s*(?:please\s+|pls\s+|kindly\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\s+)?"
    r"(?:move|shift|reschedule|re-schedule|cancel|swap|exchange|change|add|set|assign|replace|put|make|mark|"
    r"delete|remove|postpone|prepone|extend|create|update|modify|book|declare|publish|generate)\b"
    r"|^\s*(?:please\s+)?schedule\s+(?:a|an|the|one|another|extra)\b")


def _detect_intents(t: str, has_time: bool, force: bool) -> list:
    holiday = any(p.search(t) for p in _HOLIDAY_PATS)
    work = t
    for p in _HOLIDAY_PATS:
        work = p.sub(" § ", work)
    next_cls = bool(_NEXT_CLASS_RE.search(work))
    work = _NEXT_CLASS_RE.sub(" § ", work)
    changes = bool(_CHANGES_RE.search(work))
    work = _CHANGES_RE.sub(" § ", work)
    sched = bool(_SCHEDULE_RE.search(work))
    strong = bool(_SCHEDULE_STRONG_RE.search(work))
    if changes and not strong:
        sched = False
    if next_cls and has_time:
        next_cls, sched = False, True
    if not (sched or changes or holiday or next_cls) and has_time and _IMPLICIT_RE.search(t):
        sched = True
    out = []
    if sched:
        out.append("schedule")
    if next_cls:
        out.append("next_class")
    if changes:
        out.append("changes")
    if holiday:
        out.append("holidays")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 5. Filters: semester, program, time-of-day, teacher, subject
# ═════════════════════════════════════════════════════════════════════════════
def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


_SEM_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}
_SEM_PATS = [
    (re.compile(rf"(?<![\w/])(\d){_ORD}?\s*(?:,|and|&|or)\s*(\d){_ORD}?\s*-?\s*sem(?:ester)?s?\b"), "two"),
    (re.compile(rf"(?<![\w/])(\d)\s*{_ORD}?\s*-?\s*sem(?:ester)?s?\b"), "num"),
    (re.compile(r"\bsem(?:ester)?s?\s*-?\s*(\d)(?!\d)"), "num"),
    (re.compile(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth)\s+sem(?:ester)?s?\b"), "word"),
    (re.compile(r"\bsem(?:ester)?s?\s*-?\s*(viii|vii|vi|iv|v|iii|ii|i)\b"), "roman"),
]


def _extract_semesters(t: str) -> tuple:
    sems: set = set()
    cur = t
    for rx, kind in _SEM_PATS:
        for m in list(rx.finditer(cur)):
            if kind == "two":
                nums = [int(m.group(1)), int(m.group(2))]
            elif kind == "num":
                nums = [int(m.group(1))]
            elif kind == "word":
                nums = [_SEM_WORDS[m.group(1)]]
            else:
                nums = [_ROMAN[m.group(1)]]
            for n in nums:
                if _ordinal(n) in SEMESTERS:
                    sems.add(_ordinal(n))
            cur = cur[:m.start()] + " " * (m.end() - m.start()) + cur[m.end():]
    return sems, cur


_WINDOWS = {"morning": (0, 12 * 60), "forenoon": (0, 12 * 60), "afternoon": (12 * 60, 17 * 60),
            "evening": (17 * 60, 24 * 60)}
_WINDOW_RE = re.compile(r"\b(morning|forenoon|afternoon|evening)\b")


def _extract_window(t: str) -> tuple:
    ms = {m.group(1) for m in _WINDOW_RE.finditer(t)}
    if len(ms) != 1:
        return None, "", t
    name = next(iter(ms))
    return _WINDOWS[name], ("morning" if name == "forenoon" else name), _WINDOW_RE.sub(" ", t)


def _program_catalog() -> dict:
    """pkey -> display name, from the live catalogue."""
    out: dict = {}
    try:
        for key in PROGRAMME_SUBJECT_MAP.keys():
            p = key[0] if isinstance(key, tuple) else key
            out.setdefault(_pkey(p), str(p))
    except Exception:
        log.exception("could not read PROGRAMME_SUBJECT_MAP")
    return out


def _programs_in(text: str) -> set:
    found = set()
    for _pk, disp in _program_catalog().items():
        letters = re.sub(r"[^a-z0-9]", "", disp.lower())
        if not letters:
            continue
        sep = r"\.?" if len(letters) <= 3 else r"[\s.\-]*"
        pat = sep.join(re.escape(c) for c in letters) + (r"\.?" if len(letters) <= 3 else "")
        if re.search(rf"(?<![a-z0-9]){pat}(?![a-z0-9'])", text):
            found.add(disp)
    return found


@dataclass
class Filters:
    programs: set = field(default_factory=set)       # display names
    semesters: set = field(default_factory=set)      # '3rd'
    teachers: set = field(default_factory=set)       # lower-case raw names as returned by teacher_set()
    teacher_display: list = field(default_factory=list)
    subjects: set = field(default_factory=set)       # lower-case names AND codes
    subject_display: list = field(default_factory=list)
    window: Optional[tuple] = None                   # (start_min, end_min) on class START time
    window_label: str = ""
    self_mode: bool = False

    @property
    def program_keys(self) -> set:
        return {_pkey(p) for p in self.programs}

    @property
    def scoped(self) -> bool:
        return bool(self.teachers or self.subjects)

    @property
    def any(self) -> bool:
        return bool(self.programs or self.semesters or self.teachers or self.subjects or self.window)


def _minutes(s: Any) -> int:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(s or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else -1


def _entry_matches(e: dict, f: Filters) -> bool:
    if f.programs and _pkey(e.get("program")) not in f.program_keys:
        return False
    if f.semesters and str(e.get("semester", "")).strip().lower() not in f.semesters:
        return False
    if f.teachers and not ({str(t).lower() for t in teacher_set(e)} & f.teachers):
        return False
    if f.subjects:
        names = {str(e.get("subject_name") or "").strip().lower(), str(e.get("subject_code") or "").strip().lower()}
        if not (names - {""}) & f.subjects:
            return False
    if f.window:
        s = _minutes(e.get("start"))
        if s < 0 or not (f.window[0] <= s < f.window[1]):
            return False
    return True


# ── teachers ────────────────────────────────────────────────────────────────
_HONORIFICS = {"dr", "prof", "professor", "mr", "mrs", "ms", "miss", "shri", "smt", "sri",
               "sir", "madam", "mam", "maam", "er", "engr"}
_CUE = r"(?:of|for|by|with|teacher|prof|professor|dr|sir|mam|madam|maam|mr|mrs|ms|shri|smt)"


def _name_tokens(s: str) -> tuple:
    return tuple(t for t in re.findall(r"[a-z0-9]+", str(s).lower()) if t not in _HONORIFICS)


@dataclass(frozen=True)
class KT:
    raw: str          # lower-case, exactly as teacher_set() yields (used for filtering)
    display: str
    key: str
    tokens: tuple


def _all_entries(ctx, days) -> list:
    out: list = []
    for v in ctx.entries_cache.values():
        out.extend(v)
    for d in days:
        out.extend(d.get("entries", []))
        out.extend(d.get("cancelled", []))
    return out


def _known_teachers(entries: list) -> list:
    seen: dict = {}
    for e in entries:
        try:
            names = teacher_set(e)
        except Exception:
            continue
        for raw in names:
            r = str(raw).strip()
            low = r.lower()
            if not r or low in seen:
                continue
            toks = _name_tokens(r)
            if toks:
                seen[low] = KT(low, r, " ".join(toks), toks)
    return list(seen.values())


_STOP = set("""
what which when where who whom whose how why the and for are was were has have had does did can could would should
will shall may might must this that these those with from into onto about above below over under between through
until till after before during today tomorrow yesterday tonight week weeks month months year years day days next last
previous coming upcoming current class classes lecture lectures lab labs practical practicals theory timetable
schedule routine holiday holidays vacation break leave cancelled canceled cancel show list tell give please check
find there their them they mine your yours teacher teachers faculty professor prof sir madam room rooms subject
subjects semester program programme course courses free busy any all some every each other also only just like know
want need get got see time times date dates morning afternoon evening weekend
on in at to of by or as is it me my do am an be if so no up us we he she his her him you your our
""".split()) | set(_MONTHS) | set(_WD)


def _master_cells() -> list:
    """[(line_tokens:set, cells:[normalised cell strings])] from the faculty master table."""
    out = []
    for line in _faculty_lines():
        parts = line.split("|") if "|" in line else line.split(",")
        cells = [" ".join(_name_tokens(c)) for c in parts]
        cells = [c for c in cells if c]
        if cells:
            out.append((set(re.findall(r"[a-z0-9]+", line.lower())) - _HONORIFICS, cells))
    return out


def _master_lookup(tokens: list, known: list) -> tuple:
    """Resolve name tokens via the faculty master. Returns (found KT list, ambiguous bool)."""
    want = {x for x in tokens if x}
    if not want:
        return [], False
    hits = []
    for ltoks, cells in _master_cells():
        if want <= ltoks:
            ks = [k for k in known if k.key in cells]
            if ks:
                hits.append(ks)
    if len(hits) == 1:
        return hits[0], False
    return [], len(hits) > 1


def _teachers_from_text(raw_text: str, known: list, exclude_tokens: set = frozenset(),
                        want_unresolved: bool = False) -> tuple:
    """(found KT list, ambiguous [(token,[KT])], unresolved [name])."""
    low = raw_text.lower()
    flat = " ".join(re.findall(r"[a-z0-9]+", low))
    mn_tokens = [x for x in flat.split() if x not in _HONORIFICS]
    mn = " ".join(mn_tokens)
    found: dict = {}
    consumed: set = set()

    # A. full names listed in the faculty master ("Anil Sharma" -> its short name in the timetable)
    for _lt, cells in _master_cells():
        for c in cells:
            if len(c.split()) >= 2 and f" {c} " in f" {mn} ":
                for k in known:
                    if k.key in cells:
                        found[k.raw] = k
                consumed.update(c.split())

    # B. names exactly as they appear in the timetable
    for k in known:
        if k.raw in found:
            continue
        if len(k.tokens) == 1 and k.tokens[0] in consumed:
            continue          # "Anil Sharma" must not also match a different teacher called "Sharma"
        if len(k.key.replace(" ", "")) <= 3:
            if re.search(rf"\b{_CUE}\s+{re.escape(k.key)}\b", flat):
                found[k.raw] = k
                consumed.update(k.tokens)
        elif f" {k.key} " in f" {mn} ":
            found[k.raw] = k
            consumed.update(k.tokens)

    # C. a distinctive part of a name (surname / first name) — needs a cue word or a capital letter
    index: dict = {}
    for k in known:
        for tok in k.tokens:
            if len(tok) >= 4 and tok not in _STOP and tok not in consumed:
                index.setdefault(tok, {})[k.raw] = k
    ambiguous: list = []
    for tok in dict.fromkeys(mn_tokens):
        if tok not in index:
            continue
        cue = re.search(rf"\b{_CUE}\s+(?:\w+\s+)?{re.escape(tok)}\b", flat)
        cap = re.search(rf"\b{tok.capitalize()}\b|\b{tok.upper()}\b", raw_text)
        if not (cue or cap):
            continue
        cands = index[tok]
        if len(cands) == 1:
            k = next(iter(cands.values()))
            found[k.raw] = k
            consumed.update(k.tokens)
        else:
            ambiguous.append((tok, list(cands.values())))

    # D. a teacher-like mention we could not match (e.g. "Rahul" when the timetable says "RK")
    unresolved: list = []
    if want_unresolved:
        cands: list = []
        for m in re.finditer(r"\b(?:dr|prof|professor|mr|mrs|ms|sir|madam|mam|shri|smt)\.?\s+([a-z]+(?:\s+[a-z]+)?)",
                             low):
            cands.append(m.group(1).split())
        for m in re.finditer(r"\b(?:of|for|by|with)\s+(?:(?:dr|prof|professor|mr|mrs|ms|sir|madam|mam)\.?\s+)?"
                             r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", raw_text):
            cands.append(m.group(1).lower().split())
        seen_c: set = set()
        for toks in cands:
            toks = [x for x in toks if x not in _STOP and x not in _HONORIFICS and x not in exclude_tokens
                    and x not in consumed and len(x) >= 2]
            if not toks or " ".join(toks) in seen_c:
                continue
            seen_c.add(" ".join(toks))
            if _programs_in(" ".join(toks)):
                continue
            hit, amb = _master_lookup(toks, known)
            if hit:
                for k in hit:
                    found[k.raw] = k
                    consumed.update(k.tokens)
            elif amb:
                ambiguous.append((" ".join(toks), []))
            else:
                unresolved.append(" ".join(toks).title())
    return list(found.values()), ambiguous, unresolved


# ── self (logged-in teacher) ────────────────────────────────────────────────
def _alias_overrides() -> dict:
    try:
        d = json.loads(os.getenv("CALENDAR_CHAT_TEACHER_ALIASES", "") or "{}")
        return {str(k).strip().lower(): [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in d.items()}
    except Exception:
        return {}


def _faculty_lines() -> list:
    try:
        from app.scheduler.data import FACULTY_MASTER_MD
        return [l for l in str(FACULTY_MASTER_MD).splitlines() if l.strip()]
    except Exception:
        return []


def _match_alias(alias: str, known: list) -> list:
    a_low, a_tok = alias.strip().lower(), _name_tokens(alias)
    return [k for k in known if k.raw == a_low or (a_tok and k.tokens == a_tok)]


def _resolve_self(user: Any, known: list) -> list:
    """Map the logged-in user to the teacher name(s) used in the timetable. Returns [] if not certain."""
    if not known or user is None:
        return []
    # 0. explicit overrides
    ov = _alias_overrides()
    for ident in {str(getattr(user, "id", "")).lower(), str(getattr(user, "email", "") or "").lower()}:
        for alias in ov.get(ident, []):
            hit = _match_alias(alias, known)
            if hit:
                return hit
    # 1. explicit short-name / teacher-name attributes
    for attr in ("teacher_short_name", "short_name", "teacher_name", "faculty_short_name",
                 "faculty_name", "teacher_code"):
        v = getattr(user, attr, None)
        if isinstance(v, str) and v.strip():
            hit = _match_alias(v, known)
            if hit:
                return hit
    names: list = []
    for attr in ("full_name", "name", "display_name", "username"):
        v = getattr(user, attr, None)
        if isinstance(v, str) and v.strip():
            names.append(v)
    email = getattr(user, "email", None)
    if isinstance(email, str) and "@" in email:
        local = re.sub(r"\d+", " ", email.split("@")[0]).replace(".", " ").replace("_", " ").replace("-", " ")
        if len(_name_tokens(local)) >= 2:
            names.append(local)
    # 2. faculty master table (maps full name -> short name)
    lines = _faculty_lines()
    for nm in names:
        utoks = set(_name_tokens(nm))
        if not utoks or (len(utoks) == 1 and len(next(iter(utoks))) < 4):
            continue
        matched_lines = []
        for line in lines:
            ltoks = set(re.findall(r"[a-z0-9]+", line.lower()))
            if utoks <= ltoks:
                cells = [" ".join(_name_tokens(c)) for c in (line.split("|") if "|" in line else line.split(","))]
                cells = [c for c in cells if c]
                hits = [k for k in known if k.key in cells]
                if hits:
                    matched_lines.append(hits)
        if len(matched_lines) == 1:
            return matched_lines[0]
    # 3. token-set equality
    for nm in names:
        utoks = set(_name_tokens(nm))
        hit = [k for k in known if set(k.tokens) == utoks and utoks]
        if hit:
            return hit
    # 4. unique subset / initial match (>= 2 name tokens required)
    for nm in names:
        utoks = list(_name_tokens(nm))
        if len(utoks) < 2:
            continue
        hit = []
        for k in known:
            if len(k.tokens) < 2:
                continue
            used, full = set(), 0
            ok = True
            for kt in k.tokens:
                m = next((u for u in utoks if u not in used and (u == kt or (len(kt) == 1 and u.startswith(kt)))), None)
                if m is None:
                    ok = False
                    break
                used.add(m)
                full += int(len(kt) > 1)
            if ok and full >= 1:
                hit.append(k)
        if len(hit) == 1:
            return hit
    return []


# ── subjects ────────────────────────────────────────────────────────────────
def _known_subjects(entries: list) -> list:
    """[(name_lower, code_lower, display)] unique."""
    seen: dict = {}
    for e in entries:
        name = str(e.get("subject_name") or "").strip()
        code = str(e.get("subject_code") or "").strip()
        if not (name or code):
            continue
        seen.setdefault((name.lower(), code.lower()), (name.lower(), code.lower(), name or code))
    return list(seen.values())


_SUBJ_TAIL = {"i", "ii", "iii", "iv", "v", "vi", "1", "2", "3", "4", "lab", "theory", "practical"}


def _subjects_from_text(raw_text: str, known: list) -> list:
    low = raw_text.lower()
    flat = " ".join(re.findall(r"[a-z0-9]+", low))
    ctoks = " ".join(re.findall(r"[a-z]+|\d+", low))
    hits: list = []   # (match_len, name, code, display)
    for name, code, disp in known:
        best = 0
        ntoks = re.findall(r"[a-z0-9]+", name)
        variants = []
        if ntoks:
            variants.append(ntoks)
            if len(ntoks) >= 2 and ntoks[-1] in _SUBJ_TAIL:
                variants.append(ntoks[:-1])
        for v in variants:
            phrase = " ".join(v)
            if len(phrase.replace(" ", "")) >= 4 and f" {phrase} " in f" {flat} ":
                best = max(best, len(phrase))
        if code:
            ck = " ".join(re.findall(r"[a-z]+|\d+", code))
            if len(ck.replace(" ", "")) >= 4 and f" {ck} " in f" {ctoks} ":
                best = max(best, len(ck) + 100)
        if best:
            hits.append((best, name, code, disp))
    hits.sort(key=lambda x: -x[0])
    kept: list = []
    for h in hits:
        h_phrase = " ".join(re.findall(r"[a-z0-9]+", h[1]))
        if any(h_phrase and h_phrase in " ".join(re.findall(r"[a-z0-9]+", k[1])) and h[1] != k[1] for k in kept):
            continue
        kept.append(h)
    return kept


# ═════════════════════════════════════════════════════════════════════════════
# 6. LLM fallback (query-plan extraction ONLY) — strictly validated
# ═════════════════════════════════════════════════════════════════════════════
_LLM_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="calchat-llm")
_LLM_LOCK = threading.Lock()
_LLM_CALLS: deque = deque()
_LLM_CACHE: "OrderedDict[tuple, Optional[dict]]" = OrderedDict()
_LLM_CACHE_MAX = 256


def _llm_allow() -> bool:
    with _LLM_LOCK:
        now = time.monotonic()
        while _LLM_CALLS and now - _LLM_CALLS[0] > 60:
            _LLM_CALLS.popleft()
        if len(_LLM_CALLS) >= LLM_MAX_PER_MIN:
            return False
        _LLM_CALLS.append(now)
        return True


def _int_in(v: Any, lo: int, hi: int) -> Optional[int]:
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return None
    return iv if lo <= iv <= hi else None


def _validate_spec(s: Any, depth: int = 0) -> Optional[dict]:
    if not isinstance(s, dict):
        return None
    k = s.get("kind")
    if k == "date":
        day, month = _int_in(s.get("day"), 1, 31), _int_in(s.get("month"), 1, 12)
        if day is None or month is None:
            return None
        return {"kind": "date", "day": day, "month": month, "year": _int_in(s.get("year"), 2000, 2100)}
    if k == "relative_day":
        off = _int_in(s.get("offset"), -400, 400)
        return None if off is None else {"kind": "relative_day", "offset": off}
    if k == "weekday":
        w = s.get("weekday")
        wd = _WD.get(str(w).strip().lower()) if isinstance(w, str) else _int_in(w, 0, 6)
        mod = s.get("modifier")
        if wd is None or mod not in (None, "this", "next", "last"):
            return None
        return {"kind": "weekday", "weekday": wd, "modifier": mod}
    if k in ("week", "weekend"):
        off = _int_in(s.get("offset", 0), -52, 52)
        return None if off is None else {"kind": k, "offset": off}
    if k == "month":
        month, off = _int_in(s.get("month"), 1, 12), _int_in(s.get("offset"), -24, 24)
        if month is None and off is None:
            return None
        return {"kind": "month", "month": month, "year": _int_in(s.get("year"), 2000, 2100),
                "offset": off, "modifier": None}
    if k == "next_days":
        n = _int_in(s.get("n"), 1, 366)
        return None if n is None else {"kind": "next_days", "n": n}
    if k in ("rest_of_week", "rest_of_month"):
        return {"kind": k}
    if k == "range" and depth == 0:
        a, b = _validate_spec(s.get("start"), 1), _validate_spec(s.get("end"), 1)
        if a and b and a["kind"] in _RANGEABLE and b["kind"] in _RANGEABLE:
            return {"kind": "range", "start": a, "end": b}
    return None


def _llm_system_prompt(today: dt.date) -> str:
    return (
        "You convert a university timetable question into ONE JSON object. Output JSON only, no prose.\n"
        f"Today is {WEEKDAYS[today.weekday()]} {today.isoformat()}. Weeks start on Monday.\n"
        'Schema: {"intents":[],"times":[],"program":null,"semester":null,"teacher_name":null,'
        '"subject":null,"self":false}\n'
        'intents: any of "schedule" (classes/timetable on dates), "holidays" (holidays/days off), '
        '"changes" (cancelled/rescheduled/extra classes), "next_class". Use [] if the message is not about '
        "the timetable.\n"
        "times (max 3), each one of:\n"
        '{"kind":"date","day":1-31,"month":1-12,"year":null|int}\n'
        '{"kind":"relative_day","offset":int}   (today=0, tomorrow=1, yesterday=-1)\n'
        '{"kind":"weekday","weekday":"monday","modifier":null|"this"|"next"|"last"}\n'
        '{"kind":"week","offset":int}  {"kind":"weekend","offset":int}\n'
        '{"kind":"month","month":null|1-12,"year":null|int,"offset":null|int}\n'
        '{"kind":"next_days","n":int}  {"kind":"rest_of_week"}  {"kind":"rest_of_month"}\n'
        '{"kind":"range","start":<date|relative_day|weekday|month>,"end":<same>}\n'
        "Rules: NEVER compute calendar dates yourself; use relative kinds unless the user wrote an exact date. "
        "Numeric dates are DD/MM. times=[] if no time is mentioned. self=true when the user asks about their own "
        "classes (I/my/me). teacher_name only if a different teacher is named. semester is an integer 1-8."
    )


def _llm_interpret(raw: str, today: dt.date) -> Optional[dict]:
    if not LLM_ENABLED:
        return None
    key = (today.isoformat(), raw.lower())
    with _LLM_LOCK:
        if key in _LLM_CACHE:
            _LLM_CACHE.move_to_end(key)
            return _LLM_CACHE[key]
    if not _llm_allow():
        log.info("LLM fallback skipped: rate limit")
        return None
    plan: Optional[dict] = None
    try:
        from app.scheduler.llm import call_groq, extract_json
        messages = [{"role": "system", "content": _llm_system_prompt(today)},
                    {"role": "user", "content": raw}]
        fut = _LLM_POOL.submit(call_groq, messages, None, False)
        content, _model = fut.result(timeout=LLM_TIMEOUT_S)
        data = extract_json(content)
        intents = [i for i in (data.get("intents") or []) if i in
                   ("schedule", "holidays", "changes", "next_class")] if isinstance(data.get("intents"), list) else []
        times = []
        for s in (data.get("times") or [])[:3] if isinstance(data.get("times"), list) else []:
            v = _validate_spec(s)
            if v:
                times.append(v)
        plan = {
            "intents": list(dict.fromkeys(intents)),
            "times": times,
            "program": data.get("program") if isinstance(data.get("program"), str) else None,
            "semester": _int_in(data.get("semester"), 1, 8),
            "teacher_name": data.get("teacher_name") if isinstance(data.get("teacher_name"), str) else None,
            "subject": data.get("subject") if isinstance(data.get("subject"), str) else None,
            "self": bool(data.get("self")),
        }
    except _FutTimeout:
        log.warning("LLM fallback timed out")
    except Exception as exc:  # noqa: BLE001 — never let the LLM break the chatbot
        log.warning("LLM fallback failed: %s", exc)
    with _LLM_LOCK:
        _LLM_CACHE[key] = plan
        while len(_LLM_CACHE) > _LLM_CACHE_MAX:
            _LLM_CACHE.popitem(last=False)
    return plan


# ═════════════════════════════════════════════════════════════════════════════
# 7. Day views (apply filters to the resolved calendar days)
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class DayView:
    date: dt.date
    entries: list
    cancelled: list
    day_offs: list          # relevant to the asker
    hidden_day_offs: int
    full_holiday: bool
    no_timetable: bool
    version_id: Optional[int]


def _df_overlaps(df: dict, f: Filters) -> bool:
    dp, ds = df.get("program"), df.get("semester")
    if dp and f.programs and _pkey(dp) not in f.program_keys:
        return False
    if ds and f.semesters and str(ds).lower() not in f.semesters:
        return False
    return True


def _df_covers(df: dict, f: Filters) -> bool:
    dp, ds = df.get("program"), df.get("semester")
    prog_ok = dp is None or (bool(f.programs) and f.program_keys == {_pkey(dp)})
    sem_ok = ds is None or (bool(f.semesters) and f.semesters == {str(ds).lower()})
    return prog_ok and sem_ok


def _view_day(day: dict, d: dt.date, f: Filters) -> DayView:
    entries = [e for e in day["entries"] if _entry_matches(e, f)]
    cancelled = [e for e in day["cancelled"] if _entry_matches(e, f)]
    affecting = {e.get(META + "override_id") for e in cancelled if e.get(META + "cancelled_by") == "day_off"}
    rel, hidden, full = [], 0, False
    for df in day["day_offs"]:
        all_college = df.get("program") is None and df.get("semester") is None
        if not _df_overlaps(df, f):
            hidden += 1
            continue
        if f.scoped and not all_college and df.get("override_id") not in affecting:
            hidden += 1
            continue
        rel.append(df)
        if all_college or (not f.scoped and _df_covers(df, f)):
            full = True
    return DayView(d, entries, cancelled, rel, hidden, full, day.get("version_id") is None, day.get("version_id"))


# ═════════════════════════════════════════════════════════════════════════════
# 8. Formatting
# ═════════════════════════════════════════════════════════════════════════════
def _fmt_time(s: Any) -> str:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(s or ""))
    if not m:
        return str(s or "?").strip() or "?"
    h, mi = int(m.group(1)), m.group(2)
    if TIME_FORMAT == "24h":
        return f"{h:02d}:{mi}"
    return f"{h % 12 or 12}:{mi} {'AM' if h < 12 else 'PM'}"


def _scope_text(df: dict) -> str:
    parts = [p for p in (df.get("program"), (str(df["semester"]) + " sem") if df.get("semester") else None) if p]
    return " · ".join(parts) + " only" if parts else "all programmes"


def _describe_original(e: dict) -> str:
    orig = e.get(META + "original") or {}
    parts = []
    if "start" in orig or "end" in orig:
        parts.append(f"was {_fmt_time(orig.get('start', e.get('start')))} – {_fmt_time(orig.get('end', e.get('end')))}")
    for k, label in (("teacher", "teacher"), ("room", "room"), ("subject_name", "subject"),
                     ("subject_code", "code"), ("type", "type")):
        if k in orig:
            parts.append(f"{label} was {orig[k] or '—'}")
    return "; ".join(parts)


def _entry_line(e: dict, *, show_teacher: bool, show_program: bool, marker: str = "", suffix: str = "") -> str:
    name = str(e.get("subject_name") or e.get("subject_code") or "Class").strip()
    code = str(e.get("subject_code") or "").strip()
    title = f"**{name}**" + (f" ({code})" if code and code.lower() != name.lower() else "")
    bits = [f"{_fmt_time(e.get('start'))} – {_fmt_time(e.get('end'))}", title]
    if e.get("type"):
        bits.append(str(e["type"]))
    if e.get("room"):
        bits.append(f"📍 {e['room']}")
    if show_teacher and e.get("teacher"):
        bits.append(f"👤 {e['teacher']}")
    if show_program and e.get("program"):
        bits.append(f"{e['program']} {e.get('semester', '')} sem".replace("  ", " ").strip())
    line = "- " + (marker + " " if marker else "") + " · ".join(bits)
    return line + suffix


def _flag_suffix(e: dict) -> str:
    src = e.get(META + "source")
    note = e.get(META + "note")
    tail = f" — _{note}_" if note else ""
    if src == "modified":
        d = _describe_original(e)
        return f"  🔄 *changed*{' (' + d + ')' if d else ''}{tail}"
    if src == "added":
        return f"  ➕ *extra class*{tail}"
    return tail


def _scope_label(f: Filters) -> str:
    if f.self_mode and not f.teachers:
        return "you"
    bits = []
    if f.teachers:
        bits.append(", ".join(f.teacher_display) if not f.self_mode else "you")
    if f.programs or f.semesters:
        bits.append(" ".join(sorted(f.programs)) + (" " + "/".join(sorted(f.semesters)) + " sem" if f.semesters else ""))
    if f.subjects:
        bits.append(", ".join(f.subject_display))
    if f.window_label:
        bits.append(f"{f.window_label} classes only")
    return "; ".join(b.strip() for b in bits if b.strip())


def _clean_entry(e: dict) -> dict:
    out = {k: e.get(k) for k in ("program", "semester", "day", "start", "end", "subject_code",
                                 "subject_name", "teacher", "room", "type")}
    out["status"] = e.get(META + "source", "base")
    if e.get(META + "note"):
        out["note"] = e[META + "note"]
    if e.get(META + "original"):
        out["original"] = e[META + "original"]
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 'es' if word.endswith('ss') else 's'}"


def _section_schedule(views: list, spans: list, f: Filters) -> str:
    scope = _scope_label(f)
    head = f"**📅 Classes — {_spans_label(spans)}**" + (f"  _(for {scope})_" if scope else "")
    total = sum(len(v.entries) for v in views)
    n_days = len(views)
    lines = [head]
    show_teacher = not f.teachers and not f.self_mode
    progs = {(_pkey(e.get("program")), str(e.get("semester"))) for v in views for e in v.entries}
    show_program = len(progs) > 1 or not (f.programs and f.semesters)
    verbose = n_days <= 3
    compact = total > MAX_LISTED_ENTRIES

    if total == 0 and not any(v.day_offs or v.cancelled for v in views):
        who = "you have" if f.self_mode else "there is"
        lines.append(f"No classes scheduled — {who} nothing on the timetable for this period.")
    elif total:
        days_with = sum(1 for v in views if v.entries)
        lines.append(f"{_plural(total, 'class')} across {days_with} day{'s' if days_with != 1 else ''}.")
    else:
        lines.append("No classes are running in this period (see below).")

    for v in views:
        has_content = v.entries or v.day_offs or v.cancelled or v.no_timetable
        if not has_content and not verbose:
            continue
        block = [f"\n**{fmt_date(v.date)}**"]
        for df in v.day_offs:
            reason = df.get("reason") or "Day off"
            block.append(f"🎉 **Holiday:** {reason} _({_scope_text(df)})_")
        if v.no_timetable:
            block.append("_No timetable is published for this date._")
        if compact:
            block.append(f"- {_plural(len(v.entries), 'class')}")
        else:
            for e in v.entries:
                block.append(_entry_line(e, show_teacher=show_teacher, show_program=show_program,
                                         suffix=_flag_suffix(e)))
            explicit = [e for e in v.cancelled if e.get(META + "cancelled_by") == "cancel"]
            for e in explicit:
                note = e.get(META + "note")
                block.append(_entry_line(e, show_teacher=show_teacher, show_program=show_program,
                                         marker="❌ **Cancelled:**", suffix=f" — _{note}_" if note else ""))
            off_cancelled = len(v.cancelled) - len(explicit)
            if off_cancelled and not v.full_holiday:
                block.append(f"_{_plural(off_cancelled, 'class')} off because of the day-off above._")
            elif off_cancelled and v.full_holiday:
                block.append(f"_{_plural(off_cancelled, 'scheduled class')} off for the holiday._")
        if not v.entries and not v.day_offs and not v.cancelled and not v.no_timetable:
            block.append("No classes." if not f.self_mode else "You have no classes.")
        lines.extend(block)
    if compact:
        lines.append("\n_That's a lot of classes to list — tell me a programme/semester or teacher "
                     "(e.g. “B.Tech 3rd sem”) and I'll list them in full._")
    return "\n".join(lines)


@dataclass
class HolidayGroup:
    start: dt.date
    end: dt.date
    reason: str
    program: Optional[str]
    semester: Optional[str]


def _holiday_groups(views: list) -> list:
    by_key: dict = {}
    for v in views:
        for df in v.day_offs:
            key = (df.get("program"), df.get("semester"), (df.get("reason") or "").strip())
            by_key.setdefault(key, set()).add(v.date)
    groups: list = []
    for (prog, sem, reason), dates in by_key.items():
        run = None
        for d in sorted(dates):
            if run and d == run.end + dt.timedelta(days=1):
                run.end = d
            else:
                run = HolidayGroup(d, d, reason, prog, sem)
                groups.append(run)
    groups.sort(key=lambda g: (g.start, g.end))
    return groups


def _holiday_keywords(raw_text: str, groups: list, exclude: set) -> set:
    vocab: set = set()
    for g in groups:
        vocab.update(x for x in re.findall(r"[a-z0-9]+", g.reason.lower()) if len(x) >= 4)
    toks = {x for x in re.findall(r"[a-z0-9]+", raw_text.lower()) if len(x) >= 4}
    return {x for x in toks if x in vocab and x not in _STOP and x not in exclude}


def _section_holidays(views: list, spans: list, f: Filters, raw_text: str, exclude: set) -> tuple:
    scope = _scope_label(f)
    head = f"**🎉 Holidays — {_spans_label(spans)}**" + (f"  _(relevant to {scope})_" if scope else "")
    groups = _holiday_groups(views)
    kws = _holiday_keywords(raw_text, groups, exclude)
    matched_note = ""
    if kws:
        sel = [g for g in groups if any(k in g.reason.lower() for k in kws)]
        if sel:
            groups = sel
            matched_note = f"_Showing holidays matching: {', '.join(sorted(kws))}._"
    lines = [head]
    if len(views) == 1:
        v = views[0]
        if groups:
            lines.append(f"**Yes** — {fmt_date(v.date)} is a day off.")
        else:
            tail = (f" Classes are scheduled that day ({len(v.entries)})." if v.entries
                    else " No classes are scheduled that day either.")
            lines.append(f"**No** — no holiday is recorded for {fmt_date(v.date)}." + tail)
    if groups:
        for g in groups:
            span = fmt_range(g.start, g.end)
            n = (g.end - g.start).days + 1
            reason = g.reason or "Day off"
            scope_t = _scope_text({"program": g.program, "semester": g.semester})
            extra = " — affects your classes" if (f.scoped and (g.program or g.semester)) else ""
            lines.append(f"- **{span}**{f' ({n} days)' if n > 1 else ''} — {reason} _({scope_t})_{extra}")
    elif len(views) != 1:
        lines.append("No holidays are recorded in the calendar for this period.")
    if matched_note:
        lines.append(matched_note)
    hidden = sum(v.hidden_day_offs for v in views)
    if hidden:
        lines.append(f"_{hidden} programme-specific day-off{'s' if hidden != 1 else ''} not applying to "
                     f"{'you' if f.self_mode else 'this selection'} {'was' if hidden == 1 else 'were'} left out._")
    lines.append("_Only holidays/day-offs recorded in the calendar are listed (regular weekly offs such as "
                 "Sundays are not)._")
    return "\n".join(lines), groups


def _section_changes(views: list, spans: list, f: Filters) -> str:
    scope = _scope_label(f)
    head = f"**🔄 Cancellations & changes — {_spans_label(spans)}**" + (f"  _(for {scope})_" if scope else "")
    show_teacher = not f.teachers and not f.self_mode
    lines = [head]
    count = 0
    for v in views:
        rows = []
        for e in v.cancelled:
            if e.get(META + "cancelled_by") == "cancel":
                note = e.get(META + "note")
                rows.append(_entry_line(e, show_teacher=show_teacher, show_program=True,
                                        marker="❌ **Cancelled:**", suffix=f" — _{note}_" if note else ""))
        for e in v.entries:
            if e.get(META + "source") in ("modified", "added"):
                rows.append(_entry_line(e, show_teacher=show_teacher, show_program=True, suffix=_flag_suffix(e)))
        if rows:
            count += len(rows)
            lines.append(f"\n**{fmt_date(v.date)}**")
            lines.extend(rows)
    if not count:
        lines.append("No cancelled, rescheduled or extra classes in this period.")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# 9. Orchestrator
# ═════════════════════════════════════════════════════════════════════════════
def _reply(message: str, data: Optional[dict] = None) -> dict:
    return {"message": message, "schedule_updated": False, "response_data": data or {}, "parsed_actions": []}


def _clean(msg: str) -> str:
    s = unicodedata.normalize("NFKC", str(msg or ""))
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def _norm(raw: str) -> str:
    return raw.lower().replace("–", "-").replace("—", "-")


def _llm_filters(plan: dict, known_t: list, known_s: list, f: Filters, notes: list) -> None:
    """Merge validated LLM-suggested filters (only values that really exist in the data)."""
    if plan.get("program"):
        f.programs |= _programs_in(plan["program"].lower().replace("_", " "))
    if plan.get("semester") and _ordinal(plan["semester"]) in SEMESTERS:
        f.semesters.add(_ordinal(plan["semester"]))
    if plan.get("teacher_name") and not f.teachers:
        found, _amb, _unr = _teachers_from_text(plan["teacher_name"], known_t)
        if not found:
            qt = set(_name_tokens(plan["teacher_name"]))
            found = [k for k in known_t if qt and (qt <= set(k.tokens))] if len(qt) >= 1 else []
            if len({k.key for k in found}) > 1:
                found = []
        for k in found:
            f.teachers.add(k.raw)
            f.teacher_display.append(k.display)
    if plan.get("subject") and not f.subjects:
        for _l, name, code, disp in _subjects_from_text(plan["subject"], known_s):
            f.subjects.update(x for x in (name, code) if x)
            f.subject_display.append(disp)


def _next_class_section(db: Session, f: Filters, today: dt.date, plural: bool, notes: list) -> tuple:
    now = _now_local()
    end = today + dt.timedelta(days=NEXT_CLASS_LOOKAHEAD)
    ctx = _load_ctx(db, today, end)
    want = 5 if plural else 1
    ongoing, upcoming = [], []
    d = today
    while d <= end and len(upcoming) < want:
        day = _resolve_day(ctx, d)
        v = _view_day(day, d, f)
        for e in sorted(v.entries, key=lambda x: _minutes(x.get("start"))):
            s, en = _minutes(e.get("start")), _minutes(e.get("end"))
            if d == today:
                cur = now.hour * 60 + now.minute
                if s <= cur < en:
                    ongoing.append((d, e))
                    continue
                if s < cur:
                    continue
            upcoming.append((d, e))
            if len(upcoming) >= want:
                break
        d += dt.timedelta(days=1)
    scope = _scope_label(f)
    lines = [f"**⏭️ Next class**" + (f"  _(for {scope})_" if scope else "")]
    show_teacher = not f.teachers and not f.self_mode
    if not ongoing and not upcoming:
        lines.append(f"No upcoming classes in the next {NEXT_CLASS_LOOKAHEAD} days.")
    for d, e in ongoing:
        lines.append(f"\n**Happening now** ({fmt_date(d)})")
        lines.append(_entry_line(e, show_teacher=show_teacher, show_program=True, suffix=_flag_suffix(e)))
    last = None
    for d, e in upcoming:
        if d != last:
            when = "today" if d == today else "tomorrow" if d == today + dt.timedelta(days=1) else ""
            lines.append(f"\n**{fmt_date(d)}**" + (f" ({when})" if when else ""))
            last = d
        lines.append(_entry_line(e, show_teacher=show_teacher, show_program=True, suffix=_flag_suffix(e)))
    return "\n".join(lines), [{"date": d.isoformat(), **_clean_entry(e)} for d, e in ongoing + upcoming]


def _now_local() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(CAL_TZ_NAME))
    except Exception:
        return dt.datetime.now(dt.timezone.utc)


def answer_calendar_question(db: Session, message: str, user: Any = None, *,
                             force: bool = False, defer_actions: bool = False) -> Optional[dict]:
    """
    Core entry point.
      force=False  -> returns None when the message is not a calendar question (hook mode)
      force=True   -> always returns a reply (dedicated endpoints)
      defer_actions-> imperative requests ("cancel my class...") return None (leave to ActionParser)
    """
    raw = _clean(message)[:MAX_MESSAGE_CHARS]
    if not raw:
        return _reply(HELP_TEXT) if force else None
    t = _norm(raw)
    
    if re.fullmatch(r"\b(?:hi|hello|hey|greetings|morning|afternoon|evening|good\s*(?:morning|afternoon|evening|day)|howdy|sup)\b[!.?\s]*", t):
        msg = ("Hello! I'm your calendar assistant.\n\n" + HELP_TEXT)
        return _reply(msg) if force else None

    if _ACTION_RE.search(t):
        return None if defer_actions else _reply(ACTION_TEXT)

    today = _today()
    notes: list = []
    mode_teacher = user is not None

    # ── 1. parse (no DB, no LLM) ───────────────────────────────────────────
    sems, t1 = _extract_semesters(t)
    window, window_label, t2 = _extract_window(t1)
    mentions, t_left = _scan_dates(t2)
    specs = [m.spec for m in mentions]
    intents = _detect_intents(t, bool(specs), force)
    first_person = bool(_FIRST_PERSON_RE.search(t))
    residual = _residual(t2, t_left)
    plural_next = bool(_PLURAL_NEXT_RE.search(t))

    if not intents and not force:
        return None

    # ── 2. LLM fallback (only when the parser is unsure) ───────────────────
    plan = None
    if LLM_ENABLED and ((force and not intents and not specs) or (intents and residual and not specs)):
        plan = _llm_interpret(raw, today)
    if plan:
        if not intents:
            intents = plan["intents"]
        for s in plan["times"]:
            if s not in specs:
                specs.append(s)
        first_person = first_person or plan["self"]
    if not intents:
        if force and specs:
            intents = ["schedule"]
        else:
            return _reply(HELP_TEXT) if force else None
    if residual and not (plan and plan["times"]):
        return _reply(f"I'm not sure how to read “{residual[0]}” — I don't want to guess the wrong dates. "
                      "Could you give exact dates or a period, e.g. “4th October”, “12 to 16 October”, "
                      "“next week” or “next month”?")
    if plan and plan["times"] and residual:
        _note(notes, "I used AI to interpret part of your dates — please check the dates shown above.")

    # ── 3. spans ───────────────────────────────────────────────────────────
    try:
        spans: list = []
        for s in specs:
            spans.extend(_resolve_spec(s, today, notes))
        spans = _merge_spans(spans)
        if len(spans) > MAX_SPANS:
            raise ParseError("That's too many separate dates — please ask for fewer periods at a time.")

        ctx0 = _load_ctx(db, today, today)
        known_s0 = _known_subjects(_all_entries(ctx0, []))
        subj_hit0 = bool(_subjects_from_text(raw, known_s0))

        only_next = intents == ["next_class"]
        windows: dict = {}
        for it in intents:
            if it == "next_class":
                continue
            if spans:
                windows[it] = spans
            elif it == "holidays":
                windows[it] = [Span(today, today + dt.timedelta(days=DEFAULT_HOLIDAY_DAYS - 1))]
                _note(notes, f"You didn't give dates, so I looked at the next {DEFAULT_HOLIDAY_DAYS} days.")
            elif it == "changes":
                windows[it] = [Span(today, today + dt.timedelta(days=DEFAULT_CHANGES_DAYS - 1))]
                _note(notes, f"You didn't give dates, so I looked at the next {DEFAULT_CHANGES_DAYS} days.")
            else:
                n = DEFAULT_SUBJECT_DAYS if subj_hit0 else 1
                windows[it] = [Span(today, today + dt.timedelta(days=n - 1))]
                if n > 1:
                    _note(notes, f"You didn't give dates, so I looked at the next {n} days.")
        for it, sp in windows.items():
            need = sum(s.days() for s in sp)
            limit = MAX_HOLIDAY_DAYS if it == "holidays" else MAX_DETAIL_DAYS
            if need > limit:
                raise ParseError(f"That covers {need} days — more than I can list in one answer "
                                 f"(max {limit}). Please ask for a shorter period, e.g. one month.")

        # ── 4. load calendar data ──────────────────────────────────────────
        all_dates = sorted({d for sp in windows.values() for d in _dates_of(sp)})
        days: dict = {}
        ctx = ctx0
        if all_dates:
            ctx = _load_ctx(db, all_dates[0], all_dates[-1])
            days = {d: _resolve_day(ctx, d) for d in all_dates}
        entries_all = _all_entries(ctx, days.values())
        known_t = _known_teachers(entries_all)
        known_s = _known_subjects(entries_all)

        # ── 5. filters ─────────────────────────────────────────────────────
        f = Filters(semesters=set(sems), window=window, window_label=window_label)
        f.programs = _programs_in(t)
        subj_hits = _subjects_from_text(raw, known_s)
        for _l, name, code, disp in subj_hits:
            f.subjects.update(x for x in (name, code) if x)
            f.subject_display.append(disp)
        subj_tokens = set(re.findall(r"[a-z0-9]+", " ".join(h[1] + " " + h[2] for h in subj_hits).lower()))
        needs_teacher_check = bool(set(intents) & {"schedule", "changes", "next_class"})
        found_t, ambiguous, unresolved = _teachers_from_text(raw, known_t, subj_tokens, needs_teacher_check)
        if ambiguous and not found_t:
            tok, cands = ambiguous[0]
            names = ", ".join(sorted({k.display for k in cands})[:6])
            return _reply(f"“{tok.title()}” matches more than one teacher"
                          + (f" ({names})" if names else "") + ". Which one do you mean?")
        if unresolved and not found_t:
            sample = ", ".join(sorted({k.display for k in known_t})[:8])
            return _reply(f"I couldn't find a teacher called “{unresolved[0]}” in the timetable for that period, "
                          f"so I didn't want to guess. Teachers listed include: {sample}"
                          + (", …" if len(known_t) > 8 else "") + ". Please check the spelling.")
        if unresolved:
            _note(notes, f"I couldn't find a teacher called “{unresolved[0]}”, so that name was ignored.")
        for k in found_t:
            f.teachers.add(k.raw)
            f.teacher_display.append(k.display)
        if plan:
            _llm_filters(plan, known_t, known_s, f, notes)

        explicit_scope = bool(f.teachers or f.programs or f.semesters)
        if mode_teacher and not f.teachers and (first_person or not explicit_scope):
            mine = _resolve_self(user, known_t)
            if mine:
                f.self_mode = True
                f.teachers = {k.raw for k in mine}
                f.teacher_display = [k.display for k in mine]
            elif set(intents) - {"holidays"}:
                return _reply("I couldn't match your account to a teacher name in the timetable for that period, "
                              "so I can't tell which classes are yours. Ask using the name shown in the timetable, "
                              "e.g. “classes of Dr. Sharma on 4th October”.")
            else:
                _note(notes, "I couldn't match your account to a teacher, so the holidays shown are for everyone.")
        elif not mode_teacher and first_person and not explicit_scope and (set(intents) & {"schedule", "changes", "next_class"}):
            return _reply("I can't tell who “I/my” is without logging in. Tell me a programme and semester "
                          "(e.g. “B.Tech 3rd sem”) or a teacher's name, or log in as a teacher.")
        if "next_class" in intents and not f.any and not f.self_mode:
            return _reply("Whose next class? Tell me a programme and semester (e.g. “B.Tech 3rd sem”) or a "
                          "teacher's name — or log in as a teacher and ask “what's my next class?”.")

        # ── 6. build sections ──────────────────────────────────────────────
        parts: list = []
        data: dict = {"intents": intents, "filters": {
            "programs": sorted(f.programs), "semesters": sorted(f.semesters),
            "teachers": f.teacher_display, "subjects": f.subject_display, "self": f.self_mode},
            "source": "llm+rules" if plan else "rules", "notes": notes}

        def views_for(sp: list) -> list:
            return [_view_day(days[d], d, f) for d in _dates_of(sp)]

        exclude = set(re.findall(r"[a-z0-9]+", " ".join(
            [k.raw for k in found_t] + [x for x in f.subject_display]).lower()))
        for it in intents:
            if it == "next_class":
                txt, rows = _next_class_section(db, f, today, plural_next, notes)
                parts.append(txt)
                data["next_class"] = rows
                continue
            sp = windows[it]
            vs = views_for(sp)
            data.setdefault("spans", [{"start": s.start.isoformat(), "end": s.end.isoformat()} for s in sp])
            if it == "schedule":
                parts.append(_section_schedule(vs, sp, f))
                data["days"] = [{"date": v.date.isoformat(), "weekday": WEEKDAYS[v.date.weekday()],
                                 "is_holiday": v.full_holiday,
                                 "holidays": [{"reason": x.get("reason"), "program": x.get("program"),
                                               "semester": x.get("semester")} for x in v.day_offs],
                                 "entries": [_clean_entry(e) for e in v.entries],
                                 "cancelled": [_clean_entry(e) for e in v.cancelled]} for v in vs]
            elif it == "changes":
                parts.append(_section_changes(vs, sp, f))
            elif it == "holidays":
                txt, groups = _section_holidays(vs, sp, f, t_left, exclude)
                parts.append(txt)
                data["holidays"] = [{"start": g.start.isoformat(), "end": g.end.isoformat(), "reason": g.reason,
                                     "program": g.program, "semester": g.semester} for g in groups]

        past = [s for s in spans if s.end < today]
        if past:
            _note(notes, "Part of that period is in the past — showing the calendar as it currently stands.")
        if notes:
            parts.append("\n".join(f"_ℹ️ {n}_" for n in notes))
        return _reply("\n\n".join(parts), data)

    except ParseError as exc:
        return _reply(str(exc))
    except HTTPException as exc:
        detail = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
        return _reply(f"I couldn't read the calendar for that request: {detail}")


# ═════════════════════════════════════════════════════════════════════════════
# 10. Public API: hook + endpoints
# ═════════════════════════════════════════════════════════════════════════════
def try_handle_calendar_question(message: str, db: Session, user: Any = None) -> Optional[dict]:
    """
    Hook for the EXISTING chat handlers. Returns a ChatResponse-shaped dict if the message is a
    calendar question, otherwise None (so the original logic runs unchanged). Never raises.
    """
    try:
        return answer_calendar_question(db, message, user, force=False, defer_actions=True)
    except Exception:  # noqa: BLE001
        log.exception("calendar chat hook failed; falling back to the original handler")
        return None


def handle_calendar_chat(message: str, db: Session, user: Any = None) -> dict:
    """Dedicated endpoint logic: always answers."""
    try:
        return answer_calendar_question(db, message, user, force=True, defer_actions=False) or _reply(HELP_TEXT)
    except Exception:  # noqa: BLE001
        log.exception("calendar chat failed")
        try:
            db.rollback()
        except Exception:
            pass
        return _reply("Sorry, I couldn't read the calendar right now. Please try again in a moment, "
                      "or check the timetable calendar directly.")


router = APIRouter(prefix="/calendar-chat", tags=["calendar-chat"])


@router.post("/public", response_model=ChatResponse)
def calendar_chat_public(req: ChatRequest, db: Session = Depends(get_db)):
    """Unauthenticated, read-only. 'I/my' needs a programme/semester or teacher name."""
    return handle_calendar_chat(req.message, db, user=None)


@router.post("/teacher", response_model=ChatResponse)
def calendar_chat_teacher(req: ChatRequest, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """Authenticated. 'I / my classes' = the logged-in teacher."""
    return handle_calendar_chat(req.message, db, user=user)


__all__ = ["router", "answer_calendar_question", "try_handle_calendar_question", "handle_calendar_chat"]
