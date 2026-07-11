from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import redis.asyncio as redis

from app.core.config import settings
from app.features.rag.embedding import (
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingProvider,
    validate_embedding,
)
from app.features.rag.models import RagChunk
from app.features.rag.repository import RagRepository
from app.features.rag.schemas import (
    RagObservabilityEventCreate,
    RagChunkResponse,
    RagRetrievedChunk,
    RagRetrievalResponse,
)


LEGAL_KEYWORD_RE = re.compile(
    r"\b(?:section|sec\.?|u/s)?\s*(\d+[A-Za-z]?)\s*(ipc|bns|crpc|bnss|ndps|pocso)\b",
    re.I,
)


@dataclass
class CandidateScore:
    chunk: RagChunk
    score: float
    match_reasons: set[str] = field(default_factory=set)


class Reranker(Protocol):
    def rerank(self, query_text: str, candidates: list[CandidateScore]) -> list[CandidateScore]: ...


class ScoreReranker:
    def rerank(
        self,
        query_text: str,
        candidates: list[CandidateScore],
    ) -> list[CandidateScore]:
        return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


class RedisEmbeddingCache:
    def __init__(
        self,
        *,
        redis_url: str = settings.REDIS_URL,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        ttl_seconds: int = 60 * 60 * 24 * 7,
    ):
        self.client = redis.from_url(redis_url)
        self.model_name = model_name
        self.ttl_seconds = ttl_seconds

    async def get(self, text: str) -> list[float] | None:
        try:
            payload = await self.client.get(self._key(text))
        except Exception:
            return None
        if not payload:
            return None
        try:
            return validate_embedding(json.loads(payload))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    async def set(self, text: str, embedding: list[float]) -> None:
        try:
            await self.client.set(
                self._key(text),
                json.dumps(validate_embedding(embedding)),
                ex=self.ttl_seconds,
            )
        except Exception:
            return

    def _key(self, text: str) -> str:
        digest = hashlib.sha256(f"{self.model_name}:{text}".encode("utf-8")).hexdigest()
        return f"rag:embedding:{digest}"


class RagRetrievalService:
    def __init__(
        self,
        repository: RagRepository,
        embedding_provider: EmbeddingProvider,
        *,
        embedding_cache: RedisEmbeddingCache | None = None,
        reranker: Reranker | None = None,
    ):
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.embedding_cache = embedding_cache
        self.reranker = reranker or ScoreReranker()

    async def retrieve(
        self,
        *,
        lawyer_id: uuid.UUID,
        query_text: str,
        draft_type: str = "anticipatory_bail",
        case_id: uuid.UUID | None = None,
        section: str | None = None,
        limit: int = 8,
        min_confidence: float = 0.65,
    ) -> RagRetrievalResponse:
        normalized_query = _normalize_query(query_text)
        filters = {
            "lawyer_id": str(lawyer_id),
            "draft_type": draft_type,
            "case_id": str(case_id) if case_id else None,
            "section": section,
            "corpus_scopes": ["global_base", "lawyer_private"],
            "lawyer_private_boundary": str(lawyer_id),
        }

        embedding = await self._embed_query(normalized_query)
        keyword_terms = extract_legal_keywords(normalized_query)

        vector_matches = await self.repository.search_chunks_by_vector(
            lawyer_id=lawyer_id,
            embedding=embedding,
            draft_type=draft_type,
            case_id=case_id,
            section=section,
            limit=max(limit * 3, 20),
        )
        keyword_matches = await self.repository.search_chunks_by_keywords(
            lawyer_id=lawyer_id,
            keywords=keyword_terms,
            draft_type=draft_type,
            case_id=case_id,
            section=section,
            limit=max(limit * 2, 10),
        )

        candidates = _merge_candidates(vector_matches, keyword_matches, keyword_terms)
        ranked = self.reranker.rerank(normalized_query, candidates)[:limit]
        confidence = ranked[0].score if ranked else 0.0
        should_generate = confidence >= min_confidence and bool(ranked)

        query_log = await self.repository.log_query(
            lawyer_id=lawyer_id,
            case_id=case_id,
            query_text=query_text,
            filters={**filters, "keyword_terms": keyword_terms},
            confidence_score=confidence,
            generated=should_generate,
            chunks_used=[candidate.chunk.id for candidate in ranked],
        )
        if hasattr(self.repository, "create_observability_event"):
            await self.repository.create_observability_event(
                RagObservabilityEventCreate(
                    lawyer_id=lawyer_id,
                    case_id=case_id,
                    query_log_id=query_log.id,
                    event_type="retrieval_completed",
                    section=section,
                    severity="info" if should_generate else "warning",
                    metrics={
                        "confidence_score": round(confidence, 4),
                        "min_confidence": min_confidence,
                        "candidate_count": len(candidates),
                        "returned_chunk_count": len(ranked),
                        "top_score": round(ranked[0].score, 4) if ranked else 0.0,
                        "should_generate": should_generate,
                    },
                    metadata={
                        "filters": {**filters, "keyword_terms": keyword_terms},
                        "chunk_ids": [str(candidate.chunk.id) for candidate in ranked],
                        "match_reasons": [
                            sorted(candidate.match_reasons) for candidate in ranked
                        ],
                        "refusal_reason": None
                        if should_generate
                        else "LOW_RAG_CONFIDENCE",
                    },
                )
            )

        return RagRetrievalResponse(
            chunks=[
                RagRetrievedChunk(
                    chunk=RagChunkResponse.model_validate(candidate.chunk),
                    score=round(candidate.score, 4),
                    match_reasons=sorted(candidate.match_reasons),
                )
                for candidate in ranked
            ],
            confidence_score=round(confidence, 4),
            should_generate=should_generate,
            refusal_reason=None
            if should_generate
            else "LOW_RAG_CONFIDENCE: global base KB and private lawyer KB did not contain enough supporting context.",
            query_log_id=query_log.id,
            filters=filters,
        )

    async def _embed_query(self, query_text: str) -> list[float]:
        if self.embedding_cache is not None:
            cached = await self.embedding_cache.get(query_text)
            if cached is not None:
                return cached

        embeddings = await self.embedding_provider.embed_texts([query_text])
        embedding = validate_embedding(embeddings[0])
        if self.embedding_cache is not None:
            await self.embedding_cache.set(query_text, embedding)
        return embedding


def extract_legal_keywords(query_text: str) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for match in LEGAL_KEYWORD_RE.finditer(query_text):
        number = match.group(1).upper()
        code = match.group(2).upper()
        for keyword in (number, f"{number} {code}", code):
            key = keyword.lower()
            if key not in seen:
                seen.add(key)
                keywords.append(keyword)
    return keywords


def _merge_candidates(
    vector_matches: list[tuple[RagChunk, float]],
    keyword_matches: list[RagChunk],
    keyword_terms: list[str],
) -> list[CandidateScore]:
    candidates: dict[uuid.UUID, CandidateScore] = {}
    for chunk, distance in vector_matches:
        score = max(0.0, min(1.0, 1.0 - distance))
        candidates[chunk.id] = CandidateScore(
            chunk=chunk,
            score=score,
            match_reasons={"vector"},
        )

    for chunk in keyword_matches:
        candidate = candidates.get(chunk.id)
        if candidate is None:
            candidate = CandidateScore(chunk=chunk, score=0.0)
            candidates[chunk.id] = candidate
        candidate.match_reasons.add("legal_keyword")
        candidate.score = min(
            1.0,
            max(candidate.score, 0.72) + _keyword_density_boost(chunk.chunk_text, keyword_terms),
        )
    return list(candidates.values())


def _keyword_density_boost(chunk_text: str, keyword_terms: list[str]) -> float:
    lowered = chunk_text.lower()
    hits = sum(1 for keyword in keyword_terms if keyword.lower() in lowered)
    return min(0.18, hits * 0.04)


def _normalize_query(query_text: str) -> str:
    return re.sub(r"\s+", " ", query_text).strip()
