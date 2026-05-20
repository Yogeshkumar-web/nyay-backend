"""add agent_notes to typed_versions

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-14

Adds a dedicated `agent_notes` column to `typed_versions` to store the
pipe-delimited audit trail produced by the Typing Agent (processing_notes).
Previously this was temporarily shoehorned into `raw_ai_response`; that column
is kept for backward-compat but is no longer written to by the typing task.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "2d6a322274d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE typed_versions
        ADD COLUMN IF NOT EXISTS agent_notes TEXT
    """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE typed_versions DROP COLUMN IF EXISTS agent_notes")
