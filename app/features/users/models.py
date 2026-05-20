import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum as SAEnum, String, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.features.cases.models import Case


class UserRole(str, enum.Enum):
    lawyer = "lawyer"
    munshi = "munshi"
    admin = "admin"


class AuthProvider(str, enum.Enum):
    email = "email"
    google = "google"


class Designation(str, enum.Enum):
    advocate = "advocate"
    aor = "aor"
    senior_advocate = "senior_advocate"


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        Index("ix_users_email_lower", "email"),
        Index("ix_users_role", "role"),
    )

    # ─────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ─────────────────────────────────────────────
    # Auth
    # ─────────────────────────────────────────────
    auth_provider: Mapped[AuthProvider] = mapped_column(
        SAEnum(AuthProvider, name="auth_provider"),
        nullable=False,
        default=AuthProvider.email,
    )

    provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ─────────────────────────────────────────────
    # Role
    # ─────────────────────────────────────────────
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.lawyer,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ─────────────────────────────────────────────
    # Lawyer Profile
    # ─────────────────────────────────────────────
    enrollment_number: Mapped[str | None] = mapped_column(String(100))
    designation: Mapped[Designation | None] = mapped_column(
        SAEnum(Designation, name="designation")
    )

    chamber_number: Mapped[str | None] = mapped_column(String(100))

    court_name: Mapped[str | None] = mapped_column(
        String(300),
        default="High Court of Judicature at Allahabad",
    )

    # ─────────────────────────────────────────────
    # Address
    # ─────────────────────────────────────────────
    office_address_line1: Mapped[str | None] = mapped_column(String(300))
    office_address_line2: Mapped[str | None] = mapped_column(String(300))

    city: Mapped[str | None] = mapped_column(String(100))
    district: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))

    # ─────────────────────────────────────────────
    # Contact
    # ─────────────────────────────────────────────
    mobile_number: Mapped[str | None] = mapped_column(String(20))
    alternate_mobile: Mapped[str | None] = mapped_column(String(20))

    # ─────────────────────────────────────────────
    # Preferences
    # ─────────────────────────────────────────────
    default_district: Mapped[str | None] = mapped_column(String(100))

    # ─────────────────────────────────────────────
    # Flags
    # ─────────────────────────────────────────────
    is_profile_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ─────────────────────────────────────────────
    # Timestamps
    # ─────────────────────────────────────────────
    last_login_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        onupdate=func.now(),
    )

    # ─────────────────────────────────────────────
    # Relations
    # ─────────────────────────────────────────────
    cases: Mapped[list["Case"]] = relationship(
        "Case",
        back_populates="lawyer",
        foreign_keys="Case.lawyer_id",
    )
