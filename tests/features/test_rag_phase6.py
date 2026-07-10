from __future__ import annotations

import io
import uuid

from docx import Document

from app.features.drafts.export import PAGE_CONFIG, generate_docx
from app.features.rag.assembly import assemble_anticipatory_bail_html
from app.features.rag.schemas import (
    CitedJudgment,
    DraftAssemblyRequest,
    DraftAssemblySection,
)


def test_code_driven_assembly_outputs_editor_ready_html_without_fixed_template():
    facts_id = uuid.uuid4()
    request = DraftAssemblyRequest(
        case_metadata={
            "district": "Prayagraj",
            "case_crime_no": "123/2026",
            "sections": "438 CrPC",
        },
        sections=[
            DraftAssemblySection(
                section="facts",
                content="Applicant apprehends arrest.\n\nFIR is registered.",
                source_chunk_ids=[facts_id],
                confidence_score=0.82,
            ),
            DraftAssemblySection(
                section="grounds",
                content="Custodial interrogation is not required.",
                source_chunk_ids=[],
                confidence_score=0.78,
            ),
        ],
        verified_citations=[
            CitedJudgment(
                case_name="Siddharam Satlingappa Mhetre vs. State of Maharashtra",
                citation="(2011) 1 SCC 694",
                year=2011,
            )
        ],
        unverified_citations=[
            CitedJudgment(
                case_name="Imaginary Case vs. State",
                citation="(2020) 1 SCC 1",
                year=2020,
            )
        ],
    )

    result = assemble_anticipatory_bail_html(request)

    assert "<article" in result.html
    assert "Case Crime No" in result.html
    assert 'data-section="facts"' in result.html
    assert str(facts_id) in result.html
    assert "Siddharam Satlingappa" in result.html
    assert "Imaginary Case" not in result.html
    assert result.section_source_map["facts"] == [facts_id]
    assert result.excluded_citations == request.unverified_citations
    assert result.warnings


def test_docx_export_uses_legal_page_config():
    docx_bytes = generate_docx("<p>Reviewed legal draft content.</p>", "Draft")
    document = Document(io.BytesIO(docx_bytes))
    section = document.sections[0]

    assert section.page_width.twips == PAGE_CONFIG["size"]["width"]
    assert section.page_height.twips == PAGE_CONFIG["size"]["height"]
    assert section.top_margin.twips == PAGE_CONFIG["margin"]["top"]
    assert section.bottom_margin.twips == PAGE_CONFIG["margin"]["bottom"]
    assert section.left_margin.twips == PAGE_CONFIG["margin"]["left"]
    assert section.right_margin.twips == PAGE_CONFIG["margin"]["right"]
