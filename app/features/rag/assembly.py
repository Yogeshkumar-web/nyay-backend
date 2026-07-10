from __future__ import annotations

import html
import uuid

from app.features.rag.schemas import (
    CitedJudgment,
    DraftAssemblyRequest,
    DraftAssemblyResponse,
    DraftAssemblySection,
)


SECTION_TITLES = {
    "facts": "Facts",
    "grounds": "Grounds",
    "prayer": "Prayer",
}


def assemble_anticipatory_bail_html(
    request: DraftAssemblyRequest,
) -> DraftAssemblyResponse:
    rendered_sections = [
        _render_section(section)
        for section in request.sections
        if section.content.strip()
    ]
    verified_citations = [
        citation
        for citation in request.verified_citations
        if _citation_display(citation)
    ]
    citation_html = _render_verified_citations(verified_citations)
    metadata_html = _render_case_metadata(request.case_metadata)
    html_output = "\n".join(
        part
        for part in (
            '<article class="rag-draft rag-draft-anticipatory-bail">',
            metadata_html,
            *rendered_sections,
            citation_html,
            "</article>",
        )
        if part
    )
    return DraftAssemblyResponse(
        html=html_output,
        section_source_map={
            section.section: section.source_chunk_ids
            for section in request.sections
        },
        verified_citations=verified_citations,
        excluded_citations=request.unverified_citations,
        warnings=[
            "Unverified citations were excluded from assembled draft output."
        ]
        if request.unverified_citations
        else [],
    )


def _render_case_metadata(metadata: dict) -> str:
    clean_items = [
        (str(key).replace("_", " ").title(), str(value))
        for key, value in metadata.items()
        if value not in (None, "")
    ]
    if not clean_items:
        return ""
    rows = "\n".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in clean_items
    )
    return f'<section data-section="case-metadata"><table><tbody>{rows}</tbody></table></section>'


def _render_section(section: DraftAssemblySection) -> str:
    title = SECTION_TITLES.get(section.section, section.section.title())
    paragraphs = _paragraphs_from_text(section.content)
    source_ids = ",".join(str(chunk_id) for chunk_id in section.source_chunk_ids)
    return (
        f'<section data-section="{html.escape(section.section)}" '
        f'data-source-chunk-ids="{html.escape(source_ids)}">'
        f"<h2>{html.escape(title)}</h2>\n{paragraphs}</section>"
    )


def _paragraphs_from_text(content: str) -> str:
    blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
    if not blocks:
        blocks = [content.strip()]
    return "\n".join(
        f"<p>{html.escape(block).replace(chr(10), '<br/>')}</p>"
        for block in blocks
        if block
    )


def _render_verified_citations(citations: list[CitedJudgment]) -> str:
    if not citations:
        return ""
    items = "\n".join(
        f"<li>{html.escape(_citation_display(citation))}</li>"
        for citation in citations
    )
    return f'<section data-section="verified-citations"><h2>Verified Citations</h2><ol>{items}</ol></section>'


def _citation_display(citation: CitedJudgment) -> str:
    parts = [citation.case_name]
    if citation.citation:
        parts.append(citation.citation)
    elif citation.year:
        parts.append(str(citation.year))
    return ", ".join(part for part in parts if part)


def collect_source_chunk_ids(sections: list[DraftAssemblySection]) -> list[uuid.UUID]:
    ordered: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for section in sections:
        for chunk_id in section.source_chunk_ids:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            ordered.append(chunk_id)
    return ordered
