"""Main FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import engine, get_db
from app.models.base import Base
from app.services.auth_service import seed_admin_if_needed
from app.core import secure_auth
from app.services.seed_timetable import seed_default_timetable_if_needed, seed_catalog_if_needed
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup
    Base.metadata.create_all(bind=engine)

    # Create calendar system tables (idempotent)
    from app.calendar_system import init_calendar_tables
    init_calendar_tables(engine)

    # Seed admin and demo teachers on first boot
    db = next(get_db())
    try:
        from app.scheduler import constraints as C
        C.reload_catalog(db)

        seed_admin_if_needed(db)
        secure_auth.harden_seeding(db)
        seed_catalog_if_needed(db)
        # Seed default timetable from EXISTING_TIMETABLE_MD
        from app.models.user import User, RoleEnum
        admin = db.query(User).filter(User.role == RoleEnum.ADMIN).first()
        if admin:
            seed_default_timetable_if_needed(db, admin.id)

        # Warm up the fast suggester: pre-imports OR-Tools & primes busy-day cache.
        try:
            from app.scheduler.fast_suggester import warmup as _fast_warmup
            _fast_warmup()
        except Exception:
            pass
    finally:
        db.close()

    yield
    # Shutdown logic if any

app = FastAPI(
    title="Timetable API",
    description="Production backend for academic timetable scheduling.",
    version="1.0.0",
    lifespan=lifespan
)

_cors_origins = settings.cors_origin_list
_allow_all = _cors_origins == ["*"]

app.add_middleware(
    CORSMiddleware,
    # When wildcard: use regex to reflect the actual origin back (required for credentials: include)
    allow_origins=[] if _allow_all else _cors_origins,
    allow_origin_regex=".*" if _allow_all else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

secure_auth.install(app, prefix="/api/auth")
app.include_router(api_router, prefix="/api")


@app.get("/api/health")
@app.head("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}
