"""
Node 4 — reformat_sections

Takes detected_sections from Node 3 and reformats each section into clean,
print-ready text using Claude. Page images are attached for visual verification
of proper nouns, tables, and missed content.

Sections are processed in groups of SECTIONS_PER_CALL (default: 4) to balance
API call count vs. context size. Groups are processed in parallel.

Retry support: when retry_count > 0, review_suggestions from Node 5 are
injected into the prompt as {retry_context}. The reformatter sees exactly what
the reviewer found wrong and fixes it.

Fallback: if a group's Claude call fails, the original detected_sections text
is used for that group — no content is lost.
"""
import asyncio
import json
import logging
import re

from app.agents.shared.llm import call_llm
from app.agents.shared.prompt_loader import load_agent_prompt
from app.agents.typing.state import TypingAgentState

logger = logging.getLogger(__name__)

# Sections per Claude call — 4 keeps context tight while reducing round trips
SECTIONS_PER_CALL = 4

# Max images sent per reformat call — Batch 1 covers FIR title/metadata pages
# Batch -1 (last) covers narrative end. Both together give good visual coverage.
MAX_IMAGES_PER_CALL = 5


# ─────────────────────────────────────────────────────────────────────────────
# Public node
# ─────────────────────────────────────────────────────────────────────────────


async def reformat_sections(state: TypingAgentState) -> TypingAgentState:
    sections = state["detected_sections"]

    if not sections:
        logger.info(
            "reformat_sections: no sections to reformat | doc=%s", state["document_id"]
        )
        return {**state, "formatted_sections": {}}

    template = state["document_template"]

    # ── FIR: regex parser already structured all sections.
    #         Only LLM-clean the fir_contents (tehreer) narrative. ─────────────
    if template == "fir_ncrb":
        return await _reformat_fir_parsed(state, sections)

    prompt_name = "reformat_generic"

    try:
        prompt_template = load_agent_prompt("typing", prompt_name)
    except FileNotFoundError:
        logger.error(
            "reformat prompt missing: %s | doc=%s", prompt_name, state["document_id"]
        )
        return {**state, "formatted_sections": dict(sections)}

    # Build retry context string (injected into prompt on retry)
    retry_ctx = _build_retry_context(state)

    # Build representative image list (Batch 1 first N images + last batch last N images)
    ref_images = _select_reference_images(state["page_batches"])

    # Group sections into chunks for parallel processing
    section_items = list(sections.items())
    groups = [
        section_items[i : i + SECTIONS_PER_CALL]
        for i in range(0, len(section_items), SECTIONS_PER_CALL)
    ]

    tasks = [
        _reformat_group(
            group=group,
            prompt_template=prompt_template,
            retry_ctx=retry_ctx,
            ref_images=ref_images,
            doc_id=state["document_id"],
        )
        for group in groups
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Merge all group results; on exception fall back to original detected text
    formatted: dict = {}
    for group, result in zip(groups, results):
        if isinstance(result, Exception):
            logger.warning(
                "reformat_sections: group failed (%s), using original | doc=%s",
                result,
                state["document_id"],
            )
            formatted.update(dict(group))  # fallback: original detected text
        else:
            assert isinstance(result, dict)
            formatted.update(result)

    logger.info(
        "reformat_sections: %d sections reformatted | retry=%d | doc=%s",
        len(formatted),
        state["retry_count"],
        state["document_id"],
    )

    return {**state, "formatted_sections": formatted}


# ─────────────────────────────────────────────────────────────────────────────
# Per-group reformatting
# ─────────────────────────────────────────────────────────────────────────────


async def _reformat_group(
    group: list[tuple[str, str]],
    prompt_template: str,
    retry_ctx: str,
    ref_images: list[str],
    doc_id: str,
) -> dict:
    """
    Reformat a group of sections in one Claude call.
    Returns dict of section_key → reformatted_text.
    Raises on failure — caller handles fallback.
    """
    sections_json = json.dumps(dict(group), ensure_ascii=False, indent=2)

    prompt = prompt_template.replace("{retry_context}", retry_ctx).replace(
        "{sections_json}", sections_json
    )

    raw = await call_llm(prompt, images_b64=ref_images, max_tokens=4096)
    parsed = _parse_reformat_response(raw)

    # Ensure all group keys are present — missing keys fall back to original
    result = dict(group)
    result.update(parsed)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_retry_context(state: TypingAgentState) -> str:
    """Returns the retry block to inject into the prompt, or empty string."""
    if state["retry_count"] > 0 and state["review_suggestions"]:
        return (
            f"\n\nPREVIOUS ATTEMPT HAD ISSUES — FIX THESE SPECIFICALLY:\n"
            f"{state['review_suggestions']}\n"
        )
    return ""


def _select_reference_images(page_batches: list[dict]) -> list[str]:
    """
    Select up to MAX_IMAGES_PER_CALL images from available batches.
    Strategy: first 3 from Batch 1 (title/metadata pages) + last 2 from final batch.
    If only one batch: just take first MAX_IMAGES_PER_CALL images from it.
    """
    if not page_batches:
        return []

    images: list[str] = []

    # First batch — title / metadata pages
    first_images = [img for img in page_batches[0]["images_b64"] if img]
    images.extend(first_images[:3])

    # Last batch (if different from first) — end of narrative / action taken
    if len(page_batches) > 1:
        last_images = [img for img in page_batches[-1]["images_b64"] if img]
        images.extend(last_images[:2])

    return images[:MAX_IMAGES_PER_CALL]


def _parse_reformat_response(raw: str) -> dict:
    """
    Parse Claude's reformatted sections response to a dict.
    Strips markdown fences; finds first {...} block.
    Keeps all keys including empty strings (typist cleared a section intentionally).
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

    # Keep string values only; skip null (section Claude chose to omit)
    return {k: v for k, v in data.items() if isinstance(v, str)}


# ─────────────────────────────────────────────────────────────────────────────
# FIR-specific reformat: parser already did the heavy lifting.
# Only clean Section 12 (fir_contents) tehreer via LLM.
# ─────────────────────────────────────────────────────────────────────────────

_TEHREER_CLEAN_PROMPT = """You are a legal document typist for Indian courts (Allahabad High Court).

Clean the following Hindi FIR tehreer (complaint narrative) text. The text was extracted via OCR and may have:
- Extra spaces inside words (e.g. "म 0 हे 0" should stay as-is, it's an abbreviation)
- Missing spaces between sentences
- Line-break artefacts

Rules:
1. Fix spacing between words and sentences ONLY where clearly wrong
2. DO NOT change, add, or remove any content — preserve every word exactly
3. DO NOT translate anything
4. Preserve all abbreviations like "म0हे0का0", "मो0", "उ0प्र0" exactly
5. Return ONLY the cleaned text — no explanation, no preamble

TEHREER TEXT:
{tehreer}"""


async def _reformat_fir_parsed(
    state: TypingAgentState,
    sections: dict,
) -> TypingAgentState:
    """
    FIR reformat path when regex parser was used in detect step.

    All sections except fir_contents are already clean — pass them through.
    fir_contents (the tehreer narrative) is sent to LLM for spacing/punctuation cleanup.
    """
    doc_id = state["document_id"]

    # Pass all sections through as-is first
    formatted: dict = dict(sections)

    # Clean fir_contents via LLM if present
    tehreer = sections.get("fir_contents", "").strip()
    if tehreer:
        retry_ctx = _build_retry_context(state)
        extra = (
            f"\n\nADDITIONAL CONTEXT FROM REVIEWER:\n{retry_ctx}" if retry_ctx else ""
        )

        prompt = _TEHREER_CLEAN_PROMPT.replace("{tehreer}", tehreer) + extra

        try:
            cleaned = await call_llm(prompt, max_tokens=2048)
            if cleaned and len(cleaned) > len(tehreer) * 0.5:
                # Sanity check: cleaned text shouldn't be drastically shorter
                formatted["fir_contents"] = cleaned
                logger.info(
                    "reformat_fir_parsed: tehreer cleaned %d→%d chars | doc=%s",
                    len(tehreer),
                    len(cleaned),
                    doc_id,
                )
            else:
                logger.warning(
                    "reformat_fir_parsed: LLM output too short (%d chars), keeping original | doc=%s",
                    len(cleaned) if cleaned else 0,
                    doc_id,
                )
        except Exception as exc:
            logger.warning(
                "reformat_fir_parsed: tehreer LLM clean failed (%s), keeping original | doc=%s",
                exc,
                doc_id,
            )

    logger.info(
        "reformat_sections: %d FIR sections ready | retry=%d | doc=%s",
        len(formatted),
        state["retry_count"],
        doc_id,
    )
    return {**state, "formatted_sections": formatted}
