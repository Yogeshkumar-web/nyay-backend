import uuid
from datetime import datetime
from typing import List, Tuple, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from app.features.notifications.models import Notification, NotificationType


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ============================================================
    # CREATE (DEDUP SAFE)
    # ============================================================

    async def create(
        self,
        user_id: uuid.UUID,
        notification_type: NotificationType,
        title: str,
        message: Optional[str] = None,
        link: Optional[str] = None,
        dedup_key: Optional[str] = None,
        linked_entity_type: Optional[str] = None,
        linked_entity_id: Optional[uuid.UUID] = None,
    ) -> Notification:
        # ---- Deduplication check
        if dedup_key:
            stmt = select(Notification).where(
                Notification.user_id == user_id,
                Notification.dedup_key == dedup_key,
            )
            existing = (await self.db.execute(stmt)).scalar_one_or_none()
            if existing:
                return existing

        notification = Notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            message=message,
            link=link,
            dedup_key=dedup_key,
            linked_entity_type=linked_entity_type,
            linked_entity_id=linked_entity_id,
        )

        self.db.add(notification)
        await self.db.flush()  # ❗ no commit here

        return notification

    # ============================================================
    # GET (PAGINATED + OPTIMIZED)
    # ============================================================

    async def get_for_user(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[List[Notification], int]:
        offset = (page - 1) * limit

        # ---- Fetch notifications
        stmt = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        notifications = list(result.scalars())

        # ---- Unread count (fast)
        count_stmt = select(func.count()).where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        unread_count = (await self.db.execute(count_stmt)).scalar_one()

        return notifications, unread_count

    # ============================================================
    # MARK SINGLE READ (IDEMPOTENT)
    # ============================================================

    async def mark_read(
        self,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.is_read.is_(False),  # idempotent
            )
            .values(
                is_read=True,
                read_at=datetime.utcnow(),
            )
        )

        await self.db.execute(stmt)
        await self.db.commit()

    # ============================================================
    # MARK ALL READ (BULK + SAFE)
    # ============================================================

    async def mark_all_read(
        self,
        user_id: uuid.UUID,
    ) -> None:
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(
                is_read=True,
                read_at=datetime.utcnow(),
            )
        )

        await self.db.execute(stmt)
        await self.db.commit()
