"""add canonical document structure to typed revisions

Revision ID: fa1b2c3d4e5f
Revises: f9a0b1c2d3e4
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op


revision: str = "fa1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_typed_revisions
        ADD COLUMN IF NOT EXISTS canonical_structure JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS canonical_schema_version INTEGER NOT NULL DEFAULT 1
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_typed_revisions
        DROP COLUMN IF EXISTS canonical_schema_version,
        DROP COLUMN IF EXISTS canonical_structure
        """
    )
