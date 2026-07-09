"""add private lawyer RAG tables

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
Create Date: 2026-07-09
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lawyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
            source_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
            draft_type VARCHAR(100) NOT NULL DEFAULT 'anticipatory_bail',
            file_hash VARCHAR(128) NOT NULL,
            original_filename VARCHAR(500),
            source_kind VARCHAR(50) NOT NULL DEFAULT 'kb_draft',
            processing_status VARCHAR(50) NOT NULL DEFAULT 'pending',
            processing_error TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_rag_document_lawyer_hash UNIQUE (lawyer_id, file_hash)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_documents_lawyer ON rag_documents(lawyer_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_documents_case ON rag_documents(case_id)"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_documents_lawyer_draft
        ON rag_documents(lawyer_id, draft_type)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_documents_status
        ON rag_documents(processing_status)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_chunks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
            lawyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
            draft_type VARCHAR(100) NOT NULL DEFAULT 'anticipatory_bail',
            section VARCHAR(50) NOT NULL DEFAULT 'other',
            chunk_text TEXT NOT NULL,
            summary TEXT,
            keywords TEXT[],
            embedding vector(768),
            confidence_score DOUBLE PRECISION,
            token_count INTEGER,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_chunks_document ON rag_chunks(document_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_chunks_lawyer ON rag_chunks(lawyer_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rag_chunks_case ON rag_chunks(case_id)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_chunks_lawyer_draft
        ON rag_chunks(lawyer_id, draft_type)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_chunks_lawyer_section
        ON rag_chunks(lawyer_id, section)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_chunks_embedding_ivfflat
        ON rag_chunks USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        WHERE embedding IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_query_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lawyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
            query_text TEXT NOT NULL,
            filters JSONB NOT NULL DEFAULT '{}'::jsonb,
            confidence_score DOUBLE PRECISION,
            generated BOOLEAN NOT NULL DEFAULT FALSE,
            chunks_used UUID[],
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_query_logs_lawyer_created
        ON rag_query_logs(lawyer_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_query_logs_case_created
        ON rag_query_logs(case_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_query_logs_generated
        ON rag_query_logs(generated)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_verified_citations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lawyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            normalized_key VARCHAR(500) NOT NULL,
            case_name VARCHAR(500) NOT NULL,
            citation VARCHAR(255),
            year INTEGER,
            court VARCHAR(255),
            source_chunk_id UUID REFERENCES rag_chunks(id) ON DELETE SET NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_rag_verified_citation_lawyer_key
            UNIQUE (lawyer_id, normalized_key)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_verified_citations_lawyer
        ON rag_verified_citations(lawyer_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_verified_citations_case_name
        ON rag_verified_citations(case_name)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_verified_citations_citation
        ON rag_verified_citations(citation)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rag_verified_citations_active
        ON rag_verified_citations(is_active)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS draft_section_sources (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            draft_id UUID NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
            lawyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            section VARCHAR(100) NOT NULL,
            source_chunk_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
            cited_judgments JSONB NOT NULL DEFAULT '[]'::jsonb,
            verification_status VARCHAR(50) NOT NULL DEFAULT 'pending',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_draft_section_sources_draft
        ON draft_section_sources(draft_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_draft_section_sources_lawyer
        ON draft_section_sources(lawyer_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_draft_section_sources_status
        ON draft_section_sources(verification_status)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_draft_section_sources_status")
    op.execute("DROP INDEX IF EXISTS ix_draft_section_sources_lawyer")
    op.execute("DROP INDEX IF EXISTS ix_draft_section_sources_draft")
    op.execute("DROP TABLE IF EXISTS draft_section_sources")

    op.execute("DROP INDEX IF EXISTS ix_rag_verified_citations_active")
    op.execute("DROP INDEX IF EXISTS ix_rag_verified_citations_citation")
    op.execute("DROP INDEX IF EXISTS ix_rag_verified_citations_case_name")
    op.execute("DROP INDEX IF EXISTS ix_rag_verified_citations_lawyer")
    op.execute("DROP TABLE IF EXISTS rag_verified_citations")

    op.execute("DROP INDEX IF EXISTS ix_rag_query_logs_generated")
    op.execute("DROP INDEX IF EXISTS ix_rag_query_logs_case_created")
    op.execute("DROP INDEX IF EXISTS ix_rag_query_logs_lawyer_created")
    op.execute("DROP TABLE IF EXISTS rag_query_logs")

    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_embedding_ivfflat")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_lawyer_section")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_lawyer_draft")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_case")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_lawyer")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_document")
    op.execute("DROP TABLE IF EXISTS rag_chunks")

    op.execute("DROP INDEX IF EXISTS ix_rag_documents_status")
    op.execute("DROP INDEX IF EXISTS ix_rag_documents_lawyer_draft")
    op.execute("DROP INDEX IF EXISTS ix_rag_documents_case")
    op.execute("DROP INDEX IF EXISTS ix_rag_documents_lawyer")
    op.execute("DROP TABLE IF EXISTS rag_documents")
