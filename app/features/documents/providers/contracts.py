from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


class ProviderErrorCategory(str, enum.Enum):
    configuration = "configuration"
    authentication = "authentication"
    rate_limit = "rate_limit"
    timeout = "timeout"
    invalid_input = "invalid_input"
    provider_unavailable = "provider_unavailable"
    invalid_response = "invalid_response"


class DocumentProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: ProviderErrorCategory,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass(frozen=True)
class PageInput:
    page_id: uuid.UUID
    page_number: int
    mime_type: str
    content: bytes
    checksum_sha256: str
    source_filename: str


@dataclass(frozen=True)
class ExtractedPageText:
    page_id: uuid.UUID
    page_number: int
    text: str
    method: str
    confidence: float | None = None
    language: str | None = None
    warnings: tuple[str, ...] = ()
    provider_key: str = "local"
    provider_version: str | None = None
    provider_job_id: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TypedPage:
    page_id: uuid.UUID
    page_number: int
    typed_markdown: str
    warnings: tuple[str, ...] = ()
    provider_key: str = "unknown"
    provider_version: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


class VisionTextExtractor(Protocol):
    provider_key: str

    async def extract_pages(
        self,
        pages: Sequence[PageInput],
        *,
        idempotency_key: str,
    ) -> Sequence[ExtractedPageText]: ...


class DocumentTypingProvider(Protocol):
    provider_key: str

    async def type_pages(
        self,
        pages: Sequence[ExtractedPageText],
        *,
        document_type_hint: str | None,
        idempotency_key: str,
    ) -> Sequence[TypedPage]: ...

