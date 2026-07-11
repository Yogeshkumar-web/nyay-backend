"""add document processing runs

Revision ID: f5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'document_processing_run_status'
            ) THEN
                CREATE TYPE document_processing_run_status AS ENUM (
                    'uploaded',
                    'splitting_pages',
                    'ocr_running',
                    'stitching_pages',
                    'structured_extraction',
                    'ready_for_review',
                    'reviewed',
                    'docx_ready',
                    'failed'
                );
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_processing_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            started_by UUID NOT NULL REFERENCES users(id),
            job_id VARCHAR(100) NOT NULL UNIQUE,
            status document_processing_run_status NOT NULL DEFAULT 'ocr_running',
            error TEXT,
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            started_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            completed_at TIMESTAMP WITHOUT TIME ZONE,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_processing_runs_document_created
        ON document_processing_runs(document_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_processing_runs_job_id
        ON document_processing_runs(job_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_processing_runs_status
        ON document_processing_runs(status)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_processing_runs_status")
    op.execute("DROP INDEX IF EXISTS ix_document_processing_runs_job_id")
    op.execute("DROP INDEX IF EXISTS ix_document_processing_runs_document_created")
    op.execute("DROP TABLE IF EXISTS document_processing_runs")
    op.execute("DROP TYPE IF EXISTS document_processing_run_status")
