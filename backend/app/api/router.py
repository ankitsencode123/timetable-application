"""Aggregation of all API routers."""
from __future__ import annotations
from fastapi import APIRouter

from app.api import users, teachers, timetable, versions, public, chat, actions, profile
from app.api import catalog, busy_slots, schedule_smart
from app.calendar_system import router as calendar_router

api_router = APIRouter()

api_router.include_router(profile.router,        prefix="/profile",         tags=["profile"])
api_router.include_router(users.router,          prefix="/users",           tags=["users"])
api_router.include_router(teachers.router,       prefix="/teachers",        tags=["teachers"])
api_router.include_router(timetable.router,      prefix="/timetable",       tags=["timetable"])
api_router.include_router(versions.router,       prefix="/versions",        tags=["versions"])
api_router.include_router(public.router,         prefix="/public",          tags=["public"])
api_router.include_router(chat.router,           prefix="",                 tags=["chat"])
api_router.include_router(actions.router,        prefix="/actions",         tags=["actions"])
api_router.include_router(catalog.router,        prefix="/catalog",         tags=["catalog"])
api_router.include_router(busy_slots.router,     prefix="/busy-slots",      tags=["busy-slots"])
api_router.include_router(schedule_smart.router, prefix="/actions/smart-schedule", tags=["smart-schedule"])
api_router.include_router(calendar_router)   # /calendar/* and /admin/calendar/* (prefixes defined in module)
