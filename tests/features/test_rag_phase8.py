from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.features.rag.generation import RagSectionGenerationService
from app.features.rag.schemas import (
    RagDraftTraceResponse,
    RagGenerateSectionRequest,
)
from app.features.rag.service import RagService
from tests.features.test_rag_phase4 import (
    _FakeGenerationProvider,
    _FakeRetrievalService,
    _retrieval_response,
)


@pytest.mark.asyncio
async def test_generation_records_observability_events_for_success():
    repository = _ObservableGenerationRepository()
    service = RagSectionGenerationService(
        repository,
        _FakeRetrievalService(_retrieval_response()),
        _FakeGenerationProvider("Custodial interrogation is not required."),
    )

    result = await service.generate_section(
        lawyer_id=uuid.uuid4(),
        request=RagGenerateSectionRequest(
            case_facts="Applicant apprehends arrest in a non-bailable offence under 438 CrPC.",
            sections_invoked=["438 CrPC"],
            target_section="grounds",
        ),
    )

    assert result.status == "generated"
    assert [event.event_type for event in repository.events] == [
        "generation_completed"
    ]
    assert repository.events[0].metrics["source_chunk_count"] == 1
    assert repository.events[0].section == "grounds"


@pytest.mark.asyncio
async def test_generation_records_refusal_event_for_low_confidence():
    repository = _ObservableGenerationRepository()
    service = RagSectionGenerationService(
        repository,
        _FakeRetrievalService(_retrieval_response(should_generate=False)),
        _FakeGenerationProvider("should not be used"),
    )

    result = await service.generate_section(
        lawyer_id=uuid.uuid4(),
        request=RagGenerateSectionRequest(
            case_facts="Applicant apprehends arrest in a non-bailable offence under 438 CrPC.",
            sections_invoked=["438 CrPC"],
            target_section="facts",
        ),
    )

    assert result.status == "refused"
    assert len(repository.events) == 1
    assert repository.events[0].event_type == "generation_refused"
    assert repository.events[0].severity == "warning"


@pytest.mark.asyncio
async def test_draft_trace_summarizes_events_sources_and_query_logs():
    lawyer_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    query_log_id = uuid.uuid4()
    repository = _TraceRepository(
        draft_id=draft_id,
        query_log_id=query_log_id,
    )
    service = RagService(repository)  # type: ignore[arg-type]

    trace = await service.get_draft_trace(lawyer_id=lawyer_id, draft_id=draft_id)

    assert isinstance(trace, RagDraftTraceResponse)
    assert trace.draft_id == draft_id
    assert trace.query_log_ids == [query_log_id]
    assert trace.section_sources["grounds"] == repository.source_chunk_ids
    assert trace.events[0].event_type == "generation_completed"


class _ObservableGenerationRepository:
    def __init__(self):
        self.events = []

    async def get_verified_citation(self, *, lawyer_id, normalized_key):
        return None

    async def find_verified_citation(self, *, lawyer_id, normalized_keys):
        return None

    async def list_verified_citations(self, *, lawyer_id, limit=100):
        return []

    async def create_observability_event(self, event):
        self.events.append(event)
        return SimpleNamespace(id=uuid.uuid4(), **event.model_dump(), metadata_=event.metadata)


class _TraceRepository:
    def __init__(self, *, draft_id: uuid.UUID, query_log_id: uuid.UUID):
        self.draft_id = draft_id
        self.query_log_id = query_log_id
        self.source_chunk_ids = [uuid.uuid4()]

    async def get_draft_for_lawyer(self, *, draft_id, lawyer_id):
        return SimpleNamespace(id=draft_id)

    async def list_observability_events(self, **kwargs):
        return [
            SimpleNamespace(
                id=uuid.uuid4(),
                lawyer_id=kwargs["lawyer_id"],
                case_id=None,
                draft_id=self.draft_id,
                query_log_id=self.query_log_id,
                event_type="generation_completed",
                section="grounds",
                severity="info",
                metrics={"confidence_score": 0.86},
                metadata_={"source_chunk_ids": [str(self.source_chunk_ids[0])]},
                created_at=datetime.now(UTC),
            )
        ]

    async def list_draft_section_sources(self, *, draft_id, lawyer_id):
        return [
            SimpleNamespace(
                section="grounds",
                source_chunk_ids=self.source_chunk_ids,
            )
        ]
