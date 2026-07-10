from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.features.rag.chunking import extract_cited_judgments
from app.features.rag.citations import InternalCitationVerifier
from app.features.rag.models import CitationVerificationStatus
from app.features.rag.repository import RagRepository
from app.features.rag.retrieval import RagRetrievalService
from app.features.rag.schemas import (
    CitedJudgment,
    RagObservabilityEventCreate,
    RagGenerateSectionRequest,
    RagGeneratedSectionResponse,
)


class SectionGenerationProvider(Protocol):
    async def generate(self, *, system_prompt: str, user_message: str) -> str: ...


class AISectionGenerationProvider:
    async def generate(self, *, system_prompt: str, user_message: str) -> str:
        provider = settings.AI_PROVIDER.lower()
        if provider == "gemini":
            return await asyncio.to_thread(_generate_gemini, system_prompt, user_message)
        if provider == "claude":
            return await asyncio.to_thread(_generate_claude, system_prompt, user_message)
        return await asyncio.to_thread(_generate_openai_compatible, system_prompt, user_message)


@dataclass(frozen=True)
class GenerationContext:
    source_chunk_ids: list[uuid.UUID]
    source_texts: list[str]
    allowed_citations: list[CitedJudgment]
    confidence_score: float
    query_log_id: uuid.UUID | None


class RagSectionGenerationService:
    def __init__(
        self,
        repository: RagRepository,
        retrieval_service: RagRetrievalService,
        generation_provider: SectionGenerationProvider,
        citation_verifier: InternalCitationVerifier | None = None,
    ):
        self.repository = repository
        self.retrieval_service = retrieval_service
        self.generation_provider = generation_provider
        self.citation_verifier = citation_verifier or InternalCitationVerifier(repository)

    async def generate_section(
        self,
        *,
        lawyer_id: uuid.UUID,
        request: RagGenerateSectionRequest,
    ) -> RagGeneratedSectionResponse:
        if request.draft_id is not None:
            draft = await self.repository.get_draft_for_lawyer(
                draft_id=request.draft_id,
                lawyer_id=lawyer_id,
            )
            if draft is None:
                raise ForbiddenError("Draft not found or not owned by this lawyer.")

        retrieval = await self.retrieval_service.retrieve(
            lawyer_id=lawyer_id,
            query_text=_build_retrieval_query(request),
            draft_type=request.draft_type,
            case_id=request.case_id,
            section=request.target_section,
            limit=request.retrieval_limit,
            min_confidence=request.min_confidence,
        )
        if not retrieval.should_generate:
            await self._record_event(
                RagObservabilityEventCreate(
                    lawyer_id=lawyer_id,
                    case_id=request.case_id,
                    draft_id=request.draft_id,
                    query_log_id=retrieval.query_log_id,
                    event_type="generation_refused",
                    section=request.target_section,
                    severity="warning",
                    metrics={
                        "confidence_score": retrieval.confidence_score,
                        "source_chunk_count": len(retrieval.chunks),
                    },
                    metadata={
                        "refusal_reason": retrieval.refusal_reason,
                        "target_section": request.target_section,
                    },
                )
            )
            return RagGeneratedSectionResponse(
                section=request.target_section,
                confidence_score=retrieval.confidence_score,
                status="refused",
                refusal_reason=retrieval.refusal_reason,
                query_log_id=retrieval.query_log_id,
            )

        context = _build_generation_context(retrieval)
        system_prompt, user_message = build_section_prompt(request, context)
        content = await self.generation_provider.generate(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        validation_errors = await self._validate_generated_citations(
            lawyer_id=lawyer_id,
            generated_text=content,
            allowed_citations=context.allowed_citations,
        )
        cited_judgments = extract_cited_judgments(content)
        status = "generated" if not validation_errors else "rejected"
        stored_trace_id = None

        if request.draft_id is not None:
            trace = await self.repository.create_draft_section_source(
                draft_id=request.draft_id,
                lawyer_id=lawyer_id,
                section=request.target_section,
                source_chunk_ids=context.source_chunk_ids,
                cited_judgments=[citation.model_dump() for citation in cited_judgments],
                verification_status=(
                    CitationVerificationStatus.verified.value
                    if status == "generated"
                    else CitationVerificationStatus.rejected.value
                ),
                metadata={
                    "rag_confidence_score": context.confidence_score,
                    "query_log_id": str(context.query_log_id)
                    if context.query_log_id
                    else None,
                    "validation_errors": validation_errors,
                },
            )
            stored_trace_id = trace.id

        if validation_errors:
            await self._record_event(
                RagObservabilityEventCreate(
                    lawyer_id=lawyer_id,
                    case_id=request.case_id,
                    draft_id=request.draft_id,
                    query_log_id=context.query_log_id,
                    event_type="citation_verification_failed",
                    section=request.target_section,
                    severity="warning",
                    metrics={
                        "confidence_score": context.confidence_score,
                        "unsupported_citation_count": len(validation_errors),
                    },
                    metadata={"validation_errors": validation_errors},
                )
            )

        await self._record_event(
            RagObservabilityEventCreate(
                lawyer_id=lawyer_id,
                case_id=request.case_id,
                draft_id=request.draft_id,
                query_log_id=context.query_log_id,
                event_type="generation_completed"
                if status == "generated"
                else "generation_rejected",
                section=request.target_section,
                severity="info" if status == "generated" else "warning",
                metrics={
                    "confidence_score": context.confidence_score,
                    "source_chunk_count": len(context.source_chunk_ids),
                    "citation_count": len(cited_judgments),
                    "validation_error_count": len(validation_errors),
                },
                metadata={
                    "source_chunk_ids": [
                        str(chunk_id) for chunk_id in context.source_chunk_ids
                    ],
                    "draft_section_source_id": str(stored_trace_id)
                    if stored_trace_id
                    else None,
                    "status": status,
                },
            )
        )

        return RagGeneratedSectionResponse(
            section=request.target_section,
            content=content if status == "generated" else None,
            source_chunk_ids=context.source_chunk_ids,
            cited_judgments=cited_judgments,
            confidence_score=context.confidence_score,
            status=status,
            refusal_reason=None
            if status == "generated"
            else "Generated section introduced unsupported citations.",
            validation_errors=validation_errors,
            query_log_id=context.query_log_id,
            draft_section_source_id=stored_trace_id,
        )

    async def _record_event(self, event: RagObservabilityEventCreate) -> None:
        if not hasattr(self.repository, "create_observability_event"):
            return
        await self.repository.create_observability_event(event)

    async def _validate_generated_citations(
        self,
        *,
        lawyer_id: uuid.UUID,
        generated_text: str,
        allowed_citations: list[CitedJudgment],
    ) -> list[str]:
        allowed_keys = {_citation_key(citation) for citation in allowed_citations}
        errors: list[str] = []
        for citation in extract_cited_judgments(generated_text):
            key = _citation_key(citation)
            if key in allowed_keys or _citation_matches_allowed(
                citation,
                allowed_citations,
            ):
                continue
            verification = await self.citation_verifier.verify(
                lawyer_id=lawyer_id,
                citations=[citation],
            )
            if verification.verified:
                continue
            errors.append(
                f"Unsupported citation introduced: {citation.case_name}"
                + (f" ({citation.citation})" if citation.citation else "")
            )
        return errors


def build_section_prompt(
    request: RagGenerateSectionRequest,
    context: GenerationContext,
) -> tuple[str, str]:
    system_prompt = (
        "You are drafting an Indian High Court anticipatory bail application section. "
        "Use only the supplied case facts and retrieved private KB context. "
        "Do not invent facts, dates, FIR details, statutes, case names, citations, or judgments. "
        "Citations are allowed only if they appear in the Allowed Citations list. "
        "If a legal point needs a citation that is not provided, write the point without a citation. "
        "Return only the requested section text, not explanations."
    )
    source_blocks = [
        {
            "source_chunk_id": str(chunk_id),
            "text": text,
        }
        for chunk_id, text in zip(context.source_chunk_ids, context.source_texts, strict=True)
    ]
    payload = {
        "target_section": request.target_section,
        "case_facts": request.case_facts,
        "sections_invoked": request.sections_invoked,
        "additional_instructions": request.additional_instructions,
        "retrieval_confidence_score": context.confidence_score,
        "source_chunks": source_blocks,
        "allowed_citations": [citation.model_dump() for citation in context.allowed_citations],
        "required_output_rules": [
            "Do not use any source unless it is in source_chunks.",
            "Do not include citations outside allowed_citations.",
            "Keep placeholders intact if present.",
            "Use formal High Court drafting language.",
        ],
    }
    return system_prompt, json.dumps(payload, ensure_ascii=False, indent=2)


def _build_generation_context(retrieval) -> GenerationContext:
    source_chunk_ids = [item.chunk.id for item in retrieval.chunks]
    source_texts = [item.chunk.chunk_text for item in retrieval.chunks]
    allowed: dict[str, CitedJudgment] = {}
    for text in source_texts:
        for citation in extract_cited_judgments(text):
            allowed[_citation_key(citation)] = citation
    return GenerationContext(
        source_chunk_ids=source_chunk_ids,
        source_texts=source_texts,
        allowed_citations=list(allowed.values()),
        confidence_score=retrieval.confidence_score,
        query_log_id=retrieval.query_log_id,
    )


def _build_retrieval_query(request: RagGenerateSectionRequest) -> str:
    parts = [
        request.target_section,
        request.case_facts,
        " ".join(request.sections_invoked),
    ]
    return " ".join(part for part in parts if part).strip()


def _citation_key(citation: CitedJudgment) -> str:
    normalized = f"{citation.case_name}|{citation.citation or ''}|{citation.year or ''}"
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def _citation_matches_allowed(
    citation: CitedJudgment,
    allowed_citations: list[CitedJudgment],
) -> bool:
    citation_text = (citation.citation or "").lower().strip()
    citation_case = citation.case_name.lower()
    for allowed in allowed_citations:
        allowed_text = (allowed.citation or "").lower().strip()
        allowed_case = allowed.case_name.lower()
        if citation_text and allowed_text and citation_text == allowed_text:
            return True
        if citation.year and allowed.year and citation.year == allowed.year:
            if allowed_case in citation_case or citation_case in allowed_case:
                return True
    return False


def _generate_gemini(system_prompt: str, user_message: str) -> str:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=user_message,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=2500,
        ),
    )
    return (response.text or "").strip()


def _generate_claude(system_prompt: str, user_message: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return "\n".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()


def _generate_openai_compatible(system_prompt: str, user_message: str) -> str:
    from openai import OpenAI

    api_key = settings.OPENAI_API_KEY
    base_url = None
    model = settings.OPENAI_MODEL
    if settings.AI_PROVIDER.lower() == "deepseek":
        api_key = settings.DEEPSEEK_API_KEY
        base_url = settings.DEEPSEEK_BASE_URL
        model = settings.DEEPSEEK_MODEL
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=2500,
    )
    return (response.choices[0].message.content or "").strip()
