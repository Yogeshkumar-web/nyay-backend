"""add notifications fields

Revision ID: c1d2e3f4a5b6
Revises: 9e375069725a
Create Date: 2026-05-06 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "9e375069725a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("read_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("linked_entity_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("linked_entity_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("dedup_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        op.f("idx_notifications_dedup"),
        "notifications",
        ["user_id", "dedup_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("idx_notifications_dedup"), table_name="notifications")
    op.drop_column("notifications", "dedup_key")
    op.drop_column("notifications", "linked_entity_id")
    op.drop_column("notifications", "linked_entity_type")
    op.drop_column("notifications", "read_at")
