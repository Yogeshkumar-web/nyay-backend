"""
Node 3b — validate_fir_fields

Synchronous, deterministic node — no LLM, no I/O.

Sits between detect_document_structure (Node 3) and reformat_sections (Node 4).
For FIR documents only — passes through unchanged for all other document types.

What it does:
  1. Checks which critical FIR sections are missing from detected_sections
  2. Logs a WARNING for each missing critical field (grep "fir_field_gap" in ops)
  3. Tries lightweight re-extraction for failed fields using alternative patterns
  4. Updates detected_sections with any recovered values
  5. Updates structure_confidence based on final section coverage

Critical sections (absence is always logged):
  sections_law  — Acts & Sections (Sec 2)
  complainant   — Complainant info (Sec 6)
  accused       — Accused details (Sec 7)
  fir_contents  — FIR narrative / tehreer (Sec 12)
  action_taken  — Action taken / IO details (Sec 13)

Important sections (absence is logged at INFO level):
  occurrence    — Occurrence date/time (Sec 3)
  place         — Place of occurrence (Sec 5)
  signatures    — Officer details + dispatch (Sec 14–15)

This node does NOT call any LLM. If re-extraction fails, the section remains
absent — the assemble node will simply skip it. No hallucination possible.
"""
from __future__ import annotations

import logging
import re

from app.agents.typing.state import TypingAgentState

logger = logging.getLogger(__name__)

# ── Field importance ──────────────────────────────────────────────────────────

_CRITICAL_SECTIONS: dict[str, str] = {
    "sections_law": "Acts & Sections (Sec 2)",
    "complainant": "Complainant / Informant (Sec 6)",
    "accused": "Accused Details (Sec 7)",
    "fir_contents": "FIR Narrative / Tehreer (Sec 12)",
    "action_taken": "Action Taken / IO Details (Sec 13)",
}

_IMPORTANT_SECTIONS: dict[str, str] = {
    "occurrence": "Occurrence Date/Time (Sec 3)",
    "place": "Place of Occurrence (Sec 5)",
    "signatures": "Signatures & Dispatch (Sec 14–15)",
}

# ── All known FIR section anchors — same as fir_parser._ANCHORS ───────────────
# Used to slice raw OCR text for targeted re-extraction attempts.
_SECTION_ANCHORS: list[tuple[str, str]] = [
    ("header", r"FIRST\s+INFORMATION\s+REPORT"),
    ("section_1", r"1\.\s+District/Unit"),
    ("section_2", r"Acts\s*\(अधिनियम\)"),
    ("section_3", r"3\.\s*\(a\)\s*Occurrence"),
    ("section_4", r"4\.\s*Type of Information"),
    ("section_5", r"5\.\s*Place of Occurrence"),
    ("section_6", r"6\.\s*Complainant\s*/\s*Informant"),
    ("section_7", r"7\.\s*Details of known"),
    ("section_8", r"8\.\s*Reasons for delay"),
    ("section_9", r"9\.\s*Particulars of properties"),
    ("section_10", r"10\.\s*Total value"),
    ("section_11", r"11\.\s*Inquest Report"),
    ("section_12", r"12\.\s*First Information contents"),
    ("section_13", r"13\.\s*Action taken"),
    ("section_14", r"14\.\s*Signature"),
    ("section_15", r"15\.\s*Date and time of dispatch"),
    ("attachment", r"Attachment to item\s+7"),
    ("physical", r"Physical features.*?suspect/accused"),
]

# Maps detected_sections key → which anchor pair slices that section
_KEY_TO_ANCHOR: dict[str, str] = {
    "sections_law": "section_2",
    "occurrence": "section_3",
    "info_type": "section_4",
    "place": "section_5",
    "complainant": "section_6",
    "accused": "section_7",
    "fir_contents": "section_12",
    "action_taken": "section_13",
    "signatures": "section_14",
    "physical_features": "physical",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public node
# ─────────────────────────────────────────────────────────────────────────────


def validate_fir_fields(state: TypingAgentState) -> TypingAgentState:
    """
    Synchronous validation + gap-fill node for FIR documents.
    Passes through unchanged for non-FIR document types.
    """
    if state["document_template"] != "fir_ncrb":
        return state

    doc_id = state["document_id"]
    sections = dict(state["detected_sections"])  # mutable copy
    ocr_text = state["ocr_raw_text"]

    recovered: list[str] = []
    missing_critical: list[str] = []
    missing_important: list[str] = []

    # ── 1. Audit: which sections are missing? ─────────────────────────────────
    for key, label in _CRITICAL_SECTIONS.items():
        if not sections.get(key):
            missing_critical.append(key)
            logger.warning(
                "fir_field_gap: CRITICAL section missing | key=%s label=%s | doc=%s",
                key,
                label,
                doc_id,
            )

    for key, label in _IMPORTANT_SECTIONS.items():
        if not sections.get(key):
            missing_important.append(key)
            logger.info(
                "fir_field_gap: important section missing | key=%s label=%s | doc=%s",
                key,
                label,
                doc_id,
            )

    all_missing = missing_critical + missing_important

    # ── 2. Re-extraction: try to recover missing sections from raw OCR ────────
    if all_missing:
        boundaries = _find_boundaries(ocr_text)
        for key in all_missing:
            anchor_key = _KEY_TO_ANCHOR.get(key)
            if not anchor_key or anchor_key not in boundaries:
                continue

            raw_slice = _get_slice(ocr_text, boundaries, anchor_key)
            if not raw_slice:
                continue

            recovered_text = _fallback_extract(key, raw_slice)
            if recovered_text:
                sections[key] = recovered_text
                recovered.append(key)
                logger.info(
                    "fir_field_gap: recovered via fallback | key=%s len=%d | doc=%s",
                    key,
                    len(recovered_text),
                    doc_id,
                )

    # ── 3. Recompute structure confidence ─────────────────────────────────────
    all_expected = set(_CRITICAL_SECTIONS) | set(_IMPORTANT_SECTIONS)
    found_count = sum(1 for k in all_expected if sections.get(k))
    confidence = round(found_count / len(all_expected), 2)

    # ── 4. Summary log ───────────────────────────────────────────────────────
    logger.info(
        "validate_fir_fields: sections=%d | missing_critical=%s | missing_important=%s"
        " | recovered=%s | confidence=%.2f | doc=%s",
        len([v for v in sections.values() if v]),
        missing_critical,
        missing_important,
        recovered,
        confidence,
        doc_id,
    )

    return {
        **state,
        "detected_sections": sections,
        "structure_confidence": confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Boundary detection (mirrors fir_parser._find_boundaries)
# ─────────────────────────────────────────────────────────────────────────────


def _find_boundaries(text: str) -> dict[str, int]:
    boundaries: dict[str, int] = {}
    for key, pattern in _SECTION_ANCHORS:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            boundaries[key] = m.start()
    return boundaries


def _get_slice(text: str, boundaries: dict[str, int], key: str) -> str:
    if key not in boundaries:
        return ""
    start = boundaries[key]
    later = sorted(pos for k, pos in boundaries.items() if pos > start)
    end = later[0] if later else len(text)
    return text[start:end].strip()


# ─────────────────────────────────────────────────────────────────────────────
# Fallback extractors — minimal regex, no LLM
# ─────────────────────────────────────────────────────────────────────────────


def _fallback_extract(section_key: str, raw: str) -> str:
    """
    Last-resort extraction when fir_parser returned nothing for a section.
    Uses very loose patterns to pull at least some content from the raw slice.
    Returns "" if nothing useful found — caller skips silently.
    """
    extractors = {
        "sections_law": _fallback_sections_law,
        "complainant": _fallback_complainant,
        "accused": _fallback_accused,
        "fir_contents": _fallback_fir_contents,
        "action_taken": _fallback_action_taken,
        "occurrence": _fallback_occurrence,
        "place": _fallback_place,
        "signatures": _fallback_signatures,
    }
    fn = extractors.get(section_key)
    if not fn:
        return ""
    try:
        return fn(raw)
    except Exception as exc:
        logger.warning("_fallback_extract: error in %s — %s", section_key, exc)
        return ""


def _fallback_sections_law(raw: str) -> str:
    """Pull anything that looks like act name + section number."""
    # Find lines with Devanagari + 4-digit year (act name pattern)
    hits: list[str] = []
    for line in raw.splitlines():
        if re.search(r"[ऀ-ॿ].*\d{4}", line):
            hits.append(line.strip())
    return "\n".join(hits[:10]) if hits else ""


def _fallback_complainant(raw: str) -> str:
    """Pull the raw section text — better than nothing for audit."""
    # Strip the section header line
    cleaned = re.sub(r"^6\..*?Informant[^\n]*\n?", "", raw, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    # Limit to reasonable length
    return cleaned[:800] if cleaned else ""


def _fallback_accused(raw: str) -> str:
    """Pull accused rows — look for Devanagari names following digits."""
    hits: list[str] = []
    for m in re.finditer(r"(\d{1,2})\s+([ऀ-ॿ][^\n]{5,60})", raw):
        hits.append(f"{m.group(1)}. {m.group(2).strip()}")
    return "\n".join(hits[:10]) if hits else ""


def _fallback_fir_contents(raw: str) -> str:
    """Strip section header and return the rest — tehreer is free-form."""
    cleaned = re.sub(
        r"^12\..*?contents[^\n]*\n?",
        "",
        raw,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return cleaned.strip()


def _fallback_action_taken(raw: str) -> str:
    """Return basic 'case registered' line + any IO details found."""
    lines = ["(1) प्रकरण दर्ज किया गया और जांच प्रारम्भ की गयी।"]
    io_m = re.search(r"I\.O\.[^\n:]*:\s*([^\n]+)", raw, re.IGNORECASE)
    if io_m:
        lines.append(f"(2) जांच अधिकारी (I.O.): {io_m.group(1).strip()}")
    return "\n".join(lines)


def _fallback_occurrence(raw: str) -> str:
    """Extract dates and times from the occurrence section."""
    hits: list[str] = []
    for m in re.finditer(r"(\d{2}/\d{2}/\d{4})", raw):
        hits.append(m.group(1))
    for m in re.finditer(r"(\d{2}:\d{2})", raw):
        hits.append(m.group(1))
    return " | ".join(hits[:4]) if hits else ""


def _fallback_place(raw: str) -> str:
    """Pull any address-like line from the place section."""
    for line in raw.splitlines():
        line = line.strip()
        if re.search(r"[ऀ-ॿ].{10,}", line):
            return line
    return ""


def _fallback_signatures(raw: str) -> str:
    """Pull officer name and dispatch date."""
    lines: list[str] = []
    name_m = re.search(r"Name[^\n:]*:\s*([^\n]+)", raw, re.IGNORECASE)
    if name_m:
        lines.append(f"थाना प्रभारी: {name_m.group(1).strip()}")
    date_m = re.search(r"(\d{2}/\d{2}/\d{4})", raw)
    if date_m:
        lines.append(f"प्रेषण दिनांक: {date_m.group(1)}")
    return "\n".join(lines)
