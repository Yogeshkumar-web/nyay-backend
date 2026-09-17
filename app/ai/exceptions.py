"""
app/ai/exceptions.py

Typed exceptions for AI provider errors.
Callers can catch AIProviderError for generic handling,
or specific subclasses for granular retry logic.
"""
from __future__ import annotations


class AIProviderError(RuntimeError):
    """Base class for all AI provider errors."""

    def __init__(self, message: str, *, provider: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class AIRateLimitError(AIProviderError):
    """429 / quota exceeded. Always retryable with backoff."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=True)


class AITimeoutError(AIProviderError):
    """Request timed out. Retryable."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=True)


class AIAuthError(AIProviderError):
    """Invalid API key / credentials. NOT retryable."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=False)


class AIInvalidResponseError(AIProviderError):
    """Provider returned unexpected / unparseable response. NOT retryable."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=False)
