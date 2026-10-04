"""Simple JWT authentication router — replaces secure_auth for /api/auth endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.services.auth_service import authenticate_user, store_refresh_token, refresh_access_token
from app.models.user import User
from app.schemas.auth import UserPublic
from jose import JWTError

router = APIRouter()

_COOKIE = "refresh_token"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Extract and validate JWT from Authorization header or access_token cookie."""
    token: str | None = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or disabled")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    from app.models.user import RoleEnum
    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_teacher_or_admin(user: User = Depends(get_current_user)) -> User:
    from app.models.user import RoleEnum
    if user.role not in (RoleEnum.ADMIN, RoleEnum.TEACHER):
        raise HTTPException(status_code=403, detail="Teacher or Admin access required")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login")
def login(body: dict, response: Response, db: Session = Depends(get_db)):
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    try:
        user = authenticate_user(db, email, password)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    access = create_access_token(str(user.id), user.role.value)
    refresh = create_refresh_token(str(user.id))
    store_refresh_token(db, user.id, refresh)

    # Set refresh token in HttpOnly cookie
    response.set_cookie(
        key=_COOKIE, value=refresh,
        httponly=True, samesite="none", secure=True,
        max_age=7 * 24 * 3600, path="/api/auth"
    )

    return {
        "status": "authenticated",
        "access_token": access,
        "refresh_token": refresh,
        "csrf_token": access,   # reuse access token as CSRF for simplicity
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
            "is_active": user.is_active,
            "last_login": user.last_login.isoformat() if user.last_login else None,
        }
    }


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(_COOKIE)
    # Also allow Bearer token from body/header for clients that store it
    if not raw:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            raw = auth[7:]
    if not raw:
        raise HTTPException(status_code=401, detail="No refresh token provided")
    try:
        access, refresh = refresh_access_token(db, raw)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    response.set_cookie(
        key=_COOKIE, value=refresh,
        httponly=True, samesite="none", secure=True,
        max_age=7 * 24 * 3600, path="/api/auth"
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "csrf_token": access,
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(response: Response, db: Session = Depends(get_db)):
    response.delete_cookie(key=_COOKIE, path="/api/auth")
    return {"message": "Logged out"}


@router.get("/me", response_model=UserPublic)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password")
def change_password(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.auth_service import change_password as _cp
    old = body.get("old_password", "")
    new = body.get("new_password", "")
    try:
        _cp(db, user, old, new)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Password changed successfully"}


@router.post("/fix-admin")
def fix_admin(body: dict, db: Session = Depends(get_db)):
    """One-time endpoint to unlock admin account and re-hash password with bcrypt.
    Requires ADMIN_RESET_TOKEN env var to match the 'token' in the request body."""
    import os
    from datetime import datetime
    from app.core.security import hash_password
    expected_token = os.getenv("ADMIN_RESET_TOKEN", "")
    provided_token = body.get("token", "")
    if not expected_token or provided_token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid or missing reset token")
    email = body.get("email", "")
    new_password = body.get("new_password", "")
    if not email or not new_password:
        raise HTTPException(status_code=400, detail="email and new_password required")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Re-hash with bcrypt and clear lockout
    user.hashed_password = hash_password(new_password)
    user.failed_attempts = 0
    user.locked_until = None
    user.is_active = True
    db.commit()
    return {"message": f"Admin account {email} unlocked and password reset successfully"}
