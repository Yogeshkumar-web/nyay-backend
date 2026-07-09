from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class LayoutCell:
    text: str = ""
    confidence: float | None = None
    page_number: int | None = None


@dataclass(frozen=True)
class LayoutTable:
    page_number: int | None
    headers: list[str] = field(default_factory=list)
    rows: list[list[LayoutCell]] = field(default_factory=list)
    table_type: str = "unknown"
    confidence: float | None = None


_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "serial": ("s no", "sno", "serial", "sr no", "क्रम", "क्र सं", "क्रसं"),
    "acts": ("acts", "act", "अधिनियम"),
    "sections": ("sections", "section", "धारा"),
    "name": ("name", "नाम"),
    "alias": ("alias", "उपनाम"),
    "relative": ("relative", "father", "guardian", "रिश्तेदार", "पिता"),
    "present_address": ("present address", "address", "वर्तमान पता", "पता"),
    "address_type": ("address type", "पता का प्रकार"),
    "id_type": ("id type", "identification", "पहचान"),
    "id_number": ("id number", "पहचान संख्या"),
    "property_category": ("property category", "सम्पत्ति श्रेणी", "संपत्ति श्रेणी"),
    "property_type": ("property type", "सम्पत्ति के प्रकार", "संपत्ति के प्रकार"),
    "description": ("description", "विवरण"),
    "value": ("value", "मूल्य"),
    "uidb_number": ("uidb", "u d b", "यू डी", "यू.डी"),
}


def extract_tables(artifact: dict[str, Any] | None) -> list[LayoutTable]:
    """Return normalized layout tables from the persisted OCR artifact."""
    if not isinstance(artifact, dict):
        return []

    tables: list[LayoutTable] = []
    for page in artifact.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = _int_or_none(page.get("page_number"))
        for raw_table in page.get("tables") or []:
            if not isinstance(raw_table, dict):
                continue
            table = _parse_table(raw_table, page_number)
            if table.rows or table.headers:
                tables.append(table)
    return tables


def tables_by_type(artifact: dict[str, Any] | None) -> dict[str, list[LayoutTable]]:
    grouped: dict[str, list[LayoutTable]] = {}
    for table in extract_tables(artifact):
        grouped.setdefault(table.table_type, []).append(table)
    return grouped


def _parse_table(raw_table: dict[str, Any], page_number: int | None) -> LayoutTable:
    header_rows = _rows_from_artifact(raw_table.get("header_rows"), page_number)
    body_rows = _rows_from_artifact(raw_table.get("body_rows"), page_number)

    headers = [_clean_text(cell.text) for row in header_rows for cell in row]
    if not headers and body_rows:
        first_row = [_clean_text(cell.text) for cell in body_rows[0]]
        if _looks_like_header(first_row):
            headers = first_row
            body_rows = body_rows[1:]

    table_type = _detect_table_type(headers, body_rows)
    confidence = _average_confidence(
        [cell for row in header_rows + body_rows for cell in row]
    )
    return LayoutTable(
        page_number=page_number,
        headers=headers,
        rows=body_rows,
        table_type=table_type,
        confidence=confidence,
    )


def _rows_from_artifact(
    raw_rows: Any, page_number: int | None
) -> list[list[LayoutCell]]:
    rows: list[list[LayoutCell]] = []
    for raw_row in raw_rows or []:
        if not isinstance(raw_row, list):
            continue
        row = [_cell_from_artifact(cell, page_number) for cell in raw_row]
        if any(cell.text.strip() for cell in row):
            rows.append(row)
    return rows


def _cell_from_artifact(raw_cell: Any, page_number: int | None) -> LayoutCell:
    if not isinstance(raw_cell, dict):
        return LayoutCell(page_number=page_number)
    return LayoutCell(
        text=_clean_text(str(raw_cell.get("text") or "")),
        confidence=_float_or_none(raw_cell.get("confidence")),
        page_number=page_number,
    )


def _detect_table_type(headers: list[str], rows: list[list[LayoutCell]]) -> str:
    normalized = {_normalize_header(header) for header in headers if header}
    row_text = " ".join(cell.text for row in rows[:3] for cell in row).lower()

    if {"acts", "sections"} <= normalized:
        return "acts_sections"
    if {"name", "relative"} <= normalized or {"name", "present_address"} <= normalized:
        return "accused"
    if {"address_type", "present_address"} <= normalized:
        return "address"
    if {"id_type", "id_number"} <= normalized:
        return "id"
    if {
        "property_category",
        "property_type",
    } & normalized and "description" in normalized:
        return "property"
    if "uidb_number" in normalized:
        return "inquest"
    if (
        "complexion" in row_text
        or "height" in row_text
        or "identification mark" in row_text
    ):
        return "physical_features"
    return "unknown"


def _normalize_header(header: str) -> str:
    compact = _header_key(header)
    best_key = ""
    best_score = 0.0
    for key, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            score = SequenceMatcher(None, compact, _header_key(alias)).ratio()
            if score > best_score:
                best_key = key
                best_score = score
    return best_key if best_score >= 0.68 else compact


def _looks_like_header(values: list[str]) -> bool:
    normalized = {_normalize_header(value) for value in values if value}
    known = set(_HEADER_ALIASES)
    return len(normalized & known) >= 2


def _header_key(text: str) -> str:
    value = re.sub(r"\([^)]*\)", " ", text.lower())
    value = re.sub(r"[^0-9a-z\u0900-\u097f]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" :;,-")


def _average_confidence(cells: list[LayoutCell]) -> float | None:
    values = [
        cell.confidence for cell in cells if cell.confidence and cell.confidence > 0
    ]
    return round(sum(values) / len(values), 4) if values else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None
