"""
fast_suggester.py  —  fast "next best feasible slot" engine for the timetable app.

WHY THE OLD APPROACH IS SLOW
  * every candidate = deepcopy(schedule) + validate_schedule() x2 (base is re-validated
    for every candidate), each O(n^2)
  * every validate_schedule() call opens a DB session (fetch_global_busy_days)
  * Tier-3 fallback = slots x rooms x 5 durations, each with the above

WHAT THIS MODULE DOES
  Tier 1  (pure Python, ~sub-millisecond per query)
      One ScheduleIndex per request: for every (semester | teacher | room, day) a bitmask of
      occupied 30-min cells.  "Is this class free at Tue 14:30-16:30?" is a few bit-ANDs.
      H1/H2/H3 (clashes), H5 (1 theory + 1 practical / teacher / day), H6 (internal teacher
      keeps a free day) and H12 (busy days) are evaluated incrementally. NO deepcopy, NO validate.
  Tier 2  (OR-Tools CP-SAT, only when Tier 1 finds nothing)
      A small repair model: place the target class AND optionally displace up to K blocking
      classes.  Objective = (fewest displaced classes) then (least time/room movement).
      Top-k distinct solutions via no-good cuts on the target's window.
  Verify  (optional)
      The real validate_schedule() is run once on the final <=3 suggestions only.

PUBLIC API
  suggest_relocations(...)          -> {"rich_suggestions": [...], "note": str, "timing_ms": {...}}
  find_valid_slots(...)             -> drop-in for suggestion._find_valid_slots
  universal_move_suggestion(...)    -> drop-in for suggestion._universal_move_suggestion
  find_valid_room(...)              -> drop-in for suggestion._find_valid_room (simple form)
  python fast_suggester.py          -> self-test + benchmark against brute force

Requires:  pip install ortools   (only for Tier 2; Tier 1 works without it)
"""
from __future__ import annotations

import heapq
import json
import random
import time
from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------------------
# Imports from the app (with a tiny standalone fallback so the self-test runs anywhere)
# --------------------------------------------------------------------------------------
try:
    from app.scheduler.constraints import (
        INTERNAL_TEACHERS, ROOM_FACILITIES, WORKING_DAYS, normalize_program,
    )
    from app.scheduler.validator import fetch_global_busy_days, teacher_set
    STANDALONE = False
except Exception:  # pragma: no cover - only used outside the app
    import re
    STANDALONE = True
    WORKING_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    ROOM_FACILITIES = {
        "R1": {"blackboard": True, "projector": True, "lab": False},
        "R2": {"blackboard": True, "projector": True, "lab": False},
        "R3": {"blackboard": True, "projector": False, "lab": False},
        "L1": {"blackboard": True, "projector": True, "lab": True},
        "L2": {"blackboard": True, "projector": True, "lab": True},
        "L3": {"blackboard": False, "projector": True, "lab": True},
    }
    INTERNAL_TEACHERS = {"SKS", "RKP", "SCh", "NC", "RD", "PB", "SK"}

    def normalize_program(p):
        if not p:
            return ""
        s = str(p).strip().rstrip(".").strip()
        k = s.lower().replace(" ", "")
        return {"b.tech": "B.Tech", "btech": "B.Tech", "m.tech": "M.Tech",
                "mtech": "M.Tech", "m.sc": "M.Sc", "msc": "M.Sc"}.get(k, s)

    def teacher_set(c):
        t = str(c.get("teacher", "") or "")
        return {p.strip() for p in re.split(r"[,&+/]", t) if p.strip()}

    def fetch_global_busy_days():
        return {}

# --------------------------------------------------------------------------------------
# Constants / small helpers
# --------------------------------------------------------------------------------------
CELL_MIN = 30                                   # bitmask resolution (minutes)
SEARCH_START_CELL = (10 * 60) // CELL_MIN       # 10:00
SEARCH_END_CELL = (17 * 60 + 30) // CELL_MIN    # 17:30 (exclusive end of last class)
STANDARD_DURATIONS = [120, 180, 90, 60, 150]
MAX_RANKED_SUGGESTIONS = 3
DISPLACE_PENALTY = 10_000                       # CP-SAT: cost of moving one *other* class
WD_SET = frozenset(WORKING_DAYS)


def _to_min(t: str) -> int:
    h, m = str(t).strip().split(":")
    return int(h) * 60 + int(m)


def _fmt_cell(cell: int) -> str:
    m = cell * CELL_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


def _normalize_duration(minutes: int) -> int:
    if minutes <= 0:
        return 120
    return min(STANDARD_DURATIONS, key=lambda d: abs(d - minutes))


def _sem_key(e: dict) -> Tuple[str, str]:
    """Same identity the validator uses for H1."""
    return (str(e.get("program", "")).strip().rstrip(".").lower(),
            str(e.get("semester", "")).strip().lower())


def _normalize_entry(entry: dict, need_lab: bool = False, teacher: Optional[str] = None) -> dict:
    """Mirror of the normalisation at the top of suggest_alternatives()."""
    e = dict(entry or {})
    if e.get("program"):
        e["program"] = normalize_program(e["program"])
    if "subject_code" not in e and "subject" in e:
        e["subject_code"] = e.get("subject", "")
    if "subject_name" not in e:
        e["subject_name"] = e.get("subject_code", "") or e.get("subject", "")
    if "type" not in e:
        e["type"] = "Practical" if need_lab or str(e.get("subject_code", "")).endswith("-p") else "Theory"
    e.setdefault("room", "")
    if teacher and not e.get("teacher"):
        e["teacher"] = teacher
    return e


# --------------------------------------------------------------------------------------
# Tier 1 — bitmask occupancy index
# --------------------------------------------------------------------------------------
class _Row:
    __slots__ = ("i", "entry", "day", "s", "e", "mask", "sem", "teachers", "room", "typ", "res")

    def __init__(self, i: int, e: dict):
        self.i, self.entry = i, e
        self.day = e.get("day")
        try:
            self.s = _to_min(e["start"]) // CELL_MIN
            self.e = -(-_to_min(e["end"]) // CELL_MIN)          # ceil
        except Exception:
            self.s = self.e = 0
        n = max(0, self.e - self.s)
        self.mask = ((1 << n) - 1) << self.s if n else 0
        self.sem = _sem_key(e)
        self.teachers = tuple(sorted(teacher_set(e)))
        self.room = e.get("room") or ""
        self.typ = str(e.get("type", "")).strip()
        res = {(0, self.sem)}
        res.update((1, t) for t in self.teachers)
        if self.room:
            res.add((2, self.room))
        self.res = frozenset(res)


class ScheduleIndex:
    """
    occ[(kind, key, day)] = [any_mask, multi_mask]
        kind 0 = semester group, 1 = teacher, 2 = room
        any_mask   : cells used by >= 1 class
        multi_mask : cells used by >= 2 classes
    Removing one class's own contribution is O(1):
        others = (any & ~own) | (multi & own)
    which also copes with pre-existing clashes in the base schedule.
    """

    def __init__(self, schedule: Sequence[dict], busy_map: Optional[Dict[str, Set[str]]] = None,
                 internal: Optional[Iterable[str]] = None):
        self.busy = busy_map or {}
        self.internal = internal if internal is not None else INTERNAL_TEACHERS
        self.rows: List[_Row] = []
        self.occ: Dict[tuple, List[int]] = {}
        self.by_res: Dict[tuple, List[_Row]] = defaultdict(list)
        self.t_type_day: Dict[tuple, int] = defaultdict(int)
        self.t_days: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for i, e in enumerate(schedule):
            r = _Row(i, e)
            self.rows.append(r)
            for kind, key in r.res:
                k = (kind, key, r.day)
                cell = self.occ.setdefault(k, [0, 0])
                cell[1] |= cell[0] & r.mask
                cell[0] |= r.mask
                self.by_res[k].append(r)
            for t in r.teachers:
                self.t_type_day[(t, r.day, r.typ)] += 1
                if r.day in WD_SET:
                    self.t_days[t][r.day] += 1
        self.all_rooms = sorted(ROOM_FACILITIES)
        self.lab_rooms = [r for r in self.all_rooms if ROOM_FACILITIES[r].get("lab")]

    # ---- row lookup (same matching rules as _simulate_move) --------------------------
    def find_row(self, props: Optional[dict]) -> Optional[_Row]:
        if not props:
            return None
        st = props.get("start_time", props.get("start"))
        ed = props.get("end_time", props.get("end"))
        prog, sem = props.get("program"), props.get("semester")
        subj = props.get("subject_code") or props.get("subject")
        for r in self.rows:
            c = r.entry
            if props.get("day") and c.get("day") != props["day"]:
                continue
            if st and c.get("start") != st:
                continue
            if ed and c.get("end") != ed:
                continue
            if prog and normalize_program(c.get("program")) != normalize_program(prog):
                continue
            if sem and c.get("semester") != sem:
                continue
            if subj:
                cs = c.get("subject_code") or c.get("subject")
                if cs and cs != subj and cs.lower() != subj.lower():
                    continue
            return r
        return None

    # ---- primitive checks -----------------------------------------------------------
    def blocked(self, kind: int, key, day: str, window: int, own: Optional[_Row]) -> bool:
        cell = self.occ.get((kind, key, day))
        if not cell:
            return False
        a, m = cell
        if own is not None and own.day == day and (kind, key) in own.res:
            a = (a & ~own.mask) | (m & own.mask)
        return bool(a & window)

    def day_ok(self, own: Optional[_Row], teachers: Iterable[str], typ: str, day: str) -> bool:
        """Slot-independent rules for putting this class on `day`: H12, H5, H6."""
        teachers = tuple(teachers)
        for t in teachers:
            if day in self.busy.get(t, ()):                       # H12
                return False
            if typ in ("Theory", "Practical") and (own is None or own.day != day):
                if self.t_type_day.get((t, day, typ), 0) >= 1:     # H5 / H5b
                    return False
        for t in teachers:                                         # H6 (only if it is *newly* broken)
            if t not in self.internal:
                continue
            days = self.t_days.get(t) or {}
            cur = {d for d, c in days.items() if c > 0}
            before_full = len(cur & WD_SET) == len(WD_SET)
            after = set(cur)
            if own is not None and t in own.teachers and days.get(own.day, 0) == 1:
                after.discard(own.day)
            if day in WD_SET:
                after.add(day)
            if len(after & WD_SET) == len(WD_SET) and not before_full:
                return False
        return True

    def pick_room(self, own: Optional[_Row], cur_room: str, day: str, window: int,
                  need_lab: bool, max_rooms: int = 1) -> List[str]:
        """Keep the current room if free (or empty); otherwise first free compatible rooms."""
        out: List[str] = []
        if not cur_room or not self.blocked(2, cur_room, day, window, own):
            out.append(cur_room)
        if len(out) >= max_rooms:
            return out
        for r in (self.lab_rooms if need_lab else self.all_rooms):
            if r == cur_room:
                continue
            if not self.blocked(2, r, day, window, own):
                out.append(r)
                if len(out) >= max_rooms:
                    break
        return out

    # ---- Tier 1 search ---------------------------------------------------------------
    def candidate_slots(
        self, entry: dict, own: Optional[_Row], *, need_lab: bool, dur_min: int,
        preferred_day: Optional[str] = None, preferred_start_min: Optional[int] = None,
        skip_slot: Optional[Tuple[str, int]] = None, banned_days: Iterable[str] = (),
        restrict: Optional[Sequence[dict]] = None, limit: Optional[int] = 3,
    ) -> List[dict]:
        """All (day,start[,room]) windows where `entry` can live with ZERO new violations,
        best-ranked first.  `restrict` = optional list of {"day","start","end"} to consider."""
        teachers = tuple(sorted(teacher_set(entry)))
        typ = str(entry.get("type", "")).strip()
        sem = _sem_key(entry)
        cur_room = entry.get("room") or ""
        banned = set(banned_days)

        if restrict is not None:
            domain = []
            for sl in restrict:
                try:
                    s = _to_min(sl["start"]) // CELL_MIN
                    d = max(1, -(-(_to_min(sl["end"]) - _to_min(sl["start"])) // CELL_MIN))
                except Exception:
                    continue
                domain.append((sl["day"], s, d, sl["start"], sl["end"]))
        else:
            d = max(1, dur_min // CELL_MIN)
            domain = [(day, s, d, _fmt_cell(s), _fmt_cell(s + d))
                      for day in WORKING_DAYS
                      for s in range(SEARCH_START_CELL, SEARCH_END_CELL - d + 1)]

        day_cache: Dict[str, bool] = {}
        scored: List[tuple] = []
        for day, s, d, st_s, en_s in domain:
            if day in banned or (skip_slot and skip_slot == (day, s)):
                continue
            ok = day_cache.get(day)
            if ok is None:
                ok = day_cache[day] = self.day_ok(own, teachers, typ, day)
            if not ok:
                continue
            window = ((1 << d) - 1) << s
            if self.blocked(0, sem, day, window, own):
                continue
            if any(self.blocked(1, t, day, window, own) for t in teachers):
                continue
            rooms = self.pick_room(own, cur_room, day, window, need_lab, max_rooms=1)
            if not rooms:
                continue
            slot = {"day": day, "start": st_s, "end": en_s}
            if rooms[0] != cur_room:
                slot["room"] = rooms[0]
            rank = (0 if day == preferred_day else 1,
                    abs(s * CELL_MIN - preferred_start_min) if preferred_start_min is not None else 0,
                    WORKING_DAYS.index(day), s)
            scored.append((rank, slot))
        if limit is None:
            scored.sort(key=lambda x: x[0])
            return [s for _, s in scored]
        return [s for _, s in heapq.nsmallest(limit, scored, key=lambda x: x[0])]


# --------------------------------------------------------------------------------------
# Tier 2 — CP-SAT repair (move target + displace <= K blockers)
# --------------------------------------------------------------------------------------
def _placements(frozen: ScheduleIndex, e: dict, dur_min: int, need_lab: bool,
                banned: Set[str], skip_slot: Optional[Tuple[str, int]],
                rooms_per_slot: int, only: Optional[Set[Tuple[str, int]]] = None) -> List[tuple]:
    teachers = tuple(sorted(teacher_set(e)))
    typ = str(e.get("type", "")).strip()
    sem = _sem_key(e)
    cur_room = e.get("room") or ""
    dc = max(1, dur_min // CELL_MIN)
    out: List[tuple] = []
    for day in WORKING_DAYS:
        if day in banned or not frozen.day_ok(None, teachers, typ, day):
            continue
        for s in range(SEARCH_START_CELL, SEARCH_END_CELL - dc + 1):
            if (skip_slot and skip_slot == (day, s)) or (only is not None and (day, s) not in only):
                continue
            w = ((1 << dc) - 1) << s
            if frozen.blocked(0, sem, day, w, None):
                continue
            if any(frozen.blocked(1, t, day, w, None) for t in teachers):
                continue
            for room in frozen.pick_room(None, cur_room, day, w, need_lab, max_rooms=rooms_per_slot):
                out.append((day, s, room))
    return out


def _cpsat_repair(
    idx: ScheduleIndex, entry: dict, own: Optional[_Row], *, need_lab: bool, dur_min: int,
    preferred_day: Optional[str], preferred_start_min: Optional[int],
    skip_slot: Optional[Tuple[str, int]], banned_days: Iterable[str],
    only: Optional[Set[Tuple[str, int]]] = None,
    k: int = MAX_RANKED_SUGGESTIONS, max_displaced: int = 2, max_movable: int = 10,
    rooms_per_slot: int = 3, blocker_domain_cap: int = 40,
    time_limit_s: float = 1.0, workers: int = 1,
) -> List[dict]:
    """Return up to k plans: [{"moves": [...], "cost": int}], each = target move + cascade."""
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return []

    teachers = tuple(sorted(teacher_set(entry)))
    typ = str(entry.get("type", "")).strip()
    sem = _sem_key(entry)
    dc = max(1, dur_min // CELL_MIN)
    banned = set(banned_days)

    # 1) which existing classes actually block the target? (sem / teacher / H5 same-day)
    counts: Dict[int, int] = defaultdict(int)
    for day in WORKING_DAYS:
        if day in banned or any(day in idx.busy.get(t, ()) for t in teachers):
            continue
        h5 = set()
        if typ in ("Theory", "Practical") and (own is None or own.day != day):
            for t in teachers:
                h5.update(r.i for r in idx.by_res.get((1, t, day), ()) if r is not own and r.typ == typ)
        for s in range(SEARCH_START_CELL, SEARCH_END_CELL - dc + 1):
            if skip_slot and skip_slot == (day, s):
                continue
            w = ((1 << dc) - 1) << s
            b = set(h5)
            for kind, key in [(0, sem)] + [(1, t) for t in teachers]:
                b.update(r.i for r in idx.by_res.get((kind, key, day), ())
                         if r is not own and r.mask & w)
            if b and len(b) <= max_displaced:
                for i in b:
                    counts[i] += 1
    if not counts:
        return []
    blockers = [i for i, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:max_movable]
                if idx.rows[i].mask]

    # 2) everything else is frozen -> its occupancy prunes domains up-front
    exclude = set(blockers) | ({own.i} if own else set())
    frozen = ScheduleIndex([r.entry for r in idx.rows if r.i not in exclude], idx.busy, idx.internal)

    # 3) movable entities: index 0 = target, then blockers
    ents: List[dict] = [dict(entry)]
    meta: List[dict] = [{"row": own.i if own else None, "dur": dur_min,
                         "oday": preferred_day, "ostart": preferred_start_min,
                         "oroom": entry.get("room") or "", "stay": None}]
    doms: List[List[tuple]] = [_placements(frozen, entry, dur_min, need_lab, banned, skip_slot,
                                           rooms_per_slot, only)]
    for bi in blockers:
        r = idx.rows[bi]
        bdur = (r.e - r.s) * CELL_MIN
        stay = (r.day, r.s, r.room)
        dom = _placements(frozen, r.entry, bdur, r.typ == "Practical", set(), None, 1)
        # a displaced class only needs its *cheapest* alternatives (objective prefers small moves)
        dom.sort(key=lambda p: (0 if p[0] == r.day else 600) + abs(p[1] - r.s) * CELL_MIN
                 + (0 if p[2] == r.room else 30))
        dom = dom[:blocker_domain_cap]
        if stay not in dom:
            dom.append(stay)
        ents.append(r.entry)
        meta.append({"row": bi, "dur": bdur, "oday": r.day, "ostart": r.s * CELL_MIN,
                     "oroom": r.room, "stay": stay})
        doms.append(dom)
    if not doms[0]:
        return []

    # 4) model ------------------------------------------------------------------------
    model = cp_model.CpModel()
    cells: Dict[tuple, List[tuple]] = defaultdict(list)     # resource-cell -> [(var, entity)]
    h5g: Dict[tuple, List[tuple]] = defaultdict(list)
    tday: Dict[tuple, List[Any]] = defaultdict(list)
    var_of: List[List[tuple]] = []
    cost_terms, displaced_terms = [], []
    teacher_pool: Set[str] = set()

    for mi, (e, mt, dom) in enumerate(zip(ents, meta, doms)):
        ts = tuple(sorted(teacher_set(e)))
        ty = str(e.get("type", "")).strip()
        sk = _sem_key(e)
        teacher_pool.update(ts)
        mdc = max(1, mt["dur"] // CELL_MIN)
        lits, row = [], []
        for (day, s, room) in dom:
            v = model.NewBoolVar("")
            lits.append(v)
            row.append((v, day, s, room))
            for c in range(s, s + mdc):
                cells[(0, sk, day, c)].append((v, mi))
                for t in ts:
                    cells[(1, t, day, c)].append((v, mi))
                if room:
                    cells[(2, room, day, c)].append((v, mi))
            if ty in ("Theory", "Practical"):
                for t in ts:
                    h5g[(t, day, ty)].append((v, mi))
            for t in ts:
                tday[(t, day)].append(v)
            is_stay = mt["stay"] == (day, s, room)
            if mi == 0:
                cost = ((0 if day == mt["oday"] else 600)
                        + (abs(s * CELL_MIN - mt["ostart"]) if mt["ostart"] is not None else 0)
                        + (0 if room == mt["oroom"] else 30))
            elif is_stay:
                cost = 0
            else:
                cost = (DISPLACE_PENALTY + (0 if day == mt["oday"] else 600)
                        + abs(s * CELL_MIN - mt["ostart"]) + (0 if room == mt["oroom"] else 30))
                displaced_terms.append(v)
            if cost:
                cost_terms.append(cost * v)
        model.AddExactlyOne(lits)
        var_of.append(row)

    for group in list(cells.values()) + list(h5g.values()):
        if len(group) > 1 and len({mi for _, mi in group}) > 1:
            model.AddAtMostOne([v for v, _ in group])

    for t in teacher_pool:                                    # H6, only if not already broken
        if t not in idx.internal:
            continue
        base_days = {d for d, c in (idx.t_days.get(t) or {}).items() if c > 0}
        if len(base_days & WD_SET) == len(WD_SET):
            continue
        fdays = {d for d, c in (frozen.t_days.get(t) or {}).items() if c > 0}
        allowed = len(WD_SET) - 1 - len(fdays)
        if allowed < 0:
            continue
        used = []
        for d in WORKING_DAYS:
            if d in fdays or not tday.get((t, d)):
                continue
            u = model.NewBoolVar("")
            for v in tday[(t, d)]:
                model.AddImplication(v, u)
            used.append(u)
        if used:
            model.Add(sum(used) <= allowed)

    if displaced_terms:
        model.Add(sum(displaced_terms) <= max_displaced)
    if cost_terms:
        model.Minimize(sum(cost_terms))
    for mi in range(1, len(ents)):                             # warm start: everyone stays
        for v, day, s, room in var_of[mi]:
            model.AddHint(v, 1 if meta[mi]["stay"] == (day, s, room) else 0)

    # 5) top-k distinct target windows via no-good cuts -------------------------------
    plans: List[dict] = []
    for _ in range(k):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        try:
            solver.parameters.num_workers = workers
        except Exception:
            solver.parameters.num_search_workers = workers
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        moves = []
        for mi, row in enumerate(var_of):
            for v, day, s, room in row:
                if solver.Value(v):
                    mt = meta[mi]
                    if mi > 0 and mt["stay"] == (day, s, room):
                        break
                    moves.append({"row": mt["row"], "entry": ents[mi], "day": day,
                                  "start": _fmt_cell(s), "end": _fmt_cell(s + max(1, mt["dur"] // CELL_MIN)),
                                  "room": room, "room_changed": room != mt["oroom"]})
                    if mi == 0:
                        tw = (day, s)
                    break
        plans.append({"moves": moves, "cost": int(solver.ObjectiveValue()) if cost_terms else 0})
        for v, day, s, _r in var_of[0]:
            if (day, s) == tw:
                model.Add(v == 0)
    return plans


# --------------------------------------------------------------------------------------
# Caches (one DB hit per TTL, one index per distinct schedule)
# --------------------------------------------------------------------------------------
_BUSY_CACHE: Tuple[float, Dict[str, Set[str]]] = (0.0, {})
_IDX_CACHE: Tuple[Optional[tuple], Optional[ScheduleIndex]] = (None, None)


def get_busy_map(ttl: float = 30.0) -> Dict[str, Set[str]]:
    global _BUSY_CACHE
    ts, val = _BUSY_CACHE
    if time.monotonic() - ts > ttl:
        val = fetch_global_busy_days()
        _BUSY_CACHE = (time.monotonic(), val)
    return val


def warmup() -> None:
    """Call once at app startup: pre-imports OR-Tools (~0.3 s) and primes the busy-days cache."""
    try:
        from ortools.sat.python import cp_model  # noqa: F401
    except ImportError:
        pass
    get_busy_map()


def invalidate_caches() -> None:
    """Call after the timetable or busy-slots change."""
    global _BUSY_CACHE, _IDX_CACHE
    _BUSY_CACHE, _IDX_CACHE = (0.0, {}), (None, None)


def get_index(schedule: Sequence[dict], busy_map: Optional[dict] = None) -> ScheduleIndex:
    global _IDX_CACHE
    busy = busy_map if busy_map is not None else get_busy_map()
    key = (len(schedule), id(busy), hash(tuple(
        (e.get("day"), e.get("start"), e.get("end"), e.get("room"), e.get("teacher"),
         e.get("program"), e.get("semester"), e.get("subject_code"), e.get("type")) for e in schedule)))
    ckey, cidx = _IDX_CACHE
    if ckey == key and cidx is not None:
        return cidx
    idx = ScheduleIndex(schedule, busy)
    _IDX_CACHE = (key, idx)
    return idx


# --------------------------------------------------------------------------------------
# Plan application + optional verification with the REAL validator (final <=3 only)
# --------------------------------------------------------------------------------------
def _apply_plan(schedule: Sequence[dict], plan_moves: List[dict]) -> List[dict]:
    new = list(schedule)
    for m in plan_moves:
        if m["row"] is None:
            e = dict(m["entry"])
            new.append(e)
        else:
            e = dict(new[m["row"]])
            new[m["row"]] = e
        e["day"], e["start"], e["end"] = m["day"], m["start"], m["end"]
        if m.get("room") is not None:
            e["room"] = m["room"]
    return new


def _default_validator(schedule: list) -> List[dict]:
    from app.scheduler.validator import validate_schedule, validate_schema
    return validate_schedule(schedule) + validate_schema(schedule)


def _new_violations(base: List[dict], cand: List[dict]) -> int:
    bag = [json.dumps(v, sort_keys=True) for v in base]
    n = 0
    for v in cand:
        fp = json.dumps(v, sort_keys=True)
        if fp in bag:
            bag.remove(fp)
        else:
            n += 1
    return n


# --------------------------------------------------------------------------------------
# Public drop-ins (same shapes as the old helpers)
# --------------------------------------------------------------------------------------
def _context(schedule, entry, orig_target, need_lab, teacher, simulate_as_add, busy_days):
    idx = get_index(schedule)
    entry = _normalize_entry(entry, need_lab, teacher)
    if orig_target:
        orig_target = dict(orig_target)
        if orig_target.get("program"):
            orig_target["program"] = normalize_program(orig_target["program"])
    own = None if simulate_as_add else idx.find_row(orig_target or entry)
    pref_day = entry.get("day") or (orig_target or {}).get("day")
    pref_start = entry.get("start") or (orig_target or {}).get("start_time") or (orig_target or {}).get("start")
    pref_min = _to_min(pref_start) if pref_start else None
    src_start = (orig_target or {}).get("start_time") or (orig_target or {}).get("start")
    skip = ((orig_target or {}).get("day"), _to_min(src_start) // CELL_MIN) \
        if orig_target and orig_target.get("day") and src_start else None
    # merge permanent busy days of the teacher(s) with caller-supplied ones
    banned = set(busy_days or ())
    for t in teacher_set(entry):
        banned |= idx.busy.get(t, set())
    return idx, entry, own, pref_day, pref_min, skip, banned


def find_valid_slots(schedule, entry, free_slots=None, need_lab=False, orig_target=None,
                     simulate_as_add=False, limit=MAX_RANKED_SUGGESTIONS, busy_days=None,
                     teacher=None, dur_min=None, allow_cascade=True, **cp_kwargs) -> List[dict]:
    """Drop-in for suggestion._find_valid_slots.  `free_slots` (optional) restricts the search
    domain, e.g. the H5 'other days only' list.  Slots may carry a 'cascade' key (Tier 2)."""
    idx, e, own, pref_day, pref_min, skip, banned = _context(
        schedule, entry, orig_target, need_lab, teacher, simulate_as_add, busy_days)
    need_lab = need_lab or e.get("type") == "Practical"
    if dur_min is None:
        try:
            dur_min = _to_min(e["end"]) - _to_min(e["start"])
        except Exception:
            dur_min = 120
    dur_min = _normalize_duration(dur_min)
    res = idx.candidate_slots(e, own, need_lab=need_lab, dur_min=dur_min, preferred_day=pref_day,
                              preferred_start_min=pref_min, skip_slot=skip, banned_days=banned,
                              restrict=free_slots, limit=limit)
    if res or not allow_cascade:
        return res
    only = None
    if free_slots is not None:
        only = {(s["day"], _to_min(s["start"]) // CELL_MIN) for s in free_slots}
    plans = _cpsat_repair(idx, e, own, need_lab=need_lab, dur_min=dur_min, preferred_day=pref_day,
                          preferred_start_min=pref_min, skip_slot=skip, banned_days=banned,
                          only=only, k=limit, **cp_kwargs)
    return [_plan_to_slot(p) for p in plans]


def _plan_to_slot(plan: dict) -> dict:
    p = plan["moves"][0]
    slot = {"day": p["day"], "start": p["start"], "end": p["end"], "_plan": plan}
    if p["room_changed"]:
        slot["room"] = p["room"]
    if len(plan["moves"]) > 1:
        slot["cascade"] = [{k: m[k] for k in ("row", "day", "start", "end", "room")} for m in plan["moves"][1:]]
    return slot


def universal_move_suggestion(schedule, entry, action_type, need_lab, orig_target, teacher,
                              duration_minutes, busy_days=None, allow_duration_fallback=True,
                              allow_cascade=True, **cp_kwargs) -> Optional[dict]:
    """Drop-in for suggestion._universal_move_suggestion (returns ONE slot dict or None).
    Tiers: normalised duration -> other standard durations -> CP-SAT repair."""
    is_add = action_type == "ADD_CLASS"
    norm = _normalize_duration(duration_minutes)
    durs = [norm] + ([d for d in STANDARD_DURATIONS if d != norm] if allow_duration_fallback else [])
    for d in durs:
        r = find_valid_slots(schedule, entry, None, need_lab, orig_target, is_add, 1, busy_days,
                             teacher, dur_min=d, allow_cascade=False)
        if r:
            return r[0]
    if allow_cascade:
        r = find_valid_slots(schedule, entry, None, need_lab, orig_target, is_add, 1, busy_days,
                             teacher, dur_min=norm, allow_cascade=True, **cp_kwargs)
        if r:
            return r[0]
    return None


def find_valid_room(schedule, entry, free_rooms, orig_target=None, simulate_as_move=False,
                    simulate_as_add=False, target_day="", target_start="", target_end=""):
    """Drop-in for suggestion._find_valid_room: first room in free_rooms that creates no new violation."""
    idx = get_index(schedule)
    e = _normalize_entry(entry)
    own = None if simulate_as_add else idx.find_row(orig_target or e)
    day = target_day if (simulate_as_move or simulate_as_add) else e.get("day")
    st = target_start if (simulate_as_move or simulate_as_add) else e.get("start")
    en = target_end if (simulate_as_move or simulate_as_add) else e.get("end")
    s, ee = _to_min(st) // CELL_MIN, -(-_to_min(en) // CELL_MIN)
    window = ((1 << (ee - s)) - 1) << s
    if (simulate_as_move or simulate_as_add):
        teachers = tuple(sorted(teacher_set(e)))
        typ = str(e.get("type", "")).strip()
        if not idx.day_ok(own, teachers, typ, day):
            return None
        if idx.blocked(0, _sem_key(e), day, window, own) or any(
                idx.blocked(1, t, day, window, own) for t in teachers):
            return None
    for r in free_rooms:
        if not idx.blocked(2, r, day, window, own):
            return r
    return None


def suggest_relocations(
    schedule: List[dict], action_type: str, entry: dict, *, orig_target: Optional[dict] = None,
    need_lab: bool = False, teacher: Optional[str] = None, busy_days: Optional[set] = None,
    free_slots: Optional[List[dict]] = None, limit: int = MAX_RANKED_SUGGESTIONS,
    verify: bool = False, validator: Optional[Callable[[list], list]] = None,
    allow_cascade: bool = True, **cp_kwargs,
) -> Dict[str, Any]:
    """End-to-end replacement for the slot-search part of suggest_alternatives()."""
    t0 = time.perf_counter()
    is_add = action_type == "ADD_CLASS"
    e = _normalize_entry(entry, need_lab, teacher)
    slots = find_valid_slots(schedule, e, free_slots, need_lab, orig_target, is_add, limit,
                             busy_days, teacher, allow_cascade=allow_cascade, **cp_kwargs)
    t1 = time.perf_counter()
    tgt = orig_target or {"day": e.get("day"), "start_time": e.get("start"), "end_time": e.get("end"),
                          "program": e.get("program"), "semester": e.get("semester"),
                          "subject_code": e.get("subject_code") or e.get("subject")}
    out: Dict[str, Any] = {"rich_suggestions": []}
    base_v = None
    validator = validator or (None if STANDALONE else _default_validator)
    idx = get_index(schedule)
    own = None if is_add else idx.find_row(orig_target or e)

    for slot in slots:
        plan = slot.get("_plan") or {"moves": [{"row": own.i if own else None, "entry": e,
                                                 "day": slot["day"], "start": slot["start"],
                                                 "end": slot["end"], "room": slot.get("room", e.get("room", ""))}]}
        if verify and validator:
            if base_v is None:
                base_v = validator(list(schedule))
            if _new_violations(base_v, validator(_apply_plan(schedule, plan["moves"]))):
                continue
        room_txt = f" in {slot['room']}" if "room" in slot else ""
        if is_add:
            action: Dict[str, Any] = {"action": "ADD_CLASS", "spec": {
                "program": e.get("program"), "semester": e.get("semester"), "day": slot["day"],
                "start_time": slot["start"], "end_time": slot["end"],
                "subject_code": e.get("subject_code", ""), "subject_name": e.get("subject_name", ""),
                "teacher": e.get("teacher", ""), "entry_type": e.get("type", "Theory"),
                "room": slot.get("room", e.get("room", ""))}}
        else:
            action = {"action": "MOVE_CLASS", "target": tgt, "new_day": slot["day"],
                      "new_start_time": slot["start"], "new_end_time": slot["end"]}
            if "room" in slot:
                action["new_room"] = slot["room"]
        desc = "Conflict-free and validated."
        if "cascade" in slot:
            action["cascade"] = []
            for m in plan["moves"][1:]:
                ce = m["entry"]
                action["cascade"].append({
                    "action": "MOVE_CLASS",
                    "target": {"day": ce.get("day"), "start_time": ce.get("start"), "end_time": ce.get("end"),
                               "program": ce.get("program"), "semester": ce.get("semester"),
                               "subject_code": ce.get("subject_code")},
                    "new_day": m["day"], "new_start_time": m["start"], "new_end_time": m["end"],
                    "new_room": m["room"]})
            desc = (f"Requires also moving {len(action['cascade'])} other class(es) "
                    f"({', '.join(c['target']['subject_code'] for c in action['cascade'])}). "
                    "Apply the whole bundle atomically.")
        name = e.get("subject_code") or e.get("subject") or "Class"
        out["rich_suggestions"].append({
            "title": f"{'Add' if is_add else 'Move'} {name} to {slot['day']} {slot['start']}–{slot['end']}{room_txt}",
            "description": desc, "status": "Conflict-free and validated", "action": action})
    out["note"] = ("" if out["rich_suggestions"] else
                   "No conflict-free slot found, even after displacing up to "
                   f"{cp_kwargs.get('max_displaced', 2)} other classes.")
    out["timing_ms"] = {"search": round((t1 - t0) * 1000, 2),
                        "total": round((time.perf_counter() - t0) * 1000, 2)}
    return out


# --------------------------------------------------------------------------------------
# Self-test + benchmark:  python fast_suggester.py
# Compares Tier 1 against a brute-force "simulate + full validate" reference that mirrors
# the old pipeline's semantics (H1/H2/H3/H5/H6/H12, fingerprint-based "new violations").
# --------------------------------------------------------------------------------------
def _ref_validate(schedule: list, busy: dict) -> List[dict]:
    def eid(c):
        return {k: c.get(k) for k in ("day", "program", "semester", "start", "end", "subject_code",
                                      "teacher", "room")}

    def ov(a, b):
        return _to_min(a["start"]) < _to_min(b["end"]) and _to_min(b["start"]) < _to_min(a["end"])

    v, th, pr, td = [], defaultdict(int), defaultdict(int), defaultdict(set)
    for i in range(len(schedule)):
        for j in range(i + 1, len(schedule)):
            a, b = schedule[i], schedule[j]
            if a["day"] != b["day"] or not ov(a, b):
                continue
            if _sem_key(a) == _sem_key(b):
                v.append({"rule": "H1", "a": eid(a), "b": eid(b)})
            sh = teacher_set(a) & teacher_set(b)
            if sh:
                v.append({"rule": "H2", "t": sorted(sh), "a": eid(a), "b": eid(b)})
            if a.get("room") and a["room"] == b.get("room"):
                v.append({"rule": "H3", "a": eid(a), "b": eid(b)})
    for c in schedule:
        for t in teacher_set(c):
            if c["day"] in busy.get(t, ()):
                v.append({"rule": "H12", "t": t, "entry": eid(c)})
            (th if c["type"] == "Theory" else pr)[(t, c["day"])] += 1
            td[t].add(c["day"])
    v += [{"rule": "H5", "t": k} for k, n in th.items() if n > 1]
    v += [{"rule": "H5b", "t": k} for k, n in pr.items() if n > 1]
    for t in INTERNAL_TEACHERS:
        if td.get(t) and not [d for d in WORKING_DAYS if d not in td[t]]:
            v.append({"rule": "H6", "t": t})
    return v


def _selftest() -> None:
    rnd = random.Random(7)
    busy = {"EXT3": {"Saturday"}, "SK": {"Friday"}}
    rooms = sorted(ROOM_FACILITIES)
    internal = ["SKS", "RKP", "SCh", "NC", "RD", "PB", "SK"]
    teachers = internal + [f"EXT{i}" for i in range(45)]          # mostly visiting faculty
    groups = [("B.Tech", "3rd"), ("B.Tech", "5th"), ("M.Sc", "1st"), ("M.Tech", "1st")]
    sched = []
    for n in range(140):
        p, s = rnd.choice(groups)
        d = rnd.choice([2, 3, 4]) * 1
        st = rnd.randrange(SEARCH_START_CELL, SEARCH_END_CELL - d + 1)
        prac = rnd.random() < 0.3
        sched.append({"day": rnd.choice(WORKING_DAYS), "program": p, "semester": s,
                      "start": _fmt_cell(st), "end": _fmt_cell(st + d), "subject_code": f"S{n}",
                      "subject_name": f"S{n}", "teacher": rnd.choice(teachers),
                      "type": "Practical" if prac else "Theory",
                      "room": rnd.choice([r for r in rooms if ROOM_FACILITIES[r]["lab"]] if prac else rooms)})

    print(f"== Tier 1 correctness vs brute force (schedule of {len(sched)} classes, "
          f"pre-existing violations included) ==")
    base_v = [json.dumps(x, sort_keys=True) for x in _ref_validate(sched, busy)]
    idx = ScheduleIndex(sched, busy)
    mism = checked = quirks = 0
    t_fast = t_ref = 0.0
    for ei in rnd.sample(range(len(sched)), 12):
        e = sched[ei]
        dur = _to_min(e["end"]) - _to_min(e["start"])
        own = idx.find_row(e)
        t0 = time.perf_counter()
        fast = {(s["day"], s["start"]) for s in idx.candidate_slots(
            e, own, need_lab=e["type"] == "Practical", dur_min=dur,
            skip_slot=(e["day"], _to_min(e["start"]) // CELL_MIN), limit=None)}
        t_fast += time.perf_counter() - t0
        t0 = time.perf_counter()
        ref = set()
        d = dur // CELL_MIN
        for day in WORKING_DAYS:
            for s in range(SEARCH_START_CELL, SEARCH_END_CELL - d + 1):
                if (day, s) == (e["day"], _to_min(e["start"]) // CELL_MIN):
                    continue
                rm_try = [e["room"]] + [r for r in rooms if r != e["room"] and
                                        (e["type"] != "Practical" or ROOM_FACILITIES[r]["lab"])]
                for room in rm_try:
                    cand = list(sched)
                    cand[ei] = {**e, "day": day, "start": _fmt_cell(s), "end": _fmt_cell(s + d), "room": room}
                    bag, new = list(base_v), 0
                    for x in _ref_validate(cand, busy):
                        fp = json.dumps(x, sort_keys=True)
                        if fp in bag:
                            bag.remove(fp)
                        else:
                            new += 1
                    if new == 0:
                        ref.add((day, _fmt_cell(s)))
                        break
        t_ref += time.perf_counter() - t0
        checked += 1
        # old validator quirk: H5 fingerprint is (teacher, day) only, so adding a 3rd class to a
        # teacher-day that ALREADY violates H5 looks "not new".  The index (rightly) refuses it.
        ts = teacher_set(e)
        quirk = {w for w in ref - fast
                 if any(idx.t_type_day.get((t, w[0], e["type"]), 0) >= 2 for t in ts)}
        quirks += len(quirk)
        if fast != ref and not (fast <= ref and (ref - fast) <= quirk):
            mism += 1
            print("  MISMATCH", e, dur, "fast-only:", sorted(fast - ref)[:3],
                  "ref-only:", sorted(ref - fast)[:3])
    print(f"  entries checked: {checked}, real mismatches: {mism}  "
          f"(+{quirks} windows the OLD validator wrongly accepts: 3rd class on an already-violating teacher-day)")
    print(f"  brute force (simulate+validate): {t_ref * 1000:8.1f} ms total")
    print(f"  bitmask index                  : {t_fast * 1000:8.1f} ms total  "
          f"(~{t_ref / max(t_fast, 1e-9):.0f}x faster)")

    print("\n== Tier 2: fragmented semester -> no free 2h window, CP-SAT displaces a blocker ==")
    dense = []
    pat = [("09:00", "11:00"), ("11:30", "13:30"), ("14:00", "16:00")]
    for di, day in enumerate(WORKING_DAYS):
        for pi, (a, b) in enumerate(pat):
            dense.append({"day": day, "program": "B.Tech", "semester": "3rd", "start": a, "end": b,
                          "subject_code": f"D{di}{pi}", "subject_name": "x", "teacher": f"X{di}{pi}",
                          "type": "Theory", "room": rooms[(di + pi) % 3]})
    new_cls = {"day": "Monday", "program": "B.Tech", "semester": "3rd", "start": "09:00", "end": "11:00",
               "subject_code": "NEW", "subject_name": "New", "teacher": "NT", "type": "Theory", "room": "R1"}
    invalidate_caches()
    warmup()
    t0 = time.perf_counter()
    res = suggest_relocations(dense, "ADD_CLASS", new_cls, limit=3, validator=lambda s: _ref_validate(s, {}),
                              verify=True, time_limit_s=1.0)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  tier-1 empty -> CP-SAT repair in {ms:.0f} ms, {len(res['rich_suggestions'])} verified suggestion(s)")
    for s in res["rich_suggestions"]:
        print("  •", s["title"], "|", s["description"])
        for c in s["action"].get("cascade", []):
            print("      also:", c["target"]["subject_code"], "->", c["new_day"], c["new_start_time"],
                  c["new_end_time"], c["new_room"])
    print("  timing:", res["timing_ms"])


if __name__ == "__main__":
    _selftest()
