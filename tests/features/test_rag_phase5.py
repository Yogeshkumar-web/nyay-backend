from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.features.rag.citations import (
    CitationNormalizer,
    InternalCitationVerifier,
    citations_from_text_and_request,
)
from app.features.rag.generation import RagSectionGenerationService
from app.features.rag.schemas import CitedJudgment, RagGenerateSectionRequest
from tests.features.test_rag_phase4 import (
    _FakeGenerationProvider,
    _FakeRetrievalService,
    _retrieval_response,
)


def test_citation_normalizer_builds_stable_keys():
    normalizer = CitationNormalizer()

    first = normalizer.normalize(
        CitedJudgment(
            case_name="Siddharam Satlingappa Mhetre vs. State of Maharashtra",
            citation="(2011) 1 SCC 694",
            year=2011,
        )
    )
    second = normalizer.normalize(
        CitedJudgment(
            case_name="Siddharam Satlingappa Mhetre v State of Maharashtra",
            citation="2011 1 SCC 694",
            year=None,
        )
    )

    assert first.primary_key in second.alternate_keys


@pytest.mark.asyncio
async def test_internal_citation_verifier_splits_verified_and_unverified():
    lawyer_id = uuid.uuid4()
    verified = CitedJudgment(
        case_name="Siddharam Satlingappa Mhetre vs. State of Maharashtra",
        citation="(2011) 1 SCC 694",
        year=2011,
    )
    unverified = CitedJudgment(
        case_name="Imaginary Case vs. State",
        citation="(2020) 1 SCC 1",
        year=2020,
    )
    repository = _FakeCitationRepository(verified_citations=[verified])

    result = await InternalCitationVerifier(repository).verify(
        lawyer_id=lawyer_id,
        citations=[verified, unverified],
    )

    assert [item.citation.case_name for item in result.verified] == [
        verified.case_name
    ]
    assert [item.citation.case_name for item in result.unverified] == [
        unverified.case_name
    ]
    assert result.warnings


@pytest.mark.asyncio
async def test_verify_text_extracts_citations_and_checks_internal_corpus():
    lawyer_id = uuid.uuid4()
    repository = _FakeCitationRepository(
        verified_citations=[
            CitedJudgment(
                case_name="Siddharam Satlingappa Mhetre vs. State of Maharashtra",
                citation="(2011) 1 SCC 694",
                year=2011,
            )
        ]
    )

    result = await InternalCitationVerifier(repository).verify_text(
        lawyer_id=lawyer_id,
        text="Relied on Siddharam Satlingappa Mhetre vs. State of Maharashtra, (2011) 1 SCC 694.",
    )

    assert len(result.verified) == 1
    assert not result.unverified


def test_citations_from_text_and_request_dedupes():
    explicit = CitedJudgment(
        case_name="Siddharam Satlingappa Mhetre vs. State of Maharashtra",
        citation="(2011) 1 SCC 694",
        year=2011,
    )
    merged = citations_from_text_and_request(
        text="Siddharam Satlingappa Mhetre vs. State of Maharashtra, (2011) 1 SCC 694.",
        citations=[explicit],
    )

    assert len(merged) == 1


@pytest.mark.asyncio
async def test_generation_accepts_internal_verified_citation_not_in_retrieved_chunks():
    lawyer_id = uuid.uuid4()
    verified = CitedJudgment(
        case_name="Siddharam Satlingappa Mhetre vs. State of Maharashtra",
        citation="(2011) 1 SCC 694",
        year=2011,
    )
    repository = _FakeCitationRepository(verified_citations=[verified])
    service = RagSectionGenerationService(
        repository,
        _FakeRetrievalService(_retrieval_response()),
        _FakeGenerationProvider(
            "The ground is supported by Siddharam Satlingappa Mhetre vs. State of Maharashtra, (2011) 1 SCC 694."
        ),
        citation_verifier=InternalCitationVerifier(repository),
    )

    result = await service.generate_section(
        lawyer_id=lawyer_id,
        request=RagGenerateSectionRequest(
            case_facts="Applicant apprehends arrest in a non-bailable offence under 438 CrPC.",
            sections_invoked=["438 CrPC"],
            target_section="grounds",
        ),
    )

    assert result.status == "generated"
    assert result.validation_errors == []


class _FakeCitationRepository:
    def __init__(self, *, verified_citations):
        self.normalizer = CitationNormalizer()
        self.verified = {
            self.normalizer.normalize(citation).primary_key: SimpleNamespace(
                id=uuid.uuid4(),
                case_name=citation.case_name,
                citation=citation.citation,
                year=citation.year,
                court=citation.court,
            )
            for citation in verified_citations
        }

    async def find_verified_citation(self, *, lawyer_id, normalized_keys):
        for key in normalized_keys:
            if key in self.verified:
                return self.verified[key]
        return None

    async def list_verified_citations(self, *, lawyer_id, limit=100):
        return list(self.verified.values())[:limit]

    async def upsert_verified_citation(self, **kwargs):
        item = SimpleNamespace(id=uuid.uuid4(), **kwargs, is_active=True, metadata_={})
        self.verified[kwargs["normalized_key"]] = item
        return item

    async def get_draft_for_lawyer(self, *, draft_id, lawyer_id):
        return None

    async def create_draft_section_source(self, **kwargs):
        raise AssertionError("No trace should be stored in these tests.")
