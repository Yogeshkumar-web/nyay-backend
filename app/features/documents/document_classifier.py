from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.features.documents.digital_extractor import (
    DOCX_MIME,
    IMAGE_MIME_TYPES,
    PDF_MIME,
    PdfClassification,
    classify_pdf,
    validate_file_signature,
)
from app.features.documents.models import ProcessingRoute


@dataclass(frozen=True)
class Classification:
    route: ProcessingRoute
    details: dict[str, Any]
    pdf: PdfClassification | None = None


def classify_document(file_bytes: bytes, mime_type: str) -> Classification:
    validate_file_signature(file_bytes, mime_type)
    if mime_type in IMAGE_MIME_TYPES:
        return Classification(
            route=ProcessingRoute.scanned_ocr,
            details={"classifier": "mime_route_v1", "page_count": 1},
        )
    if mime_type == DOCX_MIME:
        return Classification(
            route=ProcessingRoute.digital_extract,
            details={"classifier": "mime_route_v1", "format": "docx"},
        )
    if mime_type == PDF_MIME:
        pdf = classify_pdf(file_bytes)
        return Classification(
            route=ProcessingRoute(pdf.route),
            details=pdf.details(),
            pdf=pdf,
        )
    raise ValueError("Unsupported document type.")
