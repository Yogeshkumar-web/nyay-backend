import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.features.rag.vector import Vector768

if TYPE_CHECKING:
    from app.features.cases.models import Case
    from app.features.documents.models import Document
    from app.features.drafts.models import Draft
    from app.features.users.models import User


class RagSourceKind(str, enum.Enum):
    case_upload = "case_upload"
    kb_draft = "kb_draft"
    judgment = "judgment"
    manual_note = "manual_note"


class RagProcessingStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class RagSection(str, enum.Enum):
    facts = "facts"
    grounds = "grounds"
    prayer = "prayer"
    citation = "citation"
    judgment_ratio = "judgment_ratio"
    other = "other"


class CitationVerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"


class RagDocument(Base):
    __tablename__ = "rag_documents"

    __table_args__ = (
        UniqueConstraint("lawyer_id", "file_hash", name="uq_rag_document_lawyer_hash"),
        Index("ix_rag_documents_lawyer", "lawyer_id"),
        Index("ix_rag_documents_case", "case_id"),
        Index("ix_rag_documents_lawyer_draft", "lawyer_id", "draft_type"),
        Index("ix_rag_documents_status", "processing_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lawyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
    )
    draft_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="anticipatory_bail"
    )
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500))
    source_kind: Mapped[RagSourceKind] = mapped_column(
        String(50), nullable=False, default=RagSourceKind.kb_draft.value
    )
    processing_status: Mapped[RagProcessingStatus] = mapped_column(
        String(50), nullable=False, default=RagProcessingStatus.pending.value
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(), onupdate=func.now()
    )

    lawyer: Mapped["User"] = relationship("User")
    case: Mapped["Case | None"] = relationship("Case")
    source_document: Mapped["Document | None"] = relationship("Document")
    chunks: Mapped[list["RagChunk"]] = relationship(
        "RagChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    __table_args__ = (
        Index("ix_rag_chunks_document", "document_id"),
        Index("ix_rag_chunks_lawyer", "lawyer_id"),
        Index("ix_rag_chunks_case", "case_id"),
        Index("ix_rag_chunks_lawyer_draft", "lawyer_id", "draft_type"),
        Index("ix_rag_chunks_lawyer_section", "lawyer_id", "section"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    lawyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
    )
    draft_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="anticipatory_bail"
    )
    section: Mapped[RagSection] = mapped_column(
        String(50), nullable=False, default=RagSection.other.value
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    embedding: Mapped[list[float] | None] = mapped_column(Vector768)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    token_count: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    document: Mapped["RagDocument"] = relationship(
        "RagDocument",
        back_populates="chunks",
    )
    lawyer: Mapped["User"] = relationship("User")
    case: Mapped["Case | None"] = relationship("Case")


class RagQueryLog(Base):
    __tablename__ = "rag_query_logs"

    __table_args__ = (
        Index("ix_rag_query_logs_lawyer_created", "lawyer_id", "created_at"),
        Index("ix_rag_query_logs_case_created", "case_id", "created_at"),
        Index("ix_rag_query_logs_generated", "generated"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lawyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chunks_used: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True))
    )
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    lawyer: Mapped["User"] = relationship("User")
    case: Mapped["Case | None"] = relationship("Case")


class RagVerifiedCitation(Base):
    __tablename__ = "rag_verified_citations"

    __table_args__ = (
        UniqueConstraint(
            "lawyer_id",
            "normalized_key",
            name="uq_rag_verified_citation_lawyer_key",
        ),
        Index("ix_rag_verified_citations_lawyer", "lawyer_id"),
        Index("ix_rag_verified_citations_case_name", "case_name"),
        Index("ix_rag_verified_citations_citation", "citation"),
        Index("ix_rag_verified_citations_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lawyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    normalized_key: Mapped[str] = mapped_column(String(500), nullable=False)
    case_name: Mapped[str] = mapped_column(String(500), nullable=False)
    citation: Mapped[str | None] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(Integer)
    court: Mapped[str | None] = mapped_column(String(255))
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_chunks.id", ondelete="SET NULL"),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(), onupdate=func.now()
    )

    lawyer: Mapped["User"] = relationship("User")
    source_chunk: Mapped["RagChunk | None"] = relationship("RagChunk")


class DraftSectionSource(Base):
    __tablename__ = "draft_section_sources"

    __table_args__ = (
        Index("ix_draft_section_sources_draft", "draft_id"),
        Index("ix_draft_section_sources_lawyer", "lawyer_id"),
        Index("ix_draft_section_sources_status", "verification_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    lawyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(String(100), nullable=False)
    source_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        default=list,
    )
    cited_judgments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    verification_status: Mapped[CitationVerificationStatus] = mapped_column(
        String(50),
        nullable=False,
        default=CitationVerificationStatus.pending.value,
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    draft: Mapped["Draft"] = relationship("Draft")
    lawyer: Mapped["User"] = relationship("User")
