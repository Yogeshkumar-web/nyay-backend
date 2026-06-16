"""create documents table

Revision ID: 0002
Revises: 0001
Create Date: 2025-04-15 09:30:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE document_type AS ENUM (
            'fir', 'chargesheet', 'summon', 'bail_rejection_order', 'bail_order',
            'affidavit', 'counter_affidavit', 'rejoinder', 'vakalatnama',
            'judgment', 'court_order', 'other'
        )
    """)
    op.execute("CREATE TYPE upload_status AS ENUM ('pending', 'uploading', 'uploaded', 'failed')")
    op.execute("""
        CREATE TYPE ocr_status AS ENUM (
            'pending', 'processing', 'completed', 'failed', 'not_required'
        )
    """)

    op.execute("""
        CREATE TABLE documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            uploaded_by UUID NOT NULL REFERENCES users(id),
            original_filename VARCHAR(500) NOT NULL,
            r2_key VARCHAR(1000) NOT NULL,
            r2_bucket VARCHAR(255) NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            file_size_bytes BIGINT NOT NULL,
            document_type document_type NOT NULL DEFAULT 'other',
            display_name VARCHAR(500),
            upload_status upload_status NOT NULL DEFAULT 'pending',
            ocr_status ocr_status NOT NULL DEFAULT 'pending',
            ocr_raw_text TEXT,
            ocr_language VARCHAR(10),
            page_count INTEGER,
            is_scanned BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_documents_case_id ON documents(case_id)")
    op.execute("CREATE INDEX idx_documents_uploaded_by ON documents(uploaded_by)")
    op.execute("""
        CREATE INDEX idx_documents_ocr_status ON documents(ocr_status)
        WHERE ocr_status IN ('pending', 'processing')
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TYPE IF EXISTS ocr_status")
    op.execute("DROP TYPE IF EXISTS upload_status")
    op.execute("DROP TYPE IF EXISTS document_type")