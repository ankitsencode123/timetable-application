"""FastAPI dependency functions shared across routers."""
from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token, verify_csrf_token
from app.models.user import User, RoleEnum

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    access_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Require a valid JWT access token.

    Supports two transport methods (in priority order):
    1. HttpOnly cookie `access_token` (browser clients)
    2. Authorization: Bearer header (API tools, tests)
    """
    # Pick token: cookie first, then bearer header
    raw_token: Optional[str] = access_token
    if not raw_token and credentials:
        raw_token = credentials.credentials

    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(raw_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong token type")

    user_id: Optional[str] = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def verify_csrf(
    request: Request,
    csrf_token_cookie: Optional[str] = Cookie(default=None, alias="csrf_token"),
    x_csrf_token: Optional[str] = Header(default=None, alias="x-csrf-token"),
) -> None:
    """CSRF double-submit cookie check — only for mutating HTTP methods via browser."""
    # Skip for safe methods and for requests that use bearer tokens (not browser)
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # If no CSRF cookie is set, skip (bearer-only clients don't set it)
    if csrf_token_cookie is None:
        return
    # If CSRF cookie is set, the header MUST match
    if not x_csrf_token or not verify_csrf_token(csrf_token_cookie, x_csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )


def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    access_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Like get_current_user but returns None instead of raising for public routes."""
    try:
        return get_current_user(request, credentials, access_token, db)
    except HTTPException:
        return None


def require_role(*roles: RoleEnum):
    """Return a dependency that enforces one of the given roles."""
    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {[r.value for r in roles]}",
            )
        return current_user
    return _check


require_admin = require_role(RoleEnum.ADMIN, RoleEnum.TEACHER)
require_teacher_or_admin = require_role(RoleEnum.TEACHER, RoleEnum.ADMIN)
