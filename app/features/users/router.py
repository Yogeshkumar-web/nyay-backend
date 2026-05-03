import uuid
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field, ConfigDict

from app.core.dependencies import CurrentUser, DB
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password
from app.features.users.models import UserRole
from app.features.users.repository import UserRepository

router = APIRouter(prefix="/users", tags=["Users"])


class CreateMunshiRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=8)
    phone: Optional[str] = Field(None, max_length=20)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    role: UserRole
    phone: Optional[str]
    is_active: bool


class UpdateUserRequest(BaseModel):
    is_active: Optional[bool] = None


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Returns the currently authenticated user.",
)
async def get_me(current_user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post(
    "",
    status_code=201,
    summary="Create munshi account",
    description="Lawyer creates a munshi account. Munshi can then be given case access.",
)
async def create_munshi(
    body: CreateMunshiRequest,
    current_user: CurrentUser,
    db: DB,
):
    if current_user.role not in (UserRole.lawyer, UserRole.admin):
        raise ForbiddenError("Only lawyers can create munshi accounts")

    repo = UserRepository(db)
    existing = await repo.get_by_email(body.email)
    if existing:
        raise ConflictError("Email already registered")

    user = await repo.create(
        full_name=body.full_name,
        email=body.email,
        password_hash=hash_password(body.password),
        role=UserRole.munshi,
        phone=body.phone,
    )
    return {"success": True, "data": {"user": UserResponse.model_validate(user).model_dump()}}


@router.get(
    "",
    summary="List munshis",
    description="Lists all munshi accounts associated with the lawyer's cases.",
)
async def list_munshis(
    current_user: CurrentUser,
    db: DB,
):
    if current_user.role not in (UserRole.lawyer, UserRole.admin):
        raise ForbiddenError("Only lawyers can list munshis")

    repo = UserRepository(db)
    users = await repo.list_munshis_for_lawyer(current_user.id)
    return {
        "success": True,
        "data": {"users": [UserResponse.model_validate(u).model_dump() for u in users]},
    }


@router.patch(
    "/{user_id}",
    summary="Activate or deactivate munshi",
)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    current_user: CurrentUser,
    db: DB,
):
    if current_user.role not in (UserRole.lawyer, UserRole.admin):
        raise ForbiddenError("Only lawyers can manage munshi accounts")

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFoundError("User not found")
    if user.role != UserRole.munshi:
        raise ForbiddenError("Can only manage munshi accounts")

    if body.is_active is not None:
        user = await repo.set_active(user, body.is_active)

    return {"success": True, "data": {"user": UserResponse.model_validate(user).model_dump()}}







# import uuid

# from fastapi import APIRouter, Depends
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.core.dependencies import get_current_user_id
# from app.core.exceptions import AppException
# from app.db.session import get_db
# from app.features.users.repository import UserRepository
# from app.features.users.schemas import UserOut
# from typing import Optional
# from pydantic import BaseModel, EmailStr, Field, ConfigDict
# from app.features.users.models import UserRole

# from app.core.dependencies import CurrentUser, DB
# from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
# from app.core.security import hash_password


# router = APIRouter(prefix="/users", tags=["Users"])


# class CreateMunshiRequest(BaseModel):
#     full_name: str = Field(..., min_length=2, max_length=200)
#     email: EmailStr
#     password: str = Field(..., min_length=8)
#     phone: Optional[str] = Field(None, max_length=20)


# class UserResponse(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: uuid.UUID
#     full_name: str
#     email: str
#     role: UserRole
#     phone: Optional[str]
#     is_active: bool


# class UpdateUserRequest(BaseModel):
#     is_active: Optional[bool] = None


# @router.post(
#     "",
#     status_code=201,
#     summary="Create munshi account",
#     description="Lawyer creates a munshi account. Munshi can then be given case access.",
# )
# async def create_munshi(
#     body: CreateMunshiRequest,
#     current_user: CurrentUser,
#     db: DB,
# ):
#     if current_user.role not in (UserRole.lawyer, UserRole.admin):
#         raise ForbiddenError("Only lawyers can create munshi accounts")

#     repo = UserRepository(db)
#     existing = await repo.get_by_email(body.email)
#     if existing:
#         raise ConflictError("Email already registered")

#     user = await repo.create(
#         full_name=body.full_name,
#         email=body.email,
#         password_hash=hash_password(body.password),
#         role=UserRole.munshi,
#         phone=body.phone,
#     )
#     return {"success": True, "data": {"user": UserResponse.model_validate(user).model_dump()}}


# @router.get(
#     "",
#     summary="List munshis",
#     description="Lists all munshi accounts associated with the lawyer's cases.",
# )
# async def list_munshis(
#     current_user: CurrentUser,
#     db: DB,
# ):
#     if current_user.role not in (UserRole.lawyer, UserRole.admin):
#         raise ForbiddenError("Only lawyers can list munshis")

#     repo = UserRepository(db)
#     users = await repo.list_munshis_for_lawyer(current_user.id)
#     return {
#         "success": True,
#         "data": {"users": [UserResponse.model_validate(u).model_dump() for u in users]},
#     }


# @router.patch(
#     "/{user_id}",
#     summary="Activate or deactivate munshi",
# )
# async def update_user(
#     user_id: uuid.UUID,
#     body: UpdateUserRequest,
#     current_user: CurrentUser,
#     db: DB,
# ):
#     if current_user.role not in (UserRole.lawyer, UserRole.admin):
#         raise ForbiddenError("Only lawyers can manage munshi accounts")

#     repo = UserRepository(db)
#     user = await repo.get_by_id(user_id)
#     if not user:
#         raise NotFoundError("User not found")
#     if user.role != UserRole.munshi:
#         raise ForbiddenError("Can only manage munshi accounts")

#     if body.is_active is not None:
#         user = await repo.set_active(user, body.is_active)

#     return {"success": True, "data": {"user": UserResponse.model_validate(user).model_dump()}}

# router = APIRouter(prefix="/users", tags=["Users"])


# @router.get(
#     "/me",
#     response_model=UserOut,
#     summary="Get current user",
#     description="Returns the currently authenticated user.",
# )
# async def get_me(
#     user_id: str = Depends(get_current_user_id),
#     db: AsyncSession = Depends(get_db),
# ) -> UserOut:
#     repo = UserRepository(db)
#     user = await repo.get_by_id(uuid.UUID(user_id))
#     if not user:
#         raise AppException(status_code=404, detail="User not found")
#     return user