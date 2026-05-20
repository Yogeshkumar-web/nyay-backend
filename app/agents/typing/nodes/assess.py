"""
Node 2 — assess_ocr_quality

Sends OCR text + Batch 1 page images to Claude for a multimodal quality
assessment. Claude returns a JSON report with:
  - quality_score   : 0.0–1.0
  - issues          : list of issue codes (broken_lines, table_corruption, ...)
  - language        : "hindi" | "english" | "mixed"
  - visual_vs_ocr_gaps : list of specific gaps (content in image but wrong/missing in OCR)

If page_batches is empty (fetch_error from Node 1), falls back to text-only
assessment — Claude assesses OCR quality from text alone without images.

If Claude call fails entirely, returns safe defaults so the pipeline continues.
"""
import json
import logging
import re

from app.agents.shared.llm import call_llm
from app.agents.shared.prompt_loader import load_agent_prompt
from app.agents.typing.state import TypingAgentState

logger = logging.getLogger(__name__)

# Cap OCR text sent to Claude — assessment doesn't need the full document
OCR_PREVIEW_CHARS = 8_000

# Safe defaults used when Claude call fails
_FALLBACK = {
    "quality_score": 0.5,
    "issues": ["assessment_failed"],
    "language": "unknown",
    "visual_vs_ocr_gaps": [],
    "notes": "Assessment skipped due to API error",
}


def _parse_json_response(raw: str) -> dict:
    """
    Extract JSON from Claude's response.
    Handles: plain JSON, ```json ... ``` fences, stray text before/after.
    """
    # Strip markdown code fences if present
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if fenced:
        raw = fenced.group(1)

    # Find the first { ... } block
    brace_match = re.search(r"\{[\s\S]+\}", raw)
    if brace_match:
        raw = brace_match.group(0)

    return json.loads(raw)


async def assess_ocr_quality(state: TypingAgentState) -> TypingAgentState:
    # FIR: use fast text-only heuristic — no Claude call, no images needed.
    # The deterministic parser handles all structure extraction; quality score
    # is only used for the audit log (processing_notes).
    if state["document_type"] == "fir":
        return _assess_fir_text_only(state)

    try:
        prompt_template = load_agent_prompt("typing", "assess")
    except FileNotFoundError:
        logger.error("assess.txt prompt missing — skipping assessment")
        return {**state, **_FALLBACK_STATE()}

    ocr_preview = state["ocr_raw_text"][:OCR_PREVIEW_CHARS]
    prompt = prompt_template.replace("{ocr_text}", ocr_preview)

    # ── Pick images (Batch 1 only) ────────────────────────────────────────
    images_b64: list[str] = []
    if state["page_batches"]:
        images_b64 = state["page_batches"][0]["images_b64"]
        logger.debug(
            "assess_ocr_quality: multimodal — %d images from batch 1 | doc=%s",
            len(images_b64),
            state["document_id"],
        )
    else:
        logger.info(
            "assess_ocr_quality: text-only mode (fetch_error=%s) | doc=%s",
            state["fetch_error"],
            state["document_id"],
        )

    # ── Call LLM ─────────────────────────────────────────────────────────
    try:
        raw = await call_llm(prompt, images_b64=images_b64, max_tokens=2048)
        data = _parse_json_response(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "assess_ocr_quality: JSON parse error — %s | doc=%s",
            exc,
            state["document_id"],
        )
        data = dict(_FALLBACK)
    except Exception as exc:
        logger.warning(
            "assess_ocr_quality: Claude call failed — %s | doc=%s",
            exc,
            state["document_id"],
        )
        data = dict(_FALLBACK)

    # ── Validate + coerce response fields ────────────────────────────────
    quality_score = float(data.get("quality_score") or 0.5)
    quality_score = max(0.0, min(1.0, quality_score))  # clamp to [0, 1]

    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]

    language = str(data.get("language") or "unknown").lower()
    if language not in ("hindi", "english", "mixed", "unknown"):
        language = "unknown"

    gaps = data.get("visual_vs_ocr_gaps") or []
    if not isinstance(gaps, list):
        gaps = []

    logger.info(
        "assess_ocr_quality: score=%.2f lang=%s issues=%s gaps=%d | doc=%s",
        quality_score,
        language,
        issues,
        len(gaps),
        state["document_id"],
    )

    return {
        **state,
        "ocr_quality_score": quality_score,
        "ocr_issues": issues,
        "ocr_language": language,
        "visual_vs_ocr_gaps": gaps,
    }


def _FALLBACK_STATE() -> dict:
    return {
        "ocr_quality_score": _FALLBACK["quality_score"],
        "ocr_issues": _FALLBACK["issues"],
        "ocr_language": _FALLBACK["language"],
        "visual_vs_ocr_gaps": _FALLBACK["visual_vs_ocr_gaps"],
    }


def _assess_fir_text_only(state: TypingAgentState) -> TypingAgentState:
    """
    Fast heuristic quality assessment for FIR documents — no Claude call.

    FIR text always contains both Devanagari (Hindi labels) and ASCII English
    (section anchors, dates, names). We use this as a proxy for quality:

      Both scripts present  → 0.85  (normal bilingual NCRB FIR)
      Only Devanagari       → 0.55  (OCR may have dropped English anchors)
      Only ASCII/English    → 0.50  (unlikely for NCRB FIR; possible scan issue)
      Neither               → 0.30  (very poor OCR — likely blank pages)

    NCRB FIR anchor phrases are also checked: "FIRST INFORMATION REPORT",
    "District/Unit", "Acts (अधिनियम)" — presence boosts confidence.
    """
    import re as _re

    text = state["ocr_raw_text"]
    has_devanagari = bool(_re.search(r"[ऀ-ॿ]", text))
    has_english = bool(_re.search(r"[A-Za-z]", text))

    # Anchor checks
    anchor_hits = sum(
        1
        for pat in [
            r"FIRST\s+INFORMATION\s+REPORT",
            r"District/Unit",
            r"Acts\s*\(अधिनियम\)",
            r"Complainant",
            r"Action\s+taken",
        ]
        if _re.search(pat, text, _re.IGNORECASE)
    )

    if has_devanagari and has_english:
        base_score = 0.85
        language = "mixed"
    elif has_devanagari:
        base_score = 0.55
        language = "hindi"
    elif has_english:
        base_score = 0.50
        language = "english"
    else:
        base_score = 0.30
        language = "unknown"

    # Each anchor found adds a small bonus (max +0.10)
    score = round(min(1.0, base_score + anchor_hits * 0.02), 2)

    issues: list[str] = []
    if not has_devanagari:
        issues.append("no_devanagari")
    if not has_english:
        issues.append("no_english_anchors")
    if anchor_hits < 2:
        issues.append("few_section_anchors")

    logger.info(
        "assess_ocr_quality: FIR text-only | score=%.2f lang=%s anchors=%d issues=%s | doc=%s",
        score,
        language,
        anchor_hits,
        issues,
        state["document_id"],
    )

    return {
        **state,
        "ocr_quality_score": score,
        "ocr_issues": issues,
        "ocr_language": language,
        "visual_vs_ocr_gaps": [],  # no visual access — gaps detected by validate node
    }
