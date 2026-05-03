"""create extraction and typed versions tables

Revision ID: 0003
Revises: 0002
Create Date: 2025-04-15 10:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE extraction_status AS ENUM (
            'pending', 'processing', 'completed', 'failed'
        )
    """)

    op.execute("""
        CREATE TYPE review_status AS ENUM (
            'pending', 'accepted', 'rejected', 'edited'
        )
    """)

    op.execute("""
        CREATE TABLE extraction_results (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            case_id             UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            extracted_fields    JSONB NOT NULL DEFAULT '{}',
            raw_ai_response     TEXT,
            confidence_score    NUMERIC(4,3),
            extraction_status   extraction_status NOT NULL DEFAULT 'pending',
            review_status       review_status NOT NULL DEFAULT 'pending',
            reviewed_by         UUID REFERENCES users(id),
            reviewed_at         TIMESTAMP WITH TIME ZONE,
            user_edits          JSONB,
            created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_extraction_results_document_id ON extraction_results(document_id)")
    op.execute("CREATE INDEX idx_extraction_results_case_id ON extraction_results(case_id)")
    op.execute("CREATE INDEX idx_extraction_results_review ON extraction_results(review_status)")
    op.execute("CREATE INDEX idx_extraction_fields_gin ON extraction_results USING GIN(extracted_fields)")

    op.execute("""
        CREATE TABLE typed_versions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id     UUID NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
            typed_content   TEXT NOT NULL,
            raw_ai_response TEXT,
            status          review_status NOT NULL DEFAULT 'pending',
            reviewed_by     UUID REFERENCES users(id),
            reviewed_at     TIMESTAMP WITH TIME ZONE,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_typed_versions_document_id ON typed_versions(document_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS typed_versions")
    op.execute("DROP TABLE IF EXISTS extraction_results")
    op.execute("DROP TYPE IF EXISTS review_status")
    op.execute("DROP TYPE IF EXISTS extraction_status")