"""add document DOCX export audit table

Revision ID: f8e9f0a1b2c3
Revises: f7d8e9f0a1b2
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "f7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_docx_exports (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            exported_by UUID NOT NULL REFERENCES users(id),
            filename VARCHAR(255) NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            size_bytes BIGINT NOT NULL,
            checksum_sha256 VARCHAR(64) NOT NULL,
            generator VARCHAR(100) NOT NULL,
            export_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_docx_exports_document_created
        ON document_docx_exports(document_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_docx_exports_exported_by
        ON document_docx_exports(exported_by)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_docx_exports_exported_by")
    op.execute("DROP INDEX IF EXISTS ix_document_docx_exports_document_created")
    op.execute("DROP TABLE IF EXISTS document_docx_exports")
