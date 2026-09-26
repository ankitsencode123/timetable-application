"""Authentication endpoints — login, logout, refresh, me, change-password."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, verify_csrf
from app.core.security import generate_csrf_token
from app.models.user import User
from app.schemas.auth import LoginRequest, UserPublic, ChangePasswordRequest, TokenResponse
from app.services import auth_service

router = APIRouter()
settings = get_settings()


def _cookie_kwargs() -> dict:
    """Shared cookie options from settings."""
    return dict(
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
    )


def _set_auth_cookies(response: Response, access: str, refresh: str) -> str:
    """Set access_token and refresh_token HttpOnly cookies + readable csrf_token cookie.
    Returns the CSRF token string (also sent in response body for SPA bootstrap).
    """
    ck = _cookie_kwargs()
    response.set_cookie("access_token", access, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, **ck)
    response.set_cookie("refresh_token", refresh, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, **ck)
    csrf = generate_csrf_token()
    # CSRF cookie is NOT httponly — JS needs to read it and send as header
    response.set_cookie(
        "csrf_token", csrf,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        httponly=False,
    )
    return csrf


def _clear_auth_cookies(response: Response) -> None:
    ck = _cookie_kwargs()
    for name in ("access_token", "refresh_token", "csrf_token"):
        response.delete_cookie(name, **{k: v for k, v in ck.items() if k != "httponly"})


# ── Login ──────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """Authenticate and issue HttpOnly-cookie token pair + CSRF token."""
    try:
        user = auth_service.authenticate_user(db, req.email, req.password)
    except ValueError as e:
        # Account locked — 423 Locked
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(e))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled. Please contact an administrator.",
        )

    access, refresh = auth_service.issue_tokens(user)
    auth_service.store_refresh_token(db, user.id, refresh)
    csrf = _set_auth_cookies(response, access, refresh)

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        csrf_token=csrf,
        user=UserPublic(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.value,
            is_active=user.is_active,
            last_login=user.last_login,
        ),
    )


# ── Refresh ────────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    response: Response,
    refresh_token_cookie: Optional[str] = Cookie(default=None, alias="refresh_token"),
    db: Session = Depends(get_db),
):
    """Read refresh token from cookie, rotate, set new cookies."""
    if not refresh_token_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")
    try:
        new_access, new_refresh = auth_service.refresh_access_token(db, refresh_token_cookie)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    # Look up user for response body
    from app.core.security import decode_token
    from jose import JWTError
    try:
        payload = decode_token(new_access)
        user = db.query(User).filter(User.id == int(payload["sub"])).first()
    except (JWTError, Exception):
        user = None

    csrf = _set_auth_cookies(response, new_access, new_refresh)
    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        csrf_token=csrf,
        user=UserPublic(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.value,
            is_active=user.is_active,
            last_login=user.last_login,
        ) if user else None,
    )


# ── Logout ─────────────────────────────────────────────────────────────────────

@router.post("/logout")
def logout(
    response: Response,
    refresh_token_cookie: Optional[str] = Cookie(default=None, alias="refresh_token"),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
):
    """Revoke the refresh token (DB) and clear all auth cookies."""
    if refresh_token_cookie:
        auth_service.revoke_refresh_token(db, refresh_token_cookie)
    _clear_auth_cookies(response)
    return {"message": "Logged out successfully"}


# ── Current user ───────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserPublic)
def get_me(current_user: User = Depends(get_current_user)):
    return UserPublic(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role.value,
        is_active=current_user.is_active,
        last_login=current_user.last_login,
    )


# ── Change password (self) ─────────────────────────────────────────────────────

@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
):
    """Authenticated password change — requires current password."""
    try:
        auth_service.change_password(db, current_user, req.old_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"message": "Password changed. Please log in again."}
