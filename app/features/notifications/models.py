import uuid
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    Enum as SAEnum,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

# ============================================================
# ENUM
# ============================================================


class NotificationType(str, enum.Enum):
    case_created = "case_created"
    extraction_complete = "extraction_complete"
    draft_ready = "draft_ready"
    export_ready = "export_ready"
    cause_list_match = "cause_list_match"
    info = "info"


# ============================================================
# MODEL
# ============================================================


class Notification(Base):
    __tablename__ = "notifications"

    # ------------------------------------------------------------
    # CORE
    # ------------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    notification_type: Mapped[NotificationType] = mapped_column(
        SAEnum(NotificationType, name="notification_type"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    message: Mapped[Optional[str]] = mapped_column(Text)

    # ------------------------------------------------------------
    # STATE
    # ------------------------------------------------------------
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    # ------------------------------------------------------------
    # LINKING (IMPORTANT)
    # ------------------------------------------------------------
    link: Mapped[Optional[str]] = mapped_column(String(500))
    linked_entity_type: Mapped[Optional[str]] = mapped_column(String(50))
    linked_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))

    # ------------------------------------------------------------
    # DEDUPLICATION KEY
    # ------------------------------------------------------------
    dedup_key: Mapped[Optional[str]] = mapped_column(
        String(255),
        index=True,
        comment="Used to prevent duplicate notifications (e.g. draft_ready:{draft_id})",
    )

    # ------------------------------------------------------------
    # TIMESTAMPS
    # ------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )

    # ------------------------------------------------------------
    # RELATIONSHIP
    # ------------------------------------------------------------
    user = relationship("User")

    # ------------------------------------------------------------
    # INDEXES
    # ------------------------------------------------------------
    __table_args__ = (
        # Fast unread queries
        Index(
            "idx_notifications_user_unread",
            "user_id",
            "is_read",
        ),
        # Recent notifications
        Index(
            "idx_notifications_user_created",
            "user_id",
            "created_at",
        ),
        # Deduplication
        Index(
            "idx_notifications_dedup",
            "user_id",
            "dedup_key",
        ),
    )
