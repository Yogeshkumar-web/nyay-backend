from app.core.config import settings
from app.features.documents.providers.contracts import (
    DocumentTypingProvider,
    VisionTextExtractor,
)
from app.features.documents.providers.gemini import (
    GeminiTypingAdapter,
    GeminiVisionAdapter,
)
from app.features.documents.providers.sarvam import (
    SarvamTypingAdapter,
    SarvamVisionAdapter,
)
from app.features.documents.google_document_ai import GoogleDocumentAiProcessor


def get_vision_provider() -> VisionTextExtractor:
    provider = (settings.DOCUMENT_VISION_PROVIDER or "gemini_vision").lower()
    if provider in ("gemini", "gemini_vision"):
        return GeminiVisionAdapter()
    if provider == "google_document_ai":
        return GoogleDocumentAiProcessor()
    if provider in ("sarvam", "sarvam_vision"):
        return SarvamVisionAdapter()
    raise RuntimeError(
        f"Unsupported document Vision provider: {settings.DOCUMENT_VISION_PROVIDER}"
    )


def get_typing_provider() -> DocumentTypingProvider:
    provider = (settings.DOCUMENT_TYPING_PROVIDER or "gemini").lower()
    if provider in ("gemini", "gemini_typing"):
        return GeminiTypingAdapter()
    if provider in ("sarvam", "sarvam_typing"):
        return SarvamTypingAdapter()
    raise RuntimeError(
        f"Unsupported document typing provider: {settings.DOCUMENT_TYPING_PROVIDER}"
    )


def get_document_ocr_provider():
    provider = (settings.DOCUMENT_VISION_PROVIDER or "gemini_vision").lower()
    if provider == "google_document_ai":
        return GoogleDocumentAiProcessor()
    # When using Gemini or page-by-page vision, whole-document external batch OCR is bypassed
    return None
