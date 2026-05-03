import uuid
import enum
from datetime import datetime

from sqlalchemy import Boolean, BigInteger, ForeignKey, Integer, String, Text, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base



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


class Document(Base):
    __tablename__ = "documents"

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
    is_scanned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    extraction_result: Mapped["ExtractionResult | None"] = relationship(
    "ExtractionResult", back_populates="document", uselist=False
)
    typed_version: Mapped["TypedVersion | None"] = relationship(
    "TypedVersion", back_populates="document", uselist=False
)

    # Relationships
    case: Mapped["Case"] = relationship("Case", back_populates="documents")  # type: ignore[name-defined]
    uploaded_by_user: Mapped["User"] = relationship("User", foreign_keys=[uploaded_by])  # type: ignore[name-defined]