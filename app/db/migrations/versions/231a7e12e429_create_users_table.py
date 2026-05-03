"""create users table

Revision ID: 231a7e12e429
Revises:
Create Date: 2026-04-17 23:09:06.559331
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "231a7e12e429"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE user_role AS ENUM ('lawyer', 'munshi', 'admin')")
    op.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            full_name VARCHAR(200) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            hashed_password VARCHAR(255) NOT NULL,
            role user_role NOT NULL DEFAULT 'lawyer',
            bar_enrollment_no VARCHAR(100),
            phone VARCHAR(20),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_login_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_users_email ON users(email)")
    op.execute("CREATE INDEX idx_users_role ON users(role)")

def downgrade() -> None:
    op.drop_index("idx_users_role", table_name="users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS user_role")