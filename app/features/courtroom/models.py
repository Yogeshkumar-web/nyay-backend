import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
import enum

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Enum,
    Index,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

# ============================================================
# ENUMS
# ============================================================


class SessionType(str, enum.Enum):
    opposing_counsel = "opposing_counsel"
    judge = "judge"


class SessionStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    archived = "archived"


# ============================================================
# MODEL
# ============================================================


class CourtroomSession(Base):
    __tablename__ = "courtroom_sessions"

    # ------------------------------------------------------------
    # CORE
    # ------------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    lawyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    session_type: Mapped[SessionType] = mapped_column(
        Enum(SessionType, name="sessiontype"),
        nullable=False,
        index=True,
    )

    title: Mapped[Optional[str]] = mapped_column(String(300))

    # ------------------------------------------------------------
    # CONVERSATION DATA
    # ------------------------------------------------------------
    # Structure:
    # [
    #   { role: "lawyer" | "ai", content: str, timestamp: ISO string }
    # ]
    messages: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,  # safe because SQLAlchemy handles JSONB defaults per row
    )

    # Structure:
    # [
    #   { point: str, suggestion: str, identified_at: ISO string }
    # ]
    weak_points_identified: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    # ------------------------------------------------------------
    # STATE
    # ------------------------------------------------------------
    turn_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="sessionstatus"),
        nullable=False,
        default=SessionStatus.active,
        index=True,
    )

    # ------------------------------------------------------------
    # TIMESTAMPS
    # ------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    # ------------------------------------------------------------
    # RELATIONSHIPS
    # ------------------------------------------------------------
    case = relationship("Case", backref="courtroom_sessions")
    lawyer = relationship("User", backref="courtroom_sessions")

    # ------------------------------------------------------------
    # CONSTRAINTS & INDEXES
    # ------------------------------------------------------------
    __table_args__ = (
        # Prevent negative turns
        CheckConstraint("turn_count >= 0", name="check_turn_count_positive"),
        # Fast lookup: active sessions per case
        Index(
            "idx_courtroom_case_status",
            "case_id",
            "status",
        ),
        # Fast lookup: user sessions
        Index(
            "idx_courtroom_lawyer_created",
            "lawyer_id",
            "created_at",
        ),
    )
