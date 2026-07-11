"""add global base RAG scope

Revision ID: f4a5b6c7d8e9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE rag_documents
        ADD COLUMN IF NOT EXISTS corpus_scope VARCHAR(50) NOT NULL DEFAULT 'lawyer_private'
        """
    )
    op.execute(
        """
        ALTER TABLE rag_chunks
        ADD COLUMN IF NOT EXISTS corpus_scope VARCHAR(50) NOT NULL DEFAULT 'lawyer_private'
        """
    )
    op.execute(
        """
        ALTER TABLE rag_verified_citations
        ADD COLUMN IF NOT EXISTS corpus_scope VARCHAR(50) NOT NULL DEFAULT 'lawyer_private'
        """
    )

    op.execute("ALTER TABLE rag_documents ALTER COLUMN lawyer_id DROP NOT NULL")
    op.execute("ALTER TABLE rag_chunks ALTER COLUMN lawyer_id DROP NOT NULL")
    op.execute("ALTER TABLE rag_verified_citations ALTER COLUMN lawyer_id DROP NOT NULL")

    op.execute(
        """
        ALTER TABLE rag_documents
        DROP CONSTRAINT IF EXISTS uq_rag_document_lawyer_hash
        """
    )
    op.execute(
        """
        ALTER TABLE rag_verified_citations
        DROP CONSTRAINT IF EXISTS uq_rag_verified_citation_lawyer_key
        """
    )
    op.execute(
        """
        ALTER TABLE rag_documents
        ADD CONSTRAINT uq_rag_document_lawyer_hash
        UNIQUE (lawyer_id, file_hash, corpus_scope)
        """
    )
    op.execute(
        """
        ALTER TABLE rag_verified_citations
        ADD CONSTRAINT uq_rag_verified_citation_lawyer_key
        UNIQUE (lawyer_id, normalized_key, corpus_scope)
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_document_global_hash
        ON rag_documents(file_hash)
        WHERE corpus_scope = 'global_base'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_verified_citation_global_key
        ON rag_verified_citations(normalized_key)
        WHERE corpus_scope = 'global_base'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_documents_scope ON rag_documents(corpus_scope)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rag_chunks_scope ON rag_chunks(corpus_scope)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_verified_citations_scope
        ON rag_verified_citations(corpus_scope)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_verified_citations_scope")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_scope")
    op.execute("DROP INDEX IF EXISTS ix_rag_documents_scope")
    op.execute("DROP INDEX IF EXISTS uq_rag_verified_citation_global_key")
    op.execute("DROP INDEX IF EXISTS uq_rag_document_global_hash")

    op.execute(
        """
        ALTER TABLE rag_documents
        DROP CONSTRAINT IF EXISTS uq_rag_document_lawyer_hash
        """
    )
    op.execute(
        """
        ALTER TABLE rag_verified_citations
        DROP CONSTRAINT IF EXISTS uq_rag_verified_citation_lawyer_key
        """
    )
    op.execute(
        """
        ALTER TABLE rag_documents
        ADD CONSTRAINT uq_rag_document_lawyer_hash UNIQUE (lawyer_id, file_hash)
        """
    )
    op.execute(
        """
        ALTER TABLE rag_verified_citations
        ADD CONSTRAINT uq_rag_verified_citation_lawyer_key
        UNIQUE (lawyer_id, normalized_key)
        """
    )

    op.execute("DELETE FROM rag_chunks WHERE corpus_scope = 'global_base'")
    op.execute("DELETE FROM rag_documents WHERE corpus_scope = 'global_base'")
    op.execute("DELETE FROM rag_verified_citations WHERE corpus_scope = 'global_base'")

    op.execute("ALTER TABLE rag_documents ALTER COLUMN lawyer_id SET NOT NULL")
    op.execute("ALTER TABLE rag_chunks ALTER COLUMN lawyer_id SET NOT NULL")
    op.execute("ALTER TABLE rag_verified_citations ALTER COLUMN lawyer_id SET NOT NULL")

    op.execute("ALTER TABLE rag_verified_citations DROP COLUMN IF EXISTS corpus_scope")
    op.execute("ALTER TABLE rag_chunks DROP COLUMN IF EXISTS corpus_scope")
    op.execute("ALTER TABLE rag_documents DROP COLUMN IF EXISTS corpus_scope")
