"""
app/ai/client.py

Singleton factory for the active AI provider.
Provider is selected via settings.AI_PROVIDER at import time.

Usage:
    from app.ai.client import get_ai_client, get_vision_ai_client

    client = get_ai_client()
    response = await client.complete([AIMessage(role="user", content="Hello")])

    # For multimodal (OCR etc.)
    vision = get_vision_ai_client()
    response = await vision.complete_with_images(AIImageMessage(...))
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Union

from app.ai.contracts import (
    StreamingTextProvider,
    TextCompletionProvider,
    VisionCompletionProvider,
)

logger = logging.getLogger(__name__)

# Type alias — the main client implements both text + streaming
AIClient = Union[TextCompletionProvider, StreamingTextProvider]


@lru_cache(maxsize=1)
def get_ai_client() -> AIClient:
    """
    Returns the singleton AI client for text + streaming tasks.
    Controlled by settings.AI_PROVIDER ("gemini" | "claude" | "deepseek" | "openai").

    lru_cache ensures the provider object (and its HTTP connection pool)
    is created only once per process.
    """
    from app.core.config import settings

    provider = (settings.AI_PROVIDER or "gemini").lower()
    logger.info("Initialising AI client: provider=%s", provider)

    if provider == "gemini":
        from app.ai.providers.gemini import GeminiProvider

        return GeminiProvider(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL or "gemini-2.5-flash",
        )

    if provider == "claude":
        # Claude provider — add app/ai/providers/claude.py when needed
        raise NotImplementedError(
            "Claude provider not yet in app/ai/. "
            "Use AI_PROVIDER=gemini or implement app/ai/providers/claude.py"
        )

    if provider in ("deepseek", "openai"):
        raise NotImplementedError(
            f"{provider} provider not yet in app/ai/. "
            "Implement app/ai/providers/deepseek.py or openai.py when needed."
        )

    raise ValueError(
        f"Unknown AI_PROVIDER={provider!r}. "
        "Supported values: gemini | claude | deepseek | openai"
    )


@lru_cache(maxsize=1)
def get_vision_ai_client() -> VisionCompletionProvider:
    """
    Returns the singleton AI client for vision / multimodal tasks.
    Controlled by settings.VISION_AI_PROVIDER (defaults to same as AI_PROVIDER).

    Currently only Gemini supports vision in this layer.
    """
    from app.core.config import settings

    # Fall back to AI_PROVIDER if VISION_AI_PROVIDER is not explicitly set
    provider = getattr(settings, "VISION_AI_PROVIDER", None) or settings.AI_PROVIDER
    provider = (provider or "gemini").lower()

    logger.info("Initialising Vision AI client: provider=%s", provider)

    if provider == "gemini":
        from app.ai.providers.gemini import GeminiProvider

        # Vision tasks use the flash model (fast + multimodal)
        vision_model = (
            getattr(settings, "GEMINI_VISION_MODEL", None)
            or settings.GEMINI_MODEL
            or "gemini-2.0-flash"
        )
        return GeminiProvider(
            api_key=settings.GEMINI_API_KEY,
            model=vision_model,
        )

    raise NotImplementedError(
        f"Vision AI provider {provider!r} not yet implemented in app/ai/."
    )
