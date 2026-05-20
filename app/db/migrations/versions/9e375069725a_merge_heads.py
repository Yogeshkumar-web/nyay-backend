"""merge heads

Revision ID: 9e375069725a
Revises: 0005, a3f7e1209b4c
Create Date: 2026-05-04 22:54:04.641174

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "9e375069725a"
down_revision: Union[str, Sequence[str], None] = ("0005", "a3f7e1209b4c")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
