"""add generic document processing pipeline fields

Revision ID: d7e8f9a0b1c2
Revises: a8b9c0d1e2f3
Create Date: 2026-06-01
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE document_processing_route AS ENUM (
                'pending', 'scanned_ocr', 'digital_extract', 'hybrid_extract'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE document_processing_status AS ENUM (
                'pending', 'processing', 'completed', 'failed'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS processing_route document_processing_route
            NOT NULL DEFAULT 'pending',
        ADD COLUMN IF NOT EXISTS processing_status document_processing_status
            NOT NULL DEFAULT 'pending',
        ADD COLUMN IF NOT EXISTS processing_job_id VARCHAR(100),
        ADD COLUMN IF NOT EXISTS processing_error TEXT,
        ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMP,
        ADD COLUMN IF NOT EXISTS processing_completed_at TIMESTAMP,
        ADD COLUMN IF NOT EXISTS source_text TEXT,
        ADD COLUMN IF NOT EXISTS source_artifact JSONB,
        ADD COLUMN IF NOT EXISTS classification_details JSONB
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_documents_processing_status
        ON documents(processing_status, processing_route)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documents_processing_status")
    op.execute(
        """
        ALTER TABLE documents
        DROP COLUMN IF EXISTS classification_details,
        DROP COLUMN IF EXISTS source_artifact,
        DROP COLUMN IF EXISTS source_text,
        DROP COLUMN IF EXISTS processing_completed_at,
        DROP COLUMN IF EXISTS processing_started_at,
        DROP COLUMN IF EXISTS processing_error,
        DROP COLUMN IF EXISTS processing_job_id,
        DROP COLUMN IF EXISTS processing_status,
        DROP COLUMN IF EXISTS processing_route
        """
    )
    op.execute("DROP TYPE IF EXISTS document_processing_status")
    op.execute("DROP TYPE IF EXISTS document_processing_route")
