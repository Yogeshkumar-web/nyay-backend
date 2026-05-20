"""add token_count to case_summaries and revision to drafts

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-10
"""

from typing import Sequence, Union
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # case_summaries.token_count — was in model but never migrated
    op.execute(
        """
        ALTER TABLE case_summaries
        ADD COLUMN IF NOT EXISTS token_count INTEGER
    """
    )

    # drafts.revision — was in model but never migrated
    # Default 1 for all existing rows, then set NOT NULL
    op.execute(
        """
        ALTER TABLE drafts
        ADD COLUMN IF NOT EXISTS revision INTEGER
    """
    )
    op.execute(
        """
        UPDATE drafts SET revision = 1 WHERE revision IS NULL
    """
    )
    op.execute(
        """
        ALTER TABLE drafts
        ALTER COLUMN revision SET NOT NULL,
        ALTER COLUMN revision SET DEFAULT 1
    """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS revision")
    op.execute("ALTER TABLE case_summaries DROP COLUMN IF EXISTS token_count")
