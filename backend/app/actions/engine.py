"""
Timetable Action Engine — the single orchestration point.

Responsibilities:
  1. Receive a list of ParsedAction objects.
  2. Fetch the current schedule from DB (or accept one explicitly).
  3. Simulate all actions in-memory (pure calls to executor.py).
  4. Run full H1–H11 validation on the combined result.
  5. If valid (or partial_ok) → persist as a new TimetableVersion child.
  6. Return structured results per action + overall success flag.

LLM is NEVER trusted to bypass validation. H1–H11 always runs.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.orm import Session

from app.actions.types import (
    ActionType,
    ParsedAction,
    AddClassAction,
    RemoveClassAction,
    CancelClassAction,
    ExtendClassAction,
    ShortenClassAction,
    MoveClassAction,
    SwapClassesAction,
    InterchangeClassesAction,
    ChangeTeacherAction,
    ChangeRoomAction,
    ChangeTimeAction,
    ChangeDayAction,
    ReplaceClassAction,
    GenerateTimetableAction,
    OptimizeTimetableAction,
    ValidateTimetableAction,
    RestoreVersionAction,
    AddTeacherBusyAction,
)
from app.actions import executor as ex
from app.actions.change_log import format_change_log
from app.actions.suggestions import suggest_alternatives
from app.scheduler.validator import validate_schedule, validate_schema, _entry_id
from app.models.timetable import TimetableVersion, VersionStatus
from app.models.timetable_entry import TimetableEntry
from app.models.user import User
from app.services import timetable_service, audit_service


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """Result of executing a single action."""
    action_type: str
    success: bool
    change_log: str = ""
    error: str = ""
    violated_constraint: Optional[Dict[str, Any]] = None
    suggestions: Optional[Dict[str, Any]] = None
    before: Optional[Any] = None
    after: Optional[Any] = None


@dataclass
class EngineResult:
    """Overall result of a multi-action execution."""
    success: bool
    results: List[ActionResult] = field(default_factory=list)
    new_version_id: Optional[int] = None
    violations: List[Dict[str, Any]] = field(default_factory=list)
    schema_errors: List[Dict[str, Any]] = field(default_factory=list)
    change_log: str = ""
    partial_applied: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_schedule(db: Session, version_id: Optional[int]) -> Tuple[List[dict], Optional[int]]:
    """
    Load schedule from DB. If version_id is None, uses the most recent version.
    Returns (entries_as_dicts, version_id_used).
    """
    if version_id:
        v = timetable_service.get_version(db, version_id)
    else:
        v = db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()

    if v is None:
        return [], None

    return [e.to_dict() for e in v.entries], v.id


def _save_as_draft(
    db: Session,
    user: User,
    schedule: List[dict],
    change_summary: str,
    parent_id: Optional[int],
) -> TimetableVersion:
    """Persist schedule as a new DRAFT version."""
    return timetable_service.create_draft(
        db=db,
        user=user,
        entries=schedule,
        change_summary=change_summary,
        parent_id=parent_id,
    )


# ---------------------------------------------------------------------------
# Single-action simulation
# ---------------------------------------------------------------------------

def _simulate_one(
    schedule: List[dict],
    action: ParsedAction,
    db: Session,
) -> Tuple[List[dict], ActionResult]:
    """
    Apply one action to a schedule copy.
    Returns (new_schedule, result).
    On failure, returns (original_schedule, failed_result).
    """
    atype = action.action

    try:
        if isinstance(action, AddClassAction):
            new_sched = ex.apply_add_class(schedule, action.spec)
            after = action.spec.to_schedule_dict()
            cl = format_change_log(atype, None, after)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, after=after
            )

        elif isinstance(action, RemoveClassAction):
            new_sched, removed = ex.apply_remove_class(schedule, action.target)
            cl = format_change_log(atype, removed, None)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=removed
            )

        elif isinstance(action, CancelClassAction):
            new_sched, removed = ex.apply_cancel_class(schedule, action.target)
            cl = format_change_log(atype, removed, None)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=removed
            )

        elif isinstance(action, ExtendClassAction):
            new_sched, before, after = ex.apply_extend_class(
                schedule, action.target, action.new_end_time
            )
            cl = format_change_log(atype, before, after)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, ShortenClassAction):
            new_sched, before, after = ex.apply_shorten_class(
                schedule, action.target, action.new_end_time
            )
            cl = format_change_log(atype, before, after)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, MoveClassAction):
            new_sched, before, after = ex.apply_move_class(
                schedule, action.target,
                action.new_day, action.new_start_time, action.new_end_time,
                action.new_room,
            )
            cl = format_change_log(atype, before, after)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, SwapClassesAction):
            new_sched, a_bef, a_aft, b_bef, b_aft = ex.apply_swap_classes(
                schedule, action.target_a, action.target_b
            )
            extra = {"a_before": a_bef, "a_after": a_aft,
                     "b_before": b_bef, "b_after": b_aft}
            cl = format_change_log(atype, a_bef, b_bef, extra=extra)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl,
                before={"a": a_bef, "b": b_bef},
                after={"a": a_aft, "b": b_aft},
            )

        elif isinstance(action, InterchangeClassesAction):
            new_sched, a_bef, a_aft, b_bef, b_aft = ex.apply_interchange_classes(
                schedule, action.target_a, action.target_b
            )
            extra = {"a_before": a_bef, "a_after": a_aft,
                     "b_before": b_bef, "b_after": b_aft}
            cl = format_change_log(atype, a_bef, b_bef, extra=extra)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl,
                before={"a": a_bef, "b": b_bef},
                after={"a": a_aft, "b": b_aft},
            )

        elif isinstance(action, ChangeTeacherAction):
            new_sched, before, after = ex.apply_change_teacher(
                schedule, action.target, action.new_teacher
            )
            cl = format_change_log(atype, before, after,
                                   extra={"new_teacher": action.new_teacher})
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, ChangeRoomAction):
            new_sched, before, after = ex.apply_change_room(
                schedule, action.target, action.new_room
            )
            cl = format_change_log(atype, before, after,
                                   extra={"new_room": action.new_room})
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, ChangeTimeAction):
            new_sched, before, after = ex.apply_change_time(
                schedule, action.target,
                action.new_start_time, action.new_end_time
            )
            cl = format_change_log(atype, before, after)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, ChangeDayAction):
            new_sched, before, after = ex.apply_change_day(
                schedule, action.target, action.new_day
            )
            cl = format_change_log(atype, before, after)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, ReplaceClassAction):
            new_sched, before, after = ex.apply_replace_class(
                schedule, action.target, action.new_spec
            )
            cl = format_change_log(atype, before, after)
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl, before=before, after=after
            )

        elif isinstance(action, AddTeacherBusyAction):
            from app.api.busy_slots import _entry_conflicts_busy
            from app.models.busy_slot import TeacherBusySlot
            
            # 1. Add busy slot object to DB session
            # (Will be committed eventually with the new draft, assuming this execution succeeds)
            slot = TeacherBusySlot(
                teacher_short_name=action.teacher_short_name,
                scope=action.scope,
                day_of_week=action.day_of_week,
                specific_date=action.specific_date,
                reason=action.reason
            )
            db.add(slot)
            
            # 2. Find affected classes
            affected = [e for e in schedule if e.get("teacher") == action.teacher_short_name and _entry_conflicts_busy(e, action.day_of_week)]
            
            new_sched = copy.deepcopy(schedule)
            moved, cancelled = [], []
            
            # 3. Simulate auto-reschedule
            for entry in affected:
                violation = {
                    "rule": "H6_teacher_free_day",
                    "a": entry,
                    "entry": entry,
                    "teacher": action.teacher_short_name,
                    "day": action.day_of_week,
                }
                sugg = suggest_alternatives(
                    schedule=new_sched,
                    action_type="MOVE_CLASS",
                    violation=violation,
                    teacher=action.teacher_short_name,
                    need_lab=(entry.get("type") == "Practical"),
                    orig_target={
                        "day": entry["day"],
                        "start_time": entry["start"],
                        "end_time": entry["end"],
                        "program": entry["program"],
                        "semester": entry["semester"],
                        "subject_code": entry["subject_code"],
                        "teacher": entry["teacher"],
                    },
                    busy_days={action.day_of_week} if action.day_of_week else None,
                )
                rich = sugg.get("rich_suggestions", [])
                if rich:
                    best = rich[0].get("action", {})
                    n_day = best.get("new_day", entry["day"])
                    n_start = best.get("new_start_time", entry["start"])
                    n_end = best.get("new_end_time", entry["end"])
                    n_room = best.get("new_room", entry.get("room", ""))
                    for i, e in enumerate(new_sched):
                        if (e.get("day") == entry["day"] and e.get("start") == entry["start"] and e.get("subject_code") == entry["subject_code"] and e.get("teacher") == action.teacher_short_name):
                            new_sched[i] = {**e, "day": n_day, "start": n_start, "end": n_end, "room": n_room}
                            break
                    moved.append(f"{entry['subject_code']} -> {n_day} {n_start}")
                else:
                    new_sched = [
                        e for e in new_sched 
                        if not (e.get("day") == entry["day"] and e.get("start") == entry["start"] and e.get("subject_code") == entry["subject_code"] and e.get("teacher") == action.teacher_short_name)
                    ]
                    cancelled.append(entry['subject_code'])

            msg_parts = [f"Marked {action.teacher_short_name} busy on {action.day_of_week}."]
            if moved: msg_parts.append(f"Auto-moved: {', '.join(moved)}.")
            if cancelled: msg_parts.append(f"Cancelled (no free slots): {', '.join(cancelled)}.")
                
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=" ".join(msg_parts)
            )

        elif isinstance(action, RestoreVersionAction):
            old_v = timetable_service.get_version(db, action.version_id)
            if not old_v:
                raise ValueError(f"Version {action.version_id} not found.")
            old_entries = [e.to_dict() for e in old_v.entries]
            new_sched = ex.apply_restore_version(old_entries)
            cl = format_change_log(atype, None, None,
                                   extra={"version_id": action.version_id})
            return new_sched, ActionResult(
                action_type=atype.value, success=True,
                change_log=cl
            )

        else:
            return schedule, ActionResult(
                action_type=atype.value, success=False,
                error=f"Action type {atype.value} not handled by simulation (use dedicated endpoint)."
            )

    except ValueError as e:
        error_str = str(e)
        if "No matching class found for" in error_str:
            error_str = "Class not found. It may have been recently modified or deleted by another user. Please refresh your timetable."
        return schedule, ActionResult(
            action_type=atype.value, success=False,
            error=error_str
        )


# ---------------------------------------------------------------------------
# Main Engine
# ---------------------------------------------------------------------------

class ActionEngine:
    """
    Orchestrates multi-action timetable mutations:
      parse → simulate → validate → commit (or reject).

    Args:
        db:         SQLAlchemy session.
        user:       Authenticated user performing the action(s).
        version_id: The version to mutate (None = latest).
        partial_ok: If True, apply successful sub-actions even when some fail.
                    Default = False (all-or-nothing).
    """

    def __init__(
        self,
        db: Session,
        user: User,
        version_id: Optional[int] = None,
        partial_ok: bool = False,
        skip_suggestions: bool = False,
    ):
        self.db = db
        self.user = user
        self.version_id = version_id
        self.partial_ok = partial_ok
        self.skip_suggestions = skip_suggestions

    # ------------------------------------------------------------------

    def execute(self, actions: List[ParsedAction]) -> EngineResult:
        """
        Execute a list of actions transactionally.

        Flow:
          1. Load current schedule from DB.
          2. Simulate each action sequentially on the running copy.
          3. Run full H1–H11 on the final combined schedule.
          4. If clean → commit as new TimetableVersion.
             If not clean and not partial_ok → reject all.
             If not clean and partial_ok → commit the partial set.
        """
        # ── Delegate non-schedule actions immediately ─────────────────────
        non_schedule = {
            ActionType.GENERATE_TIMETABLE,
            ActionType.OPTIMIZE_TIMETABLE,
            ActionType.VALIDATE_TIMETABLE,
            ActionType.ADD_TEACHER,
            ActionType.ADD_SUBJECT,
            ActionType.ADD_PROGRAM,
        }
        delegated_results = []
        schedule_actions = []
        for a in actions:
            if a.action in non_schedule:
                delegated_results.append(self._delegate(a))
            else:
                schedule_actions.append(a)

        if not schedule_actions:
            # Only meta-actions
            return EngineResult(
                success=all(r.success for r in delegated_results),
                results=delegated_results,
                change_log="\n\n".join(r.change_log for r in delegated_results),
            )

        # ── Load base schedule ────────────────────────────────────────────
        base_schedule, used_version_id = _load_schedule(self.db, self.version_id)
        current_schedule = copy.deepcopy(base_schedule)

        # Pre-compute base validations so we only reject actions that INTRODUCE new errors
        base_schema_errs = validate_schema(base_schedule)
        base_violations = validate_schedule(base_schedule)

        # H8/H9 violations are quantity-dependent: their actual_minutes change
        # whenever a class is added, making exact fingerprints false "new" errors.
        # We match these by (rule, program, semester, subject_code) instead.
        _QUANTITY_RULES = {"H8_wrong_weekly_hours", "H9_wrong_practical_duration"}

        def _fingerprint(err: dict) -> str:
            rule = err.get("rule", "")
            if rule in _QUANTITY_RULES:
                # Use only the identity fields — ignore the changing metric fields
                return json.dumps({
                    "rule": rule,
                    "subject_code": err.get("subject_code") or err.get("subject", ""),
                    "program": err.get("program", ""),
                    "semester": err.get("semester", ""),
                }, sort_keys=True)
            # For all other rules, location is part of identity.
            return json.dumps(err, sort_keys=True)

        def _get_new_errors(base_list: List[dict], current_list: List[dict]) -> List[dict]:
            base_reprs = [_fingerprint(x) for x in base_list]
            new_errs = []
            for item in current_list:
                item_fp = _fingerprint(item)
                if item_fp in base_reprs:
                    base_reprs.remove(item_fp)
                else:
                    new_errs.append(item)
            return new_errs

        results: List[ActionResult] = list(delegated_results)
        applied_actions: List[ParsedAction] = []
        applied_schedule = copy.deepcopy(base_schedule)

        # ── Simulate each sub-action ──────────────────────────────────────
        for action in schedule_actions:
            new_sched, result = _simulate_one(current_schedule, action, self.db)

            if not result.success:
                # Simulation error (target not found, etc.)
                if not self.partial_ok:
                    results.append(result)
                    # Short-circuit: do not apply any other action
                    return EngineResult(
                        success=False,
                        results=results,
                        violations=[],
                        schema_errors=[],
                        change_log="",
                    )
                else:
                    results.append(result)
                    continue

            # – Run H1–H11 on the intermediate schedule (fail-fast per action)
            schema_errs = validate_schema(new_sched)
            violations  = validate_schedule(new_sched)

            new_schema_errs = _get_new_errors(base_schema_errs, schema_errs)
            new_violations = _get_new_errors(base_violations, violations)

            if new_violations or new_schema_errs:
                # Build suggestions for first NEW violation
                first_v = new_violations[0] if new_violations else new_schema_errs[0]
                teacher = getattr(action, "new_teacher", None)
                need_lab = (
                    getattr(getattr(action, "spec", None), "entry_type", "") == "Practical"
                    or getattr(getattr(action, "new_spec", None), "entry_type", "") == "Practical"
                )
                orig_target = None
                if hasattr(action, "target"):
                    orig_target = action.target.model_dump(exclude_none=True)
                elif hasattr(action, "target_a"):
                    orig_target = action.target_a.model_dump(exclude_none=True)
                
                mutated_entry = None
                candidate_ids = [first_v.get(key) for key in ("entry", "a", "b") if isinstance(first_v.get(key), dict)]
                
                for c in new_sched:
                    if c not in current_schedule:
                        if _entry_id(c) in candidate_ids:
                            mutated_entry = c
                            break

                sugg = {}
                if not self.skip_suggestions:
                    sugg = suggest_alternatives(
                        current_schedule, action.action.value, first_v,
                        teacher=teacher, need_lab=need_lab, orig_target=orig_target,
                        mutated_entry=mutated_entry
                    )
                
                fail_result = ActionResult(
                    action_type=action.action.value,
                    success=False,
                    error=_violation_message(first_v),
                    violated_constraint=first_v,
                    suggestions=sugg,
                )
                results.append(fail_result)

                if not self.partial_ok:
                    return EngineResult(
                        success=False,
                        results=results,
                        violations=new_violations,
                        schema_errors=new_schema_errs,
                        change_log="",
                    )
                else:
                    continue  # skip this action, keep old schedule

            # – Accept this action
            results.append(result)
            applied_actions.append(action)
            current_schedule = new_sched
            applied_schedule = new_sched

            # Update base validations so subsequent actions in this batch use the new baseline
            base_schema_errs = schema_errs
            base_violations = violations

        # ── Final full validation on combined result ───────────────────────
        final_schema_errs = validate_schema(applied_schedule)
        final_violations  = validate_schedule(applied_schedule)
        
        # Determine if we INTRODUCED any new violations overall across the entire transaction
        overall_new_schema_errs = _get_new_errors(validate_schema(base_schedule), final_schema_errs)
        overall_new_violations = _get_new_errors(validate_schedule(base_schedule), final_violations)

        if overall_new_violations or overall_new_schema_errs:
            first_v = overall_new_violations[0] if overall_new_violations else {}
            return EngineResult(
                success=False,
                results=results,
                violations=overall_new_violations,
                schema_errors=overall_new_schema_errs,
                change_log="",
            )

        # ── All clean — persist ───────────────────────────────────────────
        change_summary = self._build_summary(applied_actions)
        new_version = _save_as_draft(
            db=self.db,
            user=self.user,
            schedule=applied_schedule,
            change_summary=change_summary,
            parent_id=used_version_id,
        )

        # Audit log
        audit_service.log(
            self.db,
            action="MULTI_ACTION_EXECUTE",
            user_id=self.user.id,
            version_id=new_version.id,
            params={"action_count": len(applied_actions)},
            before=base_schedule,
            after=applied_schedule,
        )

        combined_cl = "\n\n".join(
            r.change_log for r in results if r.success and r.change_log
        )

        return EngineResult(
            # A partial commit is persisted, but the caller must still know
            # that one or more requested actions were rejected.
            success=all(r.success for r in results),
            results=[r for r in results],
            new_version_id=new_version.id,
            violations=[],
            schema_errors=[],
            change_log=combined_cl,
            partial_applied=len(applied_actions) < len(schedule_actions),
        )

    # ------------------------------------------------------------------
    # Delegation for meta-actions
    # ------------------------------------------------------------------

    def _delegate(self, action: ParsedAction) -> ActionResult:
        """Handle GENERATE / OPTIMIZE / VALIDATE here without schedule mutation."""
        atype = action.action

        if isinstance(action, ValidateTimetableAction):
            vid = action.version_id or self.version_id
            schedule, _ = _load_schedule(self.db, vid)
            violations  = validate_schedule(schedule)
            schema_errs = validate_schema(schedule)
            cl = format_change_log(
                atype, None, None,
                extra={"violation_count": len(violations)}
            )
            return ActionResult(
                action_type=str(atype), success=True,
                change_log=cl,
                after={"violations": violations, "schema_errors": schema_errs},
            )

        elif isinstance(action, GenerateTimetableAction):
            from app.schemas.timetable import GenerateRequest
            from app.services.scheduler_service import generate_and_save
            req = GenerateRequest(
                subject_teacher_allocation=action.subject_teacher_allocation,
                teacher_preferences=action.teacher_preferences,
                room_information=action.room_information,
                extra_notes=action.extra_notes,
                change_summary=action.change_summary,
            )
            data = generate_and_save(req, self.user, self.db)
            cl = format_change_log(
                atype, None, None,
                extra={"version_id": data.get("version_id")}
            )
            return ActionResult(
                action_type=str(atype), success=True,
                change_log=cl,
                after={"version_id": data.get("version_id")},
            )

        elif isinstance(action, OptimizeTimetableAction):
            # Placeholder — future CP-SAT integration hook
            return ActionResult(
                action_type=str(atype), success=False,
                error="OPTIMIZE_TIMETABLE: CP-SAT optimizer not yet integrated."
            )

        elif atype in (ActionType.ADD_TEACHER, ActionType.ADD_SUBJECT, ActionType.ADD_PROGRAM):
            try:
                from app.scheduler import constraints as C
                if atype == ActionType.ADD_TEACHER:
                    from app.models.teacher import Teacher
                    from app.models.user import User, RoleEnum
                    from app.services.auth_service import create_user
                    import secrets
                    email = getattr(action, "email", None) or f"{action.short_name.lower()}@college.edu"
                    existing = self.db.query(User).filter(User.email == email).first()
                    password = secrets.token_urlsafe(8)
                    user_obj = existing or create_user(
                        self.db, email=email, password=password,
                        full_name=action.full_name, role=RoleEnum.TEACHER
                    )
                    t = Teacher(
                        user_id=user_obj.id,
                        short_name=action.short_name,
                        full_name=action.full_name,
                        subjects_csv=action.subjects_csv,
                        is_internal=action.is_internal,
                    )
                    self.db.add(t)
                    self.db.commit()
                    C.reload_catalog(self.db)
                    return ActionResult(
                        action_type=str(atype), success=True,
                        change_log=f"Added teacher {action.short_name} ({action.full_name}), login: {email}"
                    )

                elif atype == ActionType.ADD_SUBJECT:
                    from app.models.subject import Subject
                    s = Subject(
                        code=action.code.strip().lower(),
                        name=action.name,
                        program=action.program,
                        semester=action.semester,
                        entry_type=action.entry_type,
                        weekly_hours=action.weekly_hours,
                    )
                    self.db.add(s)
                    self.db.commit()
                    C.reload_catalog(self.db)
                    return ActionResult(
                        action_type=str(atype), success=True,
                        change_log=f"Added subject {action.code} '{action.name}' for {action.program} {action.semester}"
                    )

                elif atype == ActionType.ADD_PROGRAM:
                    from app.models.program import Program
                    p = Program(
                        name=action.name,
                        semesters_count=action.semesters_count,
                        description=action.description,
                    )
                    self.db.add(p)
                    self.db.commit()
                    C.reload_catalog(self.db)
                    return ActionResult(
                        action_type=str(atype), success=True,
                        change_log=f"Added program '{action.name}' with {action.semesters_count} semesters"
                    )
            except Exception as e:
                return ActionResult(
                    action_type=str(atype), success=False,
                    error=f"Catalog mutation failed: {e}"
                )

        return ActionResult(
            action_type=str(atype), success=False,
            error=f"Unhandled meta-action: {atype}"
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(actions: List[ParsedAction]) -> str:
        parts = [a.action.value for a in actions]
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:3]) + (f" (+{len(parts)-3} more)" if len(parts) > 3 else "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _violation_message(v: Dict[str, Any]) -> str:
    rule = v.get("rule", "unknown")
    entry = v.get("entry") or v.get("a") or {}

    msgs = {
        "schema_missing_keys":     f"Missing required fields: {', '.join(v.get('missing', []))}.",
        "schema_invalid_program":  f"Invalid program: {v.get('program', '?')}.",
        "schema_invalid_time_format": f"Invalid time format for {v.get('field')}: {v.get('value')}.",
        "H1_semester_clash":       f"**{entry.get('subject', '?')} and {v.get('b', {}).get('subject', '?')} overlap:** Both classes are scheduled for **{entry.get('program', '?')} {entry.get('semester', '?')} Semester, {entry.get('day', '?')}, {entry.get('start', '?')}–{entry.get('end', '?')}**.",
        "H2_teacher_clash":        f"**{','.join(v.get('teachers', [])) or v.get('teacher', '?')} double-booked:** Teacher is scheduled for overlapping classes on **{entry.get('day', '?')}, {entry.get('start', '?')}–{entry.get('end', '?')}**.",
        "H3_room_clash":           f"**{v.get('room','?')} double-booked:** Room is scheduled for multiple classes on **{entry.get('day', '?')}, {entry.get('start', '?')}–{entry.get('end', '?')}**.",
        "H4_room_not_lab":         f"**{entry.get('subject', '?')} requires lab:** **{entry.get('room','?')}** does not have laboratory facilities.",
        "H4_unknown_room":         f"**Unknown room:** **{entry.get('room','?')}** is not in the authoritative room list.",
        "H5_multiple_theory_same_day": f"**{v.get('teacher','?')} theory limit exceeded:** Teacher has multiple theory classes scheduled on **{v.get('day','?')}**.",
        "H5_multiple_practical_same_day": f"**{v.get('teacher','?')} practical limit exceeded:** Teacher has multiple practical sessions scheduled on **{v.get('day','?')}**.",
        "H6_no_free_day":          f"**{v.get('teacher','?')} has no free day:** Schedule violates continuous working constraints.",
        "H11_wrong_semester_subject": f"**{v.get('subject_code','?')} invalid:** Subject does not belong to **{entry.get('program','?')} {entry.get('semester','?')}**.",
        "start_not_before_end":    "**Invalid duration:** Start time must be before end time.",
    }
    msg_body = msgs.get(rule, v.get("note", rule))
    return f"Action blocked by a constraint\n\n{msg_body}"
