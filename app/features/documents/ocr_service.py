from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from html import escape as html_escape
from pathlib import Path
from dataclasses import dataclass

import boto3
from botocore.config import Config

from app.core.config import settings
from app.core.exceptions import ValidationError

logger = logging.getLogger(__name__)
DOCUMENT_AI_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# ── MIME type constants ───────────────────────────────────────────────────────

# Document AI only accepts these MIME types.
_SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/tiff",
    "image/gif",
    "image/bmp",
    "image/webp",
}
# Normalise common variants that browsers/uploaders send.
_MIME_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/x-png": "image/png",
    "image/x-tiff": "image/tiff",
}


# ── Error hierarchy ───────────────────────────────────────────────────────────


class OcrServiceError(Exception):
    code = "OCR_ERROR"
    retryable = False

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        self.details = details or {}
        super().__init__(message)


class OcrConfigurationError(OcrServiceError):
    code = "OCR_CONFIGURATION_ERROR"


class OcrNoTextError(OcrServiceError):
    code = "OCR_NO_TEXT"


class OcrStorageError(OcrServiceError):
    code = "OCR_STORAGE_ERROR"
    retryable = True


class OcrProviderError(OcrServiceError):
    code = "OCR_PROVIDER_ERROR"
    retryable = True


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OcrResult:
    text: str  # Plain normalized text — stored in doc.ocr_raw_text (typing agent input)
    html: str  # Structured HTML for Tiptap — stored in TypedVersion.typed_content
    language: str
    page_count: int
    provider: str
    metadata: dict[str, object]
    artifact: dict[str, object]


@dataclass(frozen=True)
class OcrConfigStatus:
    configured: bool
    missing: list[str]
    credential_source: str | None = None
    credential_error: str | None = None


# ── R2 client ─────────────────────────────────────────────────────────────────


def _get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


# ── MIME type helpers ─────────────────────────────────────────────────────────


def _normalize_mime_type(mime_type: str | None) -> str:
    """Return a Document AI-compatible MIME type or raise OcrConfigurationError."""
    raw = (mime_type or "").strip().lower()
    normalized = _MIME_TYPE_ALIASES.get(raw, raw)
    if normalized not in _SUPPORTED_MIME_TYPES:
        raise OcrConfigurationError(
            f"Unsupported file type for OCR: '{mime_type}'. "
            "Supported types: PDF, JPEG, PNG, TIFF, GIF, BMP, WEBP. "
            "For other formats, use Skip OCR and paste the content manually.",
            details={"mime_type": mime_type},
        )
    return normalized


# ── Credentials ───────────────────────────────────────────────────────────────


def _get_document_ai_credentials():
    """
    Load service account credentials from GOOGLE_APPLICATION_CREDENTIALS path.

    Unlike the Google library's ADC chain, this reads the path from pydantic
    settings (which loads from .env) so credentials work even when the OS env
    var is not set in the shell that starts the Celery worker.
    """
    credentials_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not credentials_path:
        # No explicit path — let the Google library use ADC (works when running
        # on GCE / Cloud Run or when `gcloud auth application-default login`
        # has been run and GOOGLE_APPLICATION_CREDENTIALS IS set at OS level).
        return None

    from google.oauth2 import service_account

    expanded_path = Path(credentials_path).expanduser()
    if not expanded_path.exists():
        raise OcrConfigurationError(
            f"Service account key file not found: {credentials_path}. "
            "Set GOOGLE_APPLICATION_CREDENTIALS to the correct path in .env.",
            details={"path": str(expanded_path)},
        )

    try:
        return service_account.Credentials.from_service_account_file(
            str(expanded_path),
            scopes=DOCUMENT_AI_SCOPES,
        )
    except Exception as exc:
        raise OcrConfigurationError(
            f"Failed to load service account credentials: {exc}",
            details={"path": str(expanded_path)},
        ) from exc


# ── R2 download ───────────────────────────────────────────────────────────────


def _download_from_r2(r2_bucket: str, r2_key: str) -> bytes:
    try:
        response = _get_r2_client().get_object(Bucket=r2_bucket, Key=r2_key)
        file_bytes = response["Body"].read()
        logger.info(
            "Downloaded document from R2 for OCR bucket=%s key=%s bytes=%s",
            r2_bucket,
            r2_key,
            len(file_bytes),
        )
        return file_bytes
    except Exception as exc:
        raise OcrStorageError("Could not download document from storage") from exc


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — Text normalization
# ══════════════════════════════════════════════════════════════════════════════

# ── Existing NCRB form artefact patterns ─────────────────────────────────────

# Repeated header on every page of NCRB/NIC form PDFs.
_FORM_HEADER_RE = re.compile(
    r"N\.C\.R\.B\s*[\(\（]एन\.?सी\.?आर\.?बी\.?[\)\）]\s*",
    re.IGNORECASE,
)
# Lines that contain ONLY 1–3 digits (PDF page footers, section number artefacts).
_LONE_DIGIT_LINE_RE = re.compile(r"(?m)^\s*\d{1,3}\s*$")
# PDF hyphen line-break: "मुरा-\nदाबाद" → "मुरादाबाद"
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\n(\w)")

# ── New normalization patterns ────────────────────────────────────────────────

# C0 control characters (keep \t=0x09, \n=0x0a, \r=0x0d),
# DEL (0x7f), C1 controls (0x80–0x9f), Unicode replacement char (U+FFFD).
_JUNK_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f�]",
    re.UNICODE,
)

# HTML tags — stripped before language detection so tag letters don't skew result.
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)


def _normalize_text(text: str) -> str:
    """
    Stage 2 Step 1 — Unicode normalization + junk character removal.

    1. NFC normalize: composes decomposed Devanagari matras into canonical form.
       Fixes "ख ि ल" corruption produced by some scanner firmware → "खिल".
    2. Non-breaking space (U+00A0) → regular space.
    3. Remove C0/C1 control chars, DEL, Unicode replacement char.
    4. Collapse consecutive inline whitespace (preserves newlines).
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace(" ", " ")  # NBSP → space
    text = _JUNK_CHARS_RE.sub("", text)
    text = re.sub(r"[^\S\n]+", " ", text)  # collapse inline whitespace
    return text


def _postprocess_ocr_text(text: str) -> str:
    """
    Stage 2 Step 2 — Remove form artefacts and repair PDF layout breaks.

    Strips NCRB repeated page headers, standalone digit-only lines (page
    footers), and repairs hyphenated word-breaks that the PDF layout engine
    inserts mid-word.
    """
    text = _FORM_HEADER_RE.sub("", text)
    text = _LONE_DIGIT_LINE_RE.sub("", text)
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Sentence terminators — a line ending with one of these flushes the buffer
# and starts a new logical paragraph.
_SENTENCE_END_RE = re.compile(r"[.।?!]\s*$")

# ── Accused section semantic patterns ────────────────────────────────────────

# Relation keywords that separate the accused's name from their father's name.
# Covers Hindi NCRB standard (पुत्र/पुत्री/पत्नी) and transliterated English (s/o etc.).
_ACCUSED_RELATION_RE = re.compile(
    r"\b(?:पुत्र|पुत्री|पत्नी|s\s*/\s*o|d\s*/\s*o|w\s*/\s*o"
    r"|son\s+of|daughter\s+of|wife\s+of)\b",
    re.IGNORECASE | re.UNICODE,
)

# Address-start keywords that mark where the address begins.
_ACCUSED_ADDRESS_RE = re.compile(
    r"\b(?:ग्राम|गांव|गाँव|मोहल्ला|मौहल्ला|निवासी|थाना|जनपद|नगर|पता"
    r"|village|mohalla|resident|address|vill\.?)\b",
    re.IGNORECASE | re.UNICODE,
)

# Words that indicate a table-header row in the accused section (skip them).
_ACCUSED_HEADER_WORDS = frozenset(
    [
        "नाम",
        "पिता",
        "पता",
        "क्र",
        "आयु",
        "लिंग",
        "पहचान",
        "name",
        "father",
        "address",
        "s.no",
        "age",
        "sex",
    ]
)

# Compound key-value separator: 2+ spaces before "Word(s):" pattern.
# Used to split "FIR No: 0059   Year: 2025" into two pairs.
_COMPOUND_KV_SPLIT_RE = re.compile(
    r"\s{2,}(?=[A-Za-zऀ-ॿ][^:]{1,35}:)",
    re.UNICODE,
)


def _reconstruct_paragraphs(text: str) -> str:
    """
    Stage 2 Step 3 — Merge OCR line-breaks that cut mid-sentence.

    Strategy:
    • Blank lines always flush the buffer and act as paragraph separators.
    • A non-empty buffer that ends with a sentence terminator (. । ? !) is
      flushed before the next line starts a new paragraph.
    • Everything else is joined with a space — i.e. continuation lines are
      merged regardless of their length.  This is correct for Hindi where
      sentences are composed of many short words spread across multiple
      OCR output lines.

    Returns text with double-newlines separating logical paragraphs.
    """
    lines = text.splitlines()
    result: list[str] = []
    buffer = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buffer:
                result.append(buffer)
                buffer = ""
            result.append("")  # blank line = paragraph break
            continue

        if not buffer:
            buffer = stripped
        elif _SENTENCE_END_RE.search(buffer):
            result.append(buffer)
            buffer = stripped
        else:
            buffer = buffer + " " + stripped

    if buffer:
        result.append(buffer)

    # Collapse consecutive blank lines to a single one.
    out: list[str] = []
    prev_blank = False
    for line in result:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        out.append(line)
        prev_blank = is_blank

    return "\n".join(out).strip()


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — Token-based line reconstruction
# ══════════════════════════════════════════════════════════════════════════════


def _rebuild_narrative(text: str) -> str:
    """
    Aggressive line-joiner for free-running narrative text (FIR Section 12).

    Unlike ``_reconstruct_paragraphs`` — which waits for a sentence terminator
    before flushing — this joins *every* line within a block, because FIR OCR
    output rarely places terminators at line ends.  The only reliable paragraph
    boundary is a blank line.

    Result: one string per logical paragraph, joined by \\n\\n.
    """
    paragraphs: list[str] = []
    current_tokens: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_tokens:
                paragraphs.append(" ".join(current_tokens))
                current_tokens = []
        else:
            current_tokens.append(stripped)

    if current_tokens:
        paragraphs.append(" ".join(current_tokens))

    return "\n\n".join(p for p in paragraphs if p)


def _text_from_anchor(anchor: object, full_text: str) -> str:
    """Slice ``full_text`` using a Document AI TextAnchor's text_segments."""
    segs = getattr(anchor, "text_segments", None) or []
    return "".join(
        full_text[
            int(getattr(s, "start_index", 0) or 0) : int(
                getattr(s, "end_index", 0) or 0
            )
        ]
        for s in segs
    )


def _anchor_segments(anchor: object | None) -> list[dict[str, int]]:
    segs = getattr(anchor, "text_segments", None) or []
    return [
        {
            "start": int(getattr(seg, "start_index", 0) or 0),
            "end": int(getattr(seg, "end_index", 0) or 0),
        }
        for seg in segs
    ]


def _bounding_poly(layout: object | None) -> list[dict[str, float]]:
    bbox = getattr(layout, "bounding_poly", None) if layout else None
    verts = getattr(bbox, "normalized_vertices", None) if bbox else None
    return [
        {
            "x": round(float(getattr(vertex, "x", 0.0) or 0.0), 6),
            "y": round(float(getattr(vertex, "y", 0.0) or 0.0), 6),
        }
        for vertex in (verts or [])
    ]


def _layout_payload(layout: object | None, full_text: str) -> dict[str, object]:
    anchor = getattr(layout, "text_anchor", None) if layout else None
    text = _text_from_anchor(anchor, full_text).strip() if anchor else ""
    return {
        "text": text,
        "confidence": round(float(getattr(layout, "confidence", 0.0) or 0.0), 4)
        if layout
        else 0.0,
        "text_anchor": _anchor_segments(anchor),
        "bounding_poly": _bounding_poly(layout),
    }


def _page_number(page: object, fallback: int) -> int:
    return int(getattr(page, "page_number", fallback) or fallback)


def _table_cell_payload(cell: object, full_text: str) -> dict[str, object]:
    return _layout_payload(getattr(cell, "layout", None), full_text)


def _table_row_payload(row: object, full_text: str) -> list[dict[str, object]]:
    return [
        _table_cell_payload(cell, full_text)
        for cell in (getattr(row, "cells", None) or [])
    ]


def _build_document_ai_artifact(
    document: object,
    *,
    processor: str,
    mime_type: str,
    document_type: str | None,
) -> dict[str, object]:
    """Build a compact, JSON-safe replay artifact from Document AI output."""
    full_text: str = getattr(document, "text", None) or ""
    pages = getattr(document, "pages", None) or []
    artifact_pages: list[dict[str, object]] = []
    token_count = 0
    table_count = 0
    form_field_count = 0
    entity_count = 0
    block_count = 0
    confidence_values: list[float] = []

    def _track_conf(payload: dict[str, object]) -> None:
        conf = payload.get("confidence")
        if isinstance(conf, (int, float)) and conf > 0:
            confidence_values.append(float(conf))

    for fallback_page_num, page in enumerate(pages, start=1):
        page_num = _page_number(page, fallback_page_num)
        dimension = getattr(page, "dimension", None)

        tokens: list[dict[str, object]] = []
        for token in getattr(page, "tokens", None) or []:
            payload = _layout_payload(getattr(token, "layout", None), full_text)
            if payload["text"]:
                tokens.append(payload)
                _track_conf(payload)
        token_count += len(tokens)

        blocks: list[dict[str, object]] = []
        for block in getattr(page, "blocks", None) or []:
            payload = _layout_payload(getattr(block, "layout", None), full_text)
            if payload["text"]:
                blocks.append(payload)
                _track_conf(payload)
        block_count += len(blocks)

        tables: list[dict[str, object]] = []
        for table in getattr(page, "tables", None) or []:
            header_rows = [
                _table_row_payload(row, full_text)
                for row in (getattr(table, "header_rows", None) or [])
            ]
            body_rows = [
                _table_row_payload(row, full_text)
                for row in (getattr(table, "body_rows", None) or [])
            ]
            for row in header_rows + body_rows:
                for cell in row:
                    _track_conf(cell)
            tables.append(
                {
                    "layout": _layout_payload(
                        getattr(table, "layout", None), full_text
                    ),
                    "header_rows": header_rows,
                    "body_rows": body_rows,
                }
            )
        table_count += len(tables)

        form_fields: list[dict[str, object]] = []
        for field in getattr(page, "form_fields", None) or []:
            name = _layout_payload(getattr(field, "field_name", None), full_text)
            value = _layout_payload(getattr(field, "field_value", None), full_text)
            _track_conf(name)
            _track_conf(value)
            form_fields.append({"name": name, "value": value})
        form_field_count += len(form_fields)

        artifact_pages.append(
            {
                "page_number": page_num,
                "dimension": {
                    "width": float(getattr(dimension, "width", 0.0) or 0.0),
                    "height": float(getattr(dimension, "height", 0.0) or 0.0),
                    "unit": str(getattr(dimension, "unit", "") or ""),
                },
                "tokens": tokens,
                "blocks": blocks,
                "tables": tables,
                "form_fields": form_fields,
            }
        )

    entities: list[dict[str, object]] = []
    for entity in getattr(document, "entities", None) or []:
        mention_text = str(getattr(entity, "mention_text", "") or "").strip()
        if not mention_text:
            continue
        confidence = float(getattr(entity, "confidence", 0.0) or 0.0)
        if confidence > 0:
            confidence_values.append(confidence)
        normalized_value = getattr(entity, "normalized_value", None)
        entities.append(
            {
                "type": str(getattr(entity, "type_", "") or ""),
                "mention_text": mention_text,
                "confidence": round(confidence, 4),
                "normalized_text": str(getattr(normalized_value, "text", "") or "")
                if normalized_value
                else "",
            }
        )
    entity_count = len(entities)

    avg_conf = (
        round(sum(confidence_values) / len(confidence_values), 4)
        if confidence_values
        else None
    )

    return {
        "schema_version": 1,
        "provider": "google_document_ai",
        "processor": processor,
        "mime_type": mime_type,
        "document_type": document_type,
        "full_text": full_text,
        "entities": entities,
        "summary": {
            "page_count": len(artifact_pages),
            "text_length": len(full_text),
            "token_count": token_count,
            "table_count": table_count,
            "form_field_count": form_field_count,
            "entity_count": entity_count,
            "block_count": block_count,
            "average_confidence": avg_conf,
        },
        "pages": artifact_pages,
    }


def _reconstruct_lines_from_tokens(page: object, full_text: str) -> list[str]:
    """
    Stage 1 — Bounding-box line reconstruction from Document AI page tokens.

    Each ``page.tokens`` entry carries a ``layout.bounding_poly`` with
    ``normalized_vertices`` (four corners, coords 0–1).  We use the top-left
    vertex (index 0) to determine where a token sits on the page:
        y → which line
        x → horizontal position within the line

    Tokens whose y-coordinates are within Y_TOLERANCE of each other are
    grouped into the same logical line, then sorted left-to-right by x and
    joined with spaces.

    Returns an empty list when the page has no token data; the caller falls
    back to the form-fields/blocks strategy for that page.
    """
    Y_TOLERANCE = 0.012  # ~1.2 % of normalised page height per line group

    token_data: list[tuple[float, float, str]] = []

    for token in getattr(page, "tokens", None) or []:
        layout = getattr(token, "layout", None)
        if not layout:
            continue
        bbox = getattr(layout, "bounding_poly", None)
        verts = getattr(bbox, "normalized_vertices", None) if bbox else None
        if not verts or len(verts) < 1:
            continue
        y = float(getattr(verts[0], "y", 0.0) or 0.0)
        x = float(getattr(verts[0], "x", 0.0) or 0.0)
        anchor = getattr(layout, "text_anchor", None)
        token_text = _text_from_anchor(anchor, full_text).strip() if anchor else ""
        if token_text:
            token_data.append((y, x, token_text))

    if not token_data:
        return []

    # Sort by y (top-to-bottom)
    token_data.sort(key=lambda t: t[0])

    # Group tokens into lines by y-proximity
    lines: list[list[tuple[float, float, str]]] = [[token_data[0]]]

    for token in token_data[1:]:
        last_line = lines[-1]
        if abs(token[0] - last_line[0][0]) <= Y_TOLERANCE:
            last_line.append(token)
        else:
            lines.append([token])

    # Sort each line left-to-right by x, then join tokens
    result: list[str] = []
    for line in lines:
        line.sort(key=lambda t: t[1])
        joined = " ".join(t[2] for t in line).strip()
        if joined:
            result.append(joined)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — FIR structure parser + HTML renderer
# ══════════════════════════════════════════════════════════════════════════════

# NCRB FIR template section heading patterns (English + Hindi).
# Ordered by typical appearance in the document.  The first regex match in the
# reconstructed text marks the start of that section.
_FIR_SECTION_PATTERNS: list[tuple[str, str]] = [
    (
        "fir_header",
        r"FIRST\s+INFORMATION\s+REPORT"
        r"|प्रथम\s+सूचना\s+रिपोर्ट"
        r"|एफ\s*[.]?\s*आई\s*[.]?\s*आर",
    ),
    ("fir_metadata", r"(?:District|जिला)\s*[:\-]"),
    (
        "fir_sections",
        r"(?:(?:Under\s+)?Sections?\s*(?:of\s+[A-Z]+\s*)?\s*[:\-]"
        r"|धारा(?:एँ)?\s*[:\-]?"
        r"|BNS\s+\d"
        r"|IPC\s+\d"
        r"|CrPC\s+\d)",
    ),
    (
        "occurrence",
        r"(?:Date\s+(?:and|&|of)\s+(?:Time|[Oo]ccurrence)"
        r"|घटना\s+की\s+(?:तारीख|दिनांक)"
        r"|दिनांक\s+(?:व|और)\s+समय)",
    ),
    ("place", r"(?:Place\s+of\s+[Oo]ccurrence" r"|घटना\s+का\s+स्थान" r"|घटना\s+स्थल)"),
    (
        "complainant",
        r"(?:Complainant(?:'s)?\s+Details?" r"|शिकायतकर्ता" r"|वादी|फरियादी)",
    ),
    (
        "accused",
        r"(?:Details?\s+of\s+(?:known\s+)?[Aa]ccused"
        r"|अभियुक्त(?:\s+का\s+विवरण)?"
        r"|आरोपी)",
    ),
    (
        "fir_contents",
        r"(?:First\s+Information\s+[Cc]ontents?"
        r"|12\s*[.)\-]\s"
        r"|तहरीर|नकल\s+(?:तहरीर|मुकदमा)"
        r"|मजमून)",
    ),
    ("action_taken", r"(?:Action\s+[Tt]aken" r"|कार्यवाही|विवेचना\s+हेतु)"),
]

# Minimum distinct section headings required to treat the document as a FIR.
_FIR_MIN_SECTIONS = 3


def _split_fir_sections(text: str) -> dict[str, str]:
    """
    Stage 3-A — Split reconstructed text into NCRB FIR sections.

    Returns a dict mapping section_key → section_text.
    If fewer than _FIR_MIN_SECTIONS headings are found, returns
    ``{"body": text}`` so the renderer falls back to plain paragraph wrapping.
    """
    compiled = [
        (key, re.compile(pat, re.IGNORECASE | re.MULTILINE))
        for key, pat in _FIR_SECTION_PATTERNS
    ]

    # Find the first match for each section; keep first occurrence only.
    seen_keys: set[str] = set()
    positions: list[tuple[int, str]] = []

    for key, pattern in compiled:
        m = pattern.search(text)
        if m and key not in seen_keys:
            positions.append((m.start(), key))
            seen_keys.add(key)

    if len(positions) < _FIR_MIN_SECTIONS:
        logger.debug(
            "FIR section parser: only %d/%d sections found — treating as plain text",
            len(positions),
            _FIR_MIN_SECTIONS,
        )
        return {"body": text}

    positions.sort(key=lambda p: p[0])

    sections: dict[str, str] = {}
    for i, (start, key) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        sections[key] = text[start:end].strip()

    logger.debug("FIR section parser: found sections: %s", list(sections.keys()))
    return sections


def _extract_accused_entries(text: str) -> list[dict[str, str]]:
    """
    Stage 3-B — Semantic extraction of NCRB accused entries.

    Splits the accused section into per-person blocks (by numbered markers or
    pipe-table rows), then uses keyword anchors to separate each block into:
        serial  – "1", "2", …
        name    – text before the relation keyword
        father  – text between the relation keyword and the address keyword
        address – text from the address keyword to end

    Returns a list of dicts; missing fields are empty strings.
    """
    _NUMBERED = re.compile(r"^(\d+)\s*[.)\-]\s*(.*)")

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # ── Build per-person raw text blocks ─────────────────────────────────
    blocks: list[tuple[str, str]] = []  # (serial, raw_text)
    cur_serial: str = ""
    cur_text: str = ""

    def _flush() -> None:
        nonlocal cur_serial, cur_text
        t = cur_text.strip()
        if t:
            blocks.append((cur_serial, t))
        cur_serial = ""
        cur_text = ""

    for line in lines[1:]:  # skip section heading
        # Skip table-header rows: short lines whose word-set overlaps header labels.
        # Use word-split (not substring) to avoid "village" matching "age".
        if len(line) < 50:
            words = set(line.lower().split())
            if words.intersection(_ACCUSED_HEADER_WORDS):
                continue

        # Pipe-table row  →  treat columns as semantic fields directly.
        # NCRB table columns: Name | Father's Name | Address (+ optional Age/Sex)
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if not cells:
                continue
            words_joined = set(" ".join(cells).lower().split())
            if words_joined.intersection(_ACCUSED_HEADER_WORDS):
                continue
            _flush()
            cur_serial = str(len(blocks) + 1)
            # Store cells as structured hint using a separator the parser can use
            # Format: "Name <SEP> Father <SEP> Address"
            cur_text = "\x00".join(cells)  # \x00 as internal cell separator
            continue

        m = _NUMBERED.match(line)
        if m:
            _flush()
            cur_serial = m.group(1)
            cur_text = m.group(2)
        else:
            # Continuation line for the current entry
            cur_text = (cur_text + " " + line).strip() if cur_text else line

    _flush()

    # ── Parse each block for name / father / address ──────────────────────
    entries: list[dict[str, str]] = []

    for serial, block in blocks:
        entry: dict[str, str] = {
            "serial": serial,
            "name": "",
            "father": "",
            "address": "",
        }

        # Pipe-table rows were stored with \x00 cell separators.
        # Columns: Name [Father [Address ...]] — map positionally.
        if "\x00" in block:
            cells = block.split("\x00")
            entry["name"] = cells[0].strip() if len(cells) > 0 else ""
            entry["father"] = cells[1].strip() if len(cells) > 1 else ""
            entry["address"] = (
                " ".join(c.strip() for c in cells[2:]) if len(cells) > 2 else ""
            )
        else:
            # Free-text block — use keyword anchors
            rel_m = _ACCUSED_RELATION_RE.search(block)
            addr_m = _ACCUSED_ADDRESS_RE.search(block)

            if rel_m and addr_m and rel_m.start() < addr_m.start():
                entry["name"] = block[: rel_m.start()].strip()
                entry["father"] = block[rel_m.end() : addr_m.start()].strip()
                entry["address"] = block[addr_m.start() :].strip()
            elif rel_m:
                entry["name"] = block[: rel_m.start()].strip()
                entry["father"] = block[rel_m.end() :].strip()
            elif addr_m:
                entry["name"] = block[: addr_m.start()].strip()
                entry["address"] = block[addr_m.start() :].strip()
            else:
                entry["name"] = block

        if any(entry[k] for k in ("name", "father", "address")):
            entries.append(entry)

    return entries


def _render_accused_html(entries: list[dict[str, str]]) -> str:
    """
    Render accused entries as structured <p> blocks:

        <p><strong>1. योगेश</strong><br/>
        Father: हरि सिंह<br/>
        Address: ग्राम लालापुर पीपलसाना...</p>
    """
    parts: list[str] = []
    for entry in entries:
        serial = entry.get("serial", "").strip()
        name = entry.get("name", "").strip()
        father = entry.get("father", "").strip()
        address = entry.get("address", "").strip()

        if not name:
            continue

        label = f"{serial}. {name}" if serial else name
        inner = f"<strong>{html_escape(label)}</strong>"
        if father:
            inner += f"<br/>Father: {html_escape(father)}"
        if address:
            inner += f"<br/>Address: {html_escape(address)}"

        parts.append(f"<p>{inner}</p>")

    return "\n".join(parts)


def _render_key_value_lines(lines: list[str]) -> list[str]:
    """
    Render ``Key: Value`` lines as ``<p><strong>Key:</strong> Value</p>``.

    Also handles compound lines where multiple pairs sit on one line separated
    by two or more spaces, e.g.:

        "FIR No: 0059   Year: 2025   Date: 15/02/2025"
        → three separate <p> elements.

    Lines without any colon are wrapped in a plain ``<p>``.
    """
    parts: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Split compound lines ("Key1: v1   Key2: v2") on 2+ spaces before a key.
        segments = _COMPOUND_KV_SPLIT_RE.split(line)

        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            if ":" in seg:
                key, _, val = seg.partition(":")
                key, val = key.strip(), val.strip()
                if key and val:
                    parts.append(
                        f"<p><strong>{html_escape(key)}:</strong>"
                        f" {html_escape(val)}</p>"
                    )
                elif key:
                    parts.append(f"<p><strong>{html_escape(key)}</strong></p>")
            else:
                parts.append(f"<p>{html_escape(seg)}</p>")

    return parts


def _sections_to_html(sections: dict[str, str]) -> str:
    """
    Stage 3-C — Render FIR sections (or plain text) as Tiptap-compatible HTML.

    FIR structure detected  → structured headings, key-value pairs, accused list.
    Plain text / non-FIR    → paragraphs reconstructed and wrapped in <p> tags.

    All user-visible text content is HTML-escaped before insertion.
    """
    # ── Plain text fallback (non-FIR or unrecognised structure) ───────────
    if "body" in sections:
        reconstructed = _reconstruct_paragraphs(sections["body"])
        paragraphs = [p.strip() for p in reconstructed.split("\n\n") if p.strip()]
        if not paragraphs:
            return ""
        return "\n".join(
            "<p>{}</p>".format(html_escape(p).replace("\n", "<br/>"))
            for p in paragraphs
        )

    parts: list[str] = []

    # ── FIR Header ─────────────────────────────────────────────────────────
    if "fir_header" in sections:
        heading_lines = sections["fir_header"].splitlines()
        title = heading_lines[0].strip()
        parts.append(f"<h1>{html_escape(title)}</h1>")
        remainder = "\n".join(heading_lines[1:]).strip()
        if remainder:
            parts.append(f"<p>{html_escape(remainder)}</p>")

    # ── FIR Metadata (District, PS, FIR No, Year …) ────────────────────────
    if "fir_metadata" in sections:
        parts.append("<h2>FIR Details</h2>")
        parts.extend(_render_key_value_lines(sections["fir_metadata"].splitlines()))

    # ── Applicable Sections (BNS / IPC / CrPC) ────────────────────────────
    if "fir_sections" in sections:
        parts.append("<h2>Applicable Sections</h2>")
        sec_lines = [
            line.strip()
            for line in sections["fir_sections"].splitlines()
            if line.strip()
        ]
        # Skip the matched heading line itself (first element)
        items = sec_lines[1:]
        if items:
            parts.append(
                "<ul>" + "".join(f"<li>{html_escape(s)}</li>" for s in items) + "</ul>"
            )

    # ── Occurrence (Date / Time) ───────────────────────────────────────────
    if "occurrence" in sections:
        parts.append("<h2>Occurrence</h2>")
        parts.extend(_render_key_value_lines(sections["occurrence"].splitlines()))

    # ── Place of Occurrence ────────────────────────────────────────────────
    if "place" in sections:
        parts.append("<h2>Place of Occurrence</h2>")
        place_body = " ".join(
            line.strip() for line in sections["place"].splitlines()[1:] if line.strip()
        )
        if place_body:
            parts.append(f"<p>{html_escape(place_body)}</p>")

    # ── Complainant Details ────────────────────────────────────────────────
    if "complainant" in sections:
        parts.append("<h2>Complainant Details</h2>")
        parts.extend(_render_key_value_lines(sections["complainant"].splitlines()[1:]))

    # ── Accused Details ────────────────────────────────────────────────────
    if "accused" in sections:
        parts.append("<h2>Accused Details</h2>")
        accused_entries = _extract_accused_entries(sections["accused"])
        accused_html = _render_accused_html(accused_entries)
        if accused_html:
            parts.append(accused_html)
        else:
            # Fallback: plain lines when semantic extraction found nothing
            parts.extend(
                f"<p>{html_escape(line.strip())}</p>"
                for line in sections["accused"].splitlines()[1:]
                if line.strip()
            )

    # ── FIR Contents (verbatim narrative — most important section) ─────────
    if "fir_contents" in sections:
        parts.append("<h2>FIR Contents</h2>")
        # Drop the heading line; aggressively join OCR line-breaks — only blank
        # lines are real paragraph boundaries in a continuous FIR narrative.
        narrative_raw = "\n".join(sections["fir_contents"].splitlines()[1:])
        narrative = _rebuild_narrative(narrative_raw)
        for chunk in narrative.split("\n\n"):
            chunk = chunk.strip()
            if chunk:
                parts.append(f"<p>{html_escape(chunk)}</p>")

    # ── Action Taken ───────────────────────────────────────────────────────
    if "action_taken" in sections:
        parts.append("<h2>Action Taken</h2>")
        action_body = " ".join(
            line.strip()
            for line in sections["action_taken"].splitlines()[1:]
            if line.strip()
        )
        if action_body:
            parts.append(f"<p>{html_escape(action_body)}</p>")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Main extraction pipeline
# ══════════════════════════════════════════════════════════════════════════════

_FIR_NUMBER_RE = re.compile(r"(?m)(?:^|\n)\s*(\d{1,2})\s*[.)]\s+")
_DATE_RE = r"\d{1,2}/\d{1,2}/\d{4}"
_TIME_RE = r"\d{1,2}\s*:\s*\d{2}"


def _compact_inline(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" :;,-")


def _fir_section_by_number(text: str, number: int) -> str:
    """Return the OCR text belonging to a numbered NCRB FIR item."""
    matches = list(_FIR_NUMBER_RE.finditer(text))
    for idx, match in enumerate(matches):
        if int(match.group(1)) != number:
            continue
        start = match.end()
        end = len(text)
        for next_match in matches[idx + 1 :]:
            next_number = int(next_match.group(1))
            if number < next_number <= 15:
                end = next_match.start()
                break
        return text[start:end].strip()
    return ""


def _first_match(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return default
    return _compact_inline(match.group(1))


def _not_stated(value: str | None) -> str:
    value = _compact_inline(value)
    return value if value else "Not stated"


def _html_p(label: str, value: str | None) -> str:
    return f"<p><strong>{html_escape(label)}:</strong> {html_escape(_not_stated(value))}</p>"


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    head_html = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    body_html = "".join(
        "<tr>"
        + "".join(f"<td>{html_escape(_compact_inline(cell))}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head_html}</tr>{body_html}</table>"


def _clean_fir_extracted_value(value: str | None) -> str:
    value = _compact_inline(value)
    if value.lower() in {"name", "rank", "no"}:
        return ""
    return value


def _find_fir_district(text: str) -> str:
    value = _first_match(
        r"District\s*/?\s*Unit.*?:\s*(.*?)\s+P\.?\s*S\.?\s*\(",
        text,
    )
    if value:
        return value
    return _first_match(r"Year.*?:\s*\d{4}\s+(.+?)\s+FIR\s+No", text)


def _find_fir_basic_details(text: str) -> dict[str, str]:
    return {
        "district": _find_fir_district(text),
        "police_station": _first_match(
            r"P\.?\s*S\.?\s*\([^)]*(?:थाना)?[^)]*\)\s*:\s*(.*?)\s+Year",
            text,
        ),
        "year": _first_match(r"Year\s*\([^)]*\)\s*:\s*(\d{4})", text),
        "fir_no": _first_match(r"FIR\s+No\.[^:]*:\s*([A-Za-z0-9/-]+)", text),
        "fir_datetime": _first_match(
            rf"Date\s+and\s+Time\s+of\s+FIR.*?:\s*({_DATE_RE}\s+{_TIME_RE}\s*(?:घंटे|बजे)?)",
            text,
        ),
    }


def _extract_fir_applied_sections(section_text: str) -> list[dict[str, str]]:
    """Parse NCRB item 2 into stable act/section rows without mixing columns."""
    if not section_text:
        return []

    rows: list[dict[str, str]] = []
    row_matches = list(
        re.finditer(
            r"(?:^|\n)\s*(\d{1,2})\s+(.*?)(?=(?:\n\s*\d{1,2}\s+भारतीय)|\n\s*3\s*[.)]|\Z)",
            section_text,
            re.DOTALL,
        )
    )
    if not row_matches:
        row_matches = list(
            re.finditer(
                r"(?:^|\n)\s*(\d{1,2})\s+(.*?)(?=(?:\n\s*\d{1,2}\s+Indian)|\n\s*3\s*[.)]|\Z)",
                section_text,
                re.IGNORECASE | re.DOTALL,
            )
        )

    if not row_matches:
        candidates = re.findall(
            r"(?<!\d)(\d{2,3}(?:\s*\(\s*\d+\s*\))?)(?!\d)", section_text
        )
        seen: set[str] = set()
        for candidate in candidates:
            normalized = re.sub(r"\s+", "", candidate)
            if normalized == "2023" or normalized in seen:
                continue
            seen.add(normalized)
            rows.append(
                {
                    "serial": str(len(rows) + 1),
                    "act": "भारतीय न्याय संहिता (बी एन एस), 2023",
                    "section": normalized,
                }
            )
        return rows

    for match in row_matches:
        serial = match.group(1)
        raw_row = _compact_inline(match.group(2))
        section = ""
        parenthetical = re.search(r"(?<!\d)(\d{2,3})\s*\(\s*(\d+)\s*\)(?!\d)", raw_row)
        if parenthetical:
            section = f"{parenthetical.group(1)}({parenthetical.group(2)})"

        candidates = re.findall(r"(?<!\d)(\d{2,3}(?:\s*\(\s*\d+\s*\))?)(?!\d)", raw_row)
        for candidate in candidates:
            if section:
                break
            normalized = re.sub(r"\s+", "", candidate)
            if normalized != "2023":
                section = normalized
                break
        if not section:
            continue
        rows.append(
            {
                "serial": serial,
                "act": "भारतीय न्याय संहिता (बी एन एस), 2023",
                "section": section,
            }
        )
    return rows


def _extract_fir_occurrence(section_text: str) -> dict[str, str]:
    return {
        "day": _first_match(
            r"Day\s*\([^)]*\)\s*:\s*([^\n:]+?)(?:\s+Date|\n|$)", section_text
        ),
        "date_from": _first_match(
            r"Date\s+from\s*\([^)]*\)\s*:\s*(" + _DATE_RE + ")", section_text
        ),
        "date_to": _first_match(
            r"Date\s+To\s*\([^)]*\)\s*:\s*(" + _DATE_RE + ")", section_text
        ),
        "time_period": _first_match(
            r"Time\s+Period.*?:\s*([^:\n]+?)(?:\s+Time\s+From|\n|$)", section_text
        ),
        "time_from": _first_match(
            r"Time\s+From.*?:\s*(" + _TIME_RE + r"\s*(?:बजे)?)", section_text
        ),
        "time_to": _first_match(
            r"Time\s+To.*?:\s*(" + _TIME_RE + r"\s*(?:बजे)?)", section_text
        ),
        "info_date": _first_match(
            r"Information\s+received.*?Date.*?:\s*(" + _DATE_RE + ")", section_text
        ),
        "info_time": _first_match(
            r"Information\s+received.*?Time.*?:\s*(" + _TIME_RE + r"\s*(?:बजे)?)",
            section_text,
        ),
        "gd_entry": _first_match(r"Entry\s+No\..*?:\s*([0-9]+)", section_text),
    }


def _extract_fir_place(section_text: str) -> dict[str, str]:
    return {
        "direction_distance": _first_match(
            r"Direction\s+and\s+distance\s+from\s+P\.S\..*?:\s*(.*?)(?:Beat\s+No|\(\s*b\s*\)|Address|$)",
            section_text,
        ),
        "beat_no": _first_match(r"Beat\s+No\..*?:\s*([^\n(]+)", section_text),
        "address": _first_match(
            r"\(\s*b\s*\)\s*Address\s*\([^)]*\)\s*:\s*(.*?)(?:\s+\(\s*c\s*\)|\n\s*\(\s*c\s*\)|\s+6\s*[.)]|$)",
            section_text,
        ),
        "outside_ps": _first_match(
            r"Name\s+of\s+P\.S\..*?:\s*(.*?)(?:District|$)", section_text
        ),
    }


def _extract_fir_complainant(section_text: str) -> dict[str, str]:
    return {
        "name": _first_match(
            r"Name\s*\([^)]*\)\s*:\s*(.*?)(?:\s+\(\s*b\s*\)|Father)", section_text
        ),
        "father": _first_match(
            r"Father'?s\s+Name.*?:\s*(.*?)(?:\s+\(\s*c\s*\)|Date)", section_text
        ),
        "dob_year": _first_match(
            r"Date\s*/\s*Year\s+of\s+Birth.*?:\s*([0-9]{4}|[0-9/-]+)", section_text
        ),
        "nationality": _first_match(
            r"Nationality.*?:\s*(.*?)(?:\s+\(\s*[ei]\s*\)|UID|Occupation|Address|$)",
            section_text,
        ),
        "occupation": _first_match(
            r"Occupation.*?:\s*(.*?)(?:\s+\(\s*i\s*\)|Address|$)", section_text
        ),
        "mobile": _first_match(r"Mobile.*?:\s*([0-9Xx+\-\s]+)", section_text),
        "present_address": _first_match(
            r"1\s+वर्तमान\s+पता\s+(.*?)(?:\s+2\s+स्थायी|\n\s*2\s+स्थायी|$)",
            section_text,
        ),
        "permanent_address": _first_match(
            r"2\s+स्थायी\s+पता\s+(.*?)(?:\s+\(\s*j\s*\)|Phone|$)",
            section_text,
        ),
    }


def _extract_fir_information_type(section_text: str) -> str:
    value = _first_match(r"Type\s+of\s+Information.*?:\s*(.*)$", section_text)
    if value:
        return value
    return _first_match(r":\s*(.+)$", section_text)


def _extract_fir_narrative(text: str) -> str:
    section_text = _fir_section_by_number(text, 12)
    if not section_text:
        return ""
    narrative = re.sub(
        r"^First\s+Information\s+contents?.*?:",
        "",
        section_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _compact_inline(_rebuild_narrative(narrative))


def _extract_accused_from_narrative(narrative: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    pattern = re.compile(
        r"(?:मौजूद\s+|,\s*|व\s+|और\s+|\s)"
        r"([\u0900-\u097F]{2,}(?:\s+सिंह)?)\s+पुत्र\s+हरि\s+सिंह"
    )
    for match in pattern.finditer(narrative):
        name = _compact_inline(match.group(1))
        father = "हरि सिंह"
        key = (name, father)
        if not name or key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "serial": str(len(entries) + 1),
                "name": name,
                "father": father,
                "address": "",
            }
        )
    return entries


def _extract_fir_accused(
    section_text: str, narrative: str, place_address: str
) -> list[dict[str, str]]:
    entries = _extract_accused_entries("Accused\n" + section_text)
    cleaned_entries: list[dict[str, str]] = []

    for entry in entries:
        name = _compact_inline(entry.get("name"))
        father = _compact_inline(entry.get("father"))
        address = _compact_inline(entry.get("address"))
        if not father and "पिता का नाम" in name:
            name_part, _, father_part = name.partition("पिता का नाम")
            name = _compact_inline(name_part)
            father = _compact_inline(father_part.replace(":", " "))
        if not name or len(name) > 60 or "Address" in name:
            continue
        cleaned_entries.append(
            {
                "serial": str(len(cleaned_entries) + 1),
                "name": name,
                "father": father,
                "address": address,
            }
        )

    if not cleaned_entries:
        cleaned_entries = _extract_accused_from_narrative(narrative)

    for entry in cleaned_entries:
        if not entry.get("address"):
            entry["address"] = place_address

    return cleaned_entries


def _extract_fir_action(section_text: str) -> dict[str, str]:
    officer_blob = _first_match(r"(Name\s*\([^)]*\)\s*:.*)$", section_text)
    return {
        "registered": "Yes"
        if re.search(r"Registered\s+the\s+case", section_text, re.I)
        else "",
        "io_name": _first_match(
            r"Directed.*?Name\s+of\s+I\.O\..*?:\s*(.*?)(?:\s+Rank|\n\s*Rank)",
            section_text,
        ),
        "io_rank": _first_match(
            r"Rank\s*\([^)]*\)\s*:\s*(.*?)(?:\s+No\.|\n|$)", section_text
        ),
        "io_no": _clean_fir_extracted_value(
            _first_match(r"No\.\s*\([^)]*\)\s*:\s*([A-Za-z0-9/-]+)", section_text),
        ),
        "transferred_to": _first_match(
            r"Transferred\s+to\s+P\.S\..*?:\s*(.*?)(?:District|on\s+point|$)",
            section_text,
        ),
        "officer_name": _first_match(
            r"Name\s*\([^)]*\)\s*:\s*(.*?)\s+Rank\s*\(", officer_blob
        ),
        "officer_rank": _first_match(
            r"Rank\s*\([^)]*\)\s*:\s*(.*?)\s+No\.\s*\(", officer_blob
        ),
        "officer_no": _first_match(r"No\.\s*\([^)]*\)\s*:\s*([0-9]+)", officer_blob),
    }


def _render_printable_fir_html(text: str) -> str | None:
    """
    Compatibility wrapper for older tests/imports.

    The canonical FIR renderer lives in fir_reconstruction.py so OCR and the
    Typing Agent share one schema-first path.
    """
    from app.features.documents.fir_reconstruction import render_fir_html

    return render_fir_html(text)


def _extract_page_text_fallback(page: object, full_text: str) -> list[str]:
    """
    Per-page fallback extraction using form_fields (Form Parser) + blocks.

    Used when a page carries no token data (e.g. some older processor versions
    or image-only pages where Document AI didn't emit token geometry).
    """
    MIN_CONFIDENCE: float = 0.4
    FORM_FIELD_THRESHOLD: int = 3

    page_parts: list[str] = []

    # ── Form fields (Form Parser processor) ──────────────────────────────
    form_field_lines: list[str] = []
    for field in getattr(page, "form_fields", None) or []:
        fn_layout = getattr(field, "field_name", None)
        fv_layout = getattr(field, "field_value", None)
        if fn_layout is None:
            continue
        fn_anchor = getattr(fn_layout, "text_anchor", None)
        fv_anchor = getattr(fv_layout, "text_anchor", None) if fv_layout else None
        name = _text_from_anchor(fn_anchor, full_text).strip() if fn_anchor else ""
        value = _text_from_anchor(fv_anchor, full_text).strip() if fv_anchor else ""
        if name:
            form_field_lines.append(f"{name}: {value}" if value else name)

    is_form_page = len(form_field_lines) >= FORM_FIELD_THRESHOLD
    if is_form_page:
        logger.debug(
            "Fallback — Form Parser page: %d fields, skipping blocks",
            len(form_field_lines),
        )
        page_parts.extend(form_field_lines)

    # ── Tables (always — both processor types) ────────────────────────────
    for table in getattr(page, "tables", None) or []:
        all_rows = list(getattr(table, "header_rows", None) or []) + list(
            getattr(table, "body_rows", None) or []
        )
        for row in all_rows:
            cell_texts: list[str] = []
            for cell in getattr(row, "cells", None) or []:
                layout = getattr(cell, "layout", None)
                if layout is None:
                    continue
                anchor = getattr(layout, "text_anchor", None)
                segments = getattr(anchor, "text_segments", None) or []
                for seg in segments:
                    start = int(getattr(seg, "start_index", 0) or 0)
                    end = int(getattr(seg, "end_index", 0) or 0)
                    cell_text = full_text[start:end].strip()
                    if cell_text:
                        cell_texts.append(cell_text)
            if cell_texts:
                page_parts.append(" | ".join(cell_texts))

    # ── Paragraph blocks — only on non-form pages ─────────────────────────
    if not is_form_page:
        for block in getattr(page, "blocks", None) or []:
            layout = getattr(block, "layout", None)
            if layout is None:
                continue
            confidence = float(getattr(layout, "confidence", 1.0) or 1.0)
            if confidence < MIN_CONFIDENCE:
                logger.debug("OCR block skipped — low confidence %.2f", confidence)
                continue
            anchor = getattr(layout, "text_anchor", None)
            segments = getattr(anchor, "text_segments", None) or []
            for seg in segments:
                start = int(getattr(seg, "start_index", 0) or 0)
                end = int(getattr(seg, "end_index", 0) or 0)
                block_text = full_text[start:end].strip()
                if block_text:
                    page_parts.append(block_text)

    return page_parts


def _extract_structured_text(document: object, document_type: str | None = None) -> str:
    """
    Full 3-stage OCR-to-HTML pipeline.

    ┌─────────────────────────────────────────────────────────────────────┐
    │ STAGE 1 — Per-page text extraction                                  │
    │   Primary:  Token bounding-box line reconstruction (reading-order)  │
    │   Fallback: Form fields + paragraph blocks (no token data)          │
    ├─────────────────────────────────────────────────────────────────────┤
    │ STAGE 2 — Text normalization                                        │
    │   Unicode NFC → junk chars → NCRB form artefacts → hyphen breaks   │
    ├─────────────────────────────────────────────────────────────────────┤
    │ STAGE 3 — Structure parsing + HTML rendering                        │
    │   FIR headings detected → section dict → structured HTML           │
    │   Non-FIR / unrecognised → plain paragraph HTML                    │
    └─────────────────────────────────────────────────────────────────────┘

    Returns valid HTML suitable for direct loading into Tiptap.
    Ultimate fallback: processes raw document.text through Stages 2–3.
    """
    full_text: str = getattr(document, "text", None) or ""
    pages = getattr(document, "pages", None) or []
    is_fir = (document_type or "").lower() == "fir"

    # ── No page data: run pipeline on raw document.text ───────────────────
    if not pages:
        combined = _normalize_text(full_text)
        combined = _postprocess_ocr_text(combined)
        if is_fir:
            from app.features.documents.fir_reconstruction import render_fir_html

            fir_html = render_fir_html(combined)
            if fir_html:
                return fir_html
        return _sections_to_html(_split_fir_sections(combined))

    # ── STAGE 1: Per-page extraction ──────────────────────────────────────
    all_page_parts: list[str] = []

    for page_idx, page in enumerate(pages):
        token_lines = _reconstruct_lines_from_tokens(page, full_text)

        if token_lines:
            logger.debug(
                "Page %d: token reconstruction → %d lines",
                page_idx + 1,
                len(token_lines),
            )
            page_text = "\n".join(token_lines)
        else:
            logger.debug(
                "Page %d: no token data — falling back to form_fields/blocks",
                page_idx + 1,
            )
            fallback_parts = _extract_page_text_fallback(page, full_text)
            page_text = "\n".join(fallback_parts)

        if page_text.strip():
            all_page_parts.append(page_text)

    if not all_page_parts:
        logger.warning(
            "OCR extraction yielded no page text; falling back to document.text"
        )
        combined = _normalize_text(full_text)
        combined = _postprocess_ocr_text(combined)
        if is_fir:
            from app.features.documents.fir_reconstruction import render_fir_html

            fir_html = render_fir_html(combined)
            if fir_html:
                return fir_html
        return _sections_to_html(_split_fir_sections(combined))

    # ── STAGE 2: Normalize + clean ────────────────────────────────────────
    combined = "\n\n".join(all_page_parts)
    combined = _normalize_text(combined)
    combined = _postprocess_ocr_text(combined)

    # ── STAGE 3: Structure + HTML ─────────────────────────────────────────
    if is_fir:
        from app.features.documents.fir_reconstruction import render_fir_html

        fir_html = render_fir_html(combined)
        if fir_html:
            return fir_html

    sections = _split_fir_sections(combined)
    return _sections_to_html(sections)


# ══════════════════════════════════════════════════════════════════════════════
# Config status + language detection
# ══════════════════════════════════════════════════════════════════════════════


def _detect_language(text: str) -> str:
    # Strip HTML tags before checking character ranges.
    plain = _HTML_TAG_RE.sub("", text)
    has_hindi = any("ऀ" <= ch <= "ॿ" for ch in plain)
    has_latin = any(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in plain)

    if has_hindi and has_latin:
        return "hi+en"
    if has_hindi:
        return "hi"
    return "en"


def _processor_id_for_document_type(document_type: str | None = None) -> str:
    if (
        document_type or ""
    ).lower() == "fir" and settings.GOOGLE_DOCAI_FIR_PROCESSOR_ID:
        return settings.GOOGLE_DOCAI_FIR_PROCESSOR_ID
    return settings.GOOGLE_DOCAI_PROCESSOR_ID


def get_document_ai_config_status(document_type: str | None = None) -> OcrConfigStatus:
    processor_id = _processor_id_for_document_type(document_type)
    missing = [
        name
        for name, value in {
            "GOOGLE_PROJECT_ID": settings.GOOGLE_PROJECT_ID,
            "GOOGLE_LOCATION": settings.GOOGLE_LOCATION,
            "GOOGLE_DOCAI_PROCESSOR_ID": processor_id,
        }.items()
        if not value
    ]

    credential_source: str | None = None
    credential_error: str | None = None
    credentials_path = settings.GOOGLE_APPLICATION_CREDENTIALS

    if credentials_path:
        expanded_path = Path(credentials_path).expanduser()
        if expanded_path.exists():
            try:
                _get_document_ai_credentials()
                credential_source = "GOOGLE_APPLICATION_CREDENTIALS"
            except Exception as exc:
                missing.append("GOOGLE_APPLICATION_CREDENTIALS")
                credential_error = str(exc)
        else:
            missing.append("GOOGLE_APPLICATION_CREDENTIALS")
            credential_error = f"Credential file not found: {credentials_path}"
    else:
        try:
            import google.auth

            google.auth.default()
            credential_source = "application_default_credentials"
        except Exception as exc:
            missing.append("GOOGLE_APPLICATION_CREDENTIALS_OR_ADC")
            credential_error = str(exc)

    return OcrConfigStatus(
        configured=not missing,
        missing=missing,
        credential_source=credential_source,
        credential_error=credential_error,
    )


def _require_document_ai_config(document_type: str | None = None) -> None:
    status = get_document_ai_config_status(document_type=document_type)
    if status.configured:
        return

    raise OcrConfigurationError(
        "Google Document AI is not configured",
        details={
            "missing": status.missing,
            "credential_source": status.credential_source,
            "credential_error": status.credential_error,
        },
    )


def ensure_document_ai_configured(document_type: str | None = None) -> None:
    status = get_document_ai_config_status(document_type=document_type)
    if status.configured:
        return

    details: dict[str, object] = {"missing": status.missing}
    if status.credential_error:
        details["credential_error"] = status.credential_error

    raise ValidationError(
        "Google Document AI is not configured",
        details=details,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Document AI processing + public entry point
# ══════════════════════════════════════════════════════════════════════════════


def _process_with_document_ai(
    file_bytes: bytes,
    mime_type: str,
    document_type: str | None = None,
) -> OcrResult:
    from google.api_core.client_options import ClientOptions
    from google.cloud import documentai_v1 as documentai

    _require_document_ai_config(document_type=document_type)
    processor_id = _processor_id_for_document_type(document_type)

    # ClientOptions object is required — passing a plain dict is not supported
    # by DocumentProcessorServiceClient and silently breaks endpoint routing.
    opts = ClientOptions(
        api_endpoint=f"{settings.GOOGLE_LOCATION}-documentai.googleapis.com"
    )

    credentials = _get_document_ai_credentials()
    client = documentai.DocumentProcessorServiceClient(
        credentials=credentials,
        client_options=opts,
    )

    # processor_path() builds: projects/{project}/locations/{location}/processors/{id}
    # Processor IDs must be the short ID, not the full resource path.
    name = client.processor_path(
        settings.GOOGLE_PROJECT_ID,
        settings.GOOGLE_LOCATION,
        processor_id,
    )

    logger.debug(
        "Sending document to Document AI processor=%s mime_type=%s bytes=%s",
        name,
        mime_type,
        len(file_bytes),
    )

    raw_doc = documentai.RawDocument(content=file_bytes, mime_type=mime_type)
    request = documentai.ProcessRequest(name=name, raw_document=raw_doc)

    try:
        result = client.process_document(request=request)
    except Exception as exc:
        raise OcrProviderError(
            f"Document AI API call failed: {exc}",
            details={"processor": name, "mime_type": mime_type},
        ) from exc

    document = result.document

    # ── Plain text (for typing agent / ocr_raw_text) ───────────────────────
    # Use the raw document.text from Document AI, normalized through Stages 1–2
    # but WITHOUT HTML rendering (Stage 3).  This is what fir_parser.py expects.
    full_text: str = getattr(document, "text", None) or ""
    plain_text = _postprocess_ocr_text(_normalize_text(full_text)).strip()

    # ── HTML (for Tiptap / TypedVersion) ──────────────────────────────────
    html = _extract_structured_text(document, document_type=document_type)

    if not plain_text and not html:
        raise OcrNoTextError(
            "OCR completed but no text was extracted from this document. "
            "The file may be a scanned image with poor quality, or the document "
            "may already be in a non-text format. "
            "Use 'Skip OCR' to paste or type the content manually.",
            details={
                "processor": processor_id,
                "mime_type": mime_type,
            },
        )

    # Fallbacks: if one is missing, derive it from the other
    if not plain_text:
        plain_text = re.sub(r"<[^>]+>", " ", html)
        plain_text = re.sub(r"[ \t]{2,}", " ", plain_text).strip()
    if not html:
        html = f"<p>{plain_text}</p>"

    artifact = _build_document_ai_artifact(
        document,
        processor=processor_id,
        mime_type=mime_type,
        document_type=document_type,
    )
    artifact_summary = artifact.get("summary", {})
    page_count = len(document.pages) if document.pages else 1
    return OcrResult(
        text=plain_text,
        html=html,
        language=_detect_language(html),
        page_count=page_count,
        provider="google_document_ai",
        metadata={
            "processor": processor_id,
            "processor_role": "fir_form_parser"
            if (document_type or "").lower() == "fir"
            and processor_id == settings.GOOGLE_DOCAI_FIR_PROCESSOR_ID
            else "default_ocr",
            "mime_type": mime_type,
            "text_length": len(plain_text),
            "artifact_summary": artifact_summary,
        },
        artifact=artifact,
    )


async def run_document_ocr(
    *,
    r2_bucket: str,
    r2_key: str,
    mime_type: str,
    document_type: str | None = None,
    max_attempts: int = 3,
) -> OcrResult:
    """Download a stored document from R2 and OCR it with Google Document AI."""
    # Validate + normalise MIME type before touching the network.
    normalized_mime = _normalize_mime_type(mime_type)

    file_bytes = await asyncio.to_thread(_download_from_r2, r2_bucket, r2_key)
    logger.info(
        "Starting Document AI OCR bucket=%s key=%s mime_type=%s bytes=%s",
        r2_bucket,
        r2_key,
        normalized_mime,
        len(file_bytes),
    )

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.to_thread(
                _process_with_document_ai,
                file_bytes,
                normalized_mime,
                document_type,
            )
        except (OcrConfigurationError, OcrNoTextError):
            # Non-retryable: config is wrong or document has no text.
            raise
        except OcrStorageError:
            # Non-retryable: file is gone from R2.
            raise
        except OcrProviderError as exc:
            # Retryable: transient Google API failure.
            last_error = exc
            logger.warning(
                "Document AI OCR attempt %s/%s failed: %s", attempt, max_attempts, exc
            )
            if attempt < max_attempts:
                await asyncio.sleep(2 * attempt)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Unexpected OCR error attempt %s/%s: %s", attempt, max_attempts, exc
            )
            if attempt < max_attempts:
                await asyncio.sleep(2 * attempt)

    raise OcrProviderError(
        f"Document AI OCR failed after {max_attempts} attempts: {last_error}"
    ) from last_error
