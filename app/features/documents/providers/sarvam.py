from __future__ import annotations

import asyncio
import json
from typing import Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.features.documents.digital_extractor import PageSplit
from app.features.documents.providers.contracts import (
    DocumentProviderError,
    ExtractedPageText,
    PageInput,
    ProviderErrorCategory,
    TypedPage,
)
from app.features.documents.sarvam_vision import SarvamVisionError, SarvamVisionOcrProvider


class SarvamVisionAdapter:
    provider_key = "sarvam_vision"

    def __init__(self, provider: SarvamVisionOcrProvider | None = None) -> None:
        self._provider = provider or SarvamVisionOcrProvider()

    async def extract_pages(
        self,
        pages: Sequence[PageInput],
        *,
        idempotency_key: str,
    ) -> Sequence[ExtractedPageText]:
        del idempotency_key
        by_number = {page.page_number: page for page in pages}
        splits = [
            PageSplit(
                page_number=page.page_number,
                filename=page.source_filename,
                mime_type=page.mime_type,
                content=page.content,
                checksum=page.checksum_sha256,
                size_bytes=len(page.content),
            )
            for page in pages
        ]
        result = None
        for attempt in range(1, settings.DOCUMENT_PROCESSING_MAX_ATTEMPTS + 1):
            try:
                result = await self._provider.process_pages(
                    splits,
                    batch_size=settings.DOCUMENT_VISION_BATCH_SIZE,
                )
                break
            except SarvamVisionError as exc:
                error = _translate_vision_error(exc)
            except (httpx.TimeoutException, TimeoutError) as exc:
                error = DocumentProviderError(
                    "Vision provider timed out.",
                    category=ProviderErrorCategory.timeout,
                    retryable=True,
                )
                error.__cause__ = exc
            except httpx.HTTPError as exc:
                error = DocumentProviderError(
                    "Vision provider is unavailable.",
                    category=ProviderErrorCategory.provider_unavailable,
                    retryable=True,
                )
                error.__cause__ = exc
            if not error.retryable or attempt == settings.DOCUMENT_PROCESSING_MAX_ATTEMPTS:
                raise error
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
        if result is None:  # pragma: no cover - loop always returns or raises
            raise RuntimeError("Vision provider did not produce a result.")

        outputs: list[ExtractedPageText] = []
        seen: set[int] = set()
        for artifact in result.artifact.get("pages", []):
            page_number = int(artifact.get("page_number") or 0)
            source = by_number.get(page_number)
            if source is None or page_number in seen:
                raise DocumentProviderError(
                    "Vision provider returned duplicate or unknown page output.",
                    category=ProviderErrorCategory.invalid_response,
                    retryable=False,
                )
            seen.add(page_number)
            confidence = artifact.get("confidence")
            try:
                normalized_confidence = (
                    float(confidence) if confidence is not None else None
                )
            except (TypeError, ValueError):
                normalized_confidence = None
            outputs.append(
                ExtractedPageText(
                    page_id=source.page_id,
                    page_number=page_number,
                    text=str(artifact.get("text") or "").strip(),
                    method="vision_ocr",
                    confidence=normalized_confidence,
                    language=result.language,
                    warnings=tuple(
                        artifact.get("warnings")
                        or artifact.get("ocr_warnings")
                        or []
                    ),
                    provider_key=self.provider_key,
                    provider_version="doc_digitization_v1",
                    provider_job_id=artifact.get("batch_job_id") or None,
                    provider_metadata={
                        "batch_index": artifact.get("batch_index"),
                        "source_filename": artifact.get("source_filename"),
                        "layout": artifact.get("layout") or {},
                    },
                )
            )
        if seen != set(by_number):
            raise DocumentProviderError(
                "Vision provider did not return every requested page.",
                category=ProviderErrorCategory.invalid_response,
                retryable=True,
            )
        return sorted(outputs, key=lambda page: page.page_number)


class _TypedPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str
    page_number: int = Field(..., ge=1)
    typed_markdown: str
    warnings: list[str] = Field(default_factory=list)


class _TypingBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[_TypedPageResult]


class SarvamTypingAdapter:
    provider_key = "sarvam_typing"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.model = model or settings.SARVAM_REASONING_MODEL

    async def type_pages(
        self,
        pages: Sequence[ExtractedPageText],
        *,
        document_type_hint: str | None,
        idempotency_key: str,
    ) -> Sequence[TypedPage]:
        if not self.api_key:
            raise DocumentProviderError(
                "Document typing provider is not configured.",
                category=ProviderErrorCategory.configuration,
                retryable=False,
            )
        typed_pages: list[TypedPage] = []
        batch_size = settings.DOCUMENT_TYPING_BATCH_SIZE
        for offset in range(0, len(pages), batch_size):
            batch = pages[offset : offset + batch_size]
            batch_key = f"{idempotency_key}:{offset // batch_size + 1}"
            for attempt in range(1, settings.DOCUMENT_PROCESSING_MAX_ATTEMPTS + 1):
                try:
                    typed_pages.extend(
                        await self._type_batch(
                            batch,
                            document_type_hint=document_type_hint,
                            idempotency_key=f"{batch_key}:{attempt}",
                        )
                    )
                    break
                except DocumentProviderError as exc:
                    if (
                        exc.category == ProviderErrorCategory.invalid_response
                        and attempt == settings.DOCUMENT_PROCESSING_MAX_ATTEMPTS
                    ):
                        typed_pages.extend(
                            _typing_fallback_pages(batch, error_message=str(exc))
                        )
                        break
                    if not exc.retryable or attempt == settings.DOCUMENT_PROCESSING_MAX_ATTEMPTS:
                        raise
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))
        return sorted(typed_pages, key=lambda page: page.page_number)

    async def _type_batch(
        self,
        pages: Sequence[ExtractedPageText],
        *,
        document_type_hint: str | None,
        idempotency_key: str,
    ) -> list[TypedPage]:
        expected = {str(page.page_id): page for page in pages}
        user_payload = {
            "document_type_hint": document_type_hint or "unknown",
            "pages": [
                {
                    "page_id": str(page.page_id),
                    "page_number": page.page_number,
                    "extracted_text": page.text,
                }
                for page in pages
            ],
        }
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _TYPING_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": 0.0,
            "max_tokens": settings.SARVAM_REASONING_MAX_TOKENS,
            "reasoning_effort": None,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "typed_legal_document_pages",
                    "strict": True,
                    "schema": _TypingBatchResult.model_json_schema(),
                },
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.SARVAM_REASONING_TIMEOUT_SECONDS)
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers={
                        "api-subscription-key": self.api_key,
                        "Content-Type": "application/json",
                        "Idempotency-Key": idempotency_key,
                    },
                    json=request,
                )
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise DocumentProviderError(
                "Document typing provider timed out.",
                category=ProviderErrorCategory.timeout,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise DocumentProviderError(
                "Document typing provider is unavailable.",
                category=ProviderErrorCategory.provider_unavailable,
                retryable=True,
            ) from exc
        if response.status_code in {401, 403}:
            raise DocumentProviderError(
                "Document typing provider authentication failed.",
                category=ProviderErrorCategory.authentication,
                retryable=False,
            )
        if response.status_code == 429:
            raise DocumentProviderError(
                "Document typing provider rate limit exceeded.",
                category=ProviderErrorCategory.rate_limit,
                retryable=True,
            )
        if response.status_code >= 500:
            raise DocumentProviderError(
                "Document typing provider is unavailable.",
                category=ProviderErrorCategory.provider_unavailable,
                retryable=True,
            )
        if response.status_code >= 400:
            raise DocumentProviderError(
                "Document typing provider rejected the input.",
                category=ProviderErrorCategory.invalid_input,
                retryable=False,
            )
        try:
            payload = response.json()
            content = _extract_typing_content(payload)
            validated = _TypingBatchResult.model_validate_json(content)
        except DocumentProviderError:
            raise
        except Exception as exc:
            raise DocumentProviderError(
                "Document typing provider returned an invalid response.",
                category=ProviderErrorCategory.invalid_response,
                retryable=True,
            ) from exc
        seen: set[str] = set()
        outputs: list[TypedPage] = []
        for result in validated.pages:
            source = expected.get(result.page_id)
            if source is None or result.page_id in seen or result.page_number != source.page_number:
                raise DocumentProviderError(
                    "Document typing provider returned mismatched page identities.",
                    category=ProviderErrorCategory.invalid_response,
                    retryable=False,
                )
            if not result.typed_markdown.strip():
                raise DocumentProviderError(
                    f"Document typing provider returned empty page {result.page_number}.",
                    category=ProviderErrorCategory.invalid_response,
                    retryable=True,
                )
            seen.add(result.page_id)
            outputs.append(
                TypedPage(
                    page_id=source.page_id,
                    page_number=source.page_number,
                    typed_markdown=result.typed_markdown.strip(),
                    warnings=tuple(result.warnings),
                    provider_key=self.provider_key,
                    provider_version=str(payload.get("model") or self.model),
                    provider_metadata={"usage": payload.get("usage")},
                )
            )
        if seen != set(expected):
            raise DocumentProviderError(
                "Document typing provider did not return every requested page.",
                category=ProviderErrorCategory.invalid_response,
                retryable=True,
            )
        return outputs


def _extract_typing_content(payload: dict) -> str:
    try:
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DocumentProviderError(
            "Document typing provider response is missing message content.",
            category=ProviderErrorCategory.invalid_response,
            retryable=True,
        ) from exc
    if isinstance(content, str) and content.strip():
        return content
    if choice.get("finish_reason") == "length":
        message = "Document typing provider exhausted its output token budget."
    else:
        message = "Document typing provider returned empty message content."
    raise DocumentProviderError(
        message,
        category=ProviderErrorCategory.invalid_response,
        retryable=True,
    )


def _typing_fallback_pages(
    pages: Sequence[ExtractedPageText], *, error_message: str
) -> list[TypedPage]:
    return [
        TypedPage(
            page_id=page.page_id,
            page_number=page.page_number,
            typed_markdown=page.text.strip(),
            warnings=tuple(
                [
                    *page.warnings,
                    "AI formatting was unavailable; review the extracted page carefully.",
                ]
            ),
            provider_key="extraction_fallback",
            provider_version="v1",
            provider_metadata={
                "fallback_reason": "typing_invalid_response",
                "provider_error": error_message,
                "source_provider": page.provider_key,
            },
        )
        for page in pages
        if page.text.strip()
    ]


def _translate_vision_error(exc: SarvamVisionError) -> DocumentProviderError:
    message = str(exc).lower()
    if "api_key" in message or "not configured" in message:
        return DocumentProviderError(
            "Vision provider is not configured.",
            category=ProviderErrorCategory.configuration,
            retryable=False,
        )
    return DocumentProviderError(
        "Vision provider could not process the requested pages.",
        category=ProviderErrorCategory.provider_unavailable,
        retryable=True,
    )


_TYPING_SYSTEM_PROMPT = """You are a legal document typing engine for Indian court documents.
Return only JSON matching the supplied schema. For every input page, return exactly one output
with the same page_id and page_number. Correct OCR noise and broken line wrapping, but preserve
all facts, names, dates, amounts, statutory sections, headings, lists, signatures, paragraph
order, Hindi/Devanagari text, and legal wording. Do not summarize, omit, translate, merge pages,
or invent missing text. Use Markdown headings for headings, GFM tables for tabular content, and
blank lines between distinct blocks. Keep repeatable rows at their actual length, omit sections
that are absent, and retain unfamiliar sections under their source heading. Put uncertainty in
warnings instead of guessing. typed_markdown must be a faithful typed version of that source
page."""
