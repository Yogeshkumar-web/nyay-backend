"""add draft review export metadata

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE drafts
        ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(id),
        ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS final_accepted_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS exported_by UUID REFERENCES users(id),
        ADD COLUMN IF NOT EXISTS exported_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_drafts_reviewed_by
        ON drafts(reviewed_by)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_drafts_exported_by
        ON drafts(exported_by)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_drafts_exported_by")
    op.execute("DROP INDEX IF EXISTS ix_drafts_reviewed_by")
    op.execute(
        """
        ALTER TABLE drafts
        DROP COLUMN IF EXISTS exported_at,
        DROP COLUMN IF EXISTS exported_by,
        DROP COLUMN IF EXISTS final_accepted_at,
        DROP COLUMN IF EXISTS reviewed_at,
        DROP COLUMN IF EXISTS reviewed_by
        """
    )
