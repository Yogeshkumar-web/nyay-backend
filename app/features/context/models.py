import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.features.cases.models import Case


# ─────────────────────────────
# Case Context (LLM Memory)
# ─────────────────────────────
class CaseContext(Base):
    __tablename__ = "case_contexts"

    __table_args__ = (
        UniqueConstraint("case_id", name="uq_case_context"),
        Index("ix_context_case_updated", "case_id", "last_updated_at"),
        Index("ix_context_token_estimate", "token_estimate"),
        # 🔥 critical for JSON queries
        Index("ix_context_json_gin", "context_json", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # 🔥 Structured context (documents, facts, etc.)
    context_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # 🔥 Replace ARRAY with JSON (safer)
    pushed_documents: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    # format:
    # {
    #   "doc_id": {
    #       "type": "...",
    #       "added_at": "...",
    #       "version": 1
    #   }
    # }

    # 🔥 Token budgeting
    token_estimate: Mapped[Optional[int]] = mapped_column(Integer)

    # ─────────────────────────────
    # Metadata
    # ─────────────────────────────
    version: Mapped[int] = mapped_column(default=1)

    last_updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        onupdate=func.now(),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(default=func.now())

    case: Mapped["Case"] = relationship(
        "Case",
        back_populates="context",
        lazy="selectin",
    )


# ─────────────────────────────
# Context Version History (CRITICAL)
# ─────────────────────────────
class CaseContextVersion(Base):
    __tablename__ = "case_context_versions"

    __table_args__ = (
        Index("ix_context_version_case", "case_id"),
        Index("ix_context_version_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(nullable=False)

    context_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    token_estimate: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(default=func.now())


# ─────────────────────────────
# Case Summary (AI Generated)
# ─────────────────────────────
class CaseSummary(Base):
    __tablename__ = "case_summaries"

    __table_args__ = (
        UniqueConstraint("case_id", name="uq_case_summary"),
        Index("ix_summary_case", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    summary_text: Mapped[str] = mapped_column(Text, nullable=False)

    # 🔥 ensures summary matches context
    context_snapshot_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    token_count: Mapped[Optional[int]] = mapped_column(Integer)

    generated_at: Mapped[datetime] = mapped_column(default=func.now())
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    case: Mapped["Case"] = relationship(
        "Case",
        back_populates="summary",
        lazy="selectin",
    )
