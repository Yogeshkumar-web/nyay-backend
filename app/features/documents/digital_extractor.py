from __future__ import annotations

import io
import hashlib
import re
import zipfile
from dataclasses import dataclass
from typing import Any, Iterator

import fitz
from docx import Document as DocxDocument
from docx.document import Document as DocxDocumentType
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph


PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/tiff", "image/webp"}
SUPPORTED_MIME_TYPES = {PDF_MIME, DOCX_MIME, *IMAGE_MIME_TYPES}
MIN_DIGITAL_PAGE_CHARS = 40


class DocumentInputError(ValueError):
    pass


@dataclass(frozen=True)
class PdfClassification:
    route: str
    page_count: int
    digital_pages: tuple[int, ...]
    scanned_pages: tuple[int, ...]
    page_text: dict[int, str]

    def details(self) -> dict[str, Any]:
        return {
            "classifier": "pymupdf_embedded_text_v1",
            "page_count": self.page_count,
            "digital_pages": list(self.digital_pages),
            "scanned_pages": list(self.scanned_pages),
            "min_digital_page_chars": MIN_DIGITAL_PAGE_CHARS,
        }


@dataclass(frozen=True)
class PageSplit:
    page_number: int
    filename: str
    mime_type: str
    content: bytes
    checksum: str
    size_bytes: int


def validate_file_signature(file_bytes: bytes, mime_type: str) -> None:
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise DocumentInputError("Unsupported document type.")
    if not file_bytes:
        raise DocumentInputError("Uploaded document is empty.")

    valid = {
        PDF_MIME: file_bytes.startswith(b"%PDF"),
        "image/jpeg": file_bytes.startswith(b"\xff\xd8\xff"),
        "image/png": file_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/tiff": file_bytes[:4] in {b"II*\x00", b"MM\x00*"},
        "image/webp": file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP",
    }
    if mime_type == DOCX_MIME:
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
                names = set(archive.namelist())
                is_valid = {
                    "[Content_Types].xml",
                    "word/document.xml",
                } <= names
        except zipfile.BadZipFile:
            is_valid = False
    else:
        is_valid = valid.get(mime_type, False)

    if not is_valid:
        raise DocumentInputError(
            "Uploaded file content does not match its declared document type."
        )


def classify_pdf(file_bytes: bytes) -> PdfClassification:
    try:
        pdf = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise DocumentInputError("PDF is corrupt or unreadable.") from exc

    try:
        if pdf.needs_pass:
            raise DocumentInputError("Password-protected PDFs are not supported.")
        if pdf.page_count == 0:
            raise DocumentInputError("PDF has no pages.")

        page_text: dict[int, str] = {}
        digital_pages: list[int] = []
        scanned_pages: list[int] = []
        for index, page in enumerate(pdf):
            page_number = index + 1
            text = _normalize_text(page.get_text("text"))
            page_text[page_number] = text
            if len(_meaningful_chars(text)) >= MIN_DIGITAL_PAGE_CHARS:
                digital_pages.append(page_number)
            else:
                scanned_pages.append(page_number)
    finally:
        pdf.close()

    route = "hybrid_extract"
    if not scanned_pages:
        route = "digital_extract"
    elif not digital_pages:
        route = "scanned_ocr"
    return PdfClassification(
        route=route,
        page_count=len(page_text),
        digital_pages=tuple(digital_pages),
        scanned_pages=tuple(scanned_pages),
        page_text=page_text,
    )


def extract_digital_pdf(file_bytes: bytes) -> tuple[str, dict[str, Any]]:
    classification = classify_pdf(file_bytes)
    pages = [
        {
            "page_number": page_number,
            "source": "embedded_pdf_text",
            "text": classification.page_text[page_number],
        }
        for page_number in classification.digital_pages
    ]
    text = _join_page_text(page["text"] for page in pages)
    if not text:
        raise DocumentInputError("PDF has no extractable digital text.")
    return text, {"schema_version": 1, "pages": pages}


def extract_docx(file_bytes: bytes) -> tuple[str, dict[str, Any]]:
    try:
        document = DocxDocument(io.BytesIO(file_bytes))
    except Exception as exc:
        raise DocumentInputError("DOCX is corrupt or unreadable.") from exc

    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            text = _normalize_text(block.text)
            if not text:
                continue
            style = block.style.name if block.style is not None else None
            blocks.append({"type": "paragraph", "style": style, "text": text})
            text_parts.append(text)
            continue

        rows: list[list[str]] = []
        for row in block.rows:
            cells = [_normalize_text(cell.text) for cell in row.cells]
            if any(cells):
                rows.append(cells)
        if rows:
            blocks.append({"type": "table", "rows": rows})
            text_parts.extend(" | ".join(cell for cell in row if cell) for row in rows)

    text = _join_page_text(text_parts)
    if not text:
        raise DocumentInputError("DOCX has no extractable text.")
    return text, {"schema_version": 1, "blocks": blocks}


def build_pdf_chunks(
    file_bytes: bytes,
    page_numbers: tuple[int, ...],
    *,
    max_pages: int = 15,
    max_bytes: int = 40 * 1024 * 1024,
) -> list[tuple[tuple[int, ...], bytes]]:
    if not page_numbers:
        return []
    try:
        source = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise DocumentInputError("PDF is corrupt or unreadable.") from exc

    chunks: list[tuple[tuple[int, ...], bytes]] = []
    try:
        for offset in range(0, len(page_numbers), max_pages):
            _append_pdf_chunk(
                source,
                page_numbers[offset : offset + max_pages],
                chunks,
                max_bytes=max_bytes,
            )
    finally:
        source.close()
    return chunks


def split_pdf_pages(file_bytes: bytes) -> list[PageSplit]:
    try:
        source = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise DocumentInputError("PDF is corrupt or unreadable.") from exc

    pages: list[PageSplit] = []
    try:
        if source.needs_pass:
            raise DocumentInputError("Password-protected PDFs are not supported.")
        if source.page_count == 0:
            raise DocumentInputError("PDF has no pages.")
        for index in range(source.page_count):
            page_number = index + 1
            page = source.load_page(index)
            matrix = fitz.Matrix(2, 2)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            content = pixmap.tobytes("png")
            pages.append(
                PageSplit(
                    page_number=page_number,
                    filename=f"page_{page_number:04d}.png",
                    mime_type="image/png",
                    content=content,
                    checksum=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
            )
    finally:
        source.close()
    return pages


def build_single_image_page(file_bytes: bytes, mime_type: str) -> PageSplit:
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/tiff": "tiff",
        "image/webp": "webp",
    }.get(mime_type)
    if extension is None:
        raise DocumentInputError("Unsupported image type.")
    return PageSplit(
        page_number=1,
        filename=f"page_0001.{extension}",
        mime_type=mime_type,
        content=file_bytes,
        checksum=hashlib.sha256(file_bytes).hexdigest(),
        size_bytes=len(file_bytes),
    )


def _append_pdf_chunk(
    source: fitz.Document,
    page_numbers: tuple[int, ...],
    chunks: list[tuple[tuple[int, ...], bytes]],
    *,
    max_bytes: int,
) -> None:
    chunk = fitz.open()
    try:
        for page_number in page_numbers:
            chunk.insert_pdf(source, from_page=page_number - 1, to_page=page_number - 1)
        chunk_bytes = chunk.tobytes(garbage=4, deflate=True)
    finally:
        chunk.close()

    if len(chunk_bytes) <= max_bytes:
        chunks.append((page_numbers, chunk_bytes))
        return
    if len(page_numbers) == 1:
        raise DocumentInputError(
            f"PDF page {page_numbers[0]} exceeds the 40 MB OCR chunk limit."
        )
    midpoint = len(page_numbers) // 2
    _append_pdf_chunk(source, page_numbers[:midpoint], chunks, max_bytes=max_bytes)
    _append_pdf_chunk(source, page_numbers[midpoint:], chunks, max_bytes=max_bytes)


def _iter_docx_blocks(document: DocxDocumentType) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _join_page_text(parts) -> str:
    return "\n\n".join(part for part in parts if part and part.strip()).strip()


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _meaningful_chars(text: str) -> str:
    return re.sub(r"\W+", "", text, flags=re.UNICODE)
