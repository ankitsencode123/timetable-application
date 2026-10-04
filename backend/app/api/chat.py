"""Chat endpoints for teachers and the public."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat_service

router = APIRouter()


@router.post("/public/chat", response_model=ChatResponse)
def handle_public_request(req: ChatRequest, db: Session = Depends(get_db)):
    """Unauthenticated chat. Read-only."""
    return chat_service.handle_public_chat(req.message, db)


@router.post("/teacher/chat", response_model=ChatResponse)
def handle_teacher_request(
    req: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Authenticated chat. Allows triggering schedule generations."""
    return chat_service.handle_teacher_chat(user, req.message, db)
