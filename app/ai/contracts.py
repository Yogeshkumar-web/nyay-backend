"""
app/ai/contracts.py

Shared data-classes and Protocol interfaces for all AI providers.
Services depend on these abstractions — never on concrete provider classes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncGenerator, Protocol, Sequence, runtime_checkable


# ─────────────────────────────────────────────────────────────
# Data-classes
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AIMessage:
    """A single turn in a conversation."""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class AIImageMessage:
    """A multimodal message with inline image bytes."""

    role: str
    text: str
    images: tuple[bytes, ...]
    mime_types: tuple[str, ...]  # e.g. ("image/png", "image/jpeg")


@dataclass(frozen=True)
class AIResponse:
    """Normalised response from any AI provider."""

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_metadata: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# Provider Protocols (ISP — each capability is its own Protocol)
# ─────────────────────────────────────────────────────────────


@runtime_checkable
class TextCompletionProvider(Protocol):
    """Synchronous (non-streaming) text generation."""

    provider_key: str

    async def complete(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> AIResponse: ...


@runtime_checkable
class StreamingTextProvider(Protocol):
    """Token-by-token streaming text generation."""

    provider_key: str

    async def stream(
        self,
        messages: Sequence[AIMessage],
        *,
        max_tokens: int = 4000,
    ) -> AsyncGenerator[str, None]: ...


@runtime_checkable
class VisionCompletionProvider(Protocol):
    """Multimodal: image bytes → text (OCR, layout understanding)."""

    provider_key: str

    async def complete_with_images(
        self,
        message: AIImageMessage,
        *,
        max_tokens: int = 2000,
    ) -> AIResponse: ...
