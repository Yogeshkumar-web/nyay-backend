"""
Node 3 — detect_document_structure

Detects the logical sections of the document by processing ALL page batches
in parallel (asyncio.gather). Each batch gets a Claude multimodal call with
its page images + the relevant OCR text slice.

Results from all batches are merged with section-specific rules:
  - "header"       → only from batch 1 (title page is always first)
  - "fir_contents" → all batches concatenated in order (narrative spans pages)
  - "accused"      → all batches concatenated (table may span pages)
  - "leftovers"    → all batches concatenated
  - Everything else → first non-empty value wins (later batch can override if earlier was null)

If a batch's Claude call raises an exception, that batch is skipped — other
batches' results still merge. The pipeline never crashes on a single batch failure.

If page_batches is empty (fetch_error), falls back to a single text-only
Claude call on the full OCR text.
"""
import asyncio
import json
import logging
import re

from app.agents.shared.llm import call_llm
from app.agents.shared.prompt_loader import load_agent_prompt
from app.agents.typing.state import TypingAgentState

logger = logging.getLogger(__name__)

# Approximate lines per page for OCR slicing (no exact page boundaries in raw text)
_LINES_PER_PAGE = 60

# FIR section keys — used to compute structure_confidence
_FIR_SECTION_KEYS = frozenset(
    {
        "header",
        "sections_law",
        "occurrence",
        "place",
        "complainant",
        "accused",
        "fir_contents",
        "action_taken",
    }
)

# Keys where ALL batches are concatenated (content spans multiple pages)
_CONCAT_KEYS = {"fir_contents", "accused", "leftovers"}


# ─────────────────────────────────────────────────────────────────────────────
# Public node
# ─────────────────────────────────────────────────────────────────────────────


async def detect_document_structure(state: TypingAgentState) -> TypingAgentState:
    doc_type = state["document_type"]
    template = _pick_template(doc_type)

    # ── FIR: deterministic regex parser — no LLM, no image needed ───────────
    if template == "fir_ncrb":
        from app.features.documents.fir_reconstruction import reconstruct_fir

        schema = reconstruct_fir(
            state["ocr_raw_text"],
            ocr_artifact=state.get("ocr_artifact"),
        )
        if schema is None:
            from app.agents.typing.fir_parser import parse_ncrb_fir

            sections = parse_ncrb_fir(state["ocr_raw_text"])
        else:
            sections = schema.to_detected_sections()
        confidence = _compute_confidence(sections, template)
        logger.info(
            "detect_document_structure: FIR regex parser → %d sections | "
            "confidence=%.2f | doc=%s",
            len(sections),
            confidence,
            state["document_id"],
        )
        return {
            **state,
            "detected_sections": sections,
            "document_template": template,
            "structure_confidence": confidence,
        }

    if not state["page_batches"]:
        # No images available — single text-only call
        logger.info(
            "detect_document_structure: text-only fallback | doc=%s",
            state["document_id"],
        )
        sections = await _detect_text_only(state, template)
        confidence = _compute_confidence(sections, template)
        return {
            **state,
            "detected_sections": sections,
            "document_template": template,
            "structure_confidence": confidence,
        }

    # ── Parallel: one Claude call per batch ──────────────────────────────
    tasks = [
        _detect_batch(
            batch=b,
            ocr_text=state["ocr_raw_text"],
            template=template,
            gaps=state["visual_vs_ocr_gaps"],
            doc_id=state["document_id"],
        )
        for b in state["page_batches"]
    ]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions — failed batches produce empty dicts, not crashes
    valid: list[dict] = []
    for i, result in enumerate(batch_results):
        if isinstance(result, Exception):
            logger.warning(
                "detect_document_structure: batch %d failed — %s | doc=%s",
                i + 1,
                result,
                state["document_id"],
            )
            valid.append({})
        else:
            assert isinstance(result, dict)
            valid.append(result)

    merged = _merge_sections(valid)
    confidence = _compute_confidence(merged, template)

    logger.info(
        "detect_document_structure: %d batches → %d sections | confidence=%.2f | doc=%s",
        len(valid),
        len([v for v in merged.values() if v]),
        confidence,
        state["document_id"],
    )

    return {
        **state,
        "detected_sections": merged,
        "document_template": template,
        "structure_confidence": confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-batch detection
# ─────────────────────────────────────────────────────────────────────────────


async def _detect_batch(
    batch: dict,
    ocr_text: str,
    template: str,
    gaps: list[str],
    doc_id: str,
) -> dict:
    """
    Send one batch (its images + OCR slice) to Claude.
    Returns a dict of section_key → text, or {} on failure.
    """
    prompt_name = "detect_fir" if template == "fir_ncrb" else "detect_generic"

    try:
        prompt_template = load_agent_prompt("typing", prompt_name)
    except FileNotFoundError:
        logger.error("detect prompt missing: %s | doc=%s", prompt_name, doc_id)
        return {}

    page_start = batch["page_start"]
    page_end = batch["page_end"]
    ocr_slice = _slice_ocr_for_pages(ocr_text, page_start, page_end)
    gaps_str = "\n".join(f"- {g}" for g in gaps) if gaps else "None identified"

    prompt = (
        prompt_template.replace("{page_start}", str(page_start))
        .replace("{page_end}", str(page_end))
        .replace("{gaps_list}", gaps_str)
        .replace("{ocr_slice}", ocr_slice)
    )

    try:
        raw = await call_llm(prompt, images_b64=batch["images_b64"], max_tokens=2048)
        return _parse_sections_json(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "_detect_batch: JSON parse error (batch %d–%d) — %s | doc=%s",
            page_start,
            page_end,
            exc,
            doc_id,
        )
        return {}
    except Exception as exc:
        logger.warning(
            "_detect_batch: Claude call failed (batch %d–%d) — %s | doc=%s",
            page_start,
            page_end,
            exc,
            doc_id,
        )
        return {}


async def _detect_text_only(state: TypingAgentState, template: str) -> dict:
    """Single LLM call using OCR text only — used when images unavailable."""
    prompt_name = "detect_fir" if template == "fir_ncrb" else "detect_generic"

    try:
        prompt_template = load_agent_prompt("typing", prompt_name)
    except FileNotFoundError:
        return {"body": state["ocr_raw_text"]}

    total = state["total_pages"] or "unknown"
    gaps_str = "Visual access unavailable — text-only mode"
    ocr_preview = state["ocr_raw_text"][:15_000]

    prompt = (
        prompt_template.replace("{page_start}", "1")
        .replace("{page_end}", str(total))
        .replace("{gaps_list}", gaps_str)
        .replace("{ocr_slice}", ocr_preview)
    )

    try:
        raw = await call_llm(prompt, max_tokens=2048)
        return _parse_sections_json(raw)
    except Exception as exc:
        logger.warning(
            "_detect_text_only failed — %s | doc=%s", exc, state["document_id"]
        )
        return {"body": state["ocr_raw_text"]}


# ─────────────────────────────────────────────────────────────────────────────
# Merge + helpers
# ─────────────────────────────────────────────────────────────────────────────


def _merge_sections(batch_results: list[dict]) -> dict:
    """
    Merge section dicts from multiple batches with section-specific rules:
      - Concat keys (fir_contents, accused, leftovers) → join all non-empty values
      - header → only batch 1 (index 0)
      - Everything else → first non-null/non-empty value wins
    """
    merged: dict = {}

    for i, result in enumerate(batch_results):
        for key, val in result.items():
            if val is None or val == "":
                continue

            if key == "header" and i > 0:
                # Header only from the first batch — ignore subsequent
                continue

            if key in _CONCAT_KEYS:
                existing = merged.get(key) or ""
                sep = "\n\n" if existing else ""
                merged[key] = existing + sep + val
            else:
                # First non-empty value wins; later batches can fill missing keys
                if not merged.get(key):
                    merged[key] = val

    return merged


def _pick_template(doc_type: str) -> str:
    return "fir_ncrb" if doc_type == "fir" else "generic"


def _compute_confidence(sections: dict, template: str) -> float:
    """
    Confidence = fraction of expected keys that have non-empty content.
    For FIR: 8 known keys. For generic: whatever keys came back / 4 (arbitrary baseline).
    """
    if template == "fir_ncrb":
        found = sum(1 for k in _FIR_SECTION_KEYS if sections.get(k))
        return round(found / len(_FIR_SECTION_KEYS), 2)
    else:
        # Generic: just check that we got something
        found = sum(1 for v in sections.values() if v)
        return round(min(found / max(4, 1), 1.0), 2)


def _slice_ocr_for_pages(ocr_text: str, page_start: int, page_end: int) -> str:
    """
    Return the OCR text lines that roughly correspond to [page_start, page_end].
    Uses a fixed lines-per-page estimate (60 lines/page) — good enough for slicing.
    Caps at 8000 chars to stay within token budget per batch call.
    """
    lines = ocr_text.splitlines()
    start_line = max(0, (page_start - 1) * _LINES_PER_PAGE)
    end_line = page_end * _LINES_PER_PAGE
    sliced = "\n".join(lines[start_line:end_line])
    return sliced[:8_000]  # hard cap per batch


def _parse_sections_json(raw: str) -> dict:
    """
    Parse Claude's section detection response to a dict.
    Strips markdown fences; finds first {...} block.
    Removes null values (section not found on these pages).
    """
    # Strip fences
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if fenced:
        raw = fenced.group(1)

    # Find JSON object
    brace = re.search(r"\{[\s\S]+\}", raw)
    if brace:
        raw = brace.group(0)

    data = json.loads(raw)

    # Remove null / empty values — sections not on these pages
    return {k: v for k, v in data.items() if v is not None and v != ""}
