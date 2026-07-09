from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.features.documents.digital_extractor import (
    DOCX_MIME,
    PDF_MIME,
    build_pdf_chunks,
    extract_digital_pdf,
    extract_docx,
)
from app.features.documents.document_classifier import classify_document
from app.features.documents.models import ProcessingRoute


class OcrProviderNotConfigured(RuntimeError):
    """Raised until a local OCR provider is wired into document processing."""


@dataclass(frozen=True)
class OcrResult:
    text: str
    language: str
    page_count: int
    artifact: dict[str, Any]


class OcrProvider(Protocol):
    async def process(
        self,
        file_bytes: bytes,
        mime_type: str,
        *,
        original_page_numbers: tuple[int, ...] | None = None,
    ) -> OcrResult: ...


class LocalOcrProvider:
    async def process(
        self,
        file_bytes: bytes,
        mime_type: str,
        *,
        original_page_numbers: tuple[int, ...] | None = None,
    ) -> OcrResult:
        raise OcrProviderNotConfigured(
            "Local OCR provider is not configured yet. Wire an Indian-language "
            "OCR model before processing scanned documents."
        )


@dataclass(frozen=True)
class ProcessingResult:
    route: ProcessingRoute
    source_text: str
    source_artifact: dict[str, Any]
    classification_details: dict[str, Any]
    is_scanned: bool
    ocr_text: str | None = None
    ocr_language: str | None = None
    ocr_artifact: dict[str, Any] | None = None
    page_count: int | None = None


async def process_document_bytes(
    file_bytes: bytes,
    mime_type: str,
    *,
    ocr_service: OcrProvider | None = None,
) -> ProcessingResult:
    classification = classify_document(file_bytes, mime_type)
    if mime_type == DOCX_MIME:
        text, artifact = extract_docx(file_bytes)
        return ProcessingResult(
            route=classification.route,
            source_text=text,
            source_artifact=artifact,
            classification_details=classification.details,
            is_scanned=False,
        )

    if (
        mime_type == PDF_MIME
        and classification.route == ProcessingRoute.digital_extract
    ):
        text, artifact = extract_digital_pdf(file_bytes)
        return ProcessingResult(
            route=classification.route,
            source_text=text,
            source_artifact=artifact,
            classification_details=classification.details,
            is_scanned=False,
            page_count=classification.pdf.page_count if classification.pdf else None,
        )

    service = ocr_service or LocalOcrProvider()
    if mime_type == PDF_MIME:
        if classification.pdf is None:
            raise RuntimeError("PDF classification details are missing.")
        scanned_pages = classification.pdf.scanned_pages
        chunks = build_pdf_chunks(file_bytes, scanned_pages)
        scanned_artifacts: list[dict[str, Any]] = []
        language = "und"
        for page_numbers, chunk_bytes in chunks:
            result = await service.process(
                chunk_bytes,
                PDF_MIME,
                original_page_numbers=page_numbers,
            )
            scanned_artifacts.extend(result.artifact["pages"])
            if result.language != "und":
                language = result.language
        pages = _merge_pdf_pages(classification.pdf.page_text, scanned_artifacts)
        text = _join_page_text(pages)
        artifact = {
            "schema_version": 1,
            "provider": "local_ocr",
            "processor_role": "document_ocr",
            "route": classification.route.value,
            "pages": pages,
        }
        return ProcessingResult(
            route=classification.route,
            source_text=text,
            source_artifact=artifact,
            classification_details={
                **classification.details,
                "ocr_chunk_count": len(chunks),
            },
            is_scanned=True,
            ocr_text=text,
            ocr_language=language,
            ocr_artifact=artifact,
            page_count=classification.pdf.page_count,
        )

    result = await service.process(file_bytes, mime_type)
    return ProcessingResult(
        route=classification.route,
        source_text=result.text,
        source_artifact=result.artifact,
        classification_details=classification.details,
        is_scanned=True,
        ocr_text=result.text,
        ocr_language=result.language,
        ocr_artifact=result.artifact,
        page_count=result.page_count,
    )


def _merge_pdf_pages(
    local_page_text: dict[int, str],
    scanned_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scanned_by_page = {int(page["page_number"]): page for page in scanned_artifacts}
    pages: list[dict[str, Any]] = []
    for page_number in sorted(local_page_text):
        if page_number in scanned_by_page:
            pages.append(scanned_by_page[page_number])
        else:
            pages.append(
                {
                    "page_number": page_number,
                    "source": "embedded_pdf_text",
                    "text": local_page_text[page_number],
                }
            )
    return pages


def _join_page_text(pages: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        str(page.get("text") or "").strip()
        for page in pages
        if str(page.get("text") or "").strip()
    ).strip()
