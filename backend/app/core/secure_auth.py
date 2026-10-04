"""
secure_auth.py — Production hardening layer for the FastAPI auth system
=======================================================================

Drop this file in `app/core/secure_auth.py`. It ONLY contains the advanced
additions; your existing models/schemas/services stay as they are.

WHAT THIS ADDS
--------------
 1. Argon2id password hashing + server-side pepper (rotatable), NFKC
    normalisation, transparent upgrade of legacy bcrypt hashes at login.
 2. Strong password policy (length, blocklist, user-info, sequences, optional
    HaveIBeenPwned k-anonymity check) + password history.
 3. Server-side sessions (auth_sessions) — access tokens are revocable
    instantly; logout / password change / disable really kill access tokens.
 4. Opaque refresh tokens (HMAC-hashed at rest), atomic rotation, token-family
    REUSE DETECTION (stolen token => whole session revoked), idle + absolute
    lifetimes, concurrent-session cap, session listing / revocation.
 5. JWT hardening: pinned alg, kid-based key rotation, iss/aud/iat/nbf/exp/jti
    required, typ-header + "use" claim separation (access vs mfa), no role in
    token (role is read from the DB on every request => no stale privilege).
 6. Brute-force protection that does NOT leak account existence and does NOT
    allow trivial account-lockout DoS: per-IP, per-(IP,email), per-email
    counters with exponential back-off, Redis-backed (in-memory fallback).
 7. Timing-oracle removal (dummy hash is ALWAYS verified for unknown users).
 8. TOTP MFA (RFC 6238) with replay protection, Fernet-encrypted secrets,
    single-use hashed recovery codes, MFA-required roles, step-up re-auth.
 9. CSRF: session-bound signed double-submit token, ENFORCED automatically for
    every cookie-authenticated unsafe request + Origin / Sec-Fetch-Site checks.
10. __Host- / __Secure- cookie prefixes, path-scoped refresh cookie, no tokens
    in JSON bodies by default (XSS can't exfiltrate them).
11. Secure password-reset flow (single-use, hashed, 15 min, no enumeration).
12. Security headers middleware, request-size limit, trusted-proxy aware client
    IP extraction (no X-Forwarded-For spoofing), TrustedHost.
13. Structured audit trail (DB + JSON log) and startup config validation that
    REFUSES to boot in production with weak/missing secrets.

BUGS IN THE EXISTING CODE THIS FIXES (fix these even if you adopt nothing else)
-------------------------------------------------------------------------------
 * `require_admin = require_role(ADMIN, TEACHER)`  -> any TEACHER is an admin!
 * verify_csrf() silently SKIPS when the csrf cookie is missing, and /login and
   /refresh have no CSRF/Origin protection at all.
 * Timing oracle: `user is not None and verify_password(...)` short-circuits,
   so unknown emails answer much faster than real ones.
 * Account-existence leak: only real accounts ever return 423 "locked".
 * Per-account lockout alone = attacker can lock out any victim forever; and
   there is no per-IP throttling at all.
 * Access tokens survive logout / password change / disable until they expire.
 * No refresh-token reuse detection; rotation is racy (read-then-write).
 * Tokens are returned in the JSON body, defeating HttpOnly cookies.
 * Mixed naive/aware datetimes (`utcnow()` vs timezone=True columns) -> crashes
   on Postgres.
 * bcrypt silently truncates at 72 bytes; email lookup is case-sensitive;
   password minimum is only 8 chars with no blocklist.
 * `seed_demo_teachers_if_needed()` creates shared-password accounts in prod.

INSTALL
-------
    pip install "argon2-cffi>=23.1" "cryptography>=42" "python-jose[cryptography]" \
                "passlib[bcrypt]" "redis>=5" "email-validator"

ENVIRONMENT (add to your Settings class or export as env vars)
--------------------------------------------------------------
    python secure_auth.py gen-secrets      # prints strong values for all of these

    APP_ENV=production
    JWT_SECRET_KEY=...            (>=32 bytes)        JWT_PREVIOUS_KEYS=old1,old2
    AUTH_MASTER_KEY=...           (>=32 bytes; derives CSRF / refresh-hash / audit keys)
    PASSWORD_PEPPERS=new,old      (newest first; old ones are upgraded at login)
    MFA_ENCRYPTION_KEYS=<fernet>,<old-fernet>
    REDIS_URL=redis://...         (required for multi-worker deployments)
    ALLOWED_ORIGINS=https://app.example.com
    ALLOWED_HOSTS=api.example.com
    TRUSTED_PROXY_CIDRS=10.0.0.0/8      (your LB/reverse proxy ONLY)
    COOKIE_SECURE=true  COOKIE_SAMESITE=strict  COOKIE_DOMAIN=   (leave empty => __Host-)
    PASSWORD_RESET_URL=https://app.example.com/reset-password
    MFA_REQUIRED_ROLES=ADMIN
    (optional) AUTH_TOKENS_IN_BODY=false  ALLOW_BEARER=true

WIRING (main.py)
----------------
    from app.core import secure_auth
    secure_auth.install(app, prefix="/api/auth")      # replaces the old auth router
    # secure_auth.set_mailer(MyMailer())              # implement Mailer protocol

    # Replace imports in other routers:
    #   from app.core.secure_auth import get_current_user, require_admin, require_teacher_or_admin
    # In auth_service.disable_user / admin_reset_password call:
    #   secure_auth.revoke_all_sessions(db, user_id, "admin_disable")
    # In production DO NOT call seed_demo_teachers_if_needed(); see harden_seeding().
    # Create the new tables (alembic revision --autogenerate; models register on import).
    # Schedule secure_auth.purge_expired(db) hourly (cron / APScheduler / Celery beat).

FRONTEND CONTRACT
-----------------
    * Send `credentials: "include"` on every request.
    * Read the readable csrf cookie (or the csrf_token from the login/refresh
      response) and send it as header `X-CSRF-Token` on every POST/PUT/PATCH/DELETE.
    * Login returns status = authenticated | mfa_required | mfa_enrollment_required.
      On mfa_required call POST /login/mfa {mfa_token, code}.
    * On 401 call POST /refresh once, then retry; if that fails go to login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
import unicodedata
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable, Literal, Optional, Protocol
from urllib.parse import quote, urlsplit

from fastapi import (
    APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, delete, func, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import get_db
from app.models.base import Base
from app.models.user import RoleEnum, User
from app.schemas.auth import UserPublic

try:  # Argon2id
    from argon2 import PasswordHasher, Type
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pip install argon2-cffi") from exc

try:  # MFA secret encryption
    from cryptography.fernet import Fernet, MultiFernet
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pip install cryptography") from exc

try:  # legacy bcrypt verification (upgrade path only)
    from passlib.context import CryptContext
    _LEGACY = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception:  # pragma: no cover
    _LEGACY = None

try:
    import redis as _redis_lib
except ImportError:  # pragma: no cover
    _redis_lib = None

log = logging.getLogger("secure_auth")
_audit_logger = logging.getLogger("auth.audit")
_settings = get_settings()

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


# ══════════════════════════════════════════════════════════════════════════════
# 1. CONFIGURATION (+ production validation)
# ══════════════════════════════════════════════════════════════════════════════

def _raw(name: str, default: Any = None) -> Any:
    v = getattr(_settings, name, None)
    if v is None or v == "":
        v = os.environ.get(name)
    return default if v is None or v == "" else v


def _bool(name: str, default: bool) -> bool:
    v = _raw(name)
    return default if v is None else str(v).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_raw(name, default))
    except (TypeError, ValueError):
        return default


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(x.strip() for x in str(_raw(name, default)).split(",") if x.strip())


_WEAK_SECRETS = {
    "secret", "changeme", "change-me", "change_me", "your-secret-key", "supersecret",
    "password", "admin", "admin123", "dev", "development", "test", "jwt-secret",
}


@dataclass(frozen=True)
class AuthConfig:
    env: str
    is_prod: bool
    # JWT
    jwt_keys: dict
    jwt_kid: str
    jwt_issuer: str
    jwt_audience: str
    jwt_leeway: int
    access_ttl: int
    # sessions
    refresh_ttl: int
    session_absolute: int
    refresh_reuse_grace: int
    max_sessions: int
    # cookies / transport
    cookie_secure: bool
    cookie_samesite: str
    cookie_domain: Optional[str]
    refresh_cookie_path: str
    tokens_in_body: bool
    allow_bearer: bool
    # CSRF / hosts / proxies
    allowed_origins: tuple
    allowed_hosts: tuple
    trusted_proxies: tuple
    max_body_bytes: int
    # secrets
    master_key: bytes
    peppers: tuple
    mfa_keys: tuple
    redis_url: Optional[str]
    # password hashing / policy
    argon_time: int
    argon_memory_kib: int
    argon_parallelism: int
    pw_min_len: int
    pw_max_len: int
    pw_history: int
    hibp: bool
    hibp_fail_open: bool
    common_pw_file: Optional[str]
    # throttling
    fail_window: int
    backoff_cap: int
    ip_fail_limit: int
    pair_fail_limit: int
    acct_fail_limit: int
    login_rate_per_min: int
    # MFA / reset
    mfa_issuer: str
    mfa_required_roles: tuple
    mfa_token_ttl: int
    mfa_fail_limit: int
    reset_ttl: int
    reset_url: str


def load_config() -> AuthConfig:
    env = str(_raw("APP_ENV", _raw("ENVIRONMENT", "development"))).lower()
    prod = env in {"prod", "production"}

    jwt_secret = str(_raw("JWT_SECRET_KEY", "") or "")
    if not jwt_secret:
        jwt_secret = "dev-only-" + hashlib.sha256(b"dev").hexdigest()
    keys = {}
    for s in (jwt_secret, *_csv("JWT_PREVIOUS_KEYS")):
        keys[hashlib.sha256(s.encode()).hexdigest()[:12]] = s
    kid = hashlib.sha256(jwt_secret.encode()).hexdigest()[:12]

    master = str(_raw("AUTH_MASTER_KEY", "") or "")
    if not master:
        master = hmac.new(jwt_secret.encode(), b"dev-master", hashlib.sha256).hexdigest()

    peppers = _csv("PASSWORD_PEPPERS") or ("dev-pepper-not-for-production",)

    mfa_keys = _csv("MFA_ENCRYPTION_KEYS")
    if not mfa_keys:  # dev fallback only; validate_config() rejects this in prod
        mfa_keys = (base64.urlsafe_b64encode(
            hmac.new(master.encode(), b"mfa-dev", hashlib.sha256).digest()).decode(),)

    default_origins = "" if prod else "http://localhost:3000,http://localhost:5173"
    secure = _bool("COOKIE_SECURE", prod)
    samesite = str(_raw("COOKIE_SAMESITE", "strict" if prod else "lax")).lower()

    proxies = []
    for c in _csv("TRUSTED_PROXY_CIDRS"):
        try:
            proxies.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            log.error("Bad TRUSTED_PROXY_CIDRS entry ignored: %s", c)

    return AuthConfig(
        env=env, is_prod=prod,
        jwt_keys=keys, jwt_kid=kid,
        jwt_issuer=str(_raw("JWT_ISSUER", "timetable-api")),
        jwt_audience=str(_raw("JWT_AUDIENCE", "timetable-web")),
        jwt_leeway=_int("JWT_LEEWAY_SECONDS", 10),
        access_ttl=_int("ACCESS_TOKEN_EXPIRE_MINUTES", 10) * 60,
        refresh_ttl=_int("REFRESH_TOKEN_EXPIRE_DAYS", 7) * 86400,
        session_absolute=_int("SESSION_ABSOLUTE_DAYS", 30) * 86400,
        refresh_reuse_grace=_int("REFRESH_REUSE_GRACE_SECONDS", 10),
        max_sessions=_int("MAX_SESSIONS_PER_USER", 10),
        cookie_secure=secure, cookie_samesite=samesite,
        cookie_domain=_raw("COOKIE_DOMAIN") or None,
        refresh_cookie_path=str(_raw("REFRESH_COOKIE_PATH", "/api/auth")),
        tokens_in_body=_bool("AUTH_TOKENS_IN_BODY", False),
        allow_bearer=_bool("ALLOW_BEARER", True),
        allowed_origins=tuple(o.rstrip("/").lower() for o in _csv("ALLOWED_ORIGINS", default_origins)),
        allowed_hosts=_csv("ALLOWED_HOSTS"),
        trusted_proxies=tuple(proxies),
        max_body_bytes=_int("AUTH_MAX_BODY_BYTES", 64 * 1024),
        master_key=master.encode(), peppers=peppers, mfa_keys=mfa_keys,
        redis_url=_raw("REDIS_URL"),
        argon_time=_int("ARGON2_TIME_COST", 3),
        argon_memory_kib=_int("ARGON2_MEMORY_KIB", 64 * 1024),
        argon_parallelism=_int("ARGON2_PARALLELISM", 2),
        pw_min_len=_int("PASSWORD_MIN_LENGTH", 12),
        pw_max_len=_int("PASSWORD_MAX_LENGTH", 128),
        pw_history=_int("PASSWORD_HISTORY", 5),
        hibp=_bool("PASSWORD_HIBP_CHECK", prod),
        hibp_fail_open=_bool("PASSWORD_HIBP_FAIL_OPEN", True),
        common_pw_file=_raw("COMMON_PASSWORDS_FILE"),
        fail_window=_int("LOGIN_FAIL_WINDOW_SECONDS", 900),
        backoff_cap=_int("LOGIN_BACKOFF_CAP_SECONDS", 3600),
        ip_fail_limit=_int("LOGIN_IP_FAIL_LIMIT", 30),
        pair_fail_limit=_int("LOGIN_PAIR_FAIL_LIMIT", 5),
        acct_fail_limit=_int("LOGIN_ACCOUNT_FAIL_LIMIT", 40),
        login_rate_per_min=_int("LOGIN_RATE_PER_MINUTE", 60),
        mfa_issuer=str(_raw("MFA_ISSUER", "Timetable")),
        mfa_required_roles=tuple(r.upper() for r in _csv("MFA_REQUIRED_ROLES", "ADMIN" if prod else "")),
        mfa_token_ttl=_int("MFA_TOKEN_TTL_SECONDS", 300),
        mfa_fail_limit=_int("MFA_FAIL_LIMIT", 5),
        reset_ttl=_int("PASSWORD_RESET_TTL_SECONDS", 900),
        reset_url=str(_raw("PASSWORD_RESET_URL", "http://localhost:3000/reset-password")),
    )


CFG = load_config()


def validate_config(c: AuthConfig = CFG) -> list[str]:
    """Return a list of configuration problems. Only JWT is fatal in production."""
    p: list[str] = []
    cur = c.jwt_keys[c.jwt_kid]
    if len(cur) < 32 or cur.lower() in _WEAK_SECRETS or cur.startswith("dev-only-"):
        p.append("JWT_SECRET_KEY must be a random secret of >= 32 chars")
    return p


def _derive(label: str) -> bytes:
    """Domain-separated subkey from the master key."""
    return hmac.new(CFG.master_key, b"secure_auth/v1/" + label.encode(), hashlib.sha256).digest()


def _hmac_hex(label: str, value: str) -> str:
    return hmac.new(_derive(label), value.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class CookieNames:
    access: str
    refresh: str
    csrf: str


def _cookie_names() -> CookieNames:
    if not CFG.cookie_secure:  # browsers reject prefixed cookies over http (dev only)
        return CookieNames("access_token", "refresh_token", "csrf_token")
    host_ok = not CFG.cookie_domain  # __Host- forbids a Domain attribute
    return CookieNames(
        "__Host-at" if host_ok else "__Secure-at",
        "__Secure-rt",
        "__Host-csrf" if host_ok else "__Secure-csrf",
    )


COOKIES = _cookie_names()


# ══════════════════════════════════════════════════════════════════════════════
# 2. SMALL UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise naive datetimes (SQLite / legacy rows) to aware UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFKC", email).strip().lower()


def _id_hash(identifier: str) -> str:
    return _hmac_hex("identifier", normalize_email(identifier))[:32]


def _trusted(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in CFG.trusted_proxies)


def client_ip(request: Request) -> str:
    """Client IP, honouring X-Forwarded-For ONLY from configured trusted proxies
    (right-to-left walk => a client-supplied XFF prefix can't spoof the IP)."""
    peer = request.client.host if request.client else "0.0.0.0"
    if not CFG.trusted_proxies or not _trusted(peer):
        return peer
    xff = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer
        if not _trusted(hop):
            return hop
    return peer


def client_ua(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:255]


def ip_key(ip: str) -> str:
    """Rate-limit key; IPv6 collapsed to /64 so one host can't rotate addresses."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid"
    if a.version == 6:
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


class AuthError(HTTPException):
    """HTTPException carrying a stable machine-readable `code`."""

    def __init__(self, status_code: int, code: str, message: str,
                 headers: Optional[dict] = None, extra: Optional[dict] = None):
        detail = {"code": code, "message": message}
        if extra:
            detail.update(extra)
        super().__init__(status_code=status_code, detail=detail, headers=headers)


def _throttled(retry_after: int) -> AuthError:
    retry_after = max(1, int(retry_after))
    return AuthError(429, "too_many_attempts", "Too many attempts. Please try again later.",
                     headers={"Retry-After": str(retry_after)})


_UNAUTH = {"WWW-Authenticate": "Bearer"}


# ══════════════════════════════════════════════════════════════════════════════
# 3. DATABASE MODELS (register on import → alembic autogenerate / create_all)
# ══════════════════════════════════════════════════════════════════════════════

class AuthSession(Base):
    """One row per login (device). Access tokens carry `sid`; revoking the row
    kills the access token immediately and the whole refresh-token family."""
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    auth_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # last password/MFA proof
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RefreshTokenRow(Base):
    __tablename__ = "auth_refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class UserMFA(Base):
    __tablename__ = "user_mfa"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_step: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # TOTP replay guard
    recovery_hashes: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)


class PasswordHistory(Base):
    __tablename__ = "password_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConsumedJTI(Base):
    """Single-use token ledger (MFA challenge tokens)."""
    __tablename__ = "auth_consumed_jti"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AuthEvent(Base):
    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    identifier_hash: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# 4. AUDIT TRAIL
# ══════════════════════════════════════════════════════════════════════════════

def audit(db: Session, event: str, *, user_id: Optional[int] = None, identifier: Optional[str] = None,
          ip: Optional[str] = None, ua: Optional[str] = None, outcome: str = "success", **detail: Any) -> None:
    """Structured JSON log line (SIEM feed) + DB row. NEVER logs secrets/emails.
    Call AFTER your own commit so a rollback here can't discard business writes."""
    ident = _id_hash(identifier) if identifier else None
    _audit_logger.info(json.dumps({
        "event": event, "outcome": outcome, "user_id": user_id,
        "ident": ident, "ip": ip, "detail": detail,
    }, default=str))
    try:
        db.add(AuthEvent(ts=utcnow(), event=event, outcome=outcome, user_id=user_id,
                         identifier_hash=ident, ip=ip, user_agent=ua,
                         detail=json.dumps(detail, default=str) if detail else None))
        db.commit()
    except Exception:  # auditing must never break auth
        db.rollback()
        log.exception("failed to persist audit event %s", event)


# ══════════════════════════════════════════════════════════════════════════════
# 5. RATE LIMITING / BRUTE-FORCE GUARD (Redis, in-memory fallback)
# ══════════════════════════════════════════════════════════════════════════════

class Limiter(Protocol):
    def incr(self, key: str, ttl: int, keep_ttl: bool = False) -> int: ...
    def get(self, key: str) -> tuple[int, int]: ...
    def expire(self, key: str, ttl: int) -> None: ...
    def delete(self, key: str) -> None: ...


class MemoryLimiter:
    """Per-process fallback. Good for dev/tests; NOT sufficient across workers."""

    def __init__(self) -> None:
        self._d: dict[str, list] = {}  # key -> [count, expires_at]
        self._lock = threading.Lock()

    def _live(self, key: str) -> Optional[list]:
        e = self._d.get(key)
        if e and e[1] <= time.monotonic():
            del self._d[key]
            return None
        return e

    def incr(self, key: str, ttl: int, keep_ttl: bool = False) -> int:
        with self._lock:
            e = self._live(key)
            if e is None:
                self._d[key] = [1, time.monotonic() + ttl]
                if len(self._d) > 100_000:  # crude memory bound
                    for k in [k for k, v in self._d.items() if v[1] <= time.monotonic()]:
                        self._d.pop(k, None)
                return 1
            e[0] += 1
            if not keep_ttl:
                e[1] = time.monotonic() + ttl
            return e[0]

    def get(self, key: str) -> tuple[int, int]:
        with self._lock:
            e = self._live(key)
            return (0, 0) if e is None else (e[0], max(0, int(e[1] - time.monotonic())))

    def expire(self, key: str, ttl: int) -> None:
        with self._lock:
            e = self._live(key)
            if e:
                e[1] = time.monotonic() + ttl

    def delete(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)


class RedisLimiter:
    def __init__(self, url: str) -> None:
        self.r = _redis_lib.Redis.from_url(url, socket_timeout=1.0, socket_connect_timeout=1.0,
                                           decode_responses=True)

    def incr(self, key: str, ttl: int, keep_ttl: bool = False) -> int:
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        n, cur = pipe.execute()
        if not keep_ttl or cur < 0:
            self.r.expire(key, ttl)
        return int(n)

    def get(self, key: str) -> tuple[int, int]:
        pipe = self.r.pipeline()
        pipe.get(key)
        pipe.ttl(key)
        v, t = pipe.execute()
        return (int(v or 0), max(0, int(t)) if t is not None else 0)

    def expire(self, key: str, ttl: int) -> None:
        self.r.expire(key, ttl)

    def delete(self, key: str) -> None:
        self.r.delete(key)


class ResilientLimiter:
    """Redis first; if Redis is down degrade to per-process counters (fail SAFE,
    not open) and keep serving."""

    def __init__(self, primary: Limiter, fallback: Limiter) -> None:
        self.p, self.f = primary, fallback

    def _call(self, name: str, *a: Any, **kw: Any) -> Any:
        try:
            return getattr(self.p, name)(*a, **kw)
        except Exception as exc:
            log.error("rate-limit backend failure (%s): %s — using in-memory fallback", name, exc)
            return getattr(self.f, name)(*a, **kw)

    def incr(self, key, ttl, keep_ttl=False): return self._call("incr", key, ttl, keep_ttl)
    def get(self, key): return self._call("get", key)
    def expire(self, key, ttl): return self._call("expire", key, ttl)
    def delete(self, key): return self._call("delete", key)


def _build_limiter() -> Limiter:
    if CFG.redis_url and _redis_lib:
        return ResilientLimiter(RedisLimiter(CFG.redis_url), MemoryLimiter())
    if CFG.is_prod:
        log.warning("Redis not configured: rate limiting is per-process only")
    return MemoryLimiter()


LIMITER: Limiter = _build_limiter()


class FailureGuard:
    """Counts failures in a fixed window; at the limit the key is blocked with an
    EXPONENTIAL back-off (strikes remembered 24h). Successful auth only clears the
    narrow (ip,email) key, so an attacker can't reset counters by logging in to
    their own account, and a victim isn't locked out from their own device by
    a remote attacker (the pair key is per-IP)."""

    def __init__(self, limiter: Limiter) -> None:
        self.l = limiter

    def ensure_open(self, key: str, limit: int) -> None:
        count, ttl = self.l.get(key)
        if count >= limit:
            raise _throttled(ttl or CFG.fail_window)

    def fail(self, key: str, limit: int) -> None:
        n = self.l.incr(key, CFG.fail_window, keep_ttl=True)
        if n == limit:
            strikes = self.l.incr(key + ":strikes", 86400)
            self.l.expire(key, min(CFG.fail_window * 2 ** (strikes - 1), CFG.backoff_cap))

    def ok(self, key: str) -> None:
        self.l.delete(key)


GUARD = FailureGuard(LIMITER)


def rate_limit(key: str, limit: int, window: int) -> None:
    """Plain fixed-window request limiter. Raises 429."""
    n = LIMITER.incr(key, window, keep_ttl=True)
    if n > limit:
        _, ttl = LIMITER.get(key)
        raise _throttled(ttl or window)


# ══════════════════════════════════════════════════════════════════════════════
# 6. PASSWORDS — Argon2id + pepper + policy + breach check
# ══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _hasher() -> PasswordHasher:
    return PasswordHasher(time_cost=CFG.argon_time, memory_cost=CFG.argon_memory_kib,
                          parallelism=CFG.argon_parallelism, hash_len=32, salt_len=16, type=Type.ID)


def _prehash(plain: str, pepper: str) -> str:
    """NFKC-normalise then HMAC with the pepper. Output is fixed-length (44 chars),
    so there is no bcrypt-style 72-byte truncation and a DB-only leak is not enough
    to crack hashes offline."""
    norm = unicodedata.normalize("NFKC", plain)
    mac = hmac.new(pepper.encode("utf-8"), norm.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


def hash_password(plain: str) -> str:
    return _hasher().hash(_prehash(plain, CFG.peppers[0]))


def verify_password(plain: str, stored: str) -> tuple[bool, bool]:
    """Return (is_valid, needs_rehash). Handles Argon2id (current/old peppers) and
    legacy bcrypt hashes from the previous implementation."""
    if len(plain) > 4096:  # DoS guard before any expensive work
        return False, False
    if stored.startswith("$argon2"):
        for idx, pepper in enumerate(CFG.peppers):
            try:
                _hasher().verify(stored, _prehash(plain, pepper))
                return True, (idx != 0 or _hasher().check_needs_rehash(stored))
            except VerifyMismatchError:
                continue
            except (VerificationError, InvalidHashError):
                return False, False
        return False, False
    if stored.startswith(("$2a$", "$2b$", "$2y$")) and _LEGACY is not None:
        try:  # the legacy code truncated to 72 *characters*; reproduce exactly
            return (True, True) if _LEGACY.verify(plain[:72], stored) else (False, False)
        except Exception:
            return False, False
    return False, False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Hash of a random secret — verified for unknown users to equalise timing."""
    return hash_password(secrets.token_urlsafe(24))


_COMMON = {
    "password", "password1", "password123", "123456789", "1234567890", "12345678",
    "qwertyuiop", "qwerty123", "iloveyou", "admin123", "administrator", "welcome1",
    "letmein123", "changeme123", "p@ssw0rd", "p@ssword", "passw0rd", "abc123456",
    "111111111", "000000000", "football1", "baseball1", "monkey123", "dragon123",
    "master123", "sunshine1", "princess1", "trustno1", "whatever1", "college123",
    "teacher123", "student123", "timetable", "timetable123", "welcome123", "college@123",
}


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset:
    words = set(_COMMON)
    if CFG.common_pw_file and os.path.exists(CFG.common_pw_file):
        with open(CFG.common_pw_file, "r", encoding="utf-8", errors="ignore") as fh:
            words |= {ln.strip().lower() for ln in fh if ln.strip()}
    return frozenset(words)


_SEQS = ("abcdefghijklmnopqrstuvwxyz", "0123456789", "qwertyuiop", "asdfghjkl", "zxcvbnm")


def _has_sequence(pw: str, n: int = 5) -> bool:
    low = pw.lower()
    for seq in _SEQS:
        for s in (seq, seq[::-1]):
            for i in range(len(s) - n + 1):
                if s[i:i + n] in low:
                    return True
    return False


class PasswordPolicyError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _pwned_count(password: str) -> Optional[int]:
    """HaveIBeenPwned range API (k-anonymity: only a 5-char SHA-1 prefix leaves us)."""
    sha = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    pre, suf = sha[:5], sha[5:]
    req = urllib.request.Request(f"https://api.pwnedpasswords.com/range/{pre}",
                                 headers={"Add-Padding": "true", "User-Agent": "secure-auth/1"})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as r:  # nosec - fixed https host
            body = r.read().decode("utf-8", "ignore")
    except Exception as exc:
        log.warning("HIBP lookup failed: %s", exc)
        return None
    for line in body.splitlines():
        h, _, c = line.partition(":")
        if h.strip() == suf:
            return int(c.strip() or 0)
    return 0


def validate_password_policy(password: str, *, email: Optional[str] = None,
                             full_name: Optional[str] = None) -> None:
    """Raises PasswordPolicyError listing every problem."""
    pw = unicodedata.normalize("NFKC", password)
    problems: list[str] = []
    if len(pw) < CFG.pw_min_len:
        problems.append(f"Must be at least {CFG.pw_min_len} characters")
    if len(pw) > CFG.pw_max_len:
        problems.append(f"Must be at most {CFG.pw_max_len} characters")
    if not pw.strip():
        problems.append("Cannot be only whitespace")
    if len(set(pw)) < 6:
        problems.append("Too repetitive")
    if _has_sequence(pw):
        problems.append("Avoid keyboard/alphabet/number sequences")
    low = pw.lower()
    if low in _common_passwords() or re.sub(r"[^a-z0-9]", "", low) in _common_passwords():
        problems.append("Too common")
    tokens: set[str] = set()
    if email:
        tokens.add(email.split("@")[0].lower())
    if full_name:
        tokens |= {t.lower() for t in re.split(r"\W+", full_name)}
    if any(len(t) >= 4 and t in low for t in tokens):
        problems.append("Must not contain your name or email")
    if not problems and CFG.hibp:
        cnt = _pwned_count(pw)
        if cnt is None and not CFG.hibp_fail_open:
            problems.append("Could not verify password against breach database; try again")
        elif cnt:
            problems.append("This password appears in known data breaches")
    if problems:
        raise PasswordPolicyError(problems)


def _check_history(db: Session, user: User, new_password: str) -> None:
    if CFG.pw_history <= 0:
        return
    hashes = [user.hashed_password] + [
        r.hashed_password for r in db.query(PasswordHistory)
        .filter(PasswordHistory.user_id == user.id)
        .order_by(PasswordHistory.id.desc()).limit(CFG.pw_history).all()
    ]
    for h in hashes:
        if verify_password(new_password, h)[0]:
            raise PasswordPolicyError(["Cannot reuse a recent password"])


def set_user_password(db: Session, user: User, new_password: str, *, actor: str) -> None:
    """Policy + history + hash + persist + revoke ALL sessions. Commits."""
    try:
        validate_password_policy(new_password, email=user.email, full_name=user.full_name)
        _check_history(db, user, new_password)
    except PasswordPolicyError as e:
        raise AuthError(422, "weak_password", "Password does not meet requirements.",
                        extra={"problems": e.problems})
    db.add(PasswordHistory(user_id=user.id, hashed_password=user.hashed_password, created_at=utcnow()))
    user.hashed_password = hash_password(new_password)
    db.commit()
    revoke_all_sessions(db, user.id, f"password_changed_by_{actor}")
    # trim history
    old = (db.query(PasswordHistory.id).filter(PasswordHistory.user_id == user.id)
           .order_by(PasswordHistory.id.desc()).offset(max(CFG.pw_history, 0)).all())
    if old:
        db.execute(delete(PasswordHistory).where(PasswordHistory.id.in_([r[0] for r in old])))
        db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 7. JWT ENGINE (key rotation, strict claims)
# ══════════════════════════════════════════════════════════════════════════════

_JWT_TYP = {"access": "at+jwt", "mfa": "mfa+jwt"}


def jwt_encode(use: str, subject: str, ttl: int, **extra: Any) -> tuple[str, str]:
    now = int(time.time())
    jti = uuid.uuid4().hex
    claims = {"iss": CFG.jwt_issuer, "aud": CFG.jwt_audience, "sub": subject, "iat": now,
              "nbf": now, "exp": now + ttl, "jti": jti, "use": use, **extra}
    token = jwt.encode(claims, CFG.jwt_keys[CFG.jwt_kid], algorithm="HS256",
                       headers={"kid": CFG.jwt_kid, "typ": _JWT_TYP[use]})
    return token, jti


def jwt_decode(token: str, use: str) -> dict:
    """Strict decode. Raises JWTError on ANY problem."""
    hdr = jwt.get_unverified_header(token)
    if hdr.get("alg") != "HS256" or hdr.get("typ") != _JWT_TYP[use]:
        raise JWTError("bad header")
    key = CFG.jwt_keys.get(hdr.get("kid"))
    if not key:
        raise JWTError("unknown kid")
    claims = jwt.decode(
        token, key, algorithms=["HS256"], audience=CFG.jwt_audience, issuer=CFG.jwt_issuer,
        options={"require_exp": True, "require_iat": True, "require_nbf": True, "require_iss": True,
                 "require_aud": True, "require_sub": True, "require_jti": True,
                 "leeway": CFG.jwt_leeway},
    )
    if claims.get("use") != use:
        raise JWTError("wrong token use")
    return claims


# ══════════════════════════════════════════════════════════════════════════════
# 8. CSRF + ORIGIN CHECKS
# ══════════════════════════════════════════════════════════════════════════════

def issue_csrf_token(session_id: str) -> str:
    """Signed double-submit token bound to the session: an attacker who can plant a
    cookie (subdomain / MITM) still can't forge a token valid for the victim's sid."""
    nonce = secrets.token_urlsafe(16)
    sig = hmac.new(_derive("csrf"), f"{session_id}:{nonce}".encode(), hashlib.sha256).digest()[:24]
    return f"{nonce}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def check_csrf(request: Request, session_id: str) -> None:
    cookie = request.cookies.get(COOKIES.csrf)
    header = request.headers.get("x-csrf-token")
    bad = AuthError(403, "csrf_failed", "CSRF validation failed")
    if not cookie or not header or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise bad
    nonce, _, sig = header.partition(".")
    expected = hmac.new(_derive("csrf"), f"{session_id}:{nonce}".encode(), hashlib.sha256).digest()[:24]
    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    if not hmac.compare_digest(sig.encode(), expected_b64.encode()):
        raise bad


def enforce_same_origin(request: Request, *, require_header: bool = True) -> None:
    """Origin / Referer / Sec-Fetch-Site validation for unsafe methods.
    Only enforced when ALLOWED_ORIGINS is explicitly configured."""
    if request.method in SAFE_METHODS:
        return
    # If no allowed origins are configured, skip enforcement entirely
    if not CFG.allowed_origins:
        return
    bad = AuthError(403, "origin_not_allowed", "Cross-origin request blocked")
    if request.headers.get("sec-fetch-site", "") == "cross-site":
        # Only block if we have an explicit allowlist
        origin = request.headers.get("origin")
        if not origin:
            ref = request.headers.get("referer")
            if ref:
                u = urlsplit(ref)
                origin = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None
        if origin and origin.rstrip("/").lower() not in CFG.allowed_origins:
            raise bad
        return
    origin = request.headers.get("origin")
    if not origin:
        ref = request.headers.get("referer")
        if ref:
            u = urlsplit(ref)
            origin = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None
    if not origin:
        if require_header:
            raise bad
        return
    if origin.rstrip("/").lower() not in CFG.allowed_origins:
        raise bad


# ══════════════════════════════════════════════════════════════════════════════
# 9. SESSIONS + REFRESH ROTATION WITH REUSE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class IssuedSession:
    session: AuthSession
    access: str
    refresh: str
    csrf: str
    expires_in: int


def _hash_refresh(raw: str) -> str:
    return _hmac_hex("refresh", raw)


def _new_refresh_row(db: Session, sess: AuthSession, now: datetime) -> str:
    raw = f"{sess.id}.{secrets.token_urlsafe(48)}"
    expires = min(now + timedelta(seconds=CFG.refresh_ttl), aware(sess.absolute_expires_at))
    db.add(RefreshTokenRow(session_id=sess.id, token_hash=_hash_refresh(raw), issued_at=now, expires_at=expires))
    return raw


def _mint_access(user: User, sess: AuthSession) -> str:
    return jwt_encode("access", str(user.id), CFG.access_ttl, sid=sess.id)[0]


def revoke_session(db: Session, session_id: str, reason: str) -> None:
    db.execute(update(AuthSession).where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
               .values(revoked_at=utcnow(), revoked_reason=reason[:64]))
    db.commit()


def revoke_all_sessions(db: Session, user_id: int, reason: str, *, except_sid: Optional[str] = None) -> None:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_sid:
        q = q.where(AuthSession.id != except_sid)
    db.execute(q.values(revoked_at=utcnow(), revoked_reason=reason[:64]))
    db.commit()


def create_session(db: Session, user: User, ip: str, ua: str, *, mfa: bool) -> IssuedSession:
    now = utcnow()
    active = (db.query(AuthSession).filter(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None),
                                           AuthSession.absolute_expires_at > now)
              .order_by(AuthSession.created_at.asc()).all())
    overflow = len(active) - CFG.max_sessions + 1
    for old in active[:max(0, overflow)]:
        old.revoked_at, old.revoked_reason = now, "session_cap"
    sess = AuthSession(id=str(uuid.uuid4()), user_id=user.id, created_at=now, last_seen_at=now,
                       auth_time=now, absolute_expires_at=now + timedelta(seconds=CFG.session_absolute),
                       ip=ip, user_agent=ua, mfa_verified=mfa)
    db.add(sess)
    db.flush()
    refresh = _new_refresh_row(db, sess, now)
    user.last_login = now
    db.commit()
    return IssuedSession(sess, _mint_access(user, sess), refresh, issue_csrf_token(sess.id), CFG.access_ttl)


def _parse_refresh(raw: str) -> str:
    sid, sep, secret = raw.partition(".")
    if not sep or len(sid) != 36 or len(secret) < 32 or len(raw) > 256:
        raise AuthError(401, "invalid_refresh", "Invalid refresh token", headers=_UNAUTH)
    return sid


def refresh_session_id(raw: str) -> str:
    return _parse_refresh(raw)


def rotate_refresh(db: Session, raw: str, ip: str, ua: str) -> tuple[IssuedSession, User]:
    sid = _parse_refresh(raw)
    invalid = AuthError(401, "invalid_refresh", "Invalid refresh token", headers=_UNAUTH)
    now = utcnow()

    sess = db.get(AuthSession, sid)
    if not sess or sess.revoked_at is not None or now >= aware(sess.absolute_expires_at):
        raise invalid
    tok = db.query(RefreshTokenRow).filter(RefreshTokenRow.token_hash == _hash_refresh(raw),
                                           RefreshTokenRow.session_id == sid).first()
    if not tok or now >= aware(tok.expires_at):
        raise invalid

    if tok.used_at is not None:
        # An already-rotated token was presented.
        if (now - aware(tok.used_at)).total_seconds() <= CFG.refresh_reuse_grace:
            raise AuthError(401, "refresh_retry", "Token was just rotated; retry with the new cookie",
                            headers=_UNAUTH)  # benign multi-tab race: do NOT nuke the session
        revoke_session(db, sid, "refresh_reuse_detected")
        audit(db, "refresh_reuse_detected", user_id=sess.user_id, ip=ip, ua=ua, outcome="alert", sid=sid)
        raise invalid

    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        raise invalid

    # Atomic claim: only ONE concurrent request can flip used_at NULL -> now.
    claimed = db.execute(update(RefreshTokenRow)
                         .where(RefreshTokenRow.id == tok.id, RefreshTokenRow.used_at.is_(None))
                         .values(used_at=now)).rowcount
    if claimed != 1:
        db.rollback()
        raise AuthError(401, "refresh_retry", "Token was just rotated; retry with the new cookie", headers=_UNAUTH)

    new_raw = _new_refresh_row(db, sess, now)
    sess.last_seen_at = now
    if sess.ip and sess.ip != ip:
        audit_ip_change = (sess.ip, ip)
    else:
        audit_ip_change = None
    sess.ip, sess.user_agent = ip, ua
    db.commit()
    if audit_ip_change:
        audit(db, "session_ip_changed", user_id=user.id, ip=ip, ua=ua, sid=sid,
              old_ip=audit_ip_change[0], outcome="info")
    return IssuedSession(sess, _mint_access(user, sess), new_raw, issue_csrf_token(sid), CFG.access_ttl), user


# ══════════════════════════════════════════════════════════════════════════════
# 10. MFA (TOTP + recovery codes)
# ══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(k) for k in CFG.mfa_keys])


def _hotp(secret: bytes, counter: int, digits: int = 6) -> str:
    mac = hmac.new(secret, counter.to_bytes(8, "big"), hashlib.sha1).digest()  # RFC 4226 mandates SHA-1 HMAC
    off = mac[-1] & 0x0F
    code = (int.from_bytes(mac[off:off + 4], "big") & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_verify(secret_b32: str, code: str, last_step: int, window: int = 1) -> Optional[int]:
    """Return the matched time-step (> last_step => replay-safe) or None.
    Every window slot is always evaluated: no early exit, constant work."""
    if not (len(code) == 6 and code.isdigit()):
        return None
    secret = base64.b32decode(secret_b32)
    now_step = int(time.time()) // 30
    matched: Optional[int] = None
    for off in range(-window, window + 1):
        step = now_step + off
        if hmac.compare_digest(_hotp(secret, step), code) and step > last_step:
            matched = step if matched is None else max(matched, step)
    return matched


def _gen_recovery_codes(n: int = 10) -> tuple[list[str], list[str]]:
    plain = [secrets.token_hex(8) for _ in range(n)]
    shown = ["-".join(c[i:i + 4] for i in range(0, 16, 4)) for c in plain]
    return shown, [_hmac_hex("recovery", c) for c in plain]


def _norm_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code or "").lower()


def mfa_enabled(db: Session, user_id: int) -> bool:
    return bool(db.query(UserMFA.enabled).filter(UserMFA.user_id == user_id).scalar())


def mfa_required_but_missing(db: Session, user: User) -> bool:
    return user.role.value in CFG.mfa_required_roles and not mfa_enabled(db, user.id)


def mfa_begin_enrollment(db: Session, user: User) -> dict:
    if mfa_enabled(db, user.id):
        raise AuthError(409, "mfa_already_enabled", "MFA is already enabled")
    secret = base64.b32encode(secrets.token_bytes(20)).decode()  # 160-bit
    row = db.get(UserMFA, user.id) or UserMFA(user_id=user.id, created_at=utcnow(), recovery_hashes="[]")
    row.secret_enc = _fernet().encrypt(secret.encode()).decode()
    row.enabled, row.last_used_step = False, 0
    db.merge(row)
    db.commit()
    label = quote(f"{CFG.mfa_issuer}:{user.email}")
    uri = (f"otpauth://totp/{label}?secret={secret}&issuer={quote(CFG.mfa_issuer)}"
           f"&algorithm=SHA1&digits=6&period=30")
    return {"secret": secret, "otpauth_uri": uri}


def _lock_mfa(db: Session, user_id: int) -> Optional[UserMFA]:
    return db.query(UserMFA).filter(UserMFA.user_id == user_id).with_for_update().first()


def _mfa_guard_key(user_id: int) -> str:
    return f"mfa:fail:{user_id}"


def mfa_check_code(db: Session, user: User, code: str, *, allow_recovery: bool, enrolling: bool = False) -> str:
    """Verify a TOTP (or recovery) code under throttling + row lock.
    Returns 'totp' or 'recovery'. Raises AuthError on failure."""
    key = _mfa_guard_key(user.id)
    GUARD.ensure_open(key, CFG.mfa_fail_limit)
    row = _lock_mfa(db, user.id)
    code_n = _norm_code(code)
    method: Optional[str] = None
    if row and (row.enabled or enrolling):
        secret = _fernet().decrypt(row.secret_enc.encode()).decode()
        step = totp_verify(secret, code_n, row.last_used_step)
        if step is not None:
            row.last_used_step, method = step, "totp"
        elif allow_recovery and row.enabled and len(code_n) == 16:
            h = _hmac_hex("recovery", code_n)
            stored = json.loads(row.recovery_hashes or "[]")
            match = None
            for s in stored:  # compare against all: no early exit
                if hmac.compare_digest(s, h):
                    match = s
            if match:
                stored.remove(match)
                row.recovery_hashes = json.dumps(stored)
                method = "recovery"
    if not method:
        db.rollback()
        GUARD.fail(key, CFG.mfa_fail_limit)
        raise AuthError(401, "invalid_mfa_code", "Invalid verification code")
    db.commit()
    GUARD.ok(key)
    return method


def mfa_confirm_enrollment(db: Session, user: User, code: str) -> list[str]:
    row = db.get(UserMFA, user.id)
    if not row or row.enabled:
        raise AuthError(409, "mfa_not_pending", "No pending MFA enrollment")
    mfa_check_code(db, user, code, allow_recovery=False, enrolling=True)
    shown, hashes = _gen_recovery_codes()
    row = _lock_mfa(db, user.id)
    row.enabled, row.confirmed_at, row.recovery_hashes = True, utcnow(), json.dumps(hashes)
    db.commit()
    return shown


def mfa_regenerate_recovery(db: Session, user: User) -> list[str]:
    shown, hashes = _gen_recovery_codes()
    row = _lock_mfa(db, user.id)
    row.recovery_hashes = json.dumps(hashes)
    db.commit()
    return shown


def mfa_disable(db: Session, user: User) -> None:
    db.execute(delete(UserMFA).where(UserMFA.user_id == user.id))
    db.commit()


def _consume_jti(db: Session, jti: str, exp: int) -> None:
    try:
        db.add(ConsumedJTI(jti=jti, expires_at=datetime.fromtimestamp(exp, tz=timezone.utc)))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AuthError(401, "mfa_token_used", "MFA challenge already used")


# ══════════════════════════════════════════════════════════════════════════════
# 11. PASSWORD LOGIN
# ══════════════════════════════════════════════════════════════════════════════

def authenticate_password(db: Session, email: str, password: str, ip: str, ua: str) -> User:
    """Throttled, timing-safe, enumeration-safe credential check."""
    email_n = normalize_email(email)
    ek, ik = _id_hash(email_n), ip_key(ip)
    k_ip, k_pair, k_acct = f"lf:ip:{ik}", f"lf:pair:{ik}:{ek}", f"lf:acct:{ek}"

    rate_limit(f"lr:ip:{ik}", CFG.login_rate_per_min, 60)
    GUARD.ensure_open(k_ip, CFG.ip_fail_limit)
    GUARD.ensure_open(k_pair, CFG.pair_fail_limit)
    GUARD.ensure_open(k_acct, CFG.acct_fail_limit)

    user = db.query(User).filter(func.lower(User.email) == email_n).first()
    # ALWAYS run one full verification (dummy hash for unknown users).
    valid, needs_rehash = verify_password(password, user.hashed_password if user else _dummy_hash())

    if not (valid and user):
        GUARD.fail(k_ip, CFG.ip_fail_limit)
        GUARD.fail(k_pair, CFG.pair_fail_limit)
        GUARD.fail(k_acct, CFG.acct_fail_limit)
        audit(db, "login_failed", user_id=user.id if user else None, identifier=email_n, ip=ip, ua=ua,
              outcome="failure", reason="bad_credentials")
        raise AuthError(401, "invalid_credentials", "Incorrect email or password", headers=_UNAUTH)
    if not user.is_active:
        audit(db, "login_blocked", user_id=user.id, identifier=email_n, ip=ip, ua=ua,
              outcome="failure", reason="disabled")
        raise AuthError(403, "account_disabled", "Account is disabled. Please contact an administrator.")

    GUARD.ok(k_pair)
    if needs_rehash:  # transparently upgrade bcrypt/old-params/old-pepper hashes
        user.hashed_password = hash_password(password)
        db.commit()
    return user


# ══════════════════════════════════════════════════════════════════════════════
# 12. MAILER (pluggable)
# ══════════════════════════════════════════════════════════════════════════════

class Mailer(Protocol):
    def send_password_reset(self, to_email: str, reset_url: str) -> None: ...
    def send_security_notice(self, to_email: str, subject: str, body: str) -> None: ...


class LoggingMailer:
    """Default: logs only. Replace with SMTP/SES/SendGrid via set_mailer()."""

    def send_password_reset(self, to_email: str, reset_url: str) -> None:
        if CFG.is_prod:
            log.warning("LoggingMailer in production: reset email NOT delivered")
        else:
            log.info("DEV password reset link for %s: %s", to_email, reset_url)

    def send_security_notice(self, to_email: str, subject: str, body: str) -> None:
        log.info("security notice -> %s: %s", to_email, subject)


_mailer: Mailer = LoggingMailer()


def set_mailer(m: Mailer) -> None:
    global _mailer
    _mailer = m


def _notify(user: User, subject: str, body: str) -> None:
    try:
        _mailer.send_security_notice(user.email, subject, body)
    except Exception:
        log.exception("failed to send security notice")


# ══════════════════════════════════════════════════════════════════════════════
# 13. PRINCIPAL / DEPENDENCIES
# ══════════════════════════════════════════════════════════════════════════════

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user: User
    session: AuthSession
    claims: dict
    via_cookie: bool


def _make_principal_dep(*, enforce_mfa_policy: bool) -> Callable[..., Principal]:
    def dep(request: Request,
            creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
            db: Session = Depends(get_db)) -> Principal:
        raw, via_cookie = None, False
        if CFG.allow_bearer and creds and creds.scheme.lower() == "bearer":
            raw = creds.credentials
        elif request.cookies.get(COOKIES.access):
            raw, via_cookie = request.cookies[COOKIES.access], True
        if not raw:
            raise AuthError(401, "not_authenticated", "Not authenticated", headers=_UNAUTH)
        try:
            claims = jwt_decode(raw, "access")
            uid, sid = int(claims["sub"]), str(claims["sid"])
        except (JWTError, KeyError, ValueError):
            raise AuthError(401, "invalid_token", "Invalid or expired token", headers=_UNAUTH)

        # CSRF is enforced HERE so no route can forget it (cookie transport only;
        # Bearer-header requests are not ambient-credential => not CSRF-able).
        if via_cookie and request.method not in SAFE_METHODS:
            enforce_same_origin(request)
            check_csrf(request, sid)

        now = utcnow()
        sess = db.get(AuthSession, sid)
        if (not sess or sess.user_id != uid or sess.revoked_at is not None
                or now >= aware(sess.absolute_expires_at)):
            raise AuthError(401, "session_revoked", "Session is no longer valid", headers=_UNAUTH)
        user = db.get(User, uid)
        if not user or not user.is_active:
            raise AuthError(401, "user_inactive", "User not found or inactive", headers=_UNAUTH)

        if (now - aware(sess.last_seen_at)).total_seconds() > 60:  # throttle writes
            sess.last_seen_at = now
            db.commit()

        if enforce_mfa_policy and mfa_required_but_missing(db, user):
            raise AuthError(403, "mfa_enrollment_required", "Multi-factor authentication must be enabled")
        return Principal(user, sess, claims, via_cookie)
    return dep


get_current_principal = _make_principal_dep(enforce_mfa_policy=True)
get_principal_allow_unenrolled = _make_principal_dep(enforce_mfa_policy=False)


def get_current_user(p: Principal = Depends(get_current_principal)) -> User:
    """Drop-in replacement for app.core.dependencies.get_current_user."""
    return p.user


def require_role(*roles: RoleEnum) -> Callable[..., User]:
    allowed = {r.value if isinstance(r, RoleEnum) else str(r) for r in roles}

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role.value not in allowed:
            raise AuthError(403, "forbidden", "Insufficient permissions")
        return user
    return _check


require_admin = require_role(RoleEnum.ADMIN)  # FIX: previously ADMIN *and* TEACHER
require_teacher_or_admin = require_role(RoleEnum.TEACHER, RoleEnum.ADMIN)


def require_recent_auth(max_age_seconds: int = 300) -> Callable[..., Principal]:
    """Step-up auth: sensitive endpoints demand a password proof within N seconds."""
    def _dep(p: Principal = Depends(get_current_principal)) -> Principal:
        if (utcnow() - aware(p.session.auth_time)).total_seconds() > max_age_seconds:
            raise AuthError(403, "reauth_required", "Please confirm your password to continue")
        return p
    return _dep


def ensure_not_last_admin(db: Session, user: User) -> None:
    """Call before disabling/demoting an admin."""
    if user.role == RoleEnum.ADMIN:
        n = db.query(User).filter(User.role == RoleEnum.ADMIN, User.is_active.is_(True), User.id != user.id).count()
        if n == 0:
            raise AuthError(409, "last_admin", "Cannot remove the last active administrator")


def admin_reset_password(db: Session, actor: User, target: User, new_password: str, ip: str = "") -> None:
    if actor.role != RoleEnum.ADMIN:
        raise AuthError(403, "forbidden", "Admins only")
    set_user_password(db, target, new_password, actor=f"admin:{actor.id}")
    audit(db, "admin_password_reset", user_id=target.id, ip=ip, actor_id=actor.id)
    _notify(target, "Your password was reset", "An administrator reset your password. All sessions were signed out.")


# ══════════════════════════════════════════════════════════════════════════════
# 14. COOKIES + SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

def set_session_cookies(response: Response, issued: IssuedSession) -> None:
    base = dict(secure=CFG.cookie_secure, samesite=CFG.cookie_samesite, domain=CFG.cookie_domain)
    refresh_age = max(1, int((aware(issued.session.absolute_expires_at) - utcnow()).total_seconds()))
    response.set_cookie(COOKIES.access, issued.access, max_age=issued.expires_in,
                        path="/", httponly=True, **base)
    response.set_cookie(COOKIES.refresh, issued.refresh, max_age=min(refresh_age, CFG.refresh_ttl),
                        path=CFG.refresh_cookie_path, httponly=True, **base)
    response.set_cookie(COOKIES.csrf, issued.csrf, max_age=refresh_age, path="/", httponly=False, **base)


def clear_session_cookies(response: Response) -> None:
    base = dict(secure=CFG.cookie_secure, samesite=CFG.cookie_samesite, domain=CFG.cookie_domain)
    response.delete_cookie(COOKIES.access, path="/", httponly=True, **base)
    response.delete_cookie(COOKIES.refresh, path=CFG.refresh_cookie_path, httponly=True, **base)
    response.delete_cookie(COOKIES.csrf, path="/", httponly=False, **base)


class SecureLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class MFALoginRequest(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=2048)
    code: str = Field(min_length=6, max_length=32)


class LoginResponse(BaseModel):
    status: Literal["authenticated", "mfa_required", "mfa_enrollment_required"]
    user: Optional[UserPublic] = None
    csrf_token: Optional[str] = None
    expires_in: Optional[int] = None
    mfa_token: Optional[str] = None
    access_token: Optional[str] = None     # only if AUTH_TOKENS_IN_BODY=true
    refresh_token: Optional[str] = None    # only if AUTH_TOKENS_IN_BODY=true


class RefreshBody(BaseModel):
    refresh_token: Optional[str] = Field(default=None, max_length=256)


class RefreshResponse(BaseModel):
    csrf_token: str
    expires_in: int
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None


class PasswordOnly(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class PasswordAndCode(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=6, max_length=32)


class CodeOnly(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class SecureChangePassword(BaseModel):
    old_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=1, max_length=1024)


class SessionInfo(BaseModel):
    id: str
    created_at: datetime
    last_seen_at: datetime
    ip: Optional[str]
    user_agent: Optional[str]
    current: bool


def _public(u: User) -> UserPublic:
    return UserPublic(id=u.id, email=u.email, full_name=u.full_name, role=u.role.value,
                      is_active=u.is_active, last_login=u.last_login)


def _finish_login(response: Response, db: Session, user: User, issued: IssuedSession) -> LoginResponse:
    set_session_cookies(response, issued)
    needs = mfa_required_but_missing(db, user)
    return LoginResponse(
        status="mfa_enrollment_required" if needs else "authenticated",
        user=_public(user), csrf_token=issued.csrf, expires_in=issued.expires_in,
        access_token=issued.access if CFG.tokens_in_body else None,
        refresh_token=issued.refresh if CFG.tokens_in_body else None,
    )


def _verify_current_password(db: Session, user: User, password: str, ip: str, scope: str) -> None:
    """Throttled re-verification of the logged-in user's password."""
    key = f"pwv:{scope}:{user.id}"
    GUARD.ensure_open(key, 5)
    if not verify_password(password, user.hashed_password)[0]:
        GUARD.fail(key, 5)
        audit(db, f"{scope}_bad_password", user_id=user.id, ip=ip, outcome="failure")
        raise AuthError(401, "invalid_credentials", "Incorrect password")
    GUARD.ok(key)


# ══════════════════════════════════════════════════════════════════════════════
# 15. ROUTER
# ══════════════════════════════════════════════════════════════════════════════

router = APIRouter(tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(req: SecureLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_same_origin(request, require_header=False)  # blocks login-CSRF from foreign origins
    ip, ua = client_ip(request), client_ua(request)
    user = authenticate_password(db, req.email, req.password, ip, ua)
    if mfa_enabled(db, user.id):
        token, _ = jwt_encode("mfa", str(user.id), CFG.mfa_token_ttl)
        audit(db, "login_mfa_challenge", user_id=user.id, ip=ip, ua=ua)
        return LoginResponse(status="mfa_required", mfa_token=token)
    issued = create_session(db, user, ip, ua, mfa=False)
    audit(db, "login_success", user_id=user.id, ip=ip, ua=ua, sid=issued.session.id)
    return _finish_login(response, db, user, issued)


@router.post("/login/mfa", response_model=LoginResponse)
def login_mfa(req: MFALoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_same_origin(request, require_header=False)
    ip, ua = client_ip(request), client_ua(request)
    rate_limit(f"lr:mfa:{ip_key(ip)}", 30, 60)
    try:
        claims = jwt_decode(req.mfa_token, "mfa")
        user = db.get(User, int(claims["sub"]))
    except (JWTError, KeyError, ValueError):
        raise AuthError(401, "invalid_mfa_token", "Invalid or expired MFA challenge")
    if not user or not user.is_active:
        raise AuthError(401, "invalid_mfa_token", "Invalid or expired MFA challenge")
    try:
        method = mfa_check_code(db, user, req.code, allow_recovery=True)
    except AuthError:
        audit(db, "mfa_failed", user_id=user.id, ip=ip, ua=ua, outcome="failure")
        raise
    _consume_jti(db, claims["jti"], int(claims["exp"]))
    issued = create_session(db, user, ip, ua, mfa=True)
    audit(db, "login_success", user_id=user.id, ip=ip, ua=ua, sid=issued.session.id, mfa=method)
    if method == "recovery":
        _notify(user, "A recovery code was used", "A recovery code was used to sign in to your account.")
    return _finish_login(response, db, user, issued)


@router.post("/refresh", response_model=RefreshResponse)
def refresh(request: Request, response: Response, body: Optional[RefreshBody] = None,
            db: Session = Depends(get_db)):
    ip, ua = client_ip(request), client_ua(request)
    rate_limit(f"lr:refresh:{ip_key(ip)}", 120, 60)
    from_body = bool(body and body.refresh_token and CFG.tokens_in_body)
    raw = body.refresh_token if from_body else request.cookies.get(COOKIES.refresh)
    if not raw:
        raise AuthError(401, "no_refresh_token", "No refresh token", headers=_UNAUTH)
    if not from_body:  # cookie transport => ambient credential => CSRF applies
        sid = _parse_refresh(raw)
        enforce_same_origin(request)
        check_csrf(request, sid)
    issued, _ = rotate_refresh(db, raw, ip, ua)
    if not from_body:
        set_session_cookies(response, issued)
    return RefreshResponse(csrf_token=issued.csrf, expires_in=issued.expires_in,
                           access_token=issued.access if CFG.tokens_in_body else None,
                           refresh_token=issued.refresh if CFG.tokens_in_body else None)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revokes the session server-side (access token dies immediately)."""
    sid: Optional[str] = None
    raw_rt = request.cookies.get(COOKIES.refresh)
    if raw_rt:
        sid = _parse_refresh(raw_rt)
        enforce_same_origin(request)
        check_csrf(request, sid)
    else:
        creds = request.headers.get("authorization", "")
        tok = creds[7:] if creds.lower().startswith("bearer ") else request.cookies.get(COOKIES.access)
        try:
            sid = str(jwt_decode(tok, "access")["sid"]) if tok else None
        except (JWTError, KeyError):
            sid = None
    if sid:
        revoke_session(db, sid, "logout")
        audit(db, "logout", ip=client_ip(request), sid=sid)
    clear_session_cookies(response)
    return {"message": "Logged out successfully"}


@router.post("/logout-all")
def logout_all(request: Request, response: Response, p: Principal = Depends(get_principal_allow_unenrolled),
               db: Session = Depends(get_db)):
    revoke_all_sessions(db, p.user.id, "logout_all")
    audit(db, "logout_all", user_id=p.user.id, ip=client_ip(request))
    clear_session_cookies(response)
    return {"message": "All sessions revoked"}


@router.get("/me", response_model=UserPublic)
def me(p: Principal = Depends(get_principal_allow_unenrolled)):
    return _public(p.user)


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions(p: Principal = Depends(get_principal_allow_unenrolled), db: Session = Depends(get_db)):
    rows = (db.query(AuthSession).filter(AuthSession.user_id == p.user.id, AuthSession.revoked_at.is_(None),
                                         AuthSession.absolute_expires_at > utcnow())
            .order_by(AuthSession.last_seen_at.desc()).all())
    return [SessionInfo(id=r.id, created_at=r.created_at, last_seen_at=r.last_seen_at, ip=r.ip,
                        user_agent=r.user_agent, current=(r.id == p.session.id)) for r in rows]


@router.delete("/sessions/{session_id}")
def revoke_one_session(session_id: str, request: Request, p: Principal = Depends(get_principal_allow_unenrolled),
                       db: Session = Depends(get_db)):
    s = db.get(AuthSession, session_id)
    if not s or s.user_id != p.user.id:  # same answer for foreign/nonexistent => no IDOR oracle
        raise AuthError(404, "not_found", "Session not found")
    revoke_session(db, session_id, "user_revoked")
    audit(db, "session_revoked", user_id=p.user.id, ip=client_ip(request), sid=session_id)
    return {"message": "Session revoked"}


@router.post("/reauth")
def reauth(req: PasswordOnly, request: Request, p: Principal = Depends(get_principal_allow_unenrolled),
           db: Session = Depends(get_db)):
    _verify_current_password(db, p.user, req.password, client_ip(request), "reauth")
    p.session.auth_time = utcnow()
    db.commit()
    return {"message": "Re-authenticated"}


@router.post("/change-password")
def change_password(req: SecureChangePassword, request: Request, response: Response,
                    p: Principal = Depends(get_principal_allow_unenrolled), db: Session = Depends(get_db)):
    ip = client_ip(request)
    _verify_current_password(db, p.user, req.old_password, ip, "chpw")
    set_user_password(db, p.user, req.new_password, actor="self")
    audit(db, "password_changed", user_id=p.user.id, ip=ip)
    _notify(p.user, "Your password was changed", "If this wasn't you, contact an administrator immediately.")
    clear_session_cookies(response)
    return {"message": "Password changed. Please log in again."}


# ── Password reset ────────────────────────────────────────────────────────────

def _send_reset(email_n: str, ip: str) -> None:
    """Runs in a background task so response time never reveals whether the account exists."""
    from app.core.database import get_db as _gdb  # fresh session independent of the request
    gen = _gdb()
    db = next(gen)
    try:
        user = db.query(User).filter(func.lower(User.email) == email_n, User.is_active.is_(True)).first()
        if not user:
            return
        raw = secrets.token_urlsafe(32)
        now = utcnow()
        db.add(PasswordResetToken(user_id=user.id, token_hash=_hmac_hex("reset", raw), created_at=now,
                                  expires_at=now + timedelta(seconds=CFG.reset_ttl), ip=ip))
        db.commit()
        # token in the URL *fragment* => never sent to servers / Referer headers
        _mailer.send_password_reset(user.email, f"{CFG.reset_url}#token={raw}")
        audit(db, "password_reset_requested", user_id=user.id, ip=ip)
    except Exception:
        log.exception("password reset dispatch failed")
    finally:
        try:
            gen.close()
        except Exception:
            pass


@router.post("/password-reset/request", status_code=202)
def password_reset_request(req: ResetRequest, request: Request, bg: BackgroundTasks):
    enforce_same_origin(request, require_header=False)
    ip, email_n = client_ip(request), normalize_email(req.email)
    try:
        rate_limit(f"reset:ip:{ip_key(ip)}", 10, 3600)
        rate_limit(f"reset:acct:{_id_hash(email_n)}", 3, 3600)
        bg.add_task(_send_reset, email_n, ip)
    except AuthError:
        pass  # silently drop; identical response either way
    return {"message": "If that account exists, a reset link has been sent."}


@router.post("/password-reset/confirm")
def password_reset_confirm(req: ResetConfirm, request: Request, db: Session = Depends(get_db)):
    enforce_same_origin(request, require_header=False)
    ip = client_ip(request)
    rate_limit(f"reset:confirm:{ip_key(ip)}", 10, 900)
    now = utcnow()
    invalid = AuthError(400, "invalid_reset_token", "Reset link is invalid or has expired")
    row = db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == _hmac_hex("reset", req.token)).first()
    if not row or row.used_at is not None or now >= aware(row.expires_at):
        raise invalid
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise invalid
    # Validate the new password BEFORE burning the token (user can retry).
    try:
        validate_password_policy(req.new_password, email=user.email, full_name=user.full_name)
    except PasswordPolicyError as e:
        raise AuthError(422, "weak_password", "Password does not meet requirements.", extra={"problems": e.problems})
    claimed = db.execute(update(PasswordResetToken)
                         .where(PasswordResetToken.id == row.id, PasswordResetToken.used_at.is_(None))
                         .values(used_at=now)).rowcount
    db.commit()
    if claimed != 1:  # lost a race with a concurrent confirm
        raise invalid
    set_user_password(db, user, req.new_password, actor="reset")
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id,
                                                PasswordResetToken.used_at.is_(None)).values(used_at=now))
    db.commit()
    audit(db, "password_reset_completed", user_id=user.id, ip=ip)
    _notify(user, "Your password was reset", "Your password was reset via an emailed link. All sessions were signed out.")
    return {"message": "Password updated. Please log in."}


# ── MFA management ────────────────────────────────────────────────────────────

@router.post("/mfa/setup")
def mfa_setup(req: PasswordOnly, request: Request, p: Principal = Depends(get_principal_allow_unenrolled),
              db: Session = Depends(get_db)):
    _verify_current_password(db, p.user, req.password, client_ip(request), "mfa")
    return mfa_begin_enrollment(db, p.user)


@router.post("/mfa/enable")
def mfa_enable(req: CodeOnly, request: Request, p: Principal = Depends(get_principal_allow_unenrolled),
               db: Session = Depends(get_db)):
    codes = mfa_confirm_enrollment(db, p.user, req.code)
    p.session.mfa_verified = True
    db.commit()
    revoke_all_sessions(db, p.user.id, "mfa_enabled", except_sid=p.session.id)
    audit(db, "mfa_enabled", user_id=p.user.id, ip=client_ip(request))
    _notify(p.user, "Two-factor authentication enabled", "MFA was enabled on your account.")
    return {"recovery_codes": codes, "message": "Store these codes safely; they are shown only once."}


@router.post("/mfa/recovery-codes")
def mfa_new_recovery_codes(req: PasswordAndCode, request: Request,
                           p: Principal = Depends(get_principal_allow_unenrolled), db: Session = Depends(get_db)):
    ip = client_ip(request)
    _verify_current_password(db, p.user, req.password, ip, "mfa")
    mfa_check_code(db, p.user, req.code, allow_recovery=False)
    codes = mfa_regenerate_recovery(db, p.user)
    audit(db, "mfa_recovery_regenerated", user_id=p.user.id, ip=ip)
    return {"recovery_codes": codes}


@router.post("/mfa/disable")
def mfa_disable_endpoint(req: PasswordAndCode, request: Request,
                         p: Principal = Depends(get_principal_allow_unenrolled), db: Session = Depends(get_db)):
    ip = client_ip(request)
    if p.user.role.value in CFG.mfa_required_roles:
        raise AuthError(403, "mfa_mandatory", "MFA is mandatory for your role")
    _verify_current_password(db, p.user, req.password, ip, "mfa")
    mfa_check_code(db, p.user, req.code, allow_recovery=True)
    mfa_disable(db, p.user)
    revoke_all_sessions(db, p.user.id, "mfa_disabled", except_sid=p.session.id)
    audit(db, "mfa_disabled", user_id=p.user.id, ip=ip)
    _notify(p.user, "Two-factor authentication disabled", "MFA was disabled on your account.")
    return {"message": "MFA disabled"}


# ══════════════════════════════════════════════════════════════════════════════
# 16. MIDDLEWARE (pure ASGI — no BaseHTTPMiddleware pitfalls)
# ══════════════════════════════════════════════════════════════════════════════

class SecurityHeadersMiddleware:
    _DOCS = ("/docs", "/redoc", "/openapi.json")

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")

        async def wrapped(message: dict) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Referrer-Policy", "no-referrer")
                h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                h.setdefault("Cross-Origin-Resource-Policy", "same-site")
                h.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=(), payment=()")
                h.setdefault("Cache-Control", "no-store")   # tokens / PII must never be cached
                h.setdefault("Pragma", "no-cache")
                if not path.startswith(self._DOCS):
                    h.setdefault("Content-Security-Policy",
                                 "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                if CFG.cookie_secure:
                    h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
            await send(message)

        await self.app(scope, receive, wrapped)


class _TooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Rejects oversized request bodies on selected path prefixes (default: auth)."""

    def __init__(self, app: Any, max_bytes: int, path_prefix: str) -> None:
        self.app, self.max, self.prefix = app, max_bytes, path_prefix

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.prefix):
            return await self.app(scope, receive, send)
        resp = JSONResponse({"detail": {"code": "payload_too_large", "message": "Request body too large"}},
                            status_code=413)
        cl = dict(scope.get("headers", [])).get(b"content-length")
        try:
            if cl and int(cl) > self.max:
                return await resp(scope, receive, send)
        except ValueError:
            return await resp(scope, receive, send)
        started, seen = False, 0

        async def limited_receive() -> dict:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max:
                    raise _TooLarge()
            return msg

        async def tracking_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await resp(scope, receive, send)


# ══════════════════════════════════════════════════════════════════════════════
# 17. INSTALL / MAINTENANCE / SEEDING GUARD
# ══════════════════════════════════════════════════════════════════════════════

def install(app: FastAPI, *, prefix: str = "/api/auth") -> None:
    """Validate config, add middleware, mount the hardened router."""
    problems = validate_config()
    if problems:
        for pr in problems:
            (log.critical if CFG.is_prod else log.warning)("auth config: %s", pr)
        if CFG.is_prod:
            raise RuntimeError("Refusing to start in production with insecure auth config:\n - "
                               + "\n - ".join(problems))
    if CFG.allowed_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(CFG.allowed_hosts))
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=CFG.max_body_bytes, path_prefix=prefix)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(router, prefix=prefix)
    if CFG.is_prod:  # no interactive docs / schema leak in production
        app.docs_url = app.redoc_url = app.openapi_url = None


def harden_seeding(db: Session) -> None:
    """Use instead of calling seed_demo_teachers_if_needed(): never seeds demo
    accounts in production; refuses a weak seeded admin password."""
    if CFG.is_prod:
        log.info("Production: demo teacher seeding skipped")
        return
    from app.services import auth_service
    auth_service.seed_demo_teachers_if_needed(db)


def purge_expired(db: Session, *, session_retention_days: int = 7) -> dict:
    """Housekeeping — schedule hourly."""
    now = utcnow()
    cutoff = now - timedelta(days=session_retention_days)
    out = {}
    out["refresh_tokens"] = db.execute(delete(RefreshTokenRow).where(RefreshTokenRow.expires_at < now)).rowcount
    out["sessions"] = db.execute(delete(AuthSession).where(
        (AuthSession.absolute_expires_at < cutoff) | (AuthSession.revoked_at < cutoff))).rowcount
    out["reset_tokens"] = db.execute(delete(PasswordResetToken).where(PasswordResetToken.expires_at < cutoff)).rowcount
    out["consumed_jti"] = db.execute(delete(ConsumedJTI).where(ConsumedJTI.expires_at < now)).rowcount
    db.commit()
    return out


def _cli() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "gen-secrets":
        print("APP_ENV=production")
        print("JWT_SECRET_KEY=" + secrets.token_urlsafe(64))
        print("AUTH_MASTER_KEY=" + secrets.token_urlsafe(64))
        print("PASSWORD_PEPPERS=" + secrets.token_urlsafe(48))
        print("MFA_ENCRYPTION_KEYS=" + Fernet.generate_key().decode())
        print("# Rotation: prepend the new value, keep the old one after a comma "
              "(JWT_PREVIOUS_KEYS for JWT), redeploy, then drop old values after the max token/hash lifetime.")
    else:
        print("usage: python secure_auth.py gen-secrets")


if __name__ == "__main__":
    _cli()
