import uuid
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from app.core.exceptions import AuthInvalidError, ConflictError, ForbiddenError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.core.config import settings
from app.features.auth.schemas import TokenPair
from app.features.users.models import AuthProvider
from app.features.users.repository import UserRepository

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)
        self.db = db

    # ─────────────────────────────────────────────
    # Register
    # ─────────────────────────────────────────────
    async def register(self, email: str, password: str, full_name: str) -> TokenPair:
        existing = await self.repo.get_by_email(email)
        if existing:
            raise ConflictError("Email already registered")

        user = await self.repo.create(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            auth_provider=AuthProvider.email,
            is_email_verified=False,
        )

        logger.info(f"User registered: {user.id}")

        return TokenPair(
            access_token=create_access_token(user.id, user.role.value),
            refresh_token=create_refresh_token(user.id),
        )

    # ─────────────────────────────────────────────
    # Login
    # ─────────────────────────────────────────────
    async def login(self, email: str, password: str) -> dict:
        user = await self.repo.get_by_email(email)

        if not user or not user.hashed_password:
            logger.warning(f"Login failed: user not found ({email})")
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not verify_password(password, user.hashed_password):
            logger.warning(f"Login failed: wrong password ({email})")
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not user.is_active:
            raise ForbiddenError("Account is deactivated")

        user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.db.commit()

        logger.info(f"User login: {user.id}")

        return {
            "access_token": create_access_token(user.id, user.role.value),
            "refresh_token": create_refresh_token(user.id),
            "user": {
                "id": str(user.id),
                "full_name": user.full_name,
                "email": user.email,
                "role": user.role.value,
                "is_profile_complete": user.is_profile_complete,
            },
        }

    # ─────────────────────────────────────────────
    # Refresh
    # ─────────────────────────────────────────────
    async def refresh(self, refresh_token: str) -> dict:
        payload = decode_refresh_token(refresh_token)

        if not payload or payload.get("type") != "refresh":
            logger.warning("Invalid refresh token")
            raise AuthInvalidError("Invalid or expired refresh token")

        user_id = payload.get("sub")
        if not user_id:
            raise AuthInvalidError()

        try:
            user = await self.repo.get_by_id(uuid.UUID(user_id))
        except Exception:
            raise AuthInvalidError()

        if not user or not user.is_active:
            raise AuthInvalidError("User not found or deactivated")

        logger.info(f"Token refreshed: {user.id}")

        return {
            "access_token": create_access_token(user.id, user.role.value),
            "refresh_token": create_refresh_token(user.id),
        }

    # ─────────────────────────────────────────────
    # Change Password
    # ─────────────────────────────────────────────
    async def change_password(
        self, user_id: uuid.UUID, current_password: str, new_password: str
    ) -> None:
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise AuthInvalidError()

        if not user.hashed_password:
            raise HTTPException(
                status_code=400,
                detail="Google account cannot change password",
            )

        if not verify_password(current_password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Incorrect password")

        await self.repo.update_password(user, hash_password(new_password))
        logger.info(f"Password changed: {user.id}")

    # ─────────────────────────────────────────────
    # Google Login
    # ─────────────────────────────────────────────
    async def login_with_google(self, token: str) -> dict:
        try:
            id_info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
                clock_skew_in_seconds=10,
            )
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid Google token")

        email = id_info.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="Email not provided")

        user = await self.repo.get_by_email(email)

        if not user:
            user = await self.repo.create(
                email=email,
                full_name=id_info.get("name", "Unknown"),
                hashed_password=None,
                auth_provider=AuthProvider.google,
                is_email_verified=True,
            )
            user.provider_id = id_info.get("sub")
            self.repo.session.add(user)

        if not user.is_active:
            raise ForbiddenError("Account is deactivated")

        if not user.provider_id:
            user.provider_id = id_info.get("sub")

        user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.db.commit()

        logger.info(f"Google login: {user.id}")

        return {
            "access_token": create_access_token(user.id, user.role.value),
            "refresh_token": create_refresh_token(user.id),
            "user": {
                "id": str(user.id),
                "full_name": user.full_name,
                "email": user.email,
                "role": user.role.value,
                "is_profile_complete": user.is_profile_complete,
            },
        }
