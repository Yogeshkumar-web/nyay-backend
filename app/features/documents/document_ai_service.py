from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.api_core import exceptions as google_exceptions
from google.cloud import documentai
from google.oauth2 import service_account

from app.core.config import settings


MAX_ONLINE_BYTES = 40 * 1024 * 1024
MAX_TRANSIENT_RETRIES = 3


class DocumentAiError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class DocumentAiResult:
    text: str
    language: str
    page_count: int
    artifact: dict[str, Any]


class DocumentAiOcrService:
    def __init__(self):
        self._validate_config()
        credential_path = _credential_path(settings.GOOGLE_APPLICATION_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_file(
            credential_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        self.client = documentai.DocumentProcessorServiceClient(
            credentials=credentials,
            client_options={
                "api_endpoint": f"{settings.GOOGLE_LOCATION}-documentai.googleapis.com"
            },
        )
        self.processor_name = self.client.processor_path(
            settings.GOOGLE_PROJECT_ID,
            settings.GOOGLE_LOCATION,
            settings.GOOGLE_DOCAI_PROCESSOR_ID,
        )

    async def process(
        self,
        file_bytes: bytes,
        mime_type: str,
        *,
        original_page_numbers: tuple[int, ...] | None = None,
    ) -> DocumentAiResult:
        if len(file_bytes) > MAX_ONLINE_BYTES:
            raise DocumentAiError(
                "Document chunk exceeds Google Document AI's 40 MB online limit."
            )

        for attempt in range(1, MAX_TRANSIENT_RETRIES + 1):
            try:
                document = await asyncio.to_thread(
                    self._process_sync,
                    file_bytes,
                    mime_type,
                )
                return _to_result(document, original_page_numbers)
            except Exception as exc:
                error = _translate_provider_error(exc)
                if not error.retryable or attempt == MAX_TRANSIENT_RETRIES:
                    raise error from exc
                await asyncio.sleep((2 ** (attempt - 1)) + random.uniform(0, 0.5))

        raise DocumentAiError("Google Document AI request failed.")

    def _process_sync(self, file_bytes: bytes, mime_type: str):
        ocr_config = documentai.OcrConfig(
            enable_native_pdf_parsing=True,
            enable_image_quality_scores=True,
            hints=documentai.OcrConfig.Hints(language_hints=["hi", "en"]),
        )
        request = documentai.ProcessRequest(
            name=self.processor_name,
            raw_document=documentai.RawDocument(
                content=file_bytes,
                mime_type=mime_type,
            ),
            process_options=documentai.ProcessOptions(ocr_config=ocr_config),
        )
        return self.client.process_document(request=request).document

    @staticmethod
    def _validate_config() -> None:
        missing = [
            name
            for name in (
                "GOOGLE_PROJECT_ID",
                "GOOGLE_LOCATION",
                "GOOGLE_DOCAI_PROCESSOR_ID",
                "GOOGLE_APPLICATION_CREDENTIALS",
            )
            if not getattr(settings, name, "")
        ]
        if missing:
            raise DocumentAiError(
                f"Google Document AI configuration is incomplete: {', '.join(missing)}."
            )
        credential_path = _credential_path(settings.GOOGLE_APPLICATION_CREDENTIALS)
        if not Path(credential_path).is_file():
            raise DocumentAiError("Google Document AI credential file was not found.")


def _credential_path(configured_path: str) -> str:
    path = Path(configured_path)
    if path.is_absolute():
        return str(path)
    backend_root = Path(__file__).resolve().parents[3]
    return str((backend_root / path).resolve())


def _translate_provider_error(exc: Exception) -> DocumentAiError:
    if isinstance(exc, DocumentAiError):
        return exc
    if isinstance(
        exc,
        (
            google_exceptions.DeadlineExceeded,
            google_exceptions.InternalServerError,
            google_exceptions.ResourceExhausted,
            google_exceptions.ServiceUnavailable,
            google_exceptions.TooManyRequests,
        ),
    ):
        return DocumentAiError(
            "Google Document AI is temporarily unavailable. Retry shortly.",
            retryable=True,
        )
    if isinstance(exc, google_exceptions.InvalidArgument):
        message = str(exc)
        if "PAGE_LIMIT_EXCEEDED" in message:
            return DocumentAiError("Document chunk exceeds the OCR page limit.")
        if "DOCUMENT_SIZE_LIMIT_EXCEEDED" in message:
            return DocumentAiError("Document chunk exceeds the OCR size limit.")
        return DocumentAiError("Google Document AI rejected the document.")
    if isinstance(exc, google_exceptions.PermissionDenied):
        return DocumentAiError("Google Document AI permission was denied.")
    if isinstance(exc, google_exceptions.Unauthenticated):
        return DocumentAiError("Google Document AI credentials are invalid.")
    return DocumentAiError("Google Document AI request failed.")


def _to_result(document, original_page_numbers: tuple[int, ...] | None) -> DocumentAiResult:
    pages: list[dict[str, Any]] = []
    language_scores: dict[str, float] = {}
    for index, page in enumerate(document.pages):
        page_number = (
            original_page_numbers[index]
            if original_page_numbers and index < len(original_page_numbers)
            else int(page.page_number or index + 1)
        )
        for language in page.detected_languages:
            language_scores[language.language_code] = language_scores.get(
                language.language_code, 0.0
            ) + float(language.confidence or 0.0)
        pages.append(_page_artifact(page, document.text or "", page_number))

    language = (
        max(language_scores, key=language_scores.get) if language_scores else "und"
    )
    return DocumentAiResult(
        text=(document.text or "").strip(),
        language=language,
        page_count=len(pages),
        artifact={
            "schema_version": 1,
            "provider": "google_document_ai",
            "processor_role": "enterprise_ocr",
            "full_text": (document.text or "").strip(),
            "pages": pages,
        },
    )


def _page_artifact(page, full_text: str, page_number: int) -> dict[str, Any]:
    return {
        "page_number": page_number,
        "source": "google_document_ai",
        "text": _layout_text(page.layout, full_text) if page.layout else "",
        "detected_languages": [
            {
                "language_code": language.language_code,
                "confidence": round(float(language.confidence or 0.0), 4),
            }
            for language in page.detected_languages
        ],
        "image_quality_score": _image_quality_score(page),
        "blocks": [_layout_artifact(item.layout, full_text) for item in page.blocks],
        "paragraphs": [
            _layout_artifact(item.layout, full_text) for item in page.paragraphs
        ],
        "lines": [_layout_artifact(item.layout, full_text) for item in page.lines],
        "tables": [_table_artifact(table, full_text) for table in page.tables],
        "form_fields": [
            {
                "name": _layout_text(field.field_name, full_text),
                "value": _layout_text(field.field_value, full_text),
                "confidence": round(float(field.field_value.confidence or 0.0), 4),
            }
            for field in page.form_fields
        ],
    }


def _layout_artifact(layout, full_text: str) -> dict[str, Any]:
    return {
        "text": _layout_text(layout, full_text),
        "confidence": round(float(layout.confidence or 0.0), 4),
        "bounding_poly": _bounding_poly(layout.bounding_poly),
    }


def _table_artifact(table, full_text: str) -> dict[str, Any]:
    return {
        "header_rows": [_row_artifact(row, full_text) for row in table.header_rows],
        "body_rows": [_row_artifact(row, full_text) for row in table.body_rows],
    }


def _row_artifact(row, full_text: str) -> list[dict[str, Any]]:
    return [
        {
            "text": _layout_text(cell.layout, full_text),
            "confidence": round(float(cell.layout.confidence or 0.0), 4),
        }
        for cell in row.cells
    ]


def _layout_text(layout, full_text: str) -> str:
    segments = getattr(getattr(layout, "text_anchor", None), "text_segments", ())
    return "".join(
        full_text[int(segment.start_index or 0) : int(segment.end_index)]
        for segment in segments
    ).strip()


def _bounding_poly(poly) -> list[dict[str, float]]:
    vertices = getattr(poly, "normalized_vertices", ())
    return [
        {"x": round(float(vertex.x or 0.0), 6), "y": round(float(vertex.y or 0.0), 6)}
        for vertex in vertices
    ]


def _image_quality_score(page) -> float | None:
    scores = getattr(page, "image_quality_scores", None)
    if scores is None:
        return None
    quality_score = getattr(scores, "quality_score", None)
    return round(float(quality_score), 4) if quality_score is not None else None
