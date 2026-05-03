import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.users.models import User
from app.features.users.models import UserRole


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def list_munshis_for_lawyer(self, lawyer_id: uuid.UUID) -> list[User]:
        """
        Returns all munshi accounts created by this lawyer.
        In v1: a munshi is linked to a lawyer via created_by_lawyer_id.
        For now, we query all munshi users who have access to any of this lawyer's cases.
        """
        from app.features.cases.models import CaseAccess, Case
        result = await self.session.execute(
            select(User)
            .join(CaseAccess, CaseAccess.user_id == User.id)
            .join(Case, Case.id == CaseAccess.case_id)
            .where(Case.lawyer_id == lawyer_id, User.role == UserRole.munshi)
            .distinct()
        )
        return list(result.scalars().all())

    async def create(
        self,
        full_name: str,
        email: str,
        password_hash: str,
        role: UserRole = UserRole.munshi,
        phone: Optional[str] = None,
    ) -> User:
        user = User(
            full_name=full_name,
            email=email,
            password_hash=password_hash,
            role=role,
            phone=phone,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def set_active(self, user: User, is_active: bool) -> User:
        user.is_active = is_active
        await self.session.flush()
        return user