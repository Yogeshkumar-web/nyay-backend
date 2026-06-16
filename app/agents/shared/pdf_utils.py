"""
PDF → batched page images utility.
Uses pymupdf (fitz) which is already installed — no extra dependency needed.
Shared by all agents that need visual document access.
"""
import base64
import logging
from typing import NamedTuple

import fitz  # pymupdf

logger = logging.getLogger(__name__)

# 150 DPI: Claude reads text clearly; token cost stays manageable
# (~1200 tokens per page at this resolution)
DPI = 150

# 10 pages × ~1200 tokens = ~12k tokens per batch — comfortable for Claude
BATCH_SIZE = 10


class PageBatch(NamedTuple):
    batch_number: int  # 1-indexed
    page_start: int  # 1-indexed, inclusive
    page_end: int  # 1-indexed, inclusive
    images_b64: list[str]


def pdf_bytes_to_batches(
    pdf_bytes: bytes,
    dpi: int = DPI,
    batch_size: int = BATCH_SIZE,
) -> list[PageBatch]:
    """
    Convert raw PDF bytes → list of PageBatch.
    Each batch contains up to `batch_size` pages rendered as base64 PNG strings.
    Returns empty list if PDF cannot be opened or has no pages.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        logger.exception("pdf_bytes_to_batches: failed to open PDF")
        return []

    if doc.page_count == 0:
        doc.close()
        return []

    mat = fitz.Matrix(dpi / 72, dpi / 72)
    all_images: list[str] = []

    for page in doc:
        try:
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pix.tobytes("png")
            all_images.append(base64.b64encode(png_bytes).decode())
        except Exception:
            logger.warning(
                "pdf_bytes_to_batches: failed to render page %d", page.number
            )
            all_images.append("")  # blank placeholder keeps page numbering intact

    doc.close()

    batches: list[PageBatch] = []
    for i in range(0, len(all_images), batch_size):
        chunk = all_images[i : i + batch_size]
        batches.append(
            PageBatch(
                batch_number=len(batches) + 1,
                page_start=i + 1,
                page_end=i + len(chunk),
                images_b64=chunk,
            )
        )

    logger.debug(
        "pdf_bytes_to_batches: %d pages → %d batches (dpi=%d, batch_size=%d)",
        len(all_images),
        len(batches),
        dpi,
        batch_size,
    )
    return batches


def images_to_claude_content(images_b64: list[str]) -> list[dict]:
    """
    Convert a list of base64 PNG strings → Anthropic multimodal content blocks.
    Blank placeholders (empty strings) are silently skipped.
    """
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img,
            },
        }
        for img in images_b64
        if img  # skip blank placeholders from failed page renders
    ]
