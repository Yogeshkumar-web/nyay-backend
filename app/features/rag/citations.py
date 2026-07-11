from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.features.rag.chunking import extract_cited_judgments
from app.features.rag.repository import RagRepository
from app.features.rag.schemas import (
    CitationVerificationItem,
    CitationVerificationResponse,
    CitedJudgment,
    VerifiedCitationCreate,
)


class CitationStatus:
    verified = "verified"
    unverified = "unverified"


@dataclass(frozen=True)
class NormalizedCitation:
    primary_key: str
    alternate_keys: tuple[str, ...]


class CitationNormalizer:
    def normalize(self, citation: CitedJudgment) -> NormalizedCitation:
        case_name = _normalize_text(citation.case_name)
        citation_text = _normalize_text(citation.citation or "")
        year = str(citation.year or _extract_year(citation.citation or "") or "")

        keys = [
            "|".join(part for part in (case_name, citation_text, year) if part),
            "|".join(part for part in (case_name, citation_text) if part),
            "|".join(part for part in (case_name, year) if part),
            "|".join(part for part in (citation_text, year) if part),
            citation_text,
        ]
        compact = tuple(dict.fromkeys(key for key in keys if key))
        return NormalizedCitation(primary_key=compact[0], alternate_keys=compact)


class InternalCitationVerifier:
    def __init__(
        self,
        repository: RagRepository,
        *,
        normalizer: CitationNormalizer | None = None,
    ):
        self.repository = repository
        self.normalizer = normalizer or CitationNormalizer()

    async def verify(
        self,
        *,
        lawyer_id: uuid.UUID,
        citations: list[CitedJudgment],
    ) -> CitationVerificationResponse:
        verified: list[CitationVerificationItem] = []
        unverified: list[CitationVerificationItem] = []

        for citation in citations:
            normalized = self.normalizer.normalize(citation)
            match = await self.repository.find_verified_citation(
                lawyer_id=lawyer_id,
                normalized_keys=normalized.alternate_keys,
            )
            if not match:
                match = await self._find_by_citation_fallback(
                    lawyer_id=lawyer_id,
                    citation=citation,
                )
            if match:
                verified.append(
                    CitationVerificationItem(
                        citation=citation,
                        status=CitationStatus.verified,
                        matched_citation_id=match.id,
                    )
                )
            else:
                unverified.append(
                    CitationVerificationItem(
                        citation=citation,
                        status=CitationStatus.unverified,
                        warning=(
                            "Citation is not present in the internal verified "
                            "corpus and must not be used in final draft output."
                        ),
                    )
                )

        return CitationVerificationResponse(
            verified=verified,
            unverified=unverified,
            warnings=[item.warning for item in unverified if item.warning],
        )

    async def verify_text(
        self,
        *,
        lawyer_id: uuid.UUID,
        text: str,
    ) -> CitationVerificationResponse:
        return await self.verify(
            lawyer_id=lawyer_id,
            citations=extract_cited_judgments(text),
        )

    async def create_verified_citation(
        self,
        *,
        lawyer_id: uuid.UUID,
        data: VerifiedCitationCreate,
    ):
        citation = CitedJudgment(
            case_name=data.case_name,
            citation=data.citation,
            year=data.year,
            court=data.court,
        )
        normalized = self.normalizer.normalize(citation)
        citation_lawyer_id = None if data.corpus_scope == "global_base" else lawyer_id
        return await self.repository.upsert_verified_citation(
            lawyer_id=citation_lawyer_id,
            normalized_key=normalized.primary_key,
            case_name=data.case_name,
            citation=data.citation,
            year=data.year,
            court=data.court,
            source_chunk_id=data.source_chunk_id,
            corpus_scope=data.corpus_scope,
            metadata=data.metadata,
        )

    async def _find_by_citation_fallback(
        self,
        *,
        lawyer_id: uuid.UUID,
        citation: CitedJudgment,
    ):
        if not citation.citation:
            return None
        citation_text = _normalize_text(citation.citation)
        year = str(citation.year or _extract_year(citation.citation) or "")
        keys = [
            "|".join(part for part in (citation_text, year) if part),
            citation_text,
        ]
        match = await self.repository.find_verified_citation(
            lawyer_id=lawyer_id,
            normalized_keys=keys,
        )
        if match:
            return match

        for verified in await self.repository.list_verified_citations(
            lawyer_id=lawyer_id,
            limit=500,
        ):
            if _normalize_text(verified.citation or "") != citation_text:
                continue
            verified_year = str(verified.year or _extract_year(verified.citation or "") or "")
            if not year or not verified_year or year == verified_year:
                return verified
        return None


def citations_from_text_and_request(
    *,
    text: str | None,
    citations: list[CitedJudgment],
) -> list[CitedJudgment]:
    merged: dict[str, CitedJudgment] = {}
    normalizer = CitationNormalizer()
    for citation in citations:
        merged[normalizer.normalize(citation).primary_key] = citation
    if text:
        for citation in extract_cited_judgments(text):
            merged[normalizer.normalize(citation).primary_key] = citation
    return list(merged.values())


def _normalize_text(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"\b(vs?|versus)\b\.?", " v ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _extract_year(value: str) -> int | None:
    match = re.search(r"\b(18|19|20|21)\d{2}\b", value)
    return int(match.group(0)) if match else None
