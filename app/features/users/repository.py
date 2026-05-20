import uuid
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.users.models import AuthProvider, User, UserRole

logger = logging.getLogger(__name__)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ─────────────────────────────────────────────
    # Lookups
    # ─────────────────────────────────────────────
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    # ─────────────────────────────────────────────
    # Create
    # ─────────────────────────────────────────────
    async def create(
        self,
        full_name: str,
        email: str,
        role: UserRole = UserRole.lawyer,
        hashed_password: Optional[str] = None,
        auth_provider: AuthProvider = AuthProvider.email,
        provider_id: Optional[str] = None,
        is_email_verified: bool = False,
        mobile_number: Optional[str] = None,
    ) -> User:
        user = User(
            full_name=full_name,
            email=email,
            hashed_password=hashed_password,
            role=role,
            auth_provider=auth_provider,
            provider_id=provider_id,
            is_email_verified=is_email_verified,
            mobile_number=mobile_number,
        )

        self.session.add(user)
        await self.session.flush()

        logger.info(f"User created: {user.id}")
        return user

    # ─────────────────────────────────────────────
    # Profile Update
    # ─────────────────────────────────────────────
    async def update_profile(self, user: User, data: dict[str, Any]) -> User:
        for field, value in data.items():
            if value is not None:
                setattr(user, field, value)

        required_fields = [
            "enrollment_number",
            "chamber_number",
            "office_address_line1",
            "city",
            "district",
            "state",
            "mobile_number",
        ]

        if all(getattr(user, f) for f in required_fields):
            user.is_profile_complete = True

        await self.session.flush()

        logger.info(f"Profile updated: {user.id}")
        return user

    # ─────────────────────────────────────────────
    # Password
    # ─────────────────────────────────────────────
    async def update_password(self, user: User, hashed_password: str) -> User:
        user.hashed_password = hashed_password
        await self.session.flush()
        return user

    # ─────────────────────────────────────────────
    # Status
    # ─────────────────────────────────────────────
    async def set_active(self, user: User, is_active: bool) -> User:
        user.is_active = is_active
        await self.session.flush()
        return user

    # ─────────────────────────────────────────────
    # Munshi List
    # ─────────────────────────────────────────────
    async def list_munshis_for_lawyer(self, lawyer_id: uuid.UUID) -> list[User]:
        from app.features.cases.models import Case, CaseAccess

        result = await self.session.execute(
            select(User)
            .join(CaseAccess, CaseAccess.user_id == User.id)
            .join(Case, Case.id == CaseAccess.case_id)
            .where(
                Case.lawyer_id == lawyer_id,
                User.role == UserRole.munshi,
            )
            .distinct()
        )
        return list(result.scalars().all())
