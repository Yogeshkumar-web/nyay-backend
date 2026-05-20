"""add doc review fields

Revision ID: a3f7e1209b4c
Revises: 5f82a115686d
Create Date: 2026-05-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3f7e1209b4c"
down_revision: Union[str, None] = "5f82a115686d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the new enum type
    op.execute(
        "CREATE TYPE doc_review_status AS ENUM ('pending', 'reviewed', 'pushed')"
    )

    # Add reviewed_content column (nullable text)
    op.add_column("documents", sa.Column("reviewed_content", sa.Text(), nullable=True))

    # Add review_status column with default 'pending'
    op.add_column(
        "documents",
        sa.Column(
            "review_status",
            sa.Enum(
                "pending",
                "reviewed",
                "pushed",
                name="doc_review_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "review_status")
    op.drop_column("documents", "reviewed_content")
    op.execute("DROP TYPE doc_review_status")
