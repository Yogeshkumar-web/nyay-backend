"""fix_cause_listings_scraped_at_defaults

Add server-side defaults for scraped_at/created_at, fix the unique
constraint name, and ensure composite indexes exist.

Revision ID: 2d6a322274d9
Revises: 8d794d69aa89
Create Date: 2026-05-10 16:01:42.800043
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2d6a322274d9"
down_revision: Union[str, Sequence[str], None] = "8d794d69aa89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Server defaults for timestamps ────────────────────────────────────
    op.alter_column(
        "cause_listings",
        "scraped_at",
        server_default=sa.text("NOW()"),
        existing_type=sa.DateTime(),
        existing_nullable=True,
        nullable=False,
    )
    op.alter_column(
        "cause_listings",
        "created_at",
        server_default=sa.text("NOW()"),
        existing_type=sa.DateTime(),
        existing_nullable=True,
        nullable=False,
    )

    # ── 2. Rename old unique constraint (use raw SQL — handles "if exists") ───
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'idx_cause_listings_unique'
                  AND conrelid = 'cause_listings'::regclass
            ) THEN
                ALTER TABLE cause_listings
                    RENAME CONSTRAINT idx_cause_listings_unique
                    TO uq_cause_listing_row;
            END IF;
        END;
        $$;
    """
    )

    # Add uq_cause_listing_row if it still doesn't exist (fresh DB scenario)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_cause_listing_row'
                  AND conrelid = 'cause_listings'::regclass
            ) THEN
                ALTER TABLE cause_listings
                    ADD CONSTRAINT uq_cause_listing_row
                    UNIQUE (listing_date, court_number, serial_number);
            END IF;
        END;
        $$;
    """
    )

    # ── 3. Composite indexes (all idempotent via IF NOT EXISTS) ──────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listing_date_court "
        "ON cause_listings (listing_date, court_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listing_date_serial "
        "ON cause_listings (listing_date, serial_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listing_case_number "
        "ON cause_listings (case_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listing_advocate "
        "ON cause_listings (advocate_name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listing_matched_case "
        "ON cause_listings (matched_case_id)"
    )


def downgrade() -> None:
    op.alter_column(
        "cause_listings",
        "scraped_at",
        server_default=None,
        existing_type=sa.DateTime(),
        existing_nullable=False,
        nullable=True,
    )
    op.alter_column(
        "cause_listings",
        "created_at",
        server_default=None,
        existing_type=sa.DateTime(),
        existing_nullable=False,
        nullable=True,
    )
