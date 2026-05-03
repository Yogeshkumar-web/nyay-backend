import uuid
import enum
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String, Text, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class ExtractionStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    edited = "edited"


class ExtractionResult(Base):
    __tablename__ = "extraction_results"
    

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    extracted_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_ai_response: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        SAEnum(ExtractionStatus, name="extraction_status"),
        nullable=False,
        default=ExtractionStatus.pending,
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        SAEnum(ReviewStatus, name="review_status"),
        nullable=False,
        default=ReviewStatus.pending,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reviewed_at: Mapped[datetime | None]
    user_edits: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    document: Mapped["Document"] = relationship("Document", back_populates="extraction_result")  # type: ignore[name-defined]
    case: Mapped["Case"] = relationship("Case", back_populates="extraction_results")  # type: ignore[name-defined]
    reviewer: Mapped["User | None"] = relationship("User", foreign_keys=[reviewed_by])  # type: ignore[name-defined]


class TypedVersion(Base):
    __tablename__ = "typed_versions"
  

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    typed_content: Mapped[str] = mapped_column(Text, nullable=False)
    raw_ai_response: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReviewStatus] = mapped_column(
        SAEnum(ReviewStatus, name="review_status", create_type=False),
        nullable=False,
        default=ReviewStatus.pending,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    document: Mapped["Document"] = relationship("Document", back_populates="typed_version")  # type: ignore[name-defined]
    reviewer: Mapped["User | None"] = relationship("User", foreign_keys=[reviewed_by])  # type: ignore[name-defined]