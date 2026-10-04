"""Authentication service — login, token refresh, logout, password management."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.security import (
    verify_password, hash_password,
    create_access_token, create_refresh_token, decode_token,
)
from app.models.user import User, RoleEnum
from app.models.refresh_token import RefreshToken
from app.core.config import get_settings
from jose import JWTError

settings = get_settings()
log = logging.getLogger(__name__)


# ── Authentication ─────────────────────────────────────────────────────────────

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Verify credentials with lockout tracking.

    Returns the User on success; None on bad credentials.
    Raises ValueError with a user-safe message when the account is locked.
    """
    # Look up by email (active or inactive — we report "incorrect" not "not found")
    user = db.query(User).filter(User.email == email).first()

    # --- Lockout check ---
    if user and user.locked_until and user.locked_until > datetime.utcnow():
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
        raise ValueError(
            f"Account temporarily locked. Try again in {remaining} minute(s)."
        )

    # --- Validate credentials (always run to prevent timing oracle) ---
    valid = (user is not None
             and verify_password(password, user.hashed_password))

    if not valid:
        if user:
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= settings.LOGIN_MAX_ATTEMPTS:
                user.locked_until = datetime.utcnow() + timedelta(
                    minutes=settings.LOGIN_LOCKOUT_MINUTES
                )
                log.warning("Account locked: %s (too many failed attempts)", email)
            db.commit()
        return None

    # --- Success: reset counters, update last_login ---
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login = datetime.utcnow()
    db.commit()
    return user


# ── Token helpers ──────────────────────────────────────────────────────────────

def issue_tokens(user: User) -> tuple[str, str]:
    access = create_access_token(str(user.id), user.role.value)
    refresh = create_refresh_token(str(user.id))
    return access, refresh


def store_refresh_token(db: Session, user_id: int, raw_token: str) -> None:
    expires_at = datetime.utcnow()
    try:
        payload = decode_token(raw_token)
        exp = payload.get("exp")
        if exp:
            expires_at = datetime.utcfromtimestamp(exp)
    except JWTError:
        expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    tok = RefreshToken(
        token_hash=RefreshToken.hash_token(raw_token),
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(tok)
    db.commit()


def refresh_access_token(db: Session, raw_refresh: str) -> tuple[str, str]:
    """Validate refresh token, revoke it, issue new pair (rotation)."""
    try:
        payload = decode_token(raw_refresh)
    except JWTError:
        raise ValueError("Invalid or expired refresh token")
    if payload.get("type") != "refresh":
        raise ValueError("Wrong token type")

    token_hash = RefreshToken.hash_token(raw_refresh)
    stored = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash,
        RefreshToken.revoked == False,
    ).first()
    if not stored:
        raise ValueError("Refresh token not found or already revoked")

    user = db.query(User).filter(User.id == stored.user_id, User.is_active == True).first()
    if not user:
        raise ValueError("User not found or inactive")

    # Revoke old token (rotation)
    stored.revoked = True
    db.commit()

    access, refresh = issue_tokens(user)
    store_refresh_token(db, user.id, refresh)
    return access, refresh


def revoke_refresh_token(db: Session, raw_refresh: str) -> None:
    token_hash = RefreshToken.hash_token(raw_refresh)
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if stored:
        stored.revoked = True
        db.commit()


def revoke_all_user_tokens(db: Session, user_id: int) -> None:
    """Revoke all active refresh tokens for a user (e.g., on disable)."""
    from app.core import secure_auth
    secure_auth.revoke_all_sessions(db, user_id, "user_disabled")


# ── Password management ────────────────────────────────────────────────────────

def change_password(db: Session, user: User, old_password: str, new_password: str) -> User:
    """Allow a user to change their own password — requires the current password."""
    if not verify_password(old_password, user.hashed_password):
        raise ValueError("Current password is incorrect")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters")
    user.hashed_password = hash_password(new_password)
    # Revoke all refresh tokens so all sessions are invalidated
    revoke_all_user_tokens(db, user.id)
    db.commit()
    return user


def admin_reset_password(db: Session, user: User, new_password: str) -> User:
    """Admin-initiated password reset — does not require old password."""
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters")
    user.hashed_password = hash_password(new_password)
    # Invalidate all sessions for the user
    revoke_all_user_tokens(db, user.id)
    db.commit()
    return user


# ── User CRUD ──────────────────────────────────────────────────────────────────

def create_user(
    db: Session,
    email: str,
    password: str,
    full_name: str,
    role: RoleEnum = RoleEnum.TEACHER,
) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def disable_user(db: Session, user_id: int) -> User:
    from app.models.timetable import TimetableVersion, VersionStatus
    from app.models.timetable_entry import TimetableEntry
    from app.scheduler.validator import teacher_set
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")
    user.is_active = False
    revoke_all_user_tokens(db, user_id)
    
    # Remove all classes of that teacher from the active DRAFT routine
    draft = db.query(TimetableVersion).filter(
        TimetableVersion.status == VersionStatus.DRAFT
    ).order_by(TimetableVersion.created_at.desc()).first()
    
    if draft:
        entries = db.query(TimetableEntry).filter(TimetableEntry.version_id == draft.id).all()
        for entry in entries:
            teachers = teacher_set({"teacher": entry.teacher})
            # Match by full_name, or if email was used
            if user.full_name in teachers:
                db.delete(entry)
                
    db.commit()
    return user


def enable_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")
    user.is_active = True
    user.failed_attempts = 0
    user.locked_until = None
    db.commit()
    return user


# ── Seeding ────────────────────────────────────────────────────────────────────

def seed_admin_if_needed(db: Session) -> None:
    """Create the admin user on first boot if no ADMIN exists."""
    exists = db.query(User).filter(User.role == RoleEnum.ADMIN).first()
    if not exists:
        log.info("Seeding admin account: %s", settings.ADMIN_EMAIL)
        create_user(
            db,
            email=settings.ADMIN_EMAIL,
            password=settings.ADMIN_PASSWORD,
            full_name=settings.ADMIN_FULL_NAME,
            role=RoleEnum.ADMIN,
        )


def seed_demo_teachers_if_needed(db: Session) -> None:
    """Seed demo teacher accounts from the SEED_TEACHERS env variable.

    SEED_TEACHERS should be a JSON array of objects:
      [{"email": "sk@college.edu", "name": "SK"},
       {"email": "sc@college.edu", "name": "SC"}, ...]
    The password for all seeded teachers is DEMO_TEACHER_PASSWORD.
    Only seeds teachers that don't already exist.
    """
    raw = settings.SEED_TEACHERS.strip()
    if not raw:
        # Built-in demo teachers for first-boot convenience
        raw = json.dumps([
            {"email": "sk@college.edu",  "name": "SK"},
            {"email": "sks@college.edu", "name": "SKS"},
            {"email": "sc@college.edu",  "name": "SC"},
            {"email": "pb@college.edu",  "name": "PB"},
            {"email": "rd@college.edu",  "name": "RD"},
        ])

    try:
        teachers = json.loads(raw)
    except json.JSONDecodeError:
        log.error("SEED_TEACHERS env var contains invalid JSON — skipping demo teacher seed")
        return

    for t in teachers:
        email = t.get("email", "")
        name = t.get("name", email)
        if not email:
            continue
        exists = db.query(User).filter(User.email == email).first()
        if not exists:
            log.info("Seeding demo teacher: %s (%s)", name, email)
            create_user(
                db,
                email=email,
                password=settings.DEMO_TEACHER_PASSWORD,
                full_name=name,
                role=RoleEnum.TEACHER,
            )
