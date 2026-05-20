"""
Node 6 — assemble_html

Deterministic, pure-Python node — no LLM call.

Takes formatted_sections (Node 4 output, possibly improved by Node 5 retry)
and assembles them into Tiptap-compatible HTML that is stored as
TypedVersion.typed_content.

FIR documents follow a canonical Allahabad HC section order.
Generic documents preserve the order sections were detected in.

── Rendering modes ────────────────────────────────────────────────────────────
FIR is a fixed-schema government form, NOT a freeform document.
Two distinct rendering strategies are used:

  TABULAR sections (metadata, tables, field:value rows):
    Each line → its own <p> tag.
    Preserves row structure of acts table, accused table, field rows.
    Example:
      नाम (Name): Satendra Singh          → <p>नाम (Name): Satendra Singh</p>
      पिता का नाम (Father): Ram Singh     → <p>पिता का नाम (Father): Ram Singh</p>

  NARRATIVE sections (tehreer / FIR contents):
    Blank-line-separated blocks → each block becomes one <p>.
    Continuous prose within a block is joined with spaces.
    Single newlines within a paragraph block are NOT treated as row breaks.

The old approach (join all lines within a block with spaces) was collapsing
field rows like "नाम: X\nपिता: Y\nजन्म: Z" into a single unreadable <p>.

── Processing notes ────────────────────────────────────────────────────────────
processing_notes is also finalised here — it summarises the full pipeline run
(quality, language, batches, retries, confidence, timestamp) for the audit
trail stored on TypedVersion.agent_notes.
"""
import logging
from datetime import datetime, timezone
from html import escape

from app.agents.typing.state import TypingAgentState

logger = logging.getLogger(__name__)

# ── FIR canonical section order (Allahabad HC / NCRB format) ─────────────────
_FIR_ORDER = [
    "header",
    "sections_law",
    "occurrence",
    "info_type",
    "place",
    "complainant",
    "accused",
    "delay_reasons",
    "properties",
    "fir_contents",
    "action_taken",
    "signatures",
    "physical_features",
    "leftovers",
]

_FIR_LABELS: dict[str, str] = {
    "header": "प्रथम सूचना रिपोर्ट (FIR)",
    "sections_law": "2. अधिनियम एवं धाराएँ (Acts & Sections)",
    "occurrence": "3. अपराध की घटना (Occurrence of Offence)",
    "info_type": "4. सूचना का प्रकार (Type of Information)",
    "place": "5. घटनास्थल (Place of Occurrence)",
    "complainant": "6. शिकायतकर्ता / सूचनाकर्ता (Complainant / Informant)",
    "accused": "7. अभियुक्त विवरण (Accused Details)",
    "delay_reasons": "8. विलंब के कारण (Reasons for Delay)",
    "properties": "9. सम्पत्ति विवरण (Property Details)",
    "fir_contents": "12. प्रथम सूचना तथ्य / तहरीर (FIR Contents)",
    "action_taken": "13. की गयी कार्यवाही (Action Taken)",
    "signatures": "14–15. हस्ताक्षर एवं प्रेषण (Signatures & Dispatch)",
    "physical_features": "अभियुक्त की शारीरिक विशेषताएँ (Physical Features)",
    "leftovers": "अन्य विवरण (Additional Details)",
}

# Sections that contain structured field:value rows or table rows.
# Each line must be rendered as its own <p> — do NOT join within a block.
_FIR_TABULAR_SECTIONS: frozenset[str] = frozenset(
    {
        "header",
        "sections_law",
        "occurrence",
        "info_type",
        "place",
        "complainant",
        "accused",
        "delay_reasons",
        "properties",
        "action_taken",
        "signatures",
        "physical_features",
    }
)

# Sections that contain free-form narrative prose.
# Blank-line-separated blocks → one <p> each.
_FIR_NARRATIVE_SECTIONS: frozenset[str] = frozenset(
    {
        "fir_contents",
        "leftovers",
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Public node
# ─────────────────────────────────────────────────────────────────────────────


def assemble_html(state: TypingAgentState) -> TypingAgentState:
    """
    Synchronous node — no I/O, no awaits.
    LangGraph supports sync nodes in async graphs.
    """
    sections = state["formatted_sections"]
    template = state["document_template"]
    doc_id = state["document_id"]

    if not sections:
        logger.info("assemble_html: no sections — empty document | doc=%s", doc_id)
        html = "<p></p>"
    elif template == "fir_ncrb":
        from app.features.documents.fir_reconstruction import render_fir_sections_html

        html = render_fir_sections_html(sections)
    else:
        html = _assemble_generic(sections)

    notes = _build_processing_notes(state)

    logger.info(
        "assemble_html: %d chars HTML | template=%s | doc=%s",
        len(html),
        template,
        doc_id,
    )

    return {**state, "final_html": html, "processing_notes": notes}


# ─────────────────────────────────────────────────────────────────────────────
# FIR assembly
# ─────────────────────────────────────────────────────────────────────────────


def _assemble_fir(sections: dict) -> str:
    """
    Assemble FIR sections in canonical HC order.
    Sections present in the dict but NOT in _FIR_ORDER are appended at end.
    Empty / whitespace-only sections are skipped.
    """
    parts: list[str] = []
    seen: set[str] = set()

    for key in _FIR_ORDER:
        text = sections.get(key, "")
        if not text or not text.strip():
            continue
        label = _FIR_LABELS.get(key, key.replace("_", " ").title())
        parts.append(_render_section(label, text, section_key=key))
        seen.add(key)

    # Any extra keys the parser detected that aren't in canonical order
    for key, text in sections.items():
        if key not in seen and text and text.strip():
            label = key.replace("_", " ").title()
            parts.append(_render_section(label, text, section_key=key))

    body = "\n".join(parts) if parts else "<p></p>"
    return f'<div class="typed-document typed-fir">\n{body}\n</div>'


# ─────────────────────────────────────────────────────────────────────────────
# Generic assembly
# ─────────────────────────────────────────────────────────────────────────────


def _assemble_generic(sections: dict) -> str:
    """
    Assemble generic document sections in detection order.
    """
    parts: list[str] = []
    for key, text in sections.items():
        if text and text.strip():
            label = key.replace("_", " ").title()
            parts.append(_render_section(label, text, section_key=key))

    body = "\n".join(parts) if parts else "<p></p>"
    return f'<div class="typed-document typed-generic">\n{body}\n</div>'


# ─────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ─────────────────────────────────────────────────────────────────────────────


def _render_section(label: str, text: str, section_key: str = "") -> str:
    """
    Render one section as <h2> + body paragraphs.

    Rendering mode is determined by section_key:

      TABULAR (field:value rows, table rows — e.g. accused, complainant, acts):
        Every non-empty line → its own <p>.
        Preserves the visual structure of the form — rows do NOT collapse.

      NARRATIVE (free-form prose — e.g. fir_contents/tehreer):
        Text split on blank lines; lines within each block joined with space.
        Mirrors a typed paragraph — suitable for continuous Hindi narrative.

      DEFAULT (unknown section_key):
        Falls back to narrative mode.
    """
    if section_key in _FIR_TABULAR_SECTIONS:
        p_tags = _tabular_to_paragraphs(text)
    else:
        p_tags = _narrative_to_paragraphs(text)

    return f"<h2>{escape(label)}</h2>\n{p_tags}"


def _tabular_to_paragraphs(text: str) -> str:
    """
    Each non-empty line → its own <p>.

    Used for structured form sections: acts table, accused table,
    field:value rows (complainant, occurrence, place, signatures, etc.)

    Before (broken):
      "नाम: Satendra Singh पिता: Ram Singh जन्म: 1980"  ← one <p>, all mashed

    After (correct):
      <p>नाम: Satendra Singh</p>
      <p>पिता: Ram Singh</p>
      <p>जन्म: 1980</p>
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f"<p>{escape(text.strip())}</p>"
    return "\n".join(f"<p>{escape(line)}</p>" for line in lines)


def _narrative_to_paragraphs(text: str) -> str:
    """
    Blank-line-separated blocks → each block becomes one <p>.
    Lines within a block are joined with a space (continuous prose).

    Used for: fir_contents (tehreer narrative), leftovers.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_blocks = text.split("\n\n")

    paragraphs: list[str] = []
    for block in raw_blocks:
        joined = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if joined:
            paragraphs.append(joined)

    if not paragraphs:
        paragraphs = [text.strip()]

    return "\n".join(f"<p>{escape(p)}</p>" for p in paragraphs)


# ─────────────────────────────────────────────────────────────────────────────
# Processing notes builder
# ─────────────────────────────────────────────────────────────────────────────


def _build_processing_notes(state: TypingAgentState) -> str:
    """
    Build a pipe-delimited audit string summarising the full pipeline run.
    Stored on TypedVersion.agent_notes for support / debugging.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [
        f"ts={ts}",
        f"quality={state['ocr_quality_score']:.2f}",
        f"lang={state['ocr_language']}",
        f"batches={state['batch_count']}",
        f"confidence={state['structure_confidence']:.2f}",
        f"sections={len(state['formatted_sections'])}",
        f"retries={state['retry_count']}",
        f"review={'pass' if state['review_passed'] else 'fail'}",
    ]
    if state.get("fetch_error"):
        parts.append(f"fetch_error={(state['fetch_error'] or '')[:60]}")
    if state.get("processing_notes"):
        # Preserve any earlier fallback notes (e.g. review_fallback:...)
        parts.append(state["processing_notes"])
    return " | ".join(parts)
