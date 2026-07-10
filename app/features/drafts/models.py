import uuid
import enum
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    BigInteger,
    ForeignKey,
    Integer,
    String,
    Text,
    Enum as SAEnum,
    Index,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.features.cases.models import Case
from app.features.users.models import User

# ============================================================
# ENUMS
# ============================================================


class DraftType(str, enum.Enum):
    bail_application = "bail_application"
    anticipatory_bail = "anticipatory_bail"
    writ_petition = "writ_petition"
    pil = "pil"
    criminal_revision = "criminal_revision"
    quashing_petition = "quashing_petition"
    affidavit = "affidavit"
    counter_affidavit = "counter_affidavit"
    rejoinder = "rejoinder"
    synopsis = "synopsis"
    other = "other"


class DraftStatus(str, enum.Enum):
    generating = "generating"
    ready = "ready"
    editing = "editing"
    final = "final"
    archived = "archived"


class ExportFormat(str, enum.Enum):
    pdf = "pdf"
    docx = "docx"


# ============================================================
# DRAFT MODEL
# ============================================================


class Draft(Base):
    __tablename__ = "drafts"

    # ------------------------
    # Core Fields
    # ------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    draft_type: Mapped[DraftType] = mapped_column(
        SAEnum(DraftType, name="draft_type"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)

    content: Mapped[Optional[str]] = mapped_column(Text)

    # ------------------------
    # Versioning & State
    # ------------------------
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    status: Mapped[DraftStatus] = mapped_column(
        SAEnum(DraftStatus, name="draft_status"),
        nullable=False,
        default=DraftStatus.generating,
        index=True,
    )

    generated_by_ai: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    ai_prompt_used: Mapped[Optional[str]] = mapped_column(Text)

    parent_draft_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drafts.id", ondelete="SET NULL"),
        index=True,
    )

    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    reviewed_at: Mapped[Optional[datetime]]

    final_accepted_at: Mapped[Optional[datetime]]

    exported_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    exported_at: Mapped[Optional[datetime]]

    # ------------------------
    # Concurrency Control (IMPORTANT)
    # ------------------------
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    # ------------------------
    # Timestamps
    # ------------------------
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # ------------------------
    # Relationships
    # ------------------------
    case: Mapped["Case"] = relationship(
        "Case",
        back_populates="drafts",
    )

    author: Mapped["User"] = relationship(
        "User",
        foreign_keys=[created_by],
    )

    reviewer: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )

    last_exporter: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[exported_by],
    )

    exports: Mapped[List["DraftExport"]] = relationship(
        "DraftExport",
        back_populates="draft",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    parent: Mapped[Optional["Draft"]] = relationship(
        "Draft",
        remote_side=[id],
        backref="children",
    )

    # ------------------------
    # Constraints
    # ------------------------
    __table_args__ = (
        # Version must always be >= 1
        CheckConstraint("version >= 1", name="check_draft_version_positive"),
        # Revision must always be >= 1
        CheckConstraint("revision >= 1", name="check_draft_revision_positive"),
        # Optimized query index
        Index(
            "idx_drafts_case_status",
            "case_id",
            "status",
        ),
        Index(
            "idx_drafts_case_type",
            "case_id",
            "draft_type",
        ),
    )


# ============================================================
# DRAFT EXPORT MODEL
# ============================================================


class DraftExport(Base):
    __tablename__ = "draft_exports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    exported_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    export_format: Mapped[ExportFormat] = mapped_column(
        SAEnum(ExportFormat, name="export_format"),
        nullable=False,
    )

    r2_key: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )

    r2_bucket: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)

    expires_at: Mapped[Optional[datetime]]

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # ------------------------
    # Relationships
    # ------------------------
    draft: Mapped["Draft"] = relationship(
        "Draft",
        back_populates="exports",
        passive_deletes=True,
    )

    exporter: Mapped["User"] = relationship(
        "User",
        foreign_keys=[exported_by],
    )

    # ------------------------
    # Indexes
    # ------------------------
    __table_args__ = (
        Index(
            "idx_draft_exports_draft_format",
            "draft_id",
            "export_format",
        ),
    )
