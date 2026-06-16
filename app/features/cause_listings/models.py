import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class CauseListing(Base):
    __tablename__ = "cause_listings"

    __table_args__ = (
        # Uniqueness — one row per (date, court, serial)
        UniqueConstraint(
            "listing_date",
            "court_number",
            "serial_number",
            name="uq_cause_listing_row",
        ),
        # Core query indexes
        Index("ix_listing_date_court", "listing_date", "court_number"),
        Index("ix_listing_case_number", "case_number"),
        Index("ix_listing_advocate", "advocate_name"),
        Index("ix_listing_matched_case", "matched_case_id"),
        Index("ix_listing_date_serial", "listing_date", "serial_number"),
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Listing Info ──────────────────────────────────────────────────────────
    listing_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    court_number: Mapped[str] = mapped_column(String(20), nullable=False)

    serial_number: Mapped[Optional[int]] = mapped_column(Integer)

    # ── Case Info (raw from cause list) ───────────────────────────────────────
    case_number: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    case_title: Mapped[Optional[str]] = mapped_column(String(1000))
    petitioner: Mapped[Optional[str]] = mapped_column(String(500))
    respondent: Mapped[Optional[str]] = mapped_column(String(500))
    advocate_name: Mapped[Optional[str]] = mapped_column(String(500), index=True)
    case_type_raw: Mapped[Optional[str]] = mapped_column(String(200))
    remarks: Mapped[Optional[str]] = mapped_column(String(500))

    # Cap raw HTML blob — 2 KB is enough for debugging
    raw_row_text: Mapped[Optional[str]] = mapped_column(Text)

    # ── Matching ──────────────────────────────────────────────────────────────
    matched_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="SET NULL"),
        index=True,
    )

    matched_case: Mapped[Optional["Case"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Case",
        lazy="selectin",  # prevents N+1 queries
    )

    # ── Metadata ──────────────────────────────────────────────────────────────
    scraped_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=datetime.utcnow,  # Python-side; updated in service on re-scrape
        server_default=func.now(),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
