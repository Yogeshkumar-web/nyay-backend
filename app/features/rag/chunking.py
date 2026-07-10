from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.features.rag.models import RagSection
from app.features.rag.schemas import AnticipatoryBailExtraction, CitedJudgment


SECTION_PATTERNS: tuple[tuple[RagSection, re.Pattern[str]], ...] = (
    (RagSection.facts, re.compile(r"\b(facts?|brief facts?|case facts?)\b", re.I)),
    (
        RagSection.grounds,
        re.compile(r"\b(grounds?|legal grounds?|grounds for bail)\b", re.I),
    ),
    (RagSection.prayer, re.compile(r"\b(prayer|relief)\b", re.I)),
    (
        RagSection.citation,
        re.compile(r"\b(citations?|judgments?|authorities|case law)\b", re.I),
    ),
)

HEADING_RE = re.compile(
    r"^\s*(?:\d+[\).:-]?\s*)?(facts?|brief facts?|case facts?|grounds?|legal grounds?|grounds for bail|prayer|relief|citations?|judgments?|authorities|case law)\s*[:\-]?\s*$",
    re.I,
)

INDIAN_SECTION_RE = re.compile(
    r"\b(?:section|sec\.?|u/s|under section)\s+([0-9A-Za-z(),/\-\s]+?)\s+(?:ipc|bns|crpc|bnss|ndps|pocso|act)\b",
    re.I,
)

CITATION_RE = re.compile(
    r"(?P<case_name>[A-Z][A-Za-z .&@'-]+?\s+v(?:s\.?|\.?)\s+[A-Z][A-Za-z .&@'-]+)"
    r"(?:,?\s*(?P<citation>(?:\(?\d{4}\)?|\d{4})\s+[^;\n]{2,80}))?",
    re.I,
)


@dataclass(frozen=True)
class LegalChunk:
    section: RagSection
    text: str
    metadata: dict = field(default_factory=dict)


def normalize_anticipatory_bail_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_line(line) for line in text.split("\n")]
    compacted = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", compacted).strip()


def extract_anticipatory_bail_schema(text: str) -> AnticipatoryBailExtraction:
    normalized = normalize_anticipatory_bail_text(text)
    chunks = chunk_anticipatory_bail_text(normalized)
    facts = "\n\n".join(chunk.text for chunk in chunks if chunk.section == RagSection.facts)
    grounds = [chunk.text for chunk in chunks if chunk.section == RagSection.grounds]
    prayer = "\n\n".join(chunk.text for chunk in chunks if chunk.section == RagSection.prayer)

    return AnticipatoryBailExtraction(
        facts=facts or normalized[:4000],
        grounds=grounds,
        sections_invoked=extract_sections_invoked(normalized),
        cited_judgments=extract_cited_judgments(normalized),
        prayer=prayer,
    )


def chunk_anticipatory_bail_text(text: str, *, max_chars: int = 2500) -> list[LegalChunk]:
    normalized = normalize_anticipatory_bail_text(text)
    if not normalized:
        return []

    grouped = _group_by_headings(normalized)
    chunks: list[LegalChunk] = []
    for section, section_text in grouped:
        for index, part in enumerate(_split_long_text(section_text, max_chars=max_chars)):
            chunks.append(
                LegalChunk(
                    section=section,
                    text=part,
                    metadata={
                        "chunk_index": len(chunks),
                        "section_part": index,
                        "chunker": "anticipatory_bail_section_v1",
                    },
                )
            )
    return chunks


def extract_sections_invoked(text: str) -> list[str]:
    sections: list[str] = []
    seen: set[str] = set()
    for match in INDIAN_SECTION_RE.finditer(text):
        value = re.sub(r"\s+", " ", match.group(0)).strip(" .,;:")
        key = value.lower()
        if key not in seen:
            seen.add(key)
            sections.append(value)
    return sections


def extract_cited_judgments(text: str) -> list[CitedJudgment]:
    judgments: list[CitedJudgment] = []
    seen: set[str] = set()
    for match in CITATION_RE.finditer(text):
        case_name = re.sub(r"\s+", " ", match.group("case_name")).strip(" .,;:")
        citation = match.group("citation")
        citation = re.sub(r"\s+", " ", citation).strip(" .,;:") if citation else None
        key = f"{case_name.lower()}|{(citation or '').lower()}"
        if key in seen:
            continue
        seen.add(key)
        judgments.append(
            CitedJudgment(
                case_name=case_name,
                citation=citation,
                year=_extract_year(citation or case_name),
            )
        )
    return judgments


def _group_by_headings(text: str) -> list[tuple[RagSection, str]]:
    groups: list[tuple[RagSection, list[str]]] = []
    current_section = RagSection.other
    current_lines: list[str] = []

    for line in text.split("\n"):
        heading = HEADING_RE.match(line)
        if heading:
            if current_lines:
                groups.append((current_section, current_lines))
                current_lines = []
            current_section = _section_for_heading(heading.group(1))
            continue
        current_lines.append(line)

    if current_lines:
        groups.append((current_section, current_lines))

    return [(section, "\n".join(lines).strip()) for section, lines in groups if lines]


def _section_for_heading(heading: str) -> RagSection:
    for section, pattern in SECTION_PATTERNS:
        if pattern.search(heading):
            return section
    return RagSection.other


def _split_long_text(text: str, *, max_chars: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return []
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if not current:
            current = paragraph
            continue
        candidate = f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        parts.append(current)
        current = paragraph
    if current:
        parts.append(current)
    return parts


def _clean_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    if re.fullmatch(r"[-_=*]{3,}", line):
        return ""
    return line


def _extract_year(text: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None
