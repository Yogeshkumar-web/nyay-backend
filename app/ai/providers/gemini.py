"""
app/ai/providers/gemini.py

Gemini provider — implements TextCompletion, Streaming, and Vision
in a single class using the google-genai SDK.

Supports:
  - complete()             → text extraction, reasoning, structured JSON
  - stream()               → draft generation (SSE)
  - complete_with_images() → OCR / multimodal (image bytes → text)
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import AsyncGenerator, Sequence

from app.ai.contracts import AIImageMessage, AIMessage, AIResponse
from app.ai.exceptions import (
    AIAuthError,
    AIInvalidResponseError,
    AIProviderError,
    AIRateLimitError,
    AITimeoutError,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2


def _classify_gemini_error(exc: Exception, provider: str) -> AIProviderError:
    """Map google-genai SDK exceptions to our typed errors."""
    msg = str(exc).lower()
    if "api_key" in msg or "authenticate" in msg or "permission" in msg:
        return AIAuthError(str(exc), provider=provider)
    if "quota" in msg or "rate" in msg or "429" in msg:
        return AIRateLimitError(str(exc), provider=provider)
    if "timeout" in msg or "deadline" in msg:
        return AITimeoutError(str(exc), provider=provider)
    return AIProviderError(str(exc), provider=provider, retryable=True)


class GeminiProvider:
    """
    Single provider class implementing all three AI protocols for Gemini.

    Usage:
        provider = GeminiProvider(api_key="...", model="gemini-2.5-flash")
        response = await provider.complete([AIMessage("user", "Hello")])
    """

    provider_key = "gemini"

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise AIAuthError(
                "GEMINI_API_KEY is not set. Add it to backend/.env",
                provider="gemini",
            )
        from google import genai  # lazy — optional dep

        self._client = genai.Client(api_key=api_key)
        self._model = model

    # ─────────────────────────────────────────────────────────
    # TextCompletionProvider
    # ─────────────────────────────────────────────────────────

    async def complete(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> AIResponse:
        """
        Non-streaming text completion.
        System message (role="system") is passed as system_instruction.
        """
        from google.genai import types as genai_types

        system_prompt, user_text = _split_messages(messages)

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                loop = asyncio.get_running_loop()
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self._client.models.generate_content(
                            model=self._model,
                            contents=user_text,
                            config=genai_types.GenerateContentConfig(
                                system_instruction=system_prompt or None,
                                max_output_tokens=max_tokens,
                                temperature=temperature,
                            ),
                        ),
                    ),
                    timeout=90.0,
                )

                text = response.text
                if not text:
                    raise AIInvalidResponseError(
                        "Gemini returned empty response", provider="gemini"
                    )

                usage = getattr(response, "usage_metadata", None)
                return AIResponse(
                    text=text,
                    provider="gemini",
                    model=self._model,
                    input_tokens=getattr(usage, "prompt_token_count", None),
                    output_tokens=getattr(usage, "candidates_token_count", None),
                )

            except AIProviderError:
                raise
            except Exception as exc:
                typed = _classify_gemini_error(exc, "gemini")
                logger.warning(
                    "Gemini complete attempt %d/%d failed: %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt == _MAX_RETRIES or not typed.retryable:
                    raise typed from exc
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)

        raise RuntimeError("Gemini complete: unreachable")  # pragma: no cover

    # ─────────────────────────────────────────────────────────
    # StreamingTextProvider
    # ─────────────────────────────────────────────────────────

    async def stream(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 4000,
    ) -> AsyncGenerator[str, None]:
        """
        Token-by-token streaming via generate_content_stream.
        Uses threading + queue to bridge sync SDK into async.
        """
        from google.genai import types as genai_types

        system_prompt, user_text = _split_messages(messages)
        model = self._model
        client = self._client

        result_queue: queue.Queue[str | Exception | object] = queue.Queue()
        _DONE = object()

        def _run_sync() -> None:
            try:
                response = client.models.generate_content_stream(
                    model=model,
                    contents=user_text,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_prompt or None,
                        max_output_tokens=max_tokens,
                    ),
                )
                for chunk in response:
                    if chunk.text:
                        result_queue.put(chunk.text)
            except Exception as exc:
                result_queue.put(exc)
            finally:
                result_queue.put(_DONE)

        thread = threading.Thread(target=_run_sync, daemon=True)
        thread.start()

        loop = asyncio.get_running_loop()
        while True:
            item = await loop.run_in_executor(None, result_queue.get)
            if item is _DONE:
                break
            if isinstance(item, Exception):
                raise _classify_gemini_error(item, "gemini") from item
            yield item  # type: ignore[misc]

    # ─────────────────────────────────────────────────────────
    # VisionCompletionProvider
    # ─────────────────────────────────────────────────────────

    async def complete_with_images(
        self,
        message: AIImageMessage,
        *,
        max_tokens: int = 2000,
    ) -> AIResponse:
        """
        Multimodal completion — sends image bytes + text to Gemini.
        Uses inline_data (no GCS upload needed for images < 20 MB).
        """
        from google.genai import types as genai_types

        parts: list = []

        # Add each image as an inline Part
        for img_bytes, mime_type in zip(message.images, message.mime_types):
            parts.append(
                genai_types.Part(
                    inline_data=genai_types.Blob(
                        mime_type=mime_type,
                        data=img_bytes,
                    )
                )
            )

        # Add the text prompt last
        parts.append(genai_types.Part(text=message.text))

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                loop = asyncio.get_running_loop()
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self._client.models.generate_content(
                            model=self._model,
                            contents=[genai_types.Content(role="user", parts=parts)],
                            config=genai_types.GenerateContentConfig(
                                max_output_tokens=max_tokens,
                            ),
                        ),
                    ),
                    timeout=120.0,
                )

                text = response.text
                if not text:
                    raise AIInvalidResponseError(
                        "Gemini Vision returned empty response", provider="gemini"
                    )

                usage = getattr(response, "usage_metadata", None)
                return AIResponse(
                    text=text,
                    provider="gemini",
                    model=self._model,
                    input_tokens=getattr(usage, "prompt_token_count", None),
                    output_tokens=getattr(usage, "candidates_token_count", None),
                )

            except AIProviderError:
                raise
            except Exception as exc:
                typed = _classify_gemini_error(exc, "gemini")
                logger.warning(
                    "Gemini vision attempt %d/%d failed: %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt == _MAX_RETRIES or not typed.retryable:
                    raise typed from exc
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)

        raise RuntimeError("Gemini vision: unreachable")  # pragma: no cover


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _split_messages(messages: Sequence[AIMessage]) -> tuple[str, str]:
    """
    Split messages into (system_prompt, user_text).
    Gemini takes system prompt separately via system_instruction.
    Multiple user messages are concatenated.
    """
    system_parts: list[str] = []
    user_parts: list[str] = []

    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        else:
            user_parts.append(msg.content)

    return "\n\n".join(system_parts), "\n\n".join(user_parts)
