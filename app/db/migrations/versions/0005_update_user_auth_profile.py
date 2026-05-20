"""update users: add auth_provider + lawyer profile fields

Revision ID: 0005
Revises: 231a7e12e429
Create Date: 2026-05-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "5f82a115686d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New ENUM types ─────────────────────────────────────────────────────
    op.execute("CREATE TYPE auth_provider AS ENUM ('email', 'google')")
    op.execute("CREATE TYPE designation AS ENUM ('advocate', 'aor', 'senior_advocate')")

    # ── Auth provider columns ──────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column(
            "auth_provider",
            sa.Enum("email", "google", name="auth_provider"),
            nullable=False,
            server_default="email",
        ),
    )
    op.add_column(
        "users",
        sa.Column("provider_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # ── Make hashed_password nullable (Google users won't have one) ────────
    op.alter_column(
        "users", "hashed_password", existing_type=sa.String(255), nullable=True
    )

    # ── Lawyer profile columns ─────────────────────────────────────────────
    op.add_column(
        "users", sa.Column("enrollment_number", sa.String(100), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "designation",
            sa.Enum("advocate", "aor", "senior_advocate", name="designation"),
            nullable=True,
        ),
    )
    op.add_column("users", sa.Column("chamber_number", sa.String(100), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "court_name",
            sa.String(300),
            nullable=True,
            server_default="High Court of Judicature at Allahabad",
        ),
    )

    # ── Address columns ────────────────────────────────────────────────────
    op.add_column(
        "users", sa.Column("office_address_line1", sa.String(300), nullable=True)
    )
    op.add_column(
        "users", sa.Column("office_address_line2", sa.String(300), nullable=True)
    )
    op.add_column("users", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("district", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("state", sa.String(100), nullable=True))

    # ── Contact columns ────────────────────────────────────────────────────
    op.add_column("users", sa.Column("mobile_number", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("alternate_mobile", sa.String(20), nullable=True))

    # ── Preference columns ─────────────────────────────────────────────────
    op.add_column("users", sa.Column("default_district", sa.String(100), nullable=True))

    # ── Profile completion flag ────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column(
            "is_profile_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # ── Drop old columns that are replaced by new ones ─────────────────────
    op.drop_column("users", "bar_enrollment_no")
    op.drop_column("users", "phone")


def downgrade() -> None:
    # Re-add old columns
    op.add_column(
        "users", sa.Column("bar_enrollment_no", sa.String(100), nullable=True)
    )
    op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))

    # Drop new columns
    op.drop_column("users", "is_profile_complete")
    op.drop_column("users", "default_district")
    op.drop_column("users", "alternate_mobile")
    op.drop_column("users", "mobile_number")
    op.drop_column("users", "state")
    op.drop_column("users", "district")
    op.drop_column("users", "city")
    op.drop_column("users", "office_address_line2")
    op.drop_column("users", "office_address_line1")
    op.drop_column("users", "court_name")
    op.drop_column("users", "chamber_number")
    op.drop_column("users", "designation")
    op.drop_column("users", "enrollment_number")
    op.alter_column(
        "users", "hashed_password", existing_type=sa.String(255), nullable=False
    )
    op.drop_column("users", "is_email_verified")
    op.drop_column("users", "provider_id")
    op.drop_column("users", "auth_provider")

    op.execute("DROP TYPE IF EXISTS designation")
    op.execute("DROP TYPE IF EXISTS auth_provider")
