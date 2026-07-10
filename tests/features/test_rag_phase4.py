from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.features.rag.chunking import (
    chunk_anticipatory_bail_text,
    extract_anticipatory_bail_schema,
)
from app.features.rag.generation import (
    RagSectionGenerationService,
    build_section_prompt,
)
from app.features.rag.schemas import (
    RagChunkResponse,
    RagGenerateSectionRequest,
    RagRetrievedChunk,
    RagRetrievalResponse,
)


def test_anonymized_samples_are_usable_for_section_chunking():
    sample_dir = Path(__file__).resolve().parents[3] / "anonymized"
    samples = sorted(sample_dir.glob("*.md"))

    assert len(samples) >= 5
    for sample in samples[:8]:
        text = sample.read_text(encoding="utf-8")
        chunks = chunk_anticipatory_bail_text(text)
        extraction = extract_anticipatory_bail_schema(text)

        assert chunks, sample.name
        assert extraction.facts, sample.name
        assert any("applicant" in chunk.text.lower() for chunk in chunks), sample.name


def test_section_prompt_constrains_sources_and_citations():
    request = RagGenerateSectionRequest(
        case_facts="Applicant apprehends arrest in a non-bailable offence under 438 CrPC.",
        sections_invoked=["438 CrPC"],
        target_section="grounds",
    )
    context = SimpleNamespace(
        source_chunk_ids=[uuid.uuid4()],
        source_texts=["Custodial interrogation is not required."],
        allowed_citations=[],
        confidence_score=0.81,
        query_log_id=uuid.uuid4(),
    )

    system_prompt, user_message = build_section_prompt(request, context)

    assert "Do not invent facts" in system_prompt
    assert "Citations are allowed only" in system_prompt
    assert "source_chunk_id" in user_message
    assert "438 CrPC" in user_message


@pytest.mark.asyncio
async def test_generation_refuses_when_retrieval_confidence_is_low():
    request = RagGenerateSectionRequest(
        case_facts="Applicant apprehends arrest in a non-bailable offence under 438 CrPC.",
        sections_invoked=["438 CrPC"],
        target_section="grounds",
    )
    service = RagSectionGenerationService(
        _FakeGenerationRepository(),
        _FakeRetrievalService(_retrieval_response(should_generate=False)),
        _FakeGenerationProvider("should not be used"),
    )

    result = await service.generate_section(lawyer_id=uuid.uuid4(), request=request)

    assert result.status == "refused"
    assert result.content is None
    assert result.refusal_reason


@pytest.mark.asyncio
async def test_generation_rejects_out_of_kb_citation():
    lawyer_id = uuid.uuid4()
    request = RagGenerateSectionRequest(
        case_facts="Applicant apprehends arrest in a non-bailable offence under 438 CrPC.",
        sections_invoked=["438 CrPC"],
        target_section="grounds",
    )
    service = RagSectionGenerationService(
        _FakeGenerationRepository(),
        _FakeRetrievalService(_retrieval_response()),
        _FakeGenerationProvider(
            "The applicant deserves protection in view of Imaginary Case vs. State, (2020) 1 SCC 1."
        ),
    )

    result = await service.generate_section(lawyer_id=lawyer_id, request=request)

    assert result.status == "rejected"
    assert result.content is None
    assert result.validation_errors


@pytest.mark.asyncio
async def test_generation_accepts_allowed_citation_and_stores_trace():
    lawyer_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    allowed = "Siddharam Satlingappa Mhetre vs. State of Maharashtra, (2011) 1 SCC 694"
    request = RagGenerateSectionRequest(
        case_facts="Applicant apprehends arrest in a non-bailable offence under 438 CrPC.",
        sections_invoked=["438 CrPC"],
        target_section="grounds",
        draft_id=draft_id,
    )
    repository = _FakeGenerationRepository(draft_exists=True)
    service = RagSectionGenerationService(
        repository,
        _FakeRetrievalService(_retrieval_response(source_text=f"Allowed authority: {allowed}.")),
        _FakeGenerationProvider(f"The ground is supported by {allowed}."),
    )

    result = await service.generate_section(lawyer_id=lawyer_id, request=request)

    assert result.status == "generated"
    assert result.content
    assert result.source_chunk_ids
    assert result.draft_section_source_id == repository.created_trace.id
    assert repository.created_trace.verification_status == "verified"


def _retrieval_response(
    *,
    should_generate: bool = True,
    source_text: str = "Grounds: custodial interrogation is not required under Section 438 CrPC.",
) -> RagRetrievalResponse:
    chunk = RagChunkResponse(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        lawyer_id=uuid.uuid4(),
        case_id=None,
        draft_type="anticipatory_bail",
        section="grounds",
        chunk_text=source_text,
        summary=None,
        keywords=None,
        confidence_score=None,
        token_count=len(source_text.split()),
        metadata_={},
        created_at=datetime.now(UTC),
    )
    return RagRetrievalResponse(
        chunks=[RagRetrievedChunk(chunk=chunk, score=0.86, match_reasons=["vector"])]
        if should_generate
        else [],
        confidence_score=0.86 if should_generate else 0.2,
        should_generate=should_generate,
        refusal_reason=None if should_generate else "LOW_RAG_CONFIDENCE",
        query_log_id=uuid.uuid4(),
        filters={"private_corpus_only": True},
    )


class _FakeRetrievalService:
    def __init__(self, response: RagRetrievalResponse):
        self.response = response

    async def retrieve(self, **kwargs):
        return self.response


class _FakeGenerationProvider:
    def __init__(self, content: str):
        self.content = content

    async def generate(self, *, system_prompt: str, user_message: str) -> str:
        assert "Do not invent facts" in system_prompt
        return self.content


class _FakeGenerationRepository:
    def __init__(self, *, draft_exists: bool = False):
        self.draft_exists = draft_exists
        self.created_trace = None

    async def get_draft_for_lawyer(self, *, draft_id, lawyer_id):
        return SimpleNamespace(id=draft_id) if self.draft_exists else None

    async def get_verified_citation(self, *, lawyer_id, normalized_key):
        return None

    async def find_verified_citation(self, *, lawyer_id, normalized_keys):
        return None

    async def list_verified_citations(self, *, lawyer_id, limit=100):
        return []

    async def create_draft_section_source(
        self,
        *,
        draft_id,
        lawyer_id,
        section,
        source_chunk_ids,
        cited_judgments,
        verification_status,
        metadata,
    ):
        self.created_trace = SimpleNamespace(
            id=uuid.uuid4(),
            draft_id=draft_id,
            lawyer_id=lawyer_id,
            section=section,
            source_chunk_ids=source_chunk_ids,
            cited_judgments=cited_judgments,
            verification_status=verification_status,
            metadata_=metadata,
        )
        return self.created_trace
