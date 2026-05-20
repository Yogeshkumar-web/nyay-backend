"""add formatted_content to extraction_results

Revision ID: a1b2c3d4e5f6
Revises: f7c9d2e1a034
Create Date: 2026-05-09

"""

from typing import Sequence, Union

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f7c9d2e1a034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE extraction_results "
        "ADD COLUMN IF NOT EXISTS formatted_content TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE extraction_results " "DROP COLUMN IF EXISTS formatted_content"
    )
