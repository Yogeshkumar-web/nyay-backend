import uuid

import pytest
from pydantic import ValidationError

from app.features.rag.models import (
    DraftSectionSource,
    RagChunk,
    RagDocument,
    RagQueryLog,
    RagVerifiedCitation,
)
from app.features.rag.schemas import RagChunkCreate, RetrievalResult


def _column_names(model) -> set[str]:
    return {column.name for column in model.__table__.columns}


def _index_names(model) -> set[str]:
    return {index.name for index in model.__table__.indexes}


def test_rag_tables_use_lawyer_case_isolation_without_tenant_columns():
    for model in (RagDocument, RagChunk, RagQueryLog, RagVerifiedCitation):
        columns = _column_names(model)
        assert "lawyer_id" in columns
        assert "tenant_id" not in columns

    assert "case_id" in _column_names(RagDocument)
    assert "case_id" in _column_names(RagChunk)
    assert "case_id" in _column_names(RagQueryLog)


def test_rag_chunk_embedding_is_vector_768():
    embedding_column = RagChunk.__table__.columns["embedding"]

    assert embedding_column.type.get_col_spec() == "vector(768)"


def test_rag_hard_filter_indexes_exist():
    assert "ix_rag_documents_lawyer" in _index_names(RagDocument)
    assert "ix_rag_documents_lawyer_draft" in _index_names(RagDocument)
    assert "ix_rag_chunks_lawyer" in _index_names(RagChunk)
    assert "ix_rag_chunks_lawyer_draft" in _index_names(RagChunk)
    assert "ix_rag_chunks_lawyer_section" in _index_names(RagChunk)
    assert "ix_rag_query_logs_lawyer_created" in _index_names(RagQueryLog)
    assert "ix_draft_section_sources_lawyer" in _index_names(DraftSectionSource)


def test_rag_chunk_schema_requires_768_dimension_embedding():
    base = {
        "document_id": uuid.uuid4(),
        "lawyer_id": uuid.uuid4(),
        "section": "facts",
        "chunk_text": "Applicant has no criminal history.",
    }

    valid = RagChunkCreate(**base, embedding=[0.1] * 768)
    assert len(valid.embedding or []) == 768

    with pytest.raises(ValidationError):
        RagChunkCreate(**base, embedding=[0.1] * 767)


def test_retrieval_result_generation_threshold():
    result = RetrievalResult(chunks=[], confidence_score=0.99)
    assert not result.should_generate()

    low_confidence = RetrievalResult(chunks=[], confidence_score=0.64)
    assert not low_confidence.should_generate()
