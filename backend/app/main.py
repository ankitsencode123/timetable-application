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
from app.services.auth_service import seed_admin_if_needed, seed_demo_teachers_if_needed
from app.services.seed_timetable import seed_default_timetable_if_needed, seed_catalog_if_needed
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup
    Base.metadata.create_all(bind=engine)

    # Seed admin and demo teachers on first boot
    db = next(get_db())
    try:
        from app.scheduler import constraints as C
        C.reload_catalog(db)

        seed_admin_if_needed(db)
        seed_demo_teachers_if_needed(db)
        seed_catalog_if_needed(db)
        # Seed default timetable from EXISTING_TIMETABLE_MD
        from app.models.user import User, RoleEnum
        admin = db.query(User).filter(User.role == RoleEnum.ADMIN).first()
        if admin:
            seed_default_timetable_if_needed(db, admin.id)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/api/health")
@app.head("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}
