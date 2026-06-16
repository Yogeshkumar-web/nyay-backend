"""
Node 1 — fetch_and_batch_pages

Fetches the PDF from R2 via a presigned URL, renders each page to a base64
PNG image at 150 DPI, and groups them into batches of 10.

This node is fully deterministic — no LLM calls.

Failure modes (all handled gracefully via fetch_error):
  - 403 Forbidden   : presigned URL expired
  - Network timeout  : R2 unreachable
  - PDF parse error  : corrupted file
  - 0 pages          : empty / unreadable document

When fetch_error is set, downstream nodes automatically fall back to
text-only mode (they check state["page_batches"] before adding images).
"""
import logging

import httpx

from app.agents.shared.pdf_utils import pdf_bytes_to_batches
from app.agents.typing.state import TypingAgentState

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 30.0  # seconds — R2 presigned URLs are usually fast


async def fetch_and_batch_pages(state: TypingAgentState) -> TypingAgentState:
    # FIR uses a deterministic regex parser (fir_parser.py) — page images are
    # not needed at any stage of the pipeline. Skip the R2 fetch + PDF render
    # entirely to avoid unnecessary I/O and processing time.
    if state["document_type"] == "fir":
        logger.info(
            "fetch_and_batch_pages: FIR document — skipping image fetch "
            "(deterministic parser needs no visual input) | doc=%s",
            state["document_id"],
        )
        return {
            **state,
            "page_batches": [],
            "batch_count": 0,
            "fetch_error": None,
        }

    url = state["presigned_url"]

    if not url:
        logger.warning(
            "fetch_and_batch_pages: no presigned_url provided | doc=%s",
            state["document_id"],
        )
        return {
            **state,
            "page_batches": [],
            "batch_count": 0,
            "fetch_error": "No presigned URL provided — text-only mode",
        }

    # ── 1. Fetch PDF bytes ────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            pdf_bytes = resp.content
    except httpx.HTTPStatusError as exc:
        err = f"HTTP {exc.response.status_code} fetching PDF (URL may be expired)"
        logger.warning("fetch_and_batch_pages: %s | doc=%s", err, state["document_id"])
        return {
            **state,
            "page_batches": [],
            "batch_count": 0,
            "fetch_error": err,
        }
    except httpx.TimeoutException:
        err = "Timeout fetching PDF from R2 (30s limit)"
        logger.warning("fetch_and_batch_pages: %s | doc=%s", err, state["document_id"])
        return {
            **state,
            "page_batches": [],
            "batch_count": 0,
            "fetch_error": err,
        }
    except Exception as exc:
        err = f"Unexpected error fetching PDF: {exc}"
        logger.warning("fetch_and_batch_pages: %s | doc=%s", err, state["document_id"])
        return {
            **state,
            "page_batches": [],
            "batch_count": 0,
            "fetch_error": err,
        }

    # ── 2. PDF → PageBatch list ───────────────────────────────────────────
    batches = pdf_bytes_to_batches(pdf_bytes)

    if not batches:
        err = "PDF could not be parsed or has no renderable pages"
        logger.warning("fetch_and_batch_pages: %s | doc=%s", err, state["document_id"])
        return {
            **state,
            "page_batches": [],
            "batch_count": 0,
            "fetch_error": err,
        }

    # ── 3. Serialize NamedTuples → dicts (LangGraph state must be JSON-safe)
    serialized = [b._asdict() for b in batches]
    total_pages = sum(len(b["images_b64"]) for b in serialized)

    logger.info(
        "fetch_and_batch_pages: %d pages → %d batches | doc=%s",
        total_pages,
        len(serialized),
        state["document_id"],
    )

    return {
        **state,
        "page_batches": serialized,
        "batch_count": len(serialized),
        "fetch_error": None,
        # Update total_pages from actual PDF if document.page_count was 0
        "total_pages": total_pages
        if state["total_pages"] == 0
        else state["total_pages"],
    }
