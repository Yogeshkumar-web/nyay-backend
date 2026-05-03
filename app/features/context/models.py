import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class CaseContext(Base):
    __tablename__ = "case_contexts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    context_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    pushed_document_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    token_estimate: Mapped[Optional[int]] = mapped_column(Integer)
    last_updated_at: Mapped[datetime] = mapped_column(default=func.now())
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    case: Mapped["Case"] = relationship("Case", back_populates="context")  # type: ignore[name-defined]


class CaseSummary(Base):
    __tablename__ = "case_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(default=func.now())
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    case: Mapped["Case"] = relationship("Case", back_populates="summary")  # type: ignore[name-defined]