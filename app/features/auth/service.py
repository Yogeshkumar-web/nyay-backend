from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.core.security import decode_access_token, decode_refresh_token
from app.features.auth.schemas import TokenPair
from app.features.users.repository import UserRepository


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)

    async def register(self, email: str, password: str, full_name: str) -> TokenPair:
        existing = await self.repo.get_by_email(email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

        user = await self.repo.create(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
        )

        return TokenPair(
            access_token=create_access_token(user.id, user.role.value),
            refresh_token=create_refresh_token(str(user.id)),
        )

    async def login(self, email: str, password: str) -> dict:
        user = await self.repo.get_by_email(email)

        if (
            not user
            or not user.hashed_password
            or not verify_password(password, user.hashed_password)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account disabled",
            )

        return {
            "access_token": create_access_token(user.id, user.role.value),
            "refresh_token": create_refresh_token(str(user.id)),
            "user": {
                "id": str(user.id),
                "full_name": user.full_name,
                "email": user.email,
                "role": user.role.value,
                "bar_enrollment_no": user.bar_enrollment_no,
                "phone": user.phone,
            },
        }

    async def refresh(self, refresh_token: str) -> dict:
        try:
            payload = decode_refresh_token(refresh_token)

            if payload.get("type") != "refresh":
                raise ValueError("Wrong token type")

            user_id = payload.get("sub")
            if not user_id:
                raise ValueError("Invalid token payload")

        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # DB se user fetch karo role ke liye
        try:
            user = await self.repo.get_by_id(uuid.UUID(user_id))
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user ID",
            )

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        return {
            "access_token": create_access_token(user.id, user.role.value),
            "refresh_token": create_refresh_token(str(user.id)),
        }
