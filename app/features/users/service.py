import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.features.users.repository import UserRepository
from app.features.users.schemas import LawyerProfileUpdate
from app.features.users.models import User

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)

    async def get_profile(self, user_id: uuid.UUID) -> User:
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise NotFoundError("User not found")
        return user

    async def update_profile(
        self, user_id: uuid.UUID, payload: LawyerProfileUpdate
    ) -> User:
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise NotFoundError("User not found")

        data = payload.model_dump(exclude_unset=True)

        updated = await self.repo.update_profile(user, data)

        logger.info(f"User profile updated: {user_id}")
        return updated
