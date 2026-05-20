"""
Sprint 2 Tests — Node 2: OCR Quality Assessment

Test matrix:
  [_parse_json_response helper]
  T1  Plain JSON string → parsed correctly
  T2  JSON wrapped in ```json ... ``` fences → parsed correctly
  T3  Stray text before/after JSON → parsed correctly
  T4  Completely invalid string → raises json.JSONDecodeError

  [assess_ocr_quality node]
  T5  Clean Claude response (multimodal path) → all fields set correctly
  T6  quality_score clamped: >1.0 → 1.0, <0.0 → 0.0
  T7  Unknown language string → coerced to "unknown"
  T8  issues field is a string, not list → wrapped in list
  T9  visual_vs_ocr_gaps is not a list → coerced to []
  T10 Claude returns bad JSON → fallback defaults, no exception
  T11 Claude API raises exception → fallback defaults, no exception
  T12 No page_batches (fetch_error set) → text-only mode, no image content blocks
  T13 With page_batches → image content blocks sent to Claude

  [graph integration]
  T14 Nodes 1+2 live → assess fields populated in final state
  T15 fetch_error set → assess still runs (text-only), pipeline continues
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import fitz
import io
import pytest

from app.agents.typing.nodes.assess import _parse_json_response, assess_ocr_quality
from app.agents.typing.graph import build_typing_graph
from app.agents.typing.state import default_state


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_pdf_bytes(num_pages: int = 2) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_state(**overrides):
    base = default_state(
        ocr_raw_text="District: Lucknow\nFIR No: 123/2024\nPolice Station: Kotwali",
        document_type="fir",
        document_id="sprint2-test-doc",
        presigned_url="https://r2.example.com/doc.pdf",
        total_pages=5,
    )
    return {**base, **overrides}


def _good_claude_response() -> dict:
    return {
        "quality_score": 0.82,
        "issues": ["broken_lines", "encoding_errors"],
        "language": "mixed",
        "visual_vs_ocr_gaps": ["Accused table on page 2 has 3 rows but OCR shows 1"],
        "notes": "Reasonable quality, some Hindi encoding issues",
    }


def _mock_claude(response_dict: dict):
    """Return a mock Anthropic client whose messages.create returns response_dict as JSON."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(response_dict))]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    return mock_client


# ─────────────────────────────────────────────────────────────────────────────
# T1–T4 : _parse_json_response unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_T1_plain_json_parsed():
    raw = '{"quality_score": 0.9, "issues": ["clean"], "language": "english"}'
    result = _parse_json_response(raw)
    assert result["quality_score"] == 0.9
    assert result["language"] == "english"


def test_T2_fenced_json_parsed():
    raw = '```json\n{"quality_score": 0.75, "issues": ["broken_lines"], "language": "hindi"}\n```'
    result = _parse_json_response(raw)
    assert result["quality_score"] == 0.75
    assert "broken_lines" in result["issues"]


def test_T3_stray_text_around_json():
    raw = 'Here is the assessment:\n{"quality_score": 0.6, "issues": [], "language": "mixed"}\nDone.'
    result = _parse_json_response(raw)
    assert result["quality_score"] == 0.6
    assert result["language"] == "mixed"


def test_T4_invalid_string_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_response("This is not JSON at all")


# ─────────────────────────────────────────────────────────────────────────────
# T5–T13 : assess_ocr_quality node tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T5_clean_response_all_fields_set():
    """T5: Valid Claude response → all four output fields populated correctly."""
    state = _make_state()
    mock_client = _mock_claude(_good_claude_response())

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ):
        result = await assess_ocr_quality(state)

    assert result["ocr_quality_score"] == 0.82
    assert "broken_lines" in result["ocr_issues"]
    assert "encoding_errors" in result["ocr_issues"]
    assert result["ocr_language"] == "mixed"
    assert len(result["visual_vs_ocr_gaps"]) == 1
    assert "Accused table" in result["visual_vs_ocr_gaps"][0]


@pytest.mark.asyncio
async def test_T6_quality_score_clamped():
    """T6: quality_score > 1.0 → clamped to 1.0; < 0.0 → clamped to 0.0."""
    state = _make_state()

    # Test > 1.0
    resp = {**_good_claude_response(), "quality_score": 1.5}
    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client",
        return_value=_mock_claude(resp),
    ):
        result = await assess_ocr_quality(state)
    assert result["ocr_quality_score"] == 1.0

    # Test < 0.0
    resp = {**_good_claude_response(), "quality_score": -0.3}
    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client",
        return_value=_mock_claude(resp),
    ):
        result = await assess_ocr_quality(state)
    assert result["ocr_quality_score"] == 0.0


@pytest.mark.asyncio
async def test_T7_unknown_language_coerced():
    """T7: Unrecognised language string → coerced to 'unknown'."""
    state = _make_state()
    resp = {**_good_claude_response(), "language": "urdu"}
    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client",
        return_value=_mock_claude(resp),
    ):
        result = await assess_ocr_quality(state)
    assert result["ocr_language"] == "unknown"


@pytest.mark.asyncio
async def test_T8_issues_string_wrapped_in_list():
    """T8: Claude returns issues as a bare string → wrapped in list."""
    state = _make_state()
    resp = {**_good_claude_response(), "issues": "broken_lines"}
    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client",
        return_value=_mock_claude(resp),
    ):
        result = await assess_ocr_quality(state)
    assert isinstance(result["ocr_issues"], list)
    assert "broken_lines" in result["ocr_issues"]


@pytest.mark.asyncio
async def test_T9_gaps_non_list_coerced_to_empty():
    """T9: visual_vs_ocr_gaps is not a list → coerced to []."""
    state = _make_state()
    resp = {**_good_claude_response(), "visual_vs_ocr_gaps": "some gap text"}
    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client",
        return_value=_mock_claude(resp),
    ):
        result = await assess_ocr_quality(state)
    assert result["visual_vs_ocr_gaps"] == []


@pytest.mark.asyncio
async def test_T10_bad_json_from_claude_uses_fallback():
    """T10: Claude returns garbage (not JSON) → fallback defaults, no exception raised."""
    state = _make_state()

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Sorry, I cannot assess this document.")]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ):
        result = await assess_ocr_quality(state)

    # Should not raise; should use fallback
    assert result["ocr_quality_score"] == 0.5
    assert "assessment_failed" in result["ocr_issues"]
    assert result["ocr_language"] == "unknown"
    assert result["visual_vs_ocr_gaps"] == []


@pytest.mark.asyncio
async def test_T11_claude_api_exception_uses_fallback():
    """T11: Claude API raises an exception → fallback defaults, no exception raised."""
    state = _make_state()

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("API unavailable"))

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ):
        result = await assess_ocr_quality(state)

    assert result["ocr_quality_score"] == 0.5
    assert "assessment_failed" in result["ocr_issues"]


@pytest.mark.asyncio
async def test_T12_no_page_batches_text_only_mode():
    """T12: page_batches=[] (fetch_error set) → no image blocks sent to Claude."""
    state = _make_state(page_batches=[], batch_count=0, fetch_error="403 URL expired")
    mock_client = _mock_claude(_good_claude_response())

    captured_content = []

    async def capture_create(**kwargs):
        captured_content.extend(kwargs["messages"][0]["content"])
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(_good_claude_response()))]
        return mock_msg

    mock_client.messages.create = capture_create

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ):
        result = await assess_ocr_quality(state)

    # Only text block — no image blocks
    assert all(block.get("type") == "text" for block in captured_content)
    # Result still populated (text-only assessment worked)
    assert result["ocr_quality_score"] == 0.82


@pytest.mark.asyncio
async def test_T13_with_page_batches_sends_images():
    """T13: page_batches present → image content blocks sent to Claude."""
    from app.agents.shared.pdf_utils import pdf_bytes_to_batches

    pdf_bytes = _make_pdf_bytes(3)
    batches = pdf_bytes_to_batches(pdf_bytes)
    serialized = [b._asdict() for b in batches]

    state = _make_state(page_batches=serialized, batch_count=1, fetch_error=None)

    captured_content = []

    async def capture_create(**kwargs):
        captured_content.extend(kwargs["messages"][0]["content"])
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(_good_claude_response()))]
        return mock_msg

    mock_client = MagicMock()
    mock_client.messages.create = capture_create

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ):
        await assess_ocr_quality(state)

    image_blocks = [b for b in captured_content if b.get("type") == "image"]
    assert len(image_blocks) == 3  # 3 pages in batch 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"


# ─────────────────────────────────────────────────────────────────────────────
# T14–T15 : Graph integration tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T14_graph_nodes_1_and_2_live():
    """T14: Nodes 1+2 live — fetch batches then assess → assess fields in final state."""
    pdf_bytes = _make_pdf_bytes(4)
    state = _make_state(presigned_url="https://r2.example.com/test.pdf", total_pages=0)

    # Mock httpx for Node 1
    mock_response = MagicMock()
    mock_response.content = pdf_bytes
    mock_response.raise_for_status = MagicMock()

    # Mock Claude for Node 2
    mock_claude = _mock_claude(_good_claude_response())

    graph = build_typing_graph()

    with patch(
        "app.agents.typing.nodes.fetch_pages.httpx.AsyncClient"
    ) as MockClient, patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_claude
    ):
        mock_ci = AsyncMock()
        mock_ci.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_ci)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        final = await graph.ainvoke(state)

    # Node 1 worked
    assert final["fetch_error"] is None
    assert final["batch_count"] == 1
    assert final["total_pages"] == 4

    # Node 2 worked
    assert final["ocr_quality_score"] == 0.82
    assert final["ocr_language"] == "mixed"
    assert len(final["visual_vs_ocr_gaps"]) == 1


@pytest.mark.asyncio
async def test_T15_graph_fetch_error_assess_still_runs():
    """T15: Node 1 sets fetch_error → Node 2 still runs in text-only mode."""
    state = _make_state(presigned_url="")  # empty URL → immediate fetch_error

    mock_claude = _mock_claude(
        {
            "quality_score": 0.6,
            "issues": ["broken_lines"],
            "language": "hindi",
            "visual_vs_ocr_gaps": [],
        }
    )

    graph = build_typing_graph()

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_claude
    ):
        final = await graph.ainvoke(state)

    # Node 1 set fetch_error
    assert final["fetch_error"] is not None
    assert final["page_batches"] == []

    # Node 2 still ran and set quality fields
    assert final["ocr_quality_score"] == 0.6
    assert final["ocr_language"] == "hindi"
    assert "broken_lines" in final["ocr_issues"]
