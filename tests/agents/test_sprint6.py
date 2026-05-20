"""
Sprint 6 tests — Node 6: assemble_html

Covers:
- _text_to_paragraphs()
- _render_section()
- _assemble_fir()   — canonical section order, extras appended, empties skipped
- _assemble_generic()
- _build_processing_notes()
- assemble_html() node
- Graph integration: full pipeline Nodes 1-6
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

from app.agents.typing.nodes.assemble import (
    _FIR_ORDER,
    _assemble_fir,
    _assemble_generic,
    _build_processing_notes,
    _render_section,
    _text_to_paragraphs,
    assemble_html,
)
from app.agents.typing.state import default_state


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


def _state(**overrides):
    s = default_state(
        ocr_raw_text="Sample OCR for sprint 6.",
        document_type="fir",
        document_id="test-doc-s6",
        presigned_url="",
        total_pages=3,
    )
    s.update(overrides)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# _text_to_paragraphs
# ─────────────────────────────────────────────────────────────────────────────


def test_T1_single_line_gives_one_paragraph():
    assert _text_to_paragraphs("Hello world") == ["Hello world"]


def test_T2_blank_line_splits_into_two_paragraphs():
    text = "Para one.\n\nPara two."
    result = _text_to_paragraphs(text)
    assert result == ["Para one.", "Para two."]


def test_T3_single_newlines_joined_with_space():
    text = "Line one\nLine two\nLine three"
    result = _text_to_paragraphs(text)
    assert result == ["Line one Line two Line three"]


def test_T4_multiple_blank_lines_treated_as_one_break():
    text = "First.\n\n\n\nSecond."
    result = _text_to_paragraphs(text)
    assert result == ["First.", "Second."]


def test_T5_whitespace_only_lines_discarded():
    text = "Real content\n   \n\nMore content"
    result = _text_to_paragraphs(text)
    assert all(p.strip() for p in result)
    assert "Real content" in result[0]


def test_T6_empty_string_returns_list_with_empty():
    result = _text_to_paragraphs("")
    # Should not crash; returns something
    assert isinstance(result, list)


def test_T7_windows_line_endings_normalised():
    text = "Line one\r\nLine two\r\n\r\nPara two"
    result = _text_to_paragraphs(text)
    assert len(result) == 2
    assert "Line one" in result[0]
    assert "Line two" in result[0]


# ─────────────────────────────────────────────────────────────────────────────
# _render_section
# ─────────────────────────────────────────────────────────────────────────────


def test_T8_render_section_has_h2_and_p():
    html = _render_section("FIR Header", "Content here.")
    assert "<h2>FIR Header</h2>" in html
    assert "<p>Content here.</p>" in html


def test_T9_render_section_escapes_html_chars():
    html = _render_section("Label", "Name: <Ram> & Kumar")
    assert "&lt;Ram&gt;" in html
    assert "&amp;" in html


def test_T10_render_section_multiple_paragraphs():
    html = _render_section("Label", "Para one.\n\nPara two.")
    assert html.count("<p>") == 2


# ─────────────────────────────────────────────────────────────────────────────
# _assemble_fir
# ─────────────────────────────────────────────────────────────────────────────


def test_T11_fir_canonical_order_maintained():
    """Sections appear in _FIR_ORDER sequence in the output HTML."""
    sections = {
        "fir_contents": "Narrative text",
        "header": "Court header",
        "accused": "Accused list",
        "action_taken": "Action taken",
    }
    html = _assemble_fir(sections)

    pos_header = html.index("Court header")
    pos_accused = html.index("Accused list")
    pos_contents = html.index("Narrative text")
    pos_action = html.index("Action taken")

    assert pos_header < pos_accused < pos_contents < pos_action


def test_T12_fir_empty_sections_skipped():
    sections = {"header": "Header text", "occurrence": "", "accused": "   "}
    html = _assemble_fir(sections)
    assert "Date / Time" not in html  # occurrence label absent
    assert "Accused" not in html  # accused label absent (whitespace-only)
    assert "Header text" in html


def test_T13_fir_unknown_keys_appended_at_end():
    sections = {
        "header": "Header text",
        "custom_field": "Custom content",
    }
    html = _assemble_fir(sections)
    pos_header = html.index("Header text")
    pos_custom = html.index("Custom content")
    assert pos_header < pos_custom


def test_T14_fir_output_wrapped_in_div():
    html = _assemble_fir({"header": "H"})
    assert html.startswith('<div class="typed-document typed-fir">')
    assert html.endswith("</div>")


def test_T15_fir_empty_sections_dict_returns_placeholder():
    html = _assemble_fir({})
    assert "<p></p>" in html


def test_T16_fir_all_known_sections_present():
    sections = {k: f"Content for {k}" for k in _FIR_ORDER}
    html = _assemble_fir(sections)
    # All sections should appear in _FIR_ORDER sequence
    positions = [html.index(f"Content for {k}") for k in _FIR_ORDER if k in sections]
    assert positions == sorted(positions)


# ─────────────────────────────────────────────────────────────────────────────
# _assemble_generic
# ─────────────────────────────────────────────────────────────────────────────


def test_T17_generic_all_sections_in_output():
    sections = {"intro": "Intro text", "body": "Body text", "conclusion": "Conclusion"}
    html = _assemble_generic(sections)
    assert "Intro text" in html
    assert "Body text" in html
    assert "Conclusion" in html


def test_T18_generic_wrapped_in_div():
    html = _assemble_generic({"body": "Content"})
    assert 'class="typed-document typed-generic"' in html


def test_T19_generic_empty_sections_skipped():
    sections = {"body": "Content", "empty": "", "blank": "   "}
    html = _assemble_generic(sections)
    assert html.count("<h2>") == 1  # only "body" rendered


def test_T20_generic_empty_dict_returns_placeholder():
    html = _assemble_generic({})
    assert "<p></p>" in html


# ─────────────────────────────────────────────────────────────────────────────
# _build_processing_notes
# ─────────────────────────────────────────────────────────────────────────────


def test_T21_processing_notes_contains_key_metrics():
    state = _state(
        ocr_quality_score=0.87,
        ocr_language="hindi_devanagari",
        batch_count=3,
        structure_confidence=0.75,
        formatted_sections={"header": "H", "accused": "A"},
        retry_count=1,
        review_passed=True,
    )
    notes = _build_processing_notes(state)
    assert "quality=0.87" in notes
    assert "lang=hindi_devanagari" in notes
    assert "batches=3" in notes
    assert "confidence=0.75" in notes
    assert "sections=2" in notes
    assert "retries=1" in notes
    assert "review=pass" in notes


def test_T22_processing_notes_includes_fetch_error_when_present():
    state = _state(fetch_error="HTTP 403 Forbidden from R2")
    notes = _build_processing_notes(state)
    assert "fetch_error=" in notes


def test_T23_processing_notes_review_fail_when_not_passed():
    state = _state(review_passed=False)
    notes = _build_processing_notes(state)
    assert "review=fail" in notes


def test_T24_processing_notes_preserves_earlier_fallback_notes():
    state = _state(processing_notes="review_fallback:Network error")
    notes = _build_processing_notes(state)
    assert "review_fallback:Network error" in notes


# ─────────────────────────────────────────────────────────────────────────────
# assemble_html node
# ─────────────────────────────────────────────────────────────────────────────


def test_T25_node_empty_sections_sets_placeholder_html():
    state = _state(formatted_sections={})
    result = assemble_html(state)
    assert result["final_html"] == "<p></p>"
    assert result["processing_notes"]  # non-empty


def test_T26_node_fir_template_produces_fir_div():
    state = _state(
        formatted_sections={"header": "HC Header", "fir_contents": "Narrative"},
        document_template="fir_ncrb",
    )
    result = assemble_html(state)
    assert 'class="typed-document typed-fir"' in result["final_html"]
    assert "HC Header" in result["final_html"]


def test_T27_node_generic_template_produces_generic_div():
    state = _state(
        formatted_sections={"body": "Body content"},
        document_template="generic",
    )
    result = assemble_html(state)
    assert 'class="typed-document typed-generic"' in result["final_html"]


def test_T28_node_processing_notes_set_in_state():
    state = _state(
        formatted_sections={"header": "H"},
        document_template="fir_ncrb",
        ocr_quality_score=0.9,
    )
    result = assemble_html(state)
    assert "quality=0.90" in result["processing_notes"]


def test_T29_node_is_synchronous_no_await_needed():
    """assemble_html is a sync function — must not be a coroutine."""
    import inspect

    assert not inspect.iscoroutinefunction(assemble_html)


def test_T30_node_all_state_keys_preserved():
    state = _state(
        formatted_sections={"header": "H"},
        document_template="fir_ncrb",
        ocr_raw_text="original OCR",
    )
    result = assemble_html(state)
    # Original fields untouched
    assert result["ocr_raw_text"] == "original OCR"
    assert result["document_id"] == "test-doc-s6"


# ─────────────────────────────────────────────────────────────────────────────
# Graph integration: full pipeline Nodes 1-6
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T31_full_graph_produces_final_html():
    """
    All 6 nodes run end-to-end. final_html is a non-empty string.
    processing_notes contains pipeline metrics.
    """
    from app.agents.typing.graph import typing_graph

    state = _state(
        document_id="doc-s6-full",
        ocr_raw_text="FIR document OCR text content for full pipeline test.",
        document_type="fir",
        presigned_url="",
    )

    def _resp(data):
        r = MagicMock()
        r.content = [MagicMock(text=json.dumps(data))]
        return r

    assess_resp = _resp(
        {
            "quality_score": 0.88,
            "issues": [],
            "language": "hindi_devanagari",
            "visual_vs_ocr_gaps": [],
        }
    )
    detect_resp = _resp(
        {
            "header": "FIR Header",
            "fir_contents": "Narrative text",
            "accused": "John Doe",
        }
    )
    reformat_resp = _resp(
        {
            "header": "Clean FIR Header",
            "fir_contents": "Clean Narrative",
            "accused": "Clean Accused",
        }
    )
    review_resp = _resp({"passed": True, "issues": [], "suggestions": ""})

    responses = [assess_resp, detect_resp, reformat_resp, review_resp]
    idx = 0

    async def mock_create(*args, **kwargs):
        nonlocal idx
        r = responses[min(idx, len(responses) - 1)]
        idx += 1
        return r

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=mock_create)

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.assess.load_agent_prompt", return_value="assess"
    ), patch(
        "app.agents.typing.nodes.detect.load_agent_prompt", return_value="detect"
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="reformat {retry_context} {sections_json}",
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="review {original_ocr} {formatted_json}",
    ):
        result = await typing_graph.ainvoke(state)

    # Node 6 ran: final_html is populated
    assert result["final_html"]
    assert 'class="typed-document typed-fir"' in result["final_html"]

    # Section content is in HTML
    assert "Clean FIR Header" in result["final_html"]
    assert "Clean Narrative" in result["final_html"]

    # processing_notes has metrics
    assert "quality=" in result["processing_notes"]
    assert "review=pass" in result["processing_notes"]


@pytest.mark.asyncio
async def test_T32_full_graph_with_retry_final_html_uses_second_reformat():
    """
    Review fails once → retry → second reformat → review passes.
    final_html contains the SECOND reformat output, not the first.
    """
    from app.agents.typing.graph import typing_graph

    state = _state(
        document_id="doc-s6-retry",
        ocr_raw_text="OCR text.",
        document_type="fir",
        presigned_url="",
    )

    def _resp(data):
        r = MagicMock()
        r.content = [MagicMock(text=json.dumps(data))]
        return r

    assess_resp = _resp(
        {
            "quality_score": 0.8,
            "issues": [],
            "language": "hindi_devanagari",
            "visual_vs_ocr_gaps": [],
        }
    )
    detect_resp = _resp({"header": "Raw Header"})
    reformat1_resp = _resp({"header": "First Pass"})
    review_fail = _resp(
        {"passed": False, "issues": ["Wrong name"], "suggestions": "Fix name."}
    )
    reformat2_resp = _resp({"header": "Second Pass"})
    review_pass = _resp({"passed": True, "issues": [], "suggestions": ""})

    responses = [
        assess_resp,
        detect_resp,
        reformat1_resp,
        review_fail,
        reformat2_resp,
        review_pass,
    ]
    idx = 0

    async def mock_create(*args, **kwargs):
        nonlocal idx
        r = responses[min(idx, len(responses) - 1)]
        idx += 1
        return r

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=mock_create)

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.assess.load_agent_prompt", return_value="assess"
    ), patch(
        "app.agents.typing.nodes.detect.load_agent_prompt", return_value="detect"
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="reformat {retry_context} {sections_json}",
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="review {original_ocr} {formatted_json}",
    ):
        result = await typing_graph.ainvoke(state)

    # Second pass content must be in the HTML
    assert "Second Pass" in result["final_html"]
    assert "First Pass" not in result["final_html"]
    assert result["retry_count"] == 1
    assert "retries=1" in result["processing_notes"]
