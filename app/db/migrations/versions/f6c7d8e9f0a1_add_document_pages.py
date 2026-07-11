"""add document pages

Revision ID: f6c7d8e9f0a1
Revises: f5b6c7d8e9f0
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f6c7d8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "f5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'document_page_status'
            ) THEN
                CREATE TYPE document_page_status AS ENUM (
                    'pending',
                    'split',
                    'ocr_running',
                    'ocr_completed',
                    'failed'
                );
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_pages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            processing_run_id UUID NOT NULL REFERENCES document_processing_runs(id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL,
            source_filename VARCHAR(255) NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            checksum_sha256 VARCHAR(64) NOT NULL,
            size_bytes BIGINT NOT NULL,
            status document_page_status NOT NULL DEFAULT 'split',
            ocr_text TEXT,
            ocr_artifact JSONB,
            error TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            UNIQUE (processing_run_id, page_number)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_pages_document_number
        ON document_pages(document_id, page_number)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_pages_run_number
        ON document_pages(processing_run_id, page_number)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_pages_status
        ON document_pages(status)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_pages_status")
    op.execute("DROP INDEX IF EXISTS ix_document_pages_run_number")
    op.execute("DROP INDEX IF EXISTS ix_document_pages_document_number")
    op.execute("DROP TABLE IF EXISTS document_pages")
    op.execute("DROP TYPE IF EXISTS document_page_status")
