"""remove split page R2 storage columns

Revision ID: f7d8e9f0a1b2
Revises: f6c7d8e9f0a1
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "f6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_pages DROP COLUMN IF EXISTS page_r2_key")
    op.execute("ALTER TABLE document_pages DROP COLUMN IF EXISTS r2_bucket")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE document_pages ADD COLUMN IF NOT EXISTS page_r2_key VARCHAR(1000)"
    )
    op.execute(
        "ALTER TABLE document_pages ADD COLUMN IF NOT EXISTS r2_bucket VARCHAR(255)"
    )
