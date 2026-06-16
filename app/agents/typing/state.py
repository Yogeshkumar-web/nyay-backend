"""
TypingAgentState — LangGraph state schema for the VakilSuite Typing Agent.

All fields must be JSON-serializable (LangGraph checkpointer requirement).
NamedTuples (PageBatch) are stored as plain dicts here.
"""
from typing import TypedDict


class TypingAgentState(TypedDict):
    # ── Input (set by typing_tasks.py before invoking the graph) ───────────
    ocr_raw_text: str  # raw OCR output from Google Document AI
    document_type: str  # "fir" | "chargesheet" | "affidavit" | etc.
    document_id: str  # UUID string — for logging / tracing only
    presigned_url: str  # R2 presigned URL — Node 1 fetches PDF from here
    total_pages: int  # Document.page_count (0 if unknown)
    ocr_artifact: dict | None  # compact Document AI layout artifact

    # ── Node 1: fetch_and_batch_pages ──────────────────────────────────────
    page_batches: list[dict]  # list of PageBatch._asdict() — JSON-safe
    batch_count: int
    fetch_error: str | None  # set on failure → downstream nodes use text-only fallback

    # ── Node 2: assess_ocr_quality ─────────────────────────────────────────
    ocr_quality_score: float  # 0.0 – 1.0
    ocr_issues: list[str]  # e.g. ["broken_lines", "table_corruption"]
    ocr_language: str  # "hindi" | "english" | "mixed" | "unknown"
    visual_vs_ocr_gaps: list[str]  # e.g. ["Accused table on page 3 missing from OCR"]

    # ── Node 3: detect_document_structure ──────────────────────────────────
    detected_sections: dict  # {"header": "...", "accused": "...", ...}
    document_template: str  # "fir_ncrb" | "generic"
    structure_confidence: float  # 0.0 – 1.0

    # ── Node 4: reformat_sections ──────────────────────────────────────────
    formatted_sections: dict  # same keys as detected_sections, cleaned text
    retry_count: int  # incremented by self_review on failure (max 2)
    retry_reason: str | None

    # ── Node 5: self_review ────────────────────────────────────────────────
    review_passed: bool
    review_issues: list[str]
    review_suggestions: str  # injected into Node 4 prompt on retry

    # ── Node 6: assemble_html ──────────────────────────────────────────────
    final_html: str  # Tiptap-compatible HTML → TypedVersion.typed_content
    processing_notes: str  # audit trail: "quality=0.87 | lang=mixed | batches=2 | ..."


def default_state(
    ocr_raw_text: str,
    document_type: str,
    document_id: str,
    presigned_url: str,
    total_pages: int,
    ocr_artifact: dict | None = None,
) -> TypingAgentState:
    """
    Returns a fully-initialized TypingAgentState with all required fields set.
    Call this in typing_tasks.py before graph.ainvoke().
    """
    return TypingAgentState(
        ocr_raw_text=ocr_raw_text,
        document_type=document_type,
        document_id=document_id,
        presigned_url=presigned_url,
        total_pages=total_pages,
        ocr_artifact=ocr_artifact,
        page_batches=[],
        batch_count=0,
        fetch_error=None,
        ocr_quality_score=0.0,
        ocr_issues=[],
        ocr_language="unknown",
        visual_vs_ocr_gaps=[],
        detected_sections={},
        document_template="generic",
        structure_confidence=0.0,
        formatted_sections={},
        retry_count=0,
        retry_reason=None,
        review_passed=False,
        review_issues=[],
        review_suggestions="",
        final_html="",
        processing_notes="",
    )
