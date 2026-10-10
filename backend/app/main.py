"""Main FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.api.auth import router as auth_router
from app.core.config import get_settings
from app.core.database import engine, get_db
from app.models.base import Base
from app.services.auth_service import seed_admin_if_needed
from app.services.seed_timetable import seed_default_timetable_if_needed, seed_catalog_if_needed

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    from app.calendar_system import init_calendar_tables
    init_calendar_tables(engine)

    from app.actions.concurrency import ensure_concurrency_schema
    ensure_concurrency_schema(engine)

    db = next(get_db())
    try:
        from app.scheduler import constraints as C
        C.reload_catalog(db)

        seed_admin_if_needed(db)
        seed_catalog_if_needed(db)

        from app.models.user import User, RoleEnum
        admin = db.query(User).filter(User.role == RoleEnum.ADMIN).first()
        if admin:
            seed_default_timetable_if_needed(db, admin.id)

        try:
            from app.scheduler.fast_suggester import warmup as _fast_warmup
            _fast_warmup()
        except Exception:
            pass
    finally:
        db.close()

    yield


app = FastAPI(
    title="Timetable API",
    description="Production backend for academic timetable scheduling.",
    version="1.0.0",
    lifespan=lifespan
)

from app.actions.concurrency import install_concurrency_handlers  # noqa: E402
install_concurrency_handlers(app)

_cors_origins = settings.cors_origin_list
_allow_all = _cors_origins == ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if _allow_all else _cors_origins,
    allow_origin_regex=".*" if _allow_all else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple auth router (no MFA, no CSRF, no sessions)
app.include_router(auth_router, prefix="/api/auth")
app.include_router(api_router, prefix="/api")


@app.get("/api/health")
@app.head("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}

