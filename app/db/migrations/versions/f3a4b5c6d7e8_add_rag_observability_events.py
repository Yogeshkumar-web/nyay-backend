"""add rag observability events

Revision ID: f3a4b5c6d7e8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_observability_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lawyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
            draft_id UUID REFERENCES drafts(id) ON DELETE CASCADE,
            query_log_id UUID REFERENCES rag_query_logs(id) ON DELETE SET NULL,
            event_type VARCHAR(100) NOT NULL,
            section VARCHAR(100),
            severity VARCHAR(20) NOT NULL DEFAULT 'info',
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_observability_lawyer_created
        ON rag_observability_events(lawyer_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_observability_case_created
        ON rag_observability_events(case_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_observability_draft
        ON rag_observability_events(draft_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_observability_query_log
        ON rag_observability_events(query_log_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_observability_event_type
        ON rag_observability_events(event_type)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_observability_event_type")
    op.execute("DROP INDEX IF EXISTS ix_rag_observability_query_log")
    op.execute("DROP INDEX IF EXISTS ix_rag_observability_draft")
    op.execute("DROP INDEX IF EXISTS ix_rag_observability_case_created")
    op.execute("DROP INDEX IF EXISTS ix_rag_observability_lawyer_created")
    op.execute("DROP TABLE IF EXISTS rag_observability_events")
