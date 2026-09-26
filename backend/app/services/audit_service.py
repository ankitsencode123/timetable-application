"""Audit logging service — write-only, never mutate existing records."""
from __future__ import annotations
import json
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.audit import AuditLog


def log(
    db: Session,
    *,
    action: str,
    user_id: Optional[int] = None,
    version_id: Optional[int] = None,
    before: Any = None,
    after: Any = None,
    params: Any = None,
    success: bool = True,
    error_detail: Optional[str] = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        version_id=version_id,
        action=action,
        before_json=json.dumps(before, default=str) if before is not None else None,
        after_json=json.dumps(after, default=str) if after is not None else None,
        params_json=json.dumps(params, default=str) if params is not None else None,
        success=success,
        error_detail=error_detail,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
