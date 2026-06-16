import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from app.core.exceptions import AuthInvalidError, ConflictError, ForbiddenError, ValidationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.core.config import settings
from app.features.users.models import AuthProvider
from app.features.users.repository import UserRepository
from app.features.users.schemas import LawyerProfileResponse

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)
        self.db = db

    # ─────────────────────────────────────────────
    # Register
    # ─────────────────────────────────────────────
    def _serialize_user(self, user) -> dict:
        return LawyerProfileResponse.model_validate(user).model_dump(mode="json")

    def _auth_result(self, user) -> dict:
        access_token = create_access_token(user.id, user.role.value)
        refresh_token = create_refresh_token(user.id)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "public_data": {
                "access_token": access_token,
                "token_type": "bearer",
                "user": self._serialize_user(user),
            },
        }

    async def register(self, email: str, password: str, full_name: str) -> dict:
        normalized_email = email.strip().lower()
        existing = await self.repo.get_by_email(normalized_email)
        if existing:
            raise ConflictError("Email already registered")

        user = await self.repo.create(
            email=normalized_email,
            full_name=full_name.strip(),
            hashed_password=hash_password(password),
            auth_provider=AuthProvider.email,
            is_email_verified=False,
        )

        logger.info(f"User registered: {user.id}")

        return self._auth_result(user)

    # ─────────────────────────────────────────────
    # Login
    # ─────────────────────────────────────────────
    async def login(self, email: str, password: str) -> dict:
        normalized_email = email.strip().lower()
        user = await self.repo.get_by_email(normalized_email)

        if not user or not user.hashed_password:
            logger.warning("Login failed: user not found or password unavailable")
            raise AuthInvalidError("Invalid email or password")

        if not verify_password(password, user.hashed_password):
            logger.warning("Login failed: wrong password")
            raise AuthInvalidError("Invalid email or password")

        if not user.is_active:
            raise ForbiddenError("Account is deactivated")

        user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.db.commit()

        logger.info(f"User login: {user.id}")

        return self._auth_result(user)

    # ─────────────────────────────────────────────
    # Refresh
    # ─────────────────────────────────────────────
    async def refresh(self, refresh_token: str | None) -> dict:
        if not refresh_token:
            raise AuthInvalidError("Invalid or expired refresh token")

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
            raise ValidationError("Google account cannot change password")

        if not verify_password(current_password, user.hashed_password):
            raise AuthInvalidError("Incorrect password")

        await self.repo.update_password(user, hash_password(new_password))
        logger.info(f"Password changed: {user.id}")

    # ─────────────────────────────────────────────
    # Google Login
    # ─────────────────────────────────────────────
    async def login_with_google(self, token: str) -> dict:
        if not settings.GOOGLE_CLIENT_ID:
            raise AuthInvalidError("Google login is not configured")

        try:
            id_info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
                clock_skew_in_seconds=10,
            )
        except Exception:
            raise AuthInvalidError("Invalid Google token")

        email = (id_info.get("email") or "").strip().lower()
        provider_id = id_info.get("sub")
        if not email:
            raise ValidationError("Email not provided by Google")
        if not provider_id:
            raise AuthInvalidError("Invalid Google token")

        provider_user = await self.repo.get_by_provider_id(provider_id)
        if provider_user and provider_user.email.lower() != email:
            raise ConflictError("Google account is already linked to another user")

        user = await self.repo.get_by_email(email)

        if not user:
            user = await self.repo.create(
                email=email,
                full_name=(id_info.get("name") or "Unknown").strip(),
                hashed_password=None,
                auth_provider=AuthProvider.google,
                provider_id=provider_id,
                is_email_verified=bool(id_info.get("email_verified", True)),
            )
        elif user.provider_id and user.provider_id != provider_id:
            raise ConflictError("Email is already linked to a different Google account")

        if not user.is_active:
            raise ForbiddenError("Account is deactivated")

        if not user.provider_id:
            user.provider_id = provider_id

        user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.db.commit()

        logger.info(f"Google login: {user.id}")

        return self._auth_result(user)
