from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Sequence

from app.core.config import settings
from app.features.documents.providers.contracts import (
    DocumentProviderError,
    ExtractedPageText,
    PageInput,
    ProviderErrorCategory,
    TypedPage,
)

logger = logging.getLogger(__name__)

_OCR_PROMPT = """You are an OCR and Document Digitization engine for legal and court documents.
Transcribe and extract the exact text visible on this page with maximum fidelity and precision.
Preserve all text, headings, numbers, dates, party names, signatures, stamps, Hindi/Devanagari, and English.
Output in clean Markdown formatting (use headings # ##, tables, lists, and paragraphs).
Do not summarize, do not omit anything, and do not add any commentary or introductory remarks."""

_TYPING_PROMPT = """You are an expert legal document typing and formatting engine for Indian court documents.
Review and format the following extracted page text into clean, faithful, beautifully formatted Markdown.
Fix obvious OCR line-wrap breaks, garbled characters, or misread text based on legal context.
Preserve all facts, numbers, dates, statutory sections, party names, case titles, Hindi/Devanagari text, and structure.
Use Markdown headings (#, ##, ###), bullet lists, and GitHub Flavored Markdown tables where appropriate.
Do not summarize, do not omit any content, and do not invent facts. Output ONLY the typed document markdown."""


def _extract_retry_delay(err_msg: str, default: float = 12.0) -> float:
    """Extract retry delay seconds from Gemini 429 error message if present."""
    # Look for patterns like 'retry in 59.75s' or 'retry in 60s' or 'retryDelay: 59s'
    match = re.search(r"retry(?:\s+in|\s*delay[:=]?)\s*([\d.]+)\s*s?", err_msg, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1))
            return max(val + 1.0, 5.0)  # add 1s safety buffer
        except (ValueError, TypeError):
            pass
    return default


class GeminiVisionAdapter:
    """Extracts text from scanned document pages or images using Gemini Multimodal Vision API."""

    provider_key = "gemini_vision"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or getattr(settings, "GEMINI_VISION_MODEL", None) or settings.GEMINI_MODEL or "gemini-3.6-flash"

    async def extract_pages(
        self,
        pages: Sequence[PageInput],
        *,
        idempotency_key: str,
    ) -> Sequence[ExtractedPageText]:
        del idempotency_key
        if not self.api_key:
            raise DocumentProviderError(
                "GEMINI_API_KEY is not configured.",
                category=ProviderErrorCategory.configuration,
                retryable=False,
            )

        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=self.api_key)
        results: list[ExtractedPageText] = []

        # Gemini Free Tier limit is 5 requests/minute (1 req every ~12 seconds).
        # We enforce a token bucket / rate limiter and intelligent 429 retry.
        rate_limit_lock = asyncio.Lock()
        last_request_time = [0.0]
        min_request_interval = 12.5  # seconds between requests to guarantee <= 5 req/min

        async def _throttle():
            async with rate_limit_lock:
                now = time.monotonic()
                elapsed = now - last_request_time[0]
                if elapsed < min_request_interval:
                    wait_time = min_request_interval - elapsed
                    logger.info("Gemini rate throttle: waiting %.2fs before next page request", wait_time)
                    await asyncio.sleep(wait_time)
                last_request_time[0] = time.monotonic()

        async def _extract_single(page: PageInput) -> ExtractedPageText:
            image_blob = genai_types.Blob(
                mime_type=page.mime_type,
                data=page.content,
            )
            contents = [
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(inline_data=image_blob),
                        genai_types.Part(text=_OCR_PROMPT),
                    ],
                )
            ]

            loop = asyncio.get_running_loop()
            max_attempts = max(settings.DOCUMENT_PROCESSING_MAX_ATTEMPTS, 5)

            for attempt in range(1, max_attempts + 1):
                await _throttle()
                try:
                    resp = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: client.models.generate_content(
                                model=self.model,
                                contents=contents,
                                config=genai_types.GenerateContentConfig(
                                    temperature=0.1,
                                    max_output_tokens=4096,
                                ),
                            ),
                        ),
                        timeout=120.0,
                    )
                    text = (resp.text or "").strip()
                    return ExtractedPageText(
                        page_id=page.page_id,
                        page_number=page.page_number,
                        text=text,
                        method="vision_ocr",
                        confidence=0.98 if text else 0.0,
                        provider_key=self.provider_key,
                        provider_version=self.model,
                    )
                except Exception as exc:
                    err_msg = str(exc)
                    is_429 = "429" in err_msg or "resource_exhausted" in err_msg.lower() or "quota" in err_msg.lower()

                    if is_429:
                        delay = _extract_retry_delay(err_msg, default=15.0)
                        logger.warning(
                            "Gemini Vision 429 quota hit on page %d (attempt %d/%d). Sleeping for %.1fs as instructed by Gemini API: %s",
                            page.page_number,
                            attempt,
                            max_attempts,
                            delay,
                            err_msg[:120],
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "Gemini Vision extract page %d failed attempt %d: %s",
                            page.page_number,
                            attempt,
                            exc,
                        )
                        await asyncio.sleep(min(2 ** (attempt - 1), 8))

                    if attempt == max_attempts:
                        category = (
                            ProviderErrorCategory.rate_limit
                            if is_429
                            else ProviderErrorCategory.provider_unavailable
                        )
                        raise DocumentProviderError(
                            f"Gemini Vision failed to extract page {page.page_number}: {exc}",
                            category=category,
                            retryable=True,
                        ) from exc

            raise RuntimeError("Unreachable")

        # Process sequentially to strictly honor free-tier request quotas
        for page in pages:
            res = await _extract_single(page)
            results.append(res)

        return sorted(results, key=lambda p: p.page_number)


class GeminiTypingAdapter:
    """Types and formats extracted page texts into faithful clean Markdown using Gemini API."""

    provider_key = "gemini_typing"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_MODEL or "gemini-3.6-flash"

    async def type_pages(
        self,
        pages: Sequence[ExtractedPageText],
        *,
        document_type_hint: str | None,
        idempotency_key: str,
    ) -> Sequence[TypedPage]:
        del idempotency_key
        if not self.api_key:
            raise DocumentProviderError(
                "GEMINI_API_KEY is not configured.",
                category=ProviderErrorCategory.configuration,
                retryable=False,
            )

        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=self.api_key)
        typed_results: list[TypedPage] = []

        rate_limit_lock = asyncio.Lock()
        last_request_time = [0.0]
        min_request_interval = 12.5

        async def _throttle():
            async with rate_limit_lock:
                now = time.monotonic()
                elapsed = now - last_request_time[0]
                if elapsed < min_request_interval:
                    wait_time = min_request_interval - elapsed
                    logger.info("Gemini typing throttle: waiting %.2fs before next request", wait_time)
                    await asyncio.sleep(wait_time)
                last_request_time[0] = time.monotonic()

        async def _type_single(page: ExtractedPageText) -> TypedPage:
            if not page.text.strip():
                return TypedPage(
                    page_id=page.page_id,
                    page_number=page.page_number,
                    typed_markdown="",
                    provider_key=self.provider_key,
                    provider_version=self.model,
                )

            user_prompt = f"Document Type Hint: {document_type_hint or 'Legal Document'}\nPage: {page.page_number}\n\nExtracted Text:\n{page.text}"
            loop = asyncio.get_running_loop()
            max_attempts = max(settings.DOCUMENT_PROCESSING_MAX_ATTEMPTS, 5)

            for attempt in range(1, max_attempts + 1):
                await _throttle()
                try:
                    resp = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: client.models.generate_content(
                                model=self.model,
                                contents=user_prompt,
                                config=genai_types.GenerateContentConfig(
                                    system_instruction=_TYPING_PROMPT,
                                    temperature=0.1,
                                    max_output_tokens=4096,
                                ),
                            ),
                        ),
                        timeout=90.0,
                    )
                    typed_text = (resp.text or "").strip()
                    return TypedPage(
                        page_id=page.page_id,
                        page_number=page.page_number,
                        typed_markdown=typed_text,
                        provider_key=self.provider_key,
                        provider_version=self.model,
                    )
                except Exception as exc:
                    err_msg = str(exc)
                    is_429 = "429" in err_msg or "resource_exhausted" in err_msg.lower() or "quota" in err_msg.lower()

                    if is_429:
                        delay = _extract_retry_delay(err_msg, default=15.0)
                        logger.warning(
                            "Gemini Typing 429 quota hit on page %d (attempt %d/%d). Sleeping for %.1fs: %s",
                            page.page_number,
                            attempt,
                            max_attempts,
                            delay,
                            err_msg[:120],
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "Gemini typing page %d failed attempt %d: %s",
                            page.page_number,
                            attempt,
                            exc,
                        )
                        await asyncio.sleep(min(2 ** (attempt - 1), 8))

                    if attempt == max_attempts:
                        # Fallback to extracted text directly so user is never blocked
                        return TypedPage(
                            page_id=page.page_id,
                            page_number=page.page_number,
                            typed_markdown=page.text,
                            warnings=(f"Typing model error: {exc}",),
                            provider_key=self.provider_key,
                            provider_version=self.model,
                        )

            return TypedPage(
                page_id=page.page_id,
                page_number=page.page_number,
                typed_markdown=page.text,
                provider_key=self.provider_key,
                provider_version=self.model,
            )

        for page in pages:
            res = await _type_single(page)
            typed_results.append(res)

        return sorted(typed_results, key=lambda p: p.page_number)
