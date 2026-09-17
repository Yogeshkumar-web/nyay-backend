from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CANONICAL_SCHEMA_VERSION = 3


CanonicalBlockType = Literal[
    "heading",
    "paragraph",
    "key_value",
    "table",
    "list",
    "signature",
    "page_break",
    "generic",
]


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(..., ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    coordinates: list[float] | None = None


class CanonicalField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


class CanonicalBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: CanonicalBlockType
    semantic_role: str = "generic"
    heading: str | None = None
    text: str | None = None
    level: int | None = Field(default=None, ge=1, le=6)
    fields: list[CanonicalField] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    ordered: bool = False
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    review_required: bool = False


class CanonicalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = CANONICAL_SCHEMA_VERSION
    document_type: str = "unknown"
    title: str | None = None
    blocks: list[CanonicalBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_block_count: int = 0
    source_markdown_hash: str


_PAGE_BLOCK_RE = re.compile(
    r"\[\[PAGE\s+(\d+)\s+START\]\]\s*(.*?)\s*"
    r"\[\[PAGE\s+\1\s+END\]\]",
    re.IGNORECASE | re.DOTALL,
)
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(\d{1,2}(?:\s*[.)]|\s*\([a-z]\)))\s*(.+?)\s*$", re.IGNORECASE
)
_KNOWN_NUMBERED_HEADING_RE = re.compile(r"^\s*(\d{1,2})\s*[.):]?\s*(.+?)\s*$")
_LIST_RE = re.compile(r"^\s*(?:([-*+])|(\d+)[.)])\s+(.+?)\s*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_KEY_VALUE_RE = re.compile(r"^\s*([^:：]{1,100})\s*[:：]\s*(.*?)\s*$")

_SEMANTIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title", ("first information report", "प्रथम सूचना रिपोर्ट")),
    (
        "police_station_details",
        ("district/unit", "police station", "p.s. (", "जिला/इकाई"),
    ),
    ("applicable_acts", ("acts", "sections", "अधिनियम", "धारा")),
    ("occurrence", ("occurrence of offence", "अपराध की घटना")),
    ("informant", ("complainant", "informant", "शिकायतकर्ता", "सूचनाकर्ता")),
    ("accused", ("accused", "अभियुक्त", "आरोपी")),
    ("victims", ("victim", "पीड़ित")),
    ("witnesses", ("witness", "गवाह")),
    ("property", ("property", "properties", "सम्पत्ति", "संपत्ति")),
    ("delay_explanation", ("reasons for delay", "देरी का कारण")),
    ("incident_narrative", ("first information contents", "प्रथम सूचना तथ्य")),
    ("investigation", ("action taken", "investigation", "कार्यवाही")),
    ("signatures", ("signature", "हस्ताक्षर")),
    ("dispatch", ("dispatch to the court", "न्यायालय को प्रेषित")),
)


def build_canonical_document(
    content_markdown: str,
    *,
    document_type: str,
    page_evidence: list[dict[str, Any]] | None = None,
) -> CanonicalDocument:
    """Build a lossless, presentation-neutral document from reviewed Markdown."""
    effective_document_type = (
        "fir" if _is_fir(content_markdown) else document_type
    )
    pages = _split_pages(content_markdown)
    evidence_by_page = {
        int(item["page_number"]): item
        for item in (page_evidence or [])
        if isinstance(item, dict) and item.get("page_number") is not None
    }
    blocks: list[CanonicalBlock] = []
    document_warnings: list[str] = []
    active_role = "generic"

    for page_number, page_text in pages:
        evidence = evidence_by_page.get(page_number, {})
        confidence = _confidence(evidence.get("confidence"))
        warnings = _string_list(evidence.get("warnings"))
        document_warnings.extend(warnings)
        page_blocks, active_role = _parse_page(
            page_text,
            page_number=page_number,
            confidence=confidence,
            warnings=warnings,
            active_role=active_role,
        )
        _enrich_blocks_with_layout(page_blocks, evidence.get("layout"))
        blocks.extend(page_blocks)

    title = next(
        (
            block.heading
            for block in blocks
            if block.type == "heading" and block.semantic_role == "title"
        ),
        None,
    )
    unresolved = sum(block.review_required for block in blocks)
    return CanonicalDocument(
        document_type=effective_document_type,
        title=title,
        blocks=blocks,
        warnings=list(dict.fromkeys(document_warnings)),
        unresolved_block_count=unresolved,
        source_markdown_hash=hashlib.sha256(
            content_markdown.encode("utf-8")
        ).hexdigest(),
    )


def canonical_document_from_dict(value: dict[str, Any] | None) -> CanonicalDocument | None:
    if not value:
        return None
    return CanonicalDocument.model_validate(value)


def _split_pages(content: str) -> list[tuple[int, str]]:
    matches = list(_PAGE_BLOCK_RE.finditer(content))
    if not matches:
        return [(1, content.strip())]

    pages: list[tuple[int, str]] = []
    cursor = 0
    for match in matches:
        prefix = content[cursor : match.start()].strip()
        if prefix:
            pages.append((int(match.group(1)), prefix))
        pages.append((int(match.group(1)), match.group(2).strip()))
        cursor = match.end()
    suffix = content[cursor:].strip()
    if suffix:
        pages.append((pages[-1][0] if pages else 1, suffix))
    return pages


def _parse_page(
    text: str,
    *,
    page_number: int,
    confidence: float | None,
    warnings: list[str],
    active_role: str,
) -> tuple[list[CanonicalBlock], str]:
    lines = text.splitlines()
    blocks: list[CanonicalBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if _is_presentation_noise(line):
            index += 1
            continue

        fir_header = _fir_header_fields(lines, index)
        if fir_header is not None:
            fields, index = fir_header
            active_role = "police_station_details"
            blocks.append(
                _block(
                    block_type="heading",
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=active_role,
                    heading="1. FIR Details",
                    level=2,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
            blocks.append(
                _block(
                    block_type="key_value",
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=active_role,
                    fields=fields,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
            continue

        heading = _heading(line)
        if heading is not None:
            heading_text, level = heading
            active_role = _semantic_role(heading_text)
            blocks.append(
                _block(
                    block_type="heading" if active_role != "generic" else "generic",
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=active_role,
                    heading=heading_text,
                    level=level,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
            index += 1
            continue

        if line.lower().startswith("<table"):
            html_lines = [line]
            index += 1
            while index < len(lines):
                html_lines.append(lines[index])
                index += 1
                if "</table>" in html_lines[-1].lower():
                    break
            headers, rows = _html_table("\n".join(html_lines))
            if headers or rows:
                role = _semantic_role(" ".join(headers))
                blocks.append(
                    _block(
                        block_type="table",
                        page_number=page_number,
                        block_index=len(blocks),
                        semantic_role=role if role != "generic" else active_role,
                        headers=headers,
                        rows=rows,
                        confidence=confidence,
                        warnings=warnings,
                    )
                )
            continue

        plain_table = _plain_table(lines, index, active_role)
        if plain_table is not None:
            headers, rows, index = plain_table
            blocks.append(
                _block(
                    block_type="table",
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=active_role,
                    headers=headers,
                    rows=rows,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
            continue

        if index + 1 < len(lines) and _is_table_start(line, lines[index + 1]):
            table_lines = [line]
            index += 2
            while index < len(lines) and "|" in lines[index]:
                if lines[index].strip():
                    table_lines.append(lines[index].strip())
                index += 1
            headers = _table_cells(table_lines[0])
            rows = [_table_cells(row) for row in table_lines[1:]]
            role = _semantic_role(" ".join(headers))
            blocks.append(
                _block(
                    block_type="table",
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=role if role != "generic" else active_role,
                    headers=headers,
                    rows=rows,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
            continue

        list_match = _LIST_RE.match(line)
        if list_match:
            ordered = list_match.group(2) is not None
            items: list[str] = []
            while index < len(lines):
                item_match = _LIST_RE.match(lines[index])
                if item_match is None or (item_match.group(2) is not None) != ordered:
                    break
                items.append(item_match.group(3).strip())
                index += 1
            blocks.append(
                _block(
                    block_type="list",
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=active_role,
                    items=items,
                    ordered=ordered,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            candidate = lines[index].strip()
            if _heading(candidate) is not None:
                break
            if index + 1 < len(lines) and _is_table_start(candidate, lines[index + 1]):
                break
            if _LIST_RE.match(candidate):
                break
            paragraph_lines.append(candidate)
            index += 1

        fields = _key_value_fields(paragraph_lines)
        if fields:
            blocks.append(
                _block(
                    block_type="key_value",
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=active_role,
                    fields=fields,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
        else:
            paragraph = "\n".join(paragraph_lines).strip()
            paragraph_role = _semantic_role(paragraph[:160])
            role = paragraph_role if paragraph_role != "generic" else active_role
            block_type: CanonicalBlockType = (
                "signature" if role == "signatures" else "paragraph"
            )
            blocks.append(
                _block(
                    block_type=block_type,
                    page_number=page_number,
                    block_index=len(blocks),
                    semantic_role=role,
                    text=paragraph,
                    confidence=confidence,
                    warnings=warnings,
                )
            )
    return blocks, active_role


def _block(
    *,
    block_type: CanonicalBlockType,
    page_number: int,
    block_index: int,
    semantic_role: str,
    confidence: float | None,
    warnings: list[str],
    heading: str | None = None,
    text: str | None = None,
    level: int | None = None,
    fields: list[CanonicalField] | None = None,
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
    items: list[str] | None = None,
    ordered: bool = False,
) -> CanonicalBlock:
    identity = "|".join(
        [
            str(page_number),
            str(block_index),
            block_type,
            heading or "",
            text or "",
            "\n".join("|".join(row) for row in (rows or [])),
        ]
    )
    review_required = bool(warnings) or (
        confidence is not None and confidence < 0.85
    )
    return CanonicalBlock(
        id=f"block_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:16]}",
        type=block_type,
        semantic_role=semantic_role,
        heading=heading,
        text=text,
        level=level,
        fields=fields or [],
        headers=headers or [],
        rows=rows or [],
        items=items or [],
        ordered=ordered,
        source_refs=[
            SourceReference(page_number=page_number, confidence=confidence)
        ],
        confidence=confidence,
        warnings=warnings,
        review_required=review_required,
    )


def _heading(line: str) -> tuple[str, int] | None:
    markdown = _MARKDOWN_HEADING_RE.match(line)
    if markdown:
        return markdown.group(2).strip(), len(markdown.group(1))
    numbered = _NUMBERED_HEADING_RE.match(line)
    if numbered and (
        len(line) <= 180
        and (
            (
                line.rstrip().endswith(":")
                and not re.match(r"^\([a-z]\)", numbered.group(2).strip(), re.I)
            )
            or _semantic_role(numbered.group(2)) != "generic"
        )
    ):
        return f"{numbered.group(1)} {numbered.group(2)}".strip(), 2
    known_numbered = _KNOWN_NUMBERED_HEADING_RE.match(line)
    if known_numbered and len(line) <= 220:
        role = _semantic_role(known_numbered.group(2))
        if role != "generic":
            return f"{known_numbered.group(1)}. {known_numbered.group(2)}".strip(), 2
    if len(line) <= 120 and line.isupper() and _semantic_role(line) != "generic":
        return line, 1
    return None


def _semantic_role(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.lower())
    for role, aliases in _SEMANTIC_ALIASES:
        if any(alias in normalized for alias in aliases):
            return role
    return "generic"


def _is_fir(value: str) -> bool:
    return bool(
        re.search(
            r"FIRST\s+INFORMATION\s+REPORT|प्रथम\s+सूचना\s+रिपोर्ट|"
            r"I\.I\.F\.?-?I",
            value,
            re.IGNORECASE,
        )
    )


def _is_presentation_noise(line: str) -> bool:
    compact = re.sub(r"\s+", " ", line).strip()
    if re.fullmatch(r"[0-9०-९০-৯]+", compact):
        return True
    if re.fullmatch(
        r"[\[(](?:no text(?: or math)? detected)[\])]", compact, re.IGNORECASE
    ):
        return True
    normalized = re.sub(r"[^a-z]+", "", compact.lower())
    return normalized.startswith(("ncrb", "ncpe", "iif", "ifi"))


def _is_table_start(line: str, next_line: str) -> bool:
    return "|" in line and bool(_TABLE_SEPARATOR_RE.match(next_line))


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _html_table(value: str) -> tuple[list[str], list[list[str]]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(value, "html.parser")
    table = soup.find("table")
    if table is None:
        return [], []
    headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
        if cells:
            rows.append(cells)
    return headers, rows


def _plain_table(
    lines: list[str], index: int, semantic_role: str
) -> tuple[list[str], list[list[str]], int] | None:
    if semantic_role != "applicable_acts":
        return None
    end = index
    candidates: list[str] = []
    while end < len(lines) and lines[end].strip():
        candidates.append(lines[end].strip())
        end += 1
    rows: list[list[str]] = []
    for candidate in candidates:
        match = re.match(
            r"^(\d+)\s+(.+?)\s+(\d+(?:\(\d+\))?)\s*$", candidate
        )
        if match:
            rows.append([match.group(1), match.group(2), match.group(3)])
    if len(rows) < 2:
        return None
    return ["S.No.", "Act", "Section"], rows, end


def _fir_header_fields(
    lines: list[str], index: int
) -> tuple[list[CanonicalField], int] | None:
    if not re.match(r"^\s*1\s*[.)]\s*District\s*/?\s*Unit", lines[index], re.I):
        return None
    end = index
    section_lines: list[str] = []
    while end < len(lines):
        candidate = lines[end].strip()
        if end > index and re.match(r"^\s*2\s*[.)]\s*S\.?\s*No", candidate, re.I):
            break
        if candidate:
            section_lines.append(candidate)
        end += 1
    source = "\n".join(section_lines)
    patterns = (
        ("District / Unit", r"District\s*/?\s*Unit[^:：]*[:：]\s*([^\n]+)"),
        (
            "Police Station",
            r"P\.?\s*S\.?\s*[^:：]*[:：]\s*(.*?)(?=\s+Year\b|\n|$)",
        ),
        ("Year", r"Year[^:：]*[:：]\s*([^\n]+)"),
        ("FIR Number", r"FIR\s*No\.?[^:：]*[:：]\s*([^\n]+)"),
        (
            "FIR Date & Time",
            r"Date\s*&?\s*Time\s+of\s+FIR[^:：]*[:：]\s*([^\n]+)",
        ),
    )
    fields: list[CanonicalField] = []
    for label, pattern in patterns:
        match = re.search(pattern, source, re.I)
        if match and match.group(1).strip():
            fields.append(CanonicalField(label=label, value=match.group(1).strip()))
    return (fields, end) if len(fields) >= 3 else None


def _key_value_fields(lines: list[str]) -> list[CanonicalField]:
    if len(lines) < 2:
        return []
    fields: list[CanonicalField] = []
    for line in lines:
        match = _KEY_VALUE_RE.match(line)
        if match is None:
            return []
        fields.append(CanonicalField(label=match.group(1).strip(), value=match.group(2).strip()))
    return fields


def _confidence(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, result))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _enrich_blocks_with_layout(
    blocks: list[CanonicalBlock], layout: Any
) -> None:
    if not isinstance(layout, dict):
        return
    candidates = [
        block
        for block in (layout.get("blocks") or [])
        if isinstance(block, dict) and str(block.get("text") or "").strip()
    ]
    used_candidates: set[int] = set()
    for block in blocks:
        block_text = _block_text(block)
        if not block_text:
            continue
        best: dict[str, Any] | None = None
        best_score = 0.0
        best_index: int | None = None
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used_candidates:
                continue
            score = SequenceMatcher(
                None,
                _match_text(block_text),
                _match_text(str(candidate.get("text") or "")),
            ).ratio()
            if score > best_score:
                best = candidate
                best_index = candidate_index
                best_score = score
        if best is None or best_score < 0.45:
            continue
        if best_index is not None:
            used_candidates.add(best_index)
        matched_confidence = _confidence(best.get("confidence"))
        if matched_confidence is not None:
            block.confidence = matched_confidence
            block.source_refs[0].confidence = matched_confidence
            if matched_confidence < 0.85:
                block.review_required = True
        block.source_refs[0].coordinates = _coordinates(
            best.get("coordinates") or best.get("bbox")
        )


def _block_text(block: CanonicalBlock) -> str:
    values = [block.heading or "", block.text or ""]
    values.extend(f"{field.label} {field.value}" for field in block.fields)
    values.extend(block.headers)
    values.extend(cell for row in block.rows for cell in row)
    values.extend(block.items)
    return " ".join(value for value in values if value).strip()


def _match_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u0900-\u097f]+", " ", value.lower()).strip()


def _coordinates(value: Any) -> list[float] | None:
    numbers: list[float] = []

    def collect(item: Any) -> None:
        if len(numbers) >= 16:
            return
        if isinstance(item, (int, float)):
            numbers.append(float(item))
        elif isinstance(item, list):
            for nested in item:
                collect(nested)
        elif isinstance(item, dict):
            for nested in item.values():
                collect(nested)

    collect(value)
    return numbers or None
