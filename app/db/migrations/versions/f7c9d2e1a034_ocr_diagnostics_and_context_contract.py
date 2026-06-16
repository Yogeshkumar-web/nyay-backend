"""add OCR diagnostics and normalize context push metadata

Revision ID: f7c9d2e1a034
Revises: e5f6a7b8c9d0
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f7c9d2e1a034"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_job_id VARCHAR(100)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_error TEXT")
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_provider VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_started_at TIMESTAMP"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_completed_at TIMESTAMP"
    )

    op.execute(
        """
        ALTER TABLE case_contexts
        ADD COLUMN IF NOT EXISTS pushed_documents JSONB NOT NULL DEFAULT '{}'::jsonb
    """
    )
    op.execute(
        """
        ALTER TABLE case_contexts
        ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1
    """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'case_contexts'
                  AND column_name = 'pushed_document_ids'
            ) THEN
                UPDATE case_contexts
                SET pushed_documents = COALESCE(
                    (
                        SELECT jsonb_object_agg(
                            pushed_id.id::text,
                            jsonb_build_object(
                                'added_at',
                                COALESCE(case_contexts.last_updated_at, case_contexts.created_at, NOW())::text,
                                'source',
                                'legacy_pushed_document_ids'
                            )
                        )
                        FROM unnest(case_contexts.pushed_document_ids) AS pushed_id(id)
                    ),
                    '{}'::jsonb
                )
                WHERE pushed_documents = '{}'::jsonb
                  AND cardinality(pushed_document_ids) > 0;
            END IF;
        END $$;
    """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_context_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            context_snapshot JSONB NOT NULL,
            token_estimate INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_context_version_case
        ON case_context_versions(case_id)
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_context_version_created
        ON case_context_versions(created_at)
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_context_case_updated
        ON case_contexts(case_id, last_updated_at)
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_context_token_estimate
        ON case_contexts(token_estimate)
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_context_json_gin
        ON case_contexts USING gin(context_json)
    """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_context_json_gin")
    op.execute("DROP INDEX IF EXISTS ix_context_token_estimate")
    op.execute("DROP INDEX IF EXISTS ix_context_case_updated")
    op.execute("DROP INDEX IF EXISTS ix_context_version_created")
    op.execute("DROP INDEX IF EXISTS ix_context_version_case")
    op.execute("DROP TABLE IF EXISTS case_context_versions")
    op.execute("ALTER TABLE case_contexts DROP COLUMN IF EXISTS version")
    op.execute("ALTER TABLE case_contexts DROP COLUMN IF EXISTS pushed_documents")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_completed_at")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_started_at")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_provider")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_error")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_job_id")
