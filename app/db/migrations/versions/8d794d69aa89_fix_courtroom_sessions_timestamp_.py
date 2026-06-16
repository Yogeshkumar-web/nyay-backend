"""fix_courtroom_sessions_timestamp_defaults

Revision ID: 8d794d69aa89
Revises: b2c3d4e5f6a7
Create Date: 2026-05-10 15:29:00.431876

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8d794d69aa89"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add server-side defaults for timestamp and JSONB columns in courtroom_sessions."""
    op.alter_column(
        "courtroom_sessions",
        "created_at",
        server_default=sa.text("NOW()"),
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "courtroom_sessions",
        "updated_at",
        server_default=sa.text("NOW()"),
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "courtroom_sessions",
        "messages",
        server_default=sa.text("'[]'::jsonb"),
        existing_nullable=False,
    )
    op.alter_column(
        "courtroom_sessions",
        "weak_points_identified",
        server_default=sa.text("'[]'::jsonb"),
        existing_nullable=False,
    )
    op.alter_column(
        "courtroom_sessions",
        "turn_count",
        server_default=sa.text("0"),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "courtroom_sessions",
        "created_at",
        server_default=None,
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "courtroom_sessions",
        "updated_at",
        server_default=None,
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "courtroom_sessions", "messages", server_default=None, existing_nullable=False
    )
    op.alter_column(
        "courtroom_sessions",
        "weak_points_identified",
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "courtroom_sessions",
        "turn_count",
        server_default=None,
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
