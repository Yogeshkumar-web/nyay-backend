import uuid
import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Enum as SAEnum,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.features.cases.models import Case
    from app.features.extraction.models import ExtractionResult, TypedVersion
    from app.features.users.models import User


class DocumentType(str, enum.Enum):
    fir = "fir"
    chargesheet = "chargesheet"
    summon = "summon"
    bail_rejection_order = "bail_rejection_order"
    bail_order = "bail_order"
    affidavit = "affidavit"
    counter_affidavit = "counter_affidavit"
    rejoinder = "rejoinder"
    vakalatnama = "vakalatnama"
    judgment = "judgment"
    court_order = "court_order"
    other = "other"


class UploadStatus(str, enum.Enum):
    pending = "pending"
    uploading = "uploading"
    uploaded = "uploaded"
    failed = "failed"


class OcrStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    not_required = "not_required"


class ProcessingRoute(str, enum.Enum):
    pending = "pending"
    scanned_ocr = "scanned_ocr"
    digital_extract = "digital_extract"
    hybrid_extract = "hybrid_extract"


class ProcessingStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class DocReviewStatus(str, enum.Enum):
    pending = "pending"
    reviewed = "reviewed"
    pushed = "pushed"


class Document(Base):
    __tablename__ = "documents"

    __table_args__ = (
        Index("ix_documents_case_created", "case_id", "created_at"),
        Index("ix_documents_status", "upload_status", "ocr_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    r2_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    r2_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    document_type: Mapped[DocumentType] = mapped_column(
        SAEnum(DocumentType, name="document_type"),
        nullable=False,
        default=DocumentType.other,
    )

    display_name: Mapped[str | None] = mapped_column(String(500))

    upload_status: Mapped[UploadStatus] = mapped_column(
        SAEnum(UploadStatus, name="upload_status"),
        nullable=False,
        default=UploadStatus.pending,
    )

    ocr_status: Mapped[OcrStatus] = mapped_column(
        SAEnum(OcrStatus, name="ocr_status"),
        nullable=False,
        default=OcrStatus.pending,
    )

    ocr_raw_text: Mapped[str | None] = mapped_column(Text)
    ocr_language: Mapped[str | None] = mapped_column(String(10))
    page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_job_id: Mapped[str | None] = mapped_column(String(100))
    ocr_error: Mapped[str | None] = mapped_column(Text)
    ocr_provider: Mapped[str | None] = mapped_column(String(100))
    ocr_artifact: Mapped[dict | None] = mapped_column(JSONB)
    ocr_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    ocr_completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    is_scanned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    processing_route: Mapped[ProcessingRoute] = mapped_column(
        SAEnum(ProcessingRoute, name="document_processing_route"),
        nullable=False,
        default=ProcessingRoute.pending,
        server_default="pending",
    )
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        SAEnum(ProcessingStatus, name="document_processing_status"),
        nullable=False,
        default=ProcessingStatus.pending,
        server_default="pending",
    )
    processing_job_id: Mapped[str | None] = mapped_column(String(100))
    processing_error: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_text: Mapped[str | None] = mapped_column(Text)
    source_artifact: Mapped[dict | None] = mapped_column(JSONB)
    classification_details: Mapped[dict | None] = mapped_column(JSONB)

    reviewed_content: Mapped[str | None] = mapped_column(Text)

    review_status: Mapped[DocReviewStatus] = mapped_column(
        SAEnum(DocReviewStatus, name="doc_review_status"),
        nullable=False,
        default=DocReviewStatus.pending,
        server_default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(default=func.now())

    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(), onupdate=func.now()
    )

    # ✅ CORRECT RELATIONSHIPS
    extraction_result: Mapped["ExtractionResult | None"] = relationship(
        "ExtractionResult",
        back_populates="document",
        uselist=False,
        lazy="selectin",
    )

    typed_version: Mapped["TypedVersion | None"] = relationship(
        "TypedVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
        lazy="selectin",
    )

    case: Mapped["Case"] = relationship(
        "Case",
        back_populates="documents",
    )

    uploaded_by_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[uploaded_by],
    )
