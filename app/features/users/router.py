import uuid
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field

from app.core.dependencies import CurrentUser, DB
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password
from app.features.users.models import UserRole
from app.features.users.repository import UserRepository
from app.features.users.schemas import (
    LawyerProfileResponse,
    LawyerProfileUpdate,
    MunshiResponse,
)
from app.features.users.service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


class CreateMunshiRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=8)
    mobile_number: Optional[str] = None


class ToggleMunshiRequest(BaseModel):
    is_active: Optional[bool] = None


# ─────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────
@router.get("/me")
async def get_me(current_user: CurrentUser):
    return {
        "success": True,
        "data": LawyerProfileResponse.model_validate(current_user).model_dump(),
    }


@router.patch("/me/profile")
async def update_profile(
    body: LawyerProfileUpdate,
    current_user: CurrentUser,
    db: DB,
):
    svc = UserService(db)
    user = await svc.update_profile(current_user.id, body)
    return {
        "success": True,
        "data": LawyerProfileResponse.model_validate(user).model_dump(),
    }


# ─────────────────────────────────────────────
# Munshi
# ─────────────────────────────────────────────
@router.post("/munshis", status_code=201)
async def create_munshi(
    body: CreateMunshiRequest,
    current_user: CurrentUser,
    db: DB,
):
    if current_user.role not in (UserRole.lawyer, UserRole.admin):
        raise ForbiddenError("Only lawyers can create munshi")

    repo = UserRepository(db)

    if await repo.get_by_email(body.email):
        raise ConflictError("Email already registered")

    user = await repo.create(
        full_name=body.full_name,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=UserRole.munshi,
        mobile_number=body.mobile_number,
    )

    return {
        "success": True,
        "data": {"user": MunshiResponse.model_validate(user).model_dump()},
    }


@router.get("/munshis")
async def list_munshis(current_user: CurrentUser, db: DB):
    if current_user.role not in (UserRole.lawyer, UserRole.admin):
        raise ForbiddenError("Only lawyers can view munshis")

    repo = UserRepository(db)
    users = await repo.list_munshis_for_lawyer(current_user.id)

    return {
        "success": True,
        "data": {
            "users": [MunshiResponse.model_validate(u).model_dump() for u in users]
        },
    }


@router.patch("/munshis/{user_id}")
async def toggle_munshi(
    user_id: uuid.UUID,
    body: ToggleMunshiRequest,
    current_user: CurrentUser,
    db: DB,
):
    if current_user.role not in (UserRole.lawyer, UserRole.admin):
        raise ForbiddenError("Only lawyers can manage munshis")

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)

    if not user:
        raise NotFoundError("User not found")

    if user.role != UserRole.munshi:
        raise ForbiddenError("Not a munshi account")

    if body.is_active is not None:
        user = await repo.set_active(user, body.is_active)

    return {
        "success": True,
        "data": {"user": MunshiResponse.model_validate(user).model_dump()},
    }
