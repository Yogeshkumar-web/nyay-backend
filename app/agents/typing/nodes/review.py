"""
Node 5 — self_review

Reviews the formatted_sections produced by Node 4 against the original OCR
text and page images. Asks Claude to verify:
  - No hallucination (content added that wasn't in the source)
  - No data loss (content in source missing from output)
  - Correct proper nouns (names, FIR numbers, dates, police station)
  - Visual fidelity (formatted text matches what images show)

Response shape expected from Claude:
  { "passed": bool, "issues": [...], "suggestions": "..." }

Retry logic (MAX_RETRIES = 2):
  - passed=False AND retry_count < MAX_RETRIES:
      → increments retry_count, sets review_suggestions → router returns "retry"
      → graph routes back to reformat_sections (Node 4 sees suggestions)
  - passed=True OR retry_count >= MAX_RETRIES:
      → router returns "continue" → assemble_html

Failure fallback: any exception or bad JSON → passed=True (avoids infinite
retry loop; processing_notes will record the failure).

OCR cap: 12 000 chars (generous — reviewer needs full context).
Images: same selection strategy as reformat (first 3 from Batch 1 + last 2
from final batch, max 5 total).
"""
import json
import logging
import re

from app.agents.shared.llm import call_llm
from app.agents.shared.prompt_loader import load_agent_prompt
from app.agents.typing.state import TypingAgentState

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
OCR_CAP = 12_000
MAX_IMAGES = 5


# ─────────────────────────────────────────────────────────────────────────────
# Public node
# ─────────────────────────────────────────────────────────────────────────────


async def self_review(state: TypingAgentState) -> TypingAgentState:
    formatted = state["formatted_sections"]
    doc_id = state["document_id"]

    # Nothing to review — treat as passed
    if not formatted:
        logger.info("self_review: no formatted sections, auto-pass | doc=%s", doc_id)
        return {
            **state,
            "review_passed": True,
            "review_issues": [],
            "review_suggestions": "",
        }

    # FIR: deterministic parser output — LLM review would distort already-correct
    # structured sections (acts table, accused table, field rows).
    # Parser is the source of truth; no LLM verification needed.
    if state["document_template"] == "fir_ncrb":
        logger.info(
            "self_review: FIR — skipping LLM review (schema-first parser output) | doc=%s",
            doc_id,
        )
        return {
            **state,
            "review_passed": True,
            "review_issues": [],
            "review_suggestions": "",
        }

    try:
        prompt_template = load_agent_prompt("typing", "review")
    except FileNotFoundError:
        logger.error("self_review: prompt file missing | doc=%s", doc_id)
        return _fallback(state, reason="prompt_missing")

    ocr_preview = state["ocr_raw_text"][:OCR_CAP]
    formatted_json = json.dumps(formatted, ensure_ascii=False, indent=2)

    prompt = prompt_template.replace("{original_ocr}", ocr_preview).replace(
        "{formatted_json}", formatted_json
    )

    ref_images = _select_reference_images(state["page_batches"])

    try:
        raw = await call_llm(prompt, images_b64=ref_images, max_tokens=2048)
        parsed = _parse_review_response(raw)
    except Exception as exc:
        logger.warning("self_review: LLM call failed — %s | doc=%s", exc, doc_id)
        return _fallback(state, reason=str(exc))

    passed = parsed["passed"]
    issues = parsed["issues"]
    suggestions = parsed["suggestions"]

    # Increment retry_count if we're sending back for another pass
    new_retry_count = state["retry_count"]
    if not passed and new_retry_count < MAX_RETRIES:
        new_retry_count += 1

    logger.info(
        "self_review: passed=%s | issues=%d | retry_count=%d→%d | doc=%s",
        passed,
        len(issues),
        state["retry_count"],
        new_retry_count,
        doc_id,
    )

    return {
        **state,
        "review_passed": passed,
        "review_issues": issues,
        "review_suggestions": suggestions,
        "retry_count": new_retry_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Router — called by graph.py conditional edge
# ─────────────────────────────────────────────────────────────────────────────


def review_router(state: TypingAgentState) -> str:
    """
    Returns "retry" → reformat_sections, or "continue" → assemble_html.
    Called by LangGraph as the conditional edge function after self_review.
    """
    if state["review_passed"]:
        return "continue"
    if state["retry_count"] >= MAX_RETRIES:
        logger.info(
            "self_review: max retries (%d) reached, forcing continue | doc=%s",
            MAX_RETRIES,
            state["document_id"],
        )
        return "continue"
    return "retry"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fallback(state: TypingAgentState, reason: str) -> TypingAgentState:
    """On any error, treat as passed to avoid an infinite retry loop."""
    return {
        **state,
        "review_passed": True,
        "review_issues": [],
        "review_suggestions": "",
        "processing_notes": state["processing_notes"] + f" | review_fallback:{reason}",
    }


def _select_reference_images(page_batches: list[dict]) -> list[str]:
    """Same strategy as reformat: first 3 from Batch 1 + last 2 from final batch."""
    if not page_batches:
        return []
    images: list[str] = []
    first = [img for img in page_batches[0]["images_b64"] if img]
    images.extend(first[:3])
    if len(page_batches) > 1:
        last = [img for img in page_batches[-1]["images_b64"] if img]
        images.extend(last[:2])
    return images[:MAX_IMAGES]


def _parse_review_response(raw: str) -> dict:
    """
    Parse Claude's review JSON.
    Returns dict with keys: passed (bool), issues (list[str]), suggestions (str).
    Raises ValueError on bad JSON — caller handles fallback.
    """
    # Strip fences
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if fenced:
        raw = fenced.group(1)

    # Find JSON object
    brace = re.search(r"\{[\s\S]+\}", raw)
    if brace:
        raw = brace.group(0)

    data = json.loads(raw)  # raises json.JSONDecodeError on bad input

    # Coerce types
    passed = bool(data.get("passed", True))

    issues = data.get("issues", [])
    if isinstance(issues, str):
        issues = [issues] if issues else []
    elif not isinstance(issues, list):
        issues = []

    suggestions = data.get("suggestions", "")
    if not isinstance(suggestions, str):
        suggestions = str(suggestions) if suggestions else ""

    return {"passed": passed, "issues": issues, "suggestions": suggestions}
