from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.features.rag.embedding import HashEmbeddingProvider
from app.features.rag.retrieval import (
    RagRetrievalService,
    extract_legal_keywords,
)


def test_extract_legal_keywords_for_indian_sections():
    assert extract_legal_keywords("Need bail under Section 438 CrPC and 420 IPC") == [
        "438",
        "438 CRPC",
        "CRPC",
        "420",
        "420 IPC",
        "IPC",
    ]


@pytest.mark.asyncio
async def test_retrieval_uses_lawyer_boundary_and_keyword_boost():
    lawyer_id = uuid.uuid4()
    chunk = _chunk(
        lawyer_id=lawyer_id,
        text="Grounds under Section 438 CrPC: custodial interrogation is not required.",
        section="grounds",
    )
    repository = _FakeRetrievalRepository(
        vector_matches=[],
        keyword_matches=[chunk],
    )

    result = await RagRetrievalService(
        repository,
        HashEmbeddingProvider(),
    ).retrieve(
        lawyer_id=lawyer_id,
        query_text="Need grounds for 438 CrPC anticipatory bail.",
        section="grounds",
    )

    assert result.should_generate is True
    assert result.chunks[0].chunk.id == chunk.id
    assert result.chunks[0].score >= 0.72
    assert result.chunks[0].match_reasons == ["legal_keyword"]
    assert result.chunks[0].chunk.corpus_scope == "lawyer_private"
    assert repository.seen_lawyer_ids == [lawyer_id, lawyer_id, lawyer_id]
    assert repository.logs[0]["generated"] is True
    assert repository.logs[0]["filters"]["corpus_scopes"] == [
        "global_base",
        "lawyer_private",
    ]


@pytest.mark.asyncio
async def test_retrieval_includes_global_base_chunks_for_new_lawyer():
    lawyer_id = uuid.uuid4()
    global_chunk = _chunk(
        lawyer_id=None,
        text="Global grounds under Section 482 BNSS: applicant may cooperate.",
        section="grounds",
        corpus_scope="global_base",
    )
    repository = _FakeRetrievalRepository(
        vector_matches=[],
        keyword_matches=[global_chunk],
    )

    result = await RagRetrievalService(
        repository,
        HashEmbeddingProvider(),
    ).retrieve(
        lawyer_id=lawyer_id,
        query_text="Need grounds for 482 BNSS anticipatory bail.",
        section="grounds",
    )

    assert result.should_generate is True
    assert result.chunks[0].chunk.lawyer_id is None
    assert result.chunks[0].chunk.corpus_scope == "global_base"


@pytest.mark.asyncio
async def test_low_confidence_retrieval_returns_structured_refusal():
    repository = _FakeRetrievalRepository(vector_matches=[], keyword_matches=[])

    result = await RagRetrievalService(
        repository,
        HashEmbeddingProvider(),
    ).retrieve(
        lawyer_id=uuid.uuid4(),
        query_text="Unrelated query with no support.",
    )

    assert result.should_generate is False
    assert result.confidence_score == 0.0
    assert result.refusal_reason
    assert result.chunks == []
    assert repository.logs[0]["generated"] is False


@pytest.mark.asyncio
async def test_retrieval_uses_cached_embedding_when_available():
    cached_embedding = [0.1] * 768
    cache = _FakeEmbeddingCache(cached_embedding)
    provider = _FailingEmbeddingProvider()
    repository = _FakeRetrievalRepository(vector_matches=[], keyword_matches=[])

    await RagRetrievalService(
        repository,
        provider,
        embedding_cache=cache,
    ).retrieve(
        lawyer_id=uuid.uuid4(),
        query_text="Need 438 CrPC grounds.",
    )

    assert cache.get_calls == ["Need 438 CrPC grounds."]
    assert cache.set_calls == []


def _chunk(
    *,
    lawyer_id: uuid.UUID | None,
    text: str,
    section: str = "grounds",
    corpus_scope: str = "lawyer_private",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        lawyer_id=lawyer_id,
        case_id=None,
        draft_type="anticipatory_bail",
        corpus_scope=corpus_scope,
        section=section,
        chunk_text=text,
        summary=None,
        keywords=None,
        confidence_score=None,
        token_count=len(text.split()),
        metadata_={},
        created_at=datetime.now(UTC),
    )


class _FakeRetrievalRepository:
    def __init__(self, *, vector_matches, keyword_matches):
        self.vector_matches = vector_matches
        self.keyword_matches = keyword_matches
        self.logs = []
        self.seen_lawyer_ids = []

    async def search_chunks_by_vector(self, *, lawyer_id, **kwargs):
        self.seen_lawyer_ids.append(lawyer_id)
        return self.vector_matches

    async def search_chunks_by_keywords(self, *, lawyer_id, **kwargs):
        self.seen_lawyer_ids.append(lawyer_id)
        return self.keyword_matches

    async def log_query(self, *, lawyer_id, **kwargs):
        self.seen_lawyer_ids.append(lawyer_id)
        self.logs.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())


class _FakeEmbeddingCache:
    def __init__(self, embedding):
        self.embedding = embedding
        self.get_calls = []
        self.set_calls = []

    async def get(self, text):
        self.get_calls.append(text)
        return self.embedding

    async def set(self, text, embedding):
        self.set_calls.append((text, embedding))


class _FailingEmbeddingProvider:
    async def embed_texts(self, texts):
        raise AssertionError("Embedding provider should not be called on cache hit.")
