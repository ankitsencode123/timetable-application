"""Self-service profile endpoints — for authenticated teachers/admins.

All routes require authentication but do NOT require ADMIN role.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.schemas.user import UserOut, UserUpdateSelf
from app.schemas.auth import ChangePasswordRequest
from app.services import auth_service

router = APIRouter()


@router.get("/me", response_model=UserOut)
def get_my_profile(current_user: User = Depends(get_current_user)):
    """Return the current user's full profile."""
    return current_user


@router.patch("/me", response_model=UserOut)
def update_my_profile(
    req: UserUpdateSelf,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    
):
    """Update own display name."""
    if req.full_name is not None:
        if len(req.full_name.strip()) < 2:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name too short")
        current_user.full_name = req.full_name.strip()
        db.commit()
        db.refresh(current_user)
    return current_user


@router.post("/change-password")
def change_my_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    
):
    """Change own password — requires current password. Invalidates all sessions."""
    try:
        auth_service.change_password(db, current_user, req.old_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"message": "Password changed successfully. Please log in again."}
