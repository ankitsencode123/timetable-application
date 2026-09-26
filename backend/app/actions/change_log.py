"""
Human-readable change log generator for each action type.

Every successful action execution produces a formatted string that is
stored in the audit log and returned to the caller.
"""
from __future__ import annotations

from app.actions.types import ActionType


def format_change_log(action_type: ActionType, before: dict | None, after: dict | None,
                      extra: dict | None = None) -> str:
    """Return a multi-line human-readable change log for a single applied action."""
    lines: list[str] = []

    def _fmt_entry(e: dict | None, label: str) -> list[str]:
        if not e:
            return []
        return [
            f"  {label}:",
            f"    Program  : {e.get('program','?')} {e.get('semester','?')}",
            f"    Day      : {e.get('day','?')}  {e.get('start','?')}–{e.get('end','?')}",
            f"    Subject  : {e.get('subject_code','?')}  {e.get('subject_name','')}",
            f"    Teacher  : {e.get('teacher','?')}",
            f"    Type     : {e.get('type','?')}",
            f"    Room     : {e.get('room','?')}",
        ]

    if action_type == ActionType.ADD_CLASS:
        lines.append("✚ Added:")
        lines.extend(_fmt_entry(after, "New class"))

    elif action_type in (ActionType.REMOVE_CLASS, ActionType.CANCEL_CLASS):
        verb = "Cancelled" if action_type == ActionType.CANCEL_CLASS else "Removed"
        lines.append(f"✕ {verb}:")
        lines.extend(_fmt_entry(before, "Removed class"))

    elif action_type == ActionType.EXTEND_CLASS:
        lines.append("↗ Extended:")
        b_end = before.get("end", "?") if before else "?"
        a_end = after.get("end", "?")  if after  else "?"
        lines.extend(_fmt_entry(before, "Class"))
        lines.append(f"  End time: {b_end} → {a_end}")

    elif action_type == ActionType.SHORTEN_CLASS:
        lines.append("↙ Shortened:")
        b_end = before.get("end", "?") if before else "?"
        a_end = after.get("end", "?")  if after  else "?"
        lines.extend(_fmt_entry(before, "Class"))
        lines.append(f"  End time: {b_end} → {a_end}")

    elif action_type == ActionType.MOVE_CLASS:
        lines.append("↻ Moved:")
        lines.extend(_fmt_entry(before, "From"))
        if after:
            lines.append(
                f"  → {after.get('day','?')}  {after.get('start','?')}–{after.get('end','?')}"
            )

    elif action_type == ActionType.SWAP_CLASSES:
        lines.append("⇄ Swapped slots:")
        a_b = extra.get("a_before") if extra else before
        b_b = extra.get("b_before") if extra else after
        if a_b:
            lines.append(
                f"  A: {a_b.get('subject_code','?')} {a_b.get('day','?')} "
                f"{a_b.get('start','?')}–{a_b.get('end','?')}"
            )
        if b_b:
            lines.append(
                f"  B: {b_b.get('subject_code','?')} {b_b.get('day','?')} "
                f"{b_b.get('start','?')}–{b_b.get('end','?')}"
            )
        if extra:
            a_a = extra.get("a_after", {})
            b_a = extra.get("b_after", {})
            if a_a:
                lines.append(
                    f"  A→: {a_a.get('day','?')} {a_a.get('start','?')}–{a_a.get('end','?')}"
                )
            if b_a:
                lines.append(
                    f"  B→: {b_a.get('day','?')} {b_a.get('start','?')}–{b_a.get('end','?')}"
                )

    elif action_type == ActionType.INTERCHANGE_CLASSES:
        lines.append("↔ Interchanged:")
        a_b = extra.get("a_before") if extra else before
        b_b = extra.get("b_before") if extra else after
        lines.extend(_fmt_entry(a_b, "Class A (before)"))
        lines.extend(_fmt_entry(b_b, "Class B (before)"))

    elif action_type == ActionType.CHANGE_TEACHER:
        new_teacher = extra.get("new_teacher", after.get("teacher", "?") if after else "?") if extra else "?"
        old_teacher = before.get("teacher", "?") if before else "?"
        lines.append("👤 Teacher changed:")
        lines.extend(_fmt_entry(before, "Class"))
        lines.append(f"  Teacher: {old_teacher} → {new_teacher}")

    elif action_type == ActionType.CHANGE_ROOM:
        new_room = extra.get("new_room", after.get("room", "?") if after else "?") if extra else "?"
        old_room = before.get("room", "?") if before else "?"
        lines.append("🚪 Room changed:")
        lines.extend(_fmt_entry(before, "Class"))
        lines.append(f"  Room: {old_room} → {new_room}")

    elif action_type == ActionType.CHANGE_TIME:
        b_t = f"{before.get('start','?')}–{before.get('end','?')}" if before else "?"
        a_t = f"{after.get('start','?')}–{after.get('end','?')}"   if after  else "?"
        lines.append("🕐 Time changed:")
        lines.extend(_fmt_entry(before, "Class"))
        lines.append(f"  Time: {b_t} → {a_t}")

    elif action_type == ActionType.CHANGE_DAY:
        old_day = before.get("day", "?") if before else "?"
        new_day = after.get("day", "?")  if after  else "?"
        lines.append("📅 Day changed:")
        lines.extend(_fmt_entry(before, "Class"))
        lines.append(f"  Day: {old_day} → {new_day}")

    elif action_type == ActionType.REPLACE_CLASS:
        lines.append("⟳ Replaced:")
        lines.extend(_fmt_entry(before, "Old class"))
        lines.extend(_fmt_entry(after,  "New class"))

    elif action_type == ActionType.GENERATE_TIMETABLE:
        lines.append("🚀 Generated timetable.")
        if extra and "version_id" in extra:
            lines.append(f"  New version: #{extra['version_id']}")

    elif action_type == ActionType.OPTIMIZE_TIMETABLE:
        lines.append("⚙ Optimized timetable.")

    elif action_type == ActionType.VALIDATE_TIMETABLE:
        lines.append("✓ Validation complete.")
        if extra:
            vc = extra.get("violation_count", 0)
            lines.append(f"  Violations: {vc}")

    elif action_type == ActionType.RESTORE_VERSION:
        v_id = extra.get("version_id", "?") if extra else "?"
        lines.append(f"↩ Restored from version #{v_id}.")

    else:
        lines.append(f"Action: {action_type}")

    return "\n".join(lines)
