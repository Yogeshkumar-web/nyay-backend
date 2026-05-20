"""
Sprint 3 Tests — Node 3: Document Structure Detection

Test matrix:
  [helpers — pure unit tests, no mocking]
  T1  _pick_template: "fir" → "fir_ncrb", anything else → "generic"
  T2  _compute_confidence FIR: 4 of 8 keys present → 0.5
  T3  _compute_confidence FIR: all 8 keys present → 1.0
  T4  _compute_confidence generic: 2 sections → 0.5 (2/4 baseline)
  T5  _slice_ocr_for_pages: pages 1-10 → first 600 lines (60×10)
  T6  _slice_ocr_for_pages: hard cap at 8000 chars

  [_merge_sections — core logic]
  T7  header → only from batch 0, batch 1 header ignored
  T8  fir_contents → concatenated from all batches
  T9  accused → concatenated from all batches
  T10 leftovers → concatenated from all batches
  T11 Non-concat key → first non-empty wins; later batch fills missing key
  T12 null/empty values filtered out before merging
  T13 All batches empty → merged result is {}

  [_parse_sections_json]
  T14 Plain JSON with nulls → nulls removed
  T15 Fenced JSON → parsed correctly
  T16 Invalid JSON → raises JSONDecodeError

  [detect_document_structure node]
  T17 Single batch → Claude called once, sections populated
  T18 Two batches → Claude called twice in parallel, results merged
  T19 One batch raises exception → other batch's result still used
  T20 All batches fail → empty sections, low confidence, no crash
  T21 page_batches=[] → text-only single call, sections populated
  T22 FIR template: structure_confidence based on FIR keys
  T23 Generic template: structure_confidence based on any keys

  [graph integration]
  T24 Nodes 1+2+3 live: fetch → assess → detect → sections in final state
  T25 fetch_error → assess text-only → detect text-only → sections still populated
"""
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import fitz
import pytest

from app.agents.typing.nodes.detect import (
    _compute_confidence,
    _merge_sections,
    _parse_sections_json,
    _pick_template,
    _slice_ocr_for_pages,
    detect_document_structure,
)
from app.agents.typing.graph import build_typing_graph
from app.agents.typing.state import default_state


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_pdf_bytes(num_pages: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_state(**overrides):
    base = default_state(
        ocr_raw_text="\n".join(f"Line {i}" for i in range(1, 301)),  # 300 lines
        document_type="fir",
        document_id="sprint3-test",
        presigned_url="https://r2.example.com/doc.pdf",
        total_pages=5,
    )
    return {**base, **overrides}


def _make_batch(
    batch_number: int, page_start: int, page_end: int, n_images: int = 2
) -> dict:
    return {
        "batch_number": batch_number,
        "page_start": page_start,
        "page_end": page_end,
        "images_b64": ["fake_b64_image"] * n_images,
    }


def _fir_sections_full() -> dict:
    """All 8 FIR section keys populated."""
    return {
        "header": "FIR No: 123/2024\nDistrict: Lucknow",
        "sections_law": "Section 302 IPC",
        "occurrence": "15/01/2024 at 10:00 AM",
        "place": "Village Rampur, Lucknow",
        "complainant": "Ram Kumar, s/o Shyam Kumar",
        "accused": "1. Vijay Singh — Village Rampur",
        "fir_contents": "The complainant states that on 15th January...",
        "action_taken": "Case registered. IO assigned.",
    }


def _mock_claude_returning(sections: dict):
    """Mock Anthropic client whose messages.create always returns given sections."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(sections))]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    return mock_client


# ─────────────────────────────────────────────────────────────────────────────
# T1–T6 : Pure helper unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_T1_pick_template():
    assert _pick_template("fir") == "fir_ncrb"
    assert _pick_template("chargesheet") == "generic"
    assert _pick_template("affidavit") == "generic"
    assert _pick_template("other") == "generic"


def test_T2_confidence_fir_half_keys():
    sections = {
        k: "some text" for k in ["header", "sections_law", "occurrence", "place"]
    }
    assert _compute_confidence(sections, "fir_ncrb") == 0.5  # 4/8


def test_T3_confidence_fir_all_keys():
    assert _compute_confidence(_fir_sections_full(), "fir_ncrb") == 1.0  # 8/8


def test_T4_confidence_generic():
    sections = {"intro": "text", "body": "text"}
    conf = _compute_confidence(sections, "generic")
    assert conf == 0.5  # 2/4 baseline


def test_T5_slice_ocr_pages_1_to_10():
    # 300 lines; pages 1-10 → lines 0–600 (but only 300 exist)
    ocr = "\n".join(f"Line {i}" for i in range(1, 301))
    sliced = _slice_ocr_for_pages(ocr, 1, 10)
    lines = sliced.splitlines()
    assert lines[0] == "Line 1"  # starts at beginning
    assert len(lines) == 300  # all 300 lines fit within page 1-10 estimate


def test_T6_slice_ocr_hard_cap():
    # Very long OCR → should be capped at 8000 chars
    ocr = "A" * 20_000
    sliced = _slice_ocr_for_pages(ocr, 1, 5)
    assert len(sliced) <= 8_000


# ─────────────────────────────────────────────────────────────────────────────
# T7–T13 : _merge_sections logic
# ─────────────────────────────────────────────────────────────────────────────


def test_T7_header_only_from_first_batch():
    results = [
        {"header": "FIR No: 100 from batch 1"},
        {"header": "FIR No: 999 from batch 2 — WRONG"},
    ]
    merged = _merge_sections(results)
    assert merged["header"] == "FIR No: 100 from batch 1"


def test_T8_fir_contents_concatenated():
    results = [
        {"fir_contents": "Part 1 of the narrative..."},
        {"fir_contents": "...continuing Part 2 of the narrative."},
    ]
    merged = _merge_sections(results)
    assert "Part 1" in merged["fir_contents"]
    assert "Part 2" in merged["fir_contents"]
    # Both parts present in order
    assert merged["fir_contents"].index("Part 1") < merged["fir_contents"].index(
        "Part 2"
    )


def test_T9_accused_concatenated():
    results = [
        {"accused": "1. Ram Singh — Village A"},
        {"accused": "2. Shyam Kumar — Village B"},
    ]
    merged = _merge_sections(results)
    assert "Ram Singh" in merged["accused"]
    assert "Shyam Kumar" in merged["accused"]


def test_T10_leftovers_concatenated():
    results = [
        {"leftovers": "Extra text from page 1"},
        {"leftovers": "Extra text from page 2"},
    ]
    merged = _merge_sections(results)
    assert "page 1" in merged["leftovers"]
    assert "page 2" in merged["leftovers"]


def test_T11_non_concat_first_non_empty_wins():
    results = [
        {"occurrence": "", "place": ""},  # batch 1: both empty
        {"occurrence": "15/01/2024", "place": ""},  # batch 2: occurrence found
        {
            "occurrence": "WRONG DATE",
            "place": "Village X",
        },  # batch 3: should NOT override occurrence
    ]
    merged = _merge_sections(results)
    assert merged["occurrence"] == "15/01/2024"  # batch 2 wins
    assert merged["place"] == "Village X"  # batch 3 fills missing key


def test_T12_null_and_empty_values_filtered():
    # header=None in batch 0 → skipped (null filtered).
    # header in batch 1 → also skipped (header-only-from-batch-0 rule).
    # occurrence in batch 0 → kept (non-null, non-empty).
    results = [
        {"header": None, "sections_law": "", "occurrence": "15/01/2024"},
        {"header": "FIR No: 123"},
    ]
    merged = _merge_sections(results)
    # occurrence was non-null in batch 0 → present
    assert "occurrence" in merged
    assert merged["occurrence"] == "15/01/2024"
    # sections_law was empty string → filtered
    assert "sections_law" not in merged
    # header from batch 1 is skipped (header-only-from-first-batch rule)
    # header from batch 0 was None → filtered → header not in merged at all
    assert "header" not in merged


def test_T13_all_batches_empty_returns_empty_dict():
    merged = _merge_sections([{}, {}, {}])
    assert merged == {}


# ─────────────────────────────────────────────────────────────────────────────
# T14–T16 : _parse_sections_json
# ─────────────────────────────────────────────────────────────────────────────


def test_T14_nulls_removed_from_parsed_json():
    raw = '{"header": "FIR No: 123", "sections_law": null, "occurrence": ""}'
    result = _parse_sections_json(raw)
    assert "header" in result
    assert "sections_law" not in result
    assert "occurrence" not in result


def test_T15_fenced_json_parsed():
    raw = '```json\n{"header": "FIR No: 456", "place": "Lucknow"}\n```'
    result = _parse_sections_json(raw)
    assert result["header"] == "FIR No: 456"
    assert result["place"] == "Lucknow"


def test_T16_invalid_json_raises():
    with pytest.raises((json.JSONDecodeError, AttributeError)):
        _parse_sections_json("Not JSON at all")


# ─────────────────────────────────────────────────────────────────────────────
# T17–T23 : detect_document_structure node
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T17_single_batch_called_once():
    """T17: 1 batch → Claude called exactly once, sections populated."""
    state = _make_state(
        page_batches=[_make_batch(1, 1, 5)],
        batch_count=1,
        visual_vs_ocr_gaps=[],
    )
    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(_fir_sections_full()))]
        return mock_msg

    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    with patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        result = await detect_document_structure(state)

    assert call_count == 1
    assert result["detected_sections"].get("header") is not None
    assert result["document_template"] == "fir_ncrb"
    assert result["structure_confidence"] == 1.0


@pytest.mark.asyncio
async def test_T18_two_batches_parallel_results_merged():
    """T18: 2 batches → Claude called twice, fir_contents concatenated."""
    batch1_sections = {
        "header": "FIR No: 123",
        "fir_contents": "Batch 1 narrative text.",
    }
    batch2_sections = {
        "fir_contents": "Batch 2 continuation.",
        "action_taken": "Case registered.",
    }

    call_count = 0
    responses = [batch1_sections, batch2_sections]

    async def mock_create(**kwargs):
        nonlocal call_count
        resp = responses[call_count % len(responses)]
        call_count += 1
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(resp))]
        return mock_msg

    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    state = _make_state(
        page_batches=[_make_batch(1, 1, 10), _make_batch(2, 11, 12)],
        batch_count=2,
        visual_vs_ocr_gaps=[],
    )

    with patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        result = await detect_document_structure(state)

    assert call_count == 2
    assert result["detected_sections"]["header"] == "FIR No: 123"
    assert "Batch 1" in result["detected_sections"]["fir_contents"]
    assert "Batch 2" in result["detected_sections"]["fir_contents"]
    assert result["detected_sections"]["action_taken"] == "Case registered."


@pytest.mark.asyncio
async def test_T19_one_batch_exception_other_succeeds():
    """T19: Batch 1 raises exception → Batch 2 result still merged, no crash."""
    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Claude timeout on batch 1")
        mock_msg = MagicMock()
        mock_msg.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "fir_contents": "Surviving batch narrative.",
                        "action_taken": "Survivor.",
                    }
                )
            )
        ]
        return mock_msg

    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    state = _make_state(
        page_batches=[_make_batch(1, 1, 10), _make_batch(2, 11, 12)],
        batch_count=2,
        visual_vs_ocr_gaps=[],
    )

    with patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        result = await detect_document_structure(state)

    # No crash
    assert isinstance(result["detected_sections"], dict)
    # Batch 2 result still used
    assert "Surviving batch narrative." in result["detected_sections"].get(
        "fir_contents", ""
    )
    assert result["detected_sections"].get("action_taken") == "Survivor."


@pytest.mark.asyncio
async def test_T20_all_batches_fail_no_crash():
    """T20: All batches raise exceptions → empty sections, confidence=0.0, no crash."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))

    state = _make_state(
        page_batches=[_make_batch(1, 1, 10), _make_batch(2, 11, 20)],
        batch_count=2,
        visual_vs_ocr_gaps=[],
    )

    with patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        result = await detect_document_structure(state)

    assert isinstance(result["detected_sections"], dict)
    assert result["structure_confidence"] == 0.0


@pytest.mark.asyncio
async def test_T21_text_only_when_no_batches():
    """T21: page_batches=[] → single text-only Claude call, sections populated."""
    mock_client = _mock_claude_returning(
        {"header": "Text-only FIR", "fir_contents": "Narrative."}
    )
    state = _make_state(
        page_batches=[],
        batch_count=0,
        fetch_error="403 expired",
        visual_vs_ocr_gaps=[],
    )

    with patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        result = await detect_document_structure(state)

    assert result["detected_sections"].get("header") == "Text-only FIR"
    assert result["detected_sections"].get("fir_contents") == "Narrative."
    assert result["document_template"] == "fir_ncrb"


@pytest.mark.asyncio
async def test_T22_fir_template_confidence_uses_fir_keys():
    """T22: FIR doc — confidence counts only the 8 known FIR keys."""
    # Return 4 of 8 FIR keys
    partial = {k: "text" for k in ["header", "sections_law", "occurrence", "place"]}
    mock_client = _mock_claude_returning(partial)
    state = _make_state(
        document_type="fir",
        page_batches=[_make_batch(1, 1, 5)],
        batch_count=1,
        visual_vs_ocr_gaps=[],
    )

    with patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        result = await detect_document_structure(state)

    assert result["document_template"] == "fir_ncrb"
    assert result["structure_confidence"] == 0.5


@pytest.mark.asyncio
async def test_T23_generic_template_confidence():
    """T23: Non-FIR doc — template is generic, confidence based on any keys."""
    mock_client = _mock_claude_returning({"intro": "text", "body": "text"})
    state = _make_state(
        document_type="affidavit",
        page_batches=[_make_batch(1, 1, 3)],
        batch_count=1,
        visual_vs_ocr_gaps=[],
    )

    with patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        result = await detect_document_structure(state)

    assert result["document_template"] == "generic"
    assert result["structure_confidence"] == 0.5  # 2/4 baseline


# ─────────────────────────────────────────────────────────────────────────────
# T24–T25 : Graph integration
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T24_graph_nodes_1_2_3_live():
    """T24: Nodes 1+2+3 all live — final state has detect output."""
    pdf_bytes = _make_pdf_bytes(5)
    state = _make_state(presigned_url="https://r2.example.com/doc.pdf", total_pages=0)

    # Node 1: httpx mock
    mock_http_resp = MagicMock()
    mock_http_resp.content = pdf_bytes
    mock_http_resp.raise_for_status = MagicMock()

    assess_response = {
        "quality_score": 0.9,
        "issues": ["clean"],
        "language": "english",
        "visual_vs_ocr_gaps": [],
    }
    detect_response = _fir_sections_full()

    call_count = 0

    async def mock_claude_create(**kwargs):
        nonlocal call_count
        call_count += 1
        # First Claude call = assess (Node 2), subsequent = detect (Node 3)
        resp_data = assess_response if call_count == 1 else detect_response
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(resp_data))]
        return mock_msg

    mock_client = MagicMock()
    mock_client.messages.create = mock_claude_create

    graph = build_typing_graph()

    with patch(
        "app.agents.typing.nodes.fetch_pages.httpx.AsyncClient"
    ) as MockHTTP, patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        mock_ci = AsyncMock()
        mock_ci.get = AsyncMock(return_value=mock_http_resp)
        MockHTTP.return_value.__aenter__ = AsyncMock(return_value=mock_ci)
        MockHTTP.return_value.__aexit__ = AsyncMock(return_value=False)

        final = await graph.ainvoke(state)

    # Node 1 output
    assert final["fetch_error"] is None
    assert final["batch_count"] == 1

    # Node 2 output
    assert final["ocr_quality_score"] == 0.9
    assert final["ocr_language"] == "english"

    # Node 3 output
    assert final["document_template"] == "fir_ncrb"
    assert final["structure_confidence"] == 1.0
    assert final["detected_sections"].get("header") is not None
    assert final["detected_sections"].get("fir_contents") is not None


@pytest.mark.asyncio
async def test_T25_graph_fetch_error_all_three_nodes_run():
    """T25: fetch_error set → Node 2 text-only → Node 3 text-only → sections populated."""
    state = _make_state(presigned_url="")  # empty URL → immediate fetch_error

    assess_resp = {
        "quality_score": 0.4,
        "issues": ["missing_sections"],
        "language": "hindi",
        "visual_vs_ocr_gaps": [],
    }
    detect_resp = {"header": "FIR No: 999", "fir_contents": "Text-only narrative."}

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        data = assess_resp if call_count == 1 else detect_resp
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(data))]
        return mock_msg

    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    graph = build_typing_graph()

    with patch(
        "app.agents.typing.nodes.assess.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.detect.get_anthropic_client", return_value=mock_client
    ):
        final = await graph.ainvoke(state)

    # Node 1: fetch_error set
    assert final["fetch_error"] is not None
    assert final["page_batches"] == []

    # Node 2: ran text-only
    assert final["ocr_quality_score"] == 0.4
    assert final["ocr_language"] == "hindi"

    # Node 3: ran text-only
    assert final["detected_sections"].get("header") == "FIR No: 999"
    assert "Text-only" in final["detected_sections"].get("fir_contents", "")
