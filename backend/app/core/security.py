"""Password hashing, JWT helpers, and CSRF token utilities."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Passwords ──────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    # bcrypt limits passwords to 72 bytes.
    safe_plain = plain[:72]
    return _pwd_context.hash(safe_plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify against bcrypt (current) or Argon2id (legacy from secure_auth)."""
    safe_plain = plain[:72]
    # Handle Argon2id hashes from the old secure_auth system
    if hashed.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher
            from argon2.exceptions import VerifyMismatchError
            import unicodedata, hmac as _hmac, hashlib, base64
            # secure_auth used a pepper before hashing — try without pepper first (dev default)
            ph = PasswordHasher()
            # Try direct verify (no pepper)
            try:
                return ph.verify(hashed, plain)
            except VerifyMismatchError:
                pass
            # Try with dev pepper
            dev_pepper = "dev-pepper-not-for-production"
            norm = unicodedata.normalize("NFKC", plain)
            mac = _hmac.new(dev_pepper.encode(), norm.encode(), hashlib.sha256).digest()
            prehashed = base64.b64encode(mac).decode("ascii")
            try:
                return ph.verify(hashed, prehashed)
            except VerifyMismatchError:
                return False
        except Exception:
            return False
    return _pwd_context.verify(safe_plain, hashed)


# ── JWT ────────────────────────────────────────────────────────────────────────

def _create_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    payload.update({"exp": expire, "jti": str(uuid.uuid4())})
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str, role: str) -> str:
    return _create_token(
        {"sub": subject, "role": role, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(subject: str) -> str:
    return _create_token(
        {"sub": subject, "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and return payload; raises JWTError on failure."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


# ── CSRF Double-Submit Cookie ──────────────────────────────────────────────────

def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(expected: str, provided: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    return hmac.compare_digest(
        expected.encode("utf-8"),
        provided.encode("utf-8"),
    )
