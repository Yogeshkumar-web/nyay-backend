from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.features.context.models import CaseContext, CaseSummary
    from app.features.documents.models import Document
    from app.features.drafts.models import Draft
    from app.features.extraction.models import ExtractionResult
    from app.features.users.models import User


class CaseType(str, enum.Enum):
    writ_petition = "writ_petition"
    pil = "pil"
    criminal_revision = "criminal_revision"
    criminal_appeal = "criminal_appeal"
    bail_application = "bail_application"
    anticipatory_bail = "anticipatory_bail"
    quashing = "quashing"
    civil_revision = "civil_revision"
    first_appeal = "first_appeal"
    second_appeal = "second_appeal"
    contempt = "contempt"
    other = "other"


class BenchType(str, enum.Enum):
    single_bench = "single_bench"
    division_bench = "division_bench"
    full_bench = "full_bench"


class CaseStage(str, enum.Enum):
    filing = "filing"
    admission = "admission"
    notice = "notice"
    counter_affidavit = "counter_affidavit"
    rejoinder = "rejoinder"
    arguments = "arguments"
    judgment = "judgment"
    disposed = "disposed"
    transferred = "transferred"


class CaseStatus(str, enum.Enum):
    active = "active"
    disposed = "disposed"
    archived = "archived"


class CaseNumberType(str, enum.Enum):
    lower_court = "lower_court"
    high_court = "high_court"
    supreme_court = "supreme_court"
    connected_matter = "connected_matter"
    other = "other"


class PartyType(str, enum.Enum):
    petitioner = "petitioner"
    respondent = "respondent"
    intervener = "intervener"
    amicus = "amicus"
    witness = "witness"
    other = "other"


class Case(Base):
    __tablename__ = "cases"

    __table_args__ = (
        Index("ix_cases_lawyer_id", "lawyer_id"),
        Index("ix_cases_status", "status"),
        Index("ix_cases_next_hearing_date", "next_hearing_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lawyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    case_title: Mapped[str] = mapped_column(String(500), nullable=False)
    case_type: Mapped[CaseType] = mapped_column(
        SAEnum(CaseType, name="case_type"), nullable=False
    )
    bench_type: Mapped[BenchType] = mapped_column(
        SAEnum(BenchType, name="bench_type"),
        nullable=False,
        default=BenchType.single_bench,
        server_default=BenchType.single_bench.value,
    )
    stage: Mapped[CaseStage] = mapped_column(
        SAEnum(CaseStage, name="case_stage"),
        nullable=False,
        default=CaseStage.filing,
        server_default=CaseStage.filing.value,
    )
    status: Mapped[CaseStatus] = mapped_column(
        SAEnum(CaseStatus, name="case_status"),
        nullable=False,
        default=CaseStatus.active,
        server_default=CaseStatus.active.value,
    )

    court_number: Mapped[str | None] = mapped_column(String(20))
    petitioner_name: Mapped[str] = mapped_column(String(500), nullable=False)
    respondent_name: Mapped[str] = mapped_column(String(500), nullable=False)
    act_name: Mapped[str | None] = mapped_column(String(255))
    brief_facts: Mapped[str | None] = mapped_column(Text)
    filing_date: Mapped[date | None] = mapped_column(Date)
    next_hearing_date: Mapped[date | None] = mapped_column(Date)
    lower_court_decision_date: Mapped[date | None] = mapped_column(Date)
    bail_rejection_date: Mapped[date | None] = mapped_column(Date)
    limitation_expiry_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
    )

    lawyer: Mapped["User"] = relationship("User", back_populates="cases")
    case_numbers: Mapped[list["CaseNumber"]] = relationship(
        "CaseNumber",
        back_populates="case",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    sections: Mapped[list["CaseSection"]] = relationship(
        "CaseSection",
        back_populates="case",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CaseSection.added_at",
    )
    parties: Mapped[list["Party"]] = relationship(
        "Party",
        back_populates="case",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    accesses: Mapped[list["CaseAccess"]] = relationship(
        "CaseAccess", back_populates="case", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="case", cascade="all, delete-orphan"
    )
    extraction_results: Mapped[list["ExtractionResult"]] = relationship(
        "ExtractionResult", back_populates="case", cascade="all, delete-orphan"
    )
    drafts: Mapped[list["Draft"]] = relationship(
        "Draft", back_populates="case", cascade="all, delete-orphan"
    )
    context: Mapped["CaseContext | None"] = relationship(
        "CaseContext",
        back_populates="case",
        cascade="all, delete-orphan",
        uselist=False,
    )
    summary: Mapped["CaseSummary | None"] = relationship(
        "CaseSummary",
        back_populates="case",
        cascade="all, delete-orphan",
        uselist=False,
    )


class CaseNumber(Base):
    __tablename__ = "case_numbers"

    __table_args__ = (
        Index("ix_case_numbers_case_id", "case_id"),
        Index("ix_case_numbers_case_number", "case_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    number_type: Mapped[CaseNumberType] = mapped_column(
        SAEnum(CaseNumberType, name="case_number_type"), nullable=False
    )
    case_number: Mapped[str] = mapped_column(String(200), nullable=False)
    court_name: Mapped[str | None] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now()
    )

    case: Mapped["Case"] = relationship("Case", back_populates="case_numbers")


class CaseSection(Base):
    __tablename__ = "case_sections"

    __table_args__ = (Index("ix_case_sections_case_id", "case_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    section: Mapped[str] = mapped_column(String(200), nullable=False)
    act_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now()
    )
    removed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime())
    notes: Mapped[str | None] = mapped_column(String(500))

    case: Mapped["Case"] = relationship("Case", back_populates="sections")


class CaseAccess(Base):
    __tablename__ = "case_access"

    __table_args__ = (
        UniqueConstraint("case_id", "user_id", name="uq_case_user"),
        Index("ix_case_access_case_id", "case_id"),
        Index("ix_case_access_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    can_edit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now()
    )

    case: Mapped["Case"] = relationship("Case", back_populates="accesses")


class Party(Base):
    __tablename__ = "parties"

    __table_args__ = (Index("ix_parties_case_id", "case_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    party_type: Mapped[PartyType] = mapped_column(
        SAEnum(PartyType, name="party_type"), nullable=False
    )
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
    )

    case: Mapped["Case"] = relationship("Case", back_populates="parties")
