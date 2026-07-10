from __future__ import annotations

import uuid

from app.core.exceptions import ForbiddenError
from app.features.documents.sarvam_vision import SarvamVisionOcrProvider
from app.features.rag.assembly import assemble_anticipatory_bail_html
from app.features.rag.citations import (
    InternalCitationVerifier,
    citations_from_text_and_request,
)
from app.features.rag.embedding import SentenceTransformerEmbeddingProvider
from app.features.rag.generation import (
    AISectionGenerationProvider,
    RagSectionGenerationService,
)
from app.features.rag.ingestion import RagIngestionResult, RagIngestionService
from app.features.rag.repository import RagRepository
from app.features.rag.retrieval import RagRetrievalService, RedisEmbeddingCache
from app.features.rag.schemas import (
    CitationVerificationRequest,
    CitationVerificationResponse,
    DraftAssemblyRequest,
    DraftAssemblyResponse,
    RagDraftTraceResponse,
    RagGenerateSectionRequest,
    RagGeneratedSectionResponse,
    RagObservabilityEventResponse,
    RagRetrieveRequest,
    RagRetrievalResponse,
    VerifiedCitationCreate,
    VerifiedCitationResponse,
)


class RagService:
    def __init__(self, repository: RagRepository):
        self.repository = repository

    async def ingest_knowledge_base_file(
        self,
        *,
        lawyer_id: uuid.UUID,
        file_bytes: bytes,
        mime_type: str,
        original_filename: str | None,
        case_id: uuid.UUID | None = None,
    ) -> RagIngestionResult:
        ingestion = RagIngestionService(
            self.repository,
            SentenceTransformerEmbeddingProvider(),
            ocr_provider=SarvamVisionOcrProvider(),
        )
        return await ingestion.ingest_file(
            lawyer_id=lawyer_id,
            case_id=case_id,
            file_bytes=file_bytes,
            mime_type=mime_type,
            original_filename=original_filename,
            source_kind="kb_draft",
        )

    async def retrieve(
        self,
        *,
        lawyer_id: uuid.UUID,
        request: RagRetrieveRequest,
    ) -> RagRetrievalResponse:
        retrieval = RagRetrievalService(
            self.repository,
            SentenceTransformerEmbeddingProvider(),
            embedding_cache=RedisEmbeddingCache(),
        )
        return await retrieval.retrieve(
            lawyer_id=lawyer_id,
            query_text=request.query_text,
            case_id=request.case_id,
            draft_type=request.draft_type,
            section=request.section,
            limit=request.limit,
            min_confidence=request.min_confidence,
        )

    async def generate_section(
        self,
        *,
        lawyer_id: uuid.UUID,
        request: RagGenerateSectionRequest,
    ) -> RagGeneratedSectionResponse:
        embedding_provider = SentenceTransformerEmbeddingProvider()
        retrieval = RagRetrievalService(
            self.repository,
            embedding_provider,
            embedding_cache=RedisEmbeddingCache(),
        )
        generator = RagSectionGenerationService(
            self.repository,
            retrieval,
            AISectionGenerationProvider(),
        )
        return await generator.generate_section(lawyer_id=lawyer_id, request=request)

    async def create_verified_citation(
        self,
        *,
        lawyer_id: uuid.UUID,
        request: VerifiedCitationCreate,
    ) -> VerifiedCitationResponse:
        verifier = InternalCitationVerifier(self.repository)
        citation = await verifier.create_verified_citation(
            lawyer_id=lawyer_id,
            data=request,
        )
        return VerifiedCitationResponse.model_validate(citation)

    async def list_verified_citations(
        self,
        *,
        lawyer_id: uuid.UUID,
        limit: int = 100,
    ) -> list[VerifiedCitationResponse]:
        citations = await self.repository.list_verified_citations(
            lawyer_id=lawyer_id,
            limit=limit,
        )
        return [VerifiedCitationResponse.model_validate(citation) for citation in citations]

    async def verify_citations(
        self,
        *,
        lawyer_id: uuid.UUID,
        request: CitationVerificationRequest,
    ) -> CitationVerificationResponse:
        verifier = InternalCitationVerifier(self.repository)
        citations = citations_from_text_and_request(
            text=request.text,
            citations=request.citations,
        )
        return await verifier.verify(lawyer_id=lawyer_id, citations=citations)

    async def assemble_draft(
        self,
        *,
        request: DraftAssemblyRequest,
    ) -> DraftAssemblyResponse:
        return assemble_anticipatory_bail_html(request)

    async def list_observability_events(
        self,
        *,
        lawyer_id: uuid.UUID,
        case_id: uuid.UUID | None = None,
        draft_id: uuid.UUID | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RagObservabilityEventResponse]:
        events = await self.repository.list_observability_events(
            lawyer_id=lawyer_id,
            case_id=case_id,
            draft_id=draft_id,
            event_type=event_type,
            limit=limit,
        )
        return [RagObservabilityEventResponse.model_validate(event) for event in events]

    async def get_draft_trace(
        self,
        *,
        lawyer_id: uuid.UUID,
        draft_id: uuid.UUID,
    ) -> RagDraftTraceResponse:
        draft = await self.repository.get_draft_for_lawyer(
            draft_id=draft_id,
            lawyer_id=lawyer_id,
        )
        if draft is None:
            raise ForbiddenError("Draft not found or not owned by this lawyer.")

        events = await self.list_observability_events(
            lawyer_id=lawyer_id,
            draft_id=draft_id,
            limit=500,
        )
        sources = await self.repository.list_draft_section_sources(
            draft_id=draft_id,
            lawyer_id=lawyer_id,
        )
        section_sources = {
            source.section: source.source_chunk_ids
            for source in sources
        }
        query_log_ids = []
        seen = set()
        for event in events:
            if event.query_log_id is None or event.query_log_id in seen:
                continue
            query_log_ids.append(event.query_log_id)
            seen.add(event.query_log_id)
        return RagDraftTraceResponse(
            draft_id=draft_id,
            events=events,
            section_sources=section_sources,
            query_log_ids=query_log_ids,
        )
