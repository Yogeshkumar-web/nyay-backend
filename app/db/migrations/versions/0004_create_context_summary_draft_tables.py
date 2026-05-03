"""create context summary and draft tables

Revision ID: 0004
Revises: 0003
Create Date: 2025-04-15 11:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # case_contexts
    op.execute("""
        CREATE TABLE case_contexts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id             UUID NOT NULL UNIQUE REFERENCES cases(id) ON DELETE CASCADE,
            context_json        JSONB NOT NULL DEFAULT '{}',
            pushed_document_ids UUID[] NOT NULL DEFAULT '{}',
            token_estimate      INTEGER,
            last_updated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_case_contexts_case_id ON case_contexts(case_id)")

    # case_summaries
    op.execute("""
        CREATE TABLE case_summaries (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id               UUID NOT NULL UNIQUE REFERENCES cases(id) ON DELETE CASCADE,
            summary_text          TEXT NOT NULL,
            context_snapshot_hash VARCHAR(64) NOT NULL,
            generated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            created_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_case_summaries_case_id ON case_summaries(case_id)")

    # draft enums
    op.execute("""
        CREATE TYPE draft_type AS ENUM (
            'bail_application', 'anticipatory_bail', 'writ_petition', 'pil',
            'criminal_revision', 'quashing_petition', 'affidavit',
            'counter_affidavit', 'rejoinder', 'synopsis', 'other'
        )
    """)
    op.execute("""
        CREATE TYPE draft_status AS ENUM (
            'generating', 'ready', 'editing', 'final', 'archived'
        )
    """)

    # drafts
    op.execute("""
        CREATE TABLE drafts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id         UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            created_by      UUID NOT NULL REFERENCES users(id),
            draft_type      draft_type NOT NULL,
            title           VARCHAR(500) NOT NULL,
            content         TEXT,
            version         INTEGER NOT NULL DEFAULT 1,
            status          draft_status NOT NULL DEFAULT 'generating',
            generated_by_ai BOOLEAN NOT NULL DEFAULT TRUE,
            ai_prompt_used  TEXT,
            parent_draft_id UUID REFERENCES drafts(id),
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_drafts_case_id ON drafts(case_id)")
    op.execute("CREATE INDEX idx_drafts_created_by ON drafts(created_by)")
    op.execute("CREATE INDEX idx_drafts_status ON drafts(status)")

    # draft_exports
    op.execute("""
        CREATE TYPE export_format AS ENUM ('pdf', 'docx')
    """)
    op.execute("""
        CREATE TABLE draft_exports (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            draft_id        UUID NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
            exported_by     UUID NOT NULL REFERENCES users(id),
            export_format   export_format NOT NULL,
            r2_key          VARCHAR(1000) NOT NULL,
            r2_bucket       VARCHAR(255) NOT NULL,
            file_size_bytes BIGINT,
            expires_at      TIMESTAMP WITH TIME ZONE,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_draft_exports_draft_id ON draft_exports(draft_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS draft_exports")
    op.execute("DROP TABLE IF EXISTS drafts")
    op.execute("DROP TABLE IF EXISTS case_summaries")
    op.execute("DROP TABLE IF EXISTS case_contexts")
    op.execute("DROP TYPE IF EXISTS export_format")
    op.execute("DROP TYPE IF EXISTS draft_status")
    op.execute("DROP TYPE IF EXISTS draft_type")