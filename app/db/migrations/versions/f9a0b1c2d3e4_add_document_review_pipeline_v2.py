"""add document digitization and approval pipeline v2

Revision ID: f9a0b1c2d3e4
Revises: f8e9f0a1b2c3
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "f8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # PostgreSQL requires newly added enum values to be committed before use.
    with op.get_context().autocommit_block():
        for enum_name, values in {
            "document_processing_status": ["queued", "ready_for_review", "approved"],
            "document_processing_run_status": [
                "queued",
                "classifying_pages",
                "extracting_pages",
                "typing_pages",
                "approved",
            ],
            "document_page_status": [
                "inventoried",
                "classified",
                "extracting",
                "extracted",
                "typing",
                "typed",
                "blank",
            ],
            "doc_review_status": ["review_required", "approved"],
        }.items():
            for value in values:
                op.execute(
                    f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'"
                )

    for statement in (
        """
        DO $$ BEGIN
            CREATE TYPE document_page_classification AS ENUM (
                'digital', 'scanned', 'digital_low_quality', 'blank', 'failed'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """,
        """
        DO $$ BEGIN
            CREATE TYPE document_extraction_method AS ENUM (
                'embedded_text', 'vision_ocr', 'none'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """,
        """
        DO $$ BEGIN
            CREATE TYPE typed_revision_status AS ENUM (
                'draft', 'approved', 'superseded'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """,
    ):
        op.execute(statement)

    op.execute(
        """
        ALTER TABLE document_processing_runs
        ADD COLUMN IF NOT EXISTS pipeline_version VARCHAR(50) NOT NULL DEFAULT 'document_pipeline_v2',
        ADD COLUMN IF NOT EXISTS vision_provider_key VARCHAR(100),
        ADD COLUMN IF NOT EXISTS typing_provider_key VARCHAR(100),
        ADD COLUMN IF NOT EXISTS total_pages INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS completed_pages INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS failed_pages INTEGER NOT NULL DEFAULT 0
        """
    )
    op.execute(
        """
        ALTER TABLE document_pages
        ADD COLUMN IF NOT EXISTS classification document_page_classification,
        ADD COLUMN IF NOT EXISTS extraction_method document_extraction_method,
        ADD COLUMN IF NOT EXISTS embedded_text TEXT,
        ADD COLUMN IF NOT EXISTS extracted_text TEXT,
        ADD COLUMN IF NOT EXISTS typed_markdown TEXT,
        ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS language VARCHAR(20),
        ADD COLUMN IF NOT EXISTS provider_key VARCHAR(100),
        ADD COLUMN IF NOT EXISTS provider_version VARCHAR(100),
        ADD COLUMN IF NOT EXISTS provider_job_id VARCHAR(255),
        ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS classification_artifact JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS provider_artifact JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS content_hashes JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_typed_revisions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            processing_run_id UUID REFERENCES document_processing_runs(id) ON DELETE SET NULL,
            parent_revision_id UUID REFERENCES document_typed_revisions(id) ON DELETE SET NULL,
            revision_number INTEGER NOT NULL,
            lock_version INTEGER NOT NULL DEFAULT 1,
            content_markdown TEXT NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            status typed_revision_status NOT NULL DEFAULT 'draft',
            created_by UUID NOT NULL REFERENCES users(id),
            approved_by UUID REFERENCES users(id),
            approved_at TIMESTAMP WITHOUT TIME ZONE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_document_typed_revisions_document_number
            ON document_typed_revisions(document_id, revision_number)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_typed_revisions_status
            ON document_typed_revisions(status)
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS active_processing_run_id UUID,
        ADD COLUMN IF NOT EXISTS latest_typed_revision_id UUID,
        ADD COLUMN IF NOT EXISTS approved_typed_revision_id UUID
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        ADD CONSTRAINT fk_documents_active_processing_run
            FOREIGN KEY (active_processing_run_id) REFERENCES document_processing_runs(id) ON DELETE SET NULL,
        ADD CONSTRAINT fk_documents_latest_typed_revision
            FOREIGN KEY (latest_typed_revision_id) REFERENCES document_typed_revisions(id) ON DELETE SET NULL,
        ADD CONSTRAINT fk_documents_approved_typed_revision
            FOREIGN KEY (approved_typed_revision_id) REFERENCES document_typed_revisions(id) ON DELETE SET NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_domain_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            event_type VARCHAR(100) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            published_at TIMESTAMP WITHOUT TIME ZONE,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_domain_events_unpublished
            ON document_domain_events(published_at, occurred_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_domain_events_document
            ON document_domain_events(document_id, occurred_at)
        """
    )
    op.execute(
        """
        ALTER TABLE document_docx_exports
        ADD COLUMN IF NOT EXISTS approved_revision_id UUID
            REFERENCES document_typed_revisions(id) ON DELETE SET NULL
        """
    )

    op.execute(
        """
        WITH legacy AS (
            SELECT
                d.id AS document_id,
                d.uploaded_by AS created_by,
                COALESCE(tv.typed_content, d.reviewed_content, d.source_text, d.ocr_raw_text) AS content,
                CASE
                    WHEN tv.status::text = 'accepted' OR d.review_status::text = 'pushed'
                    THEN 'approved'::typed_revision_status
                    ELSE 'draft'::typed_revision_status
                END AS revision_status
            FROM documents d
            LEFT JOIN typed_versions tv ON tv.document_id = d.id
            WHERE COALESCE(tv.typed_content, d.reviewed_content, d.source_text, d.ocr_raw_text) IS NOT NULL
              AND BTRIM(COALESCE(tv.typed_content, d.reviewed_content, d.source_text, d.ocr_raw_text)) <> ''
        ), inserted AS (
            INSERT INTO document_typed_revisions (
                document_id, revision_number, content_markdown, content_hash,
                status, created_by, approved_by, approved_at
            )
            SELECT
                document_id,
                1,
                content,
                ENCODE(DIGEST(content, 'sha256'), 'hex'),
                revision_status,
                created_by,
                CASE WHEN revision_status = 'approved' THEN created_by ELSE NULL END,
                CASE WHEN revision_status = 'approved' THEN NOW() ELSE NULL END
            FROM legacy
            ON CONFLICT (document_id, revision_number) DO NOTHING
            RETURNING id, document_id, status
        )
        UPDATE documents d
        SET latest_typed_revision_id = r.id,
            approved_typed_revision_id = CASE WHEN r.status = 'approved' THEN r.id ELSE NULL END,
            review_status = CASE
                WHEN r.status = 'approved' THEN 'approved'::doc_review_status
                ELSE 'review_required'::doc_review_status
            END
        FROM document_typed_revisions r
        WHERE r.document_id = d.id AND r.revision_number = 1;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE document_docx_exports DROP COLUMN IF EXISTS approved_revision_id")
    op.execute("DROP TABLE IF EXISTS document_domain_events")
    op.execute(
        """
        ALTER TABLE documents
        DROP CONSTRAINT IF EXISTS fk_documents_approved_typed_revision,
        DROP CONSTRAINT IF EXISTS fk_documents_latest_typed_revision,
        DROP CONSTRAINT IF EXISTS fk_documents_active_processing_run,
        DROP COLUMN IF EXISTS approved_typed_revision_id,
        DROP COLUMN IF EXISTS latest_typed_revision_id,
        DROP COLUMN IF EXISTS active_processing_run_id
        """
    )
    op.execute("DROP TABLE IF EXISTS document_typed_revisions")
    op.execute(
        """
        ALTER TABLE document_pages
        DROP COLUMN IF EXISTS content_hashes,
        DROP COLUMN IF EXISTS provider_artifact,
        DROP COLUMN IF EXISTS classification_artifact,
        DROP COLUMN IF EXISTS warnings,
        DROP COLUMN IF EXISTS attempt_count,
        DROP COLUMN IF EXISTS provider_job_id,
        DROP COLUMN IF EXISTS provider_version,
        DROP COLUMN IF EXISTS provider_key,
        DROP COLUMN IF EXISTS language,
        DROP COLUMN IF EXISTS confidence,
        DROP COLUMN IF EXISTS typed_markdown,
        DROP COLUMN IF EXISTS extracted_text,
        DROP COLUMN IF EXISTS embedded_text,
        DROP COLUMN IF EXISTS extraction_method,
        DROP COLUMN IF EXISTS classification
        """
    )
    op.execute(
        """
        ALTER TABLE document_processing_runs
        DROP COLUMN IF EXISTS failed_pages,
        DROP COLUMN IF EXISTS completed_pages,
        DROP COLUMN IF EXISTS total_pages,
        DROP COLUMN IF EXISTS typing_provider_key,
        DROP COLUMN IF EXISTS vision_provider_key,
        DROP COLUMN IF EXISTS pipeline_version
        """
    )
    op.execute("DROP TYPE IF EXISTS typed_revision_status")
    op.execute("DROP TYPE IF EXISTS document_extraction_method")
    op.execute("DROP TYPE IF EXISTS document_page_classification")
