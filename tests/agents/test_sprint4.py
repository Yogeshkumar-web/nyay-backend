"""
Sprint 4 tests — Node 4: reformat_sections

Covers:
- _build_retry_context()
- _select_reference_images()
- _parse_reformat_response()
- reformat_sections() node (Claude mocked)
- Graph integration: Nodes 1-4 chained
"""
import json
import pytest

from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.typing.nodes.reformat import (
    _build_retry_context,
    _parse_reformat_response,
    _select_reference_images,
    reformat_sections,
)
from app.agents.typing.state import default_state


def _state(**overrides):
    """Create a fully-initialized state with sensible test defaults."""
    s = default_state(
        ocr_raw_text="Sample OCR text for testing.",
        document_type="fir",
        document_id="test-doc-s4",
        presigned_url="",
        total_pages=5,
    )
    s.update(overrides)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# _build_retry_context
# ─────────────────────────────────────────────────────────────────────────────


def test_T1_retry_ctx_empty_when_retry_count_zero():
    state = _state(retry_count=0, review_suggestions="some issues")
    assert _build_retry_context(state) == ""


def test_T2_retry_ctx_empty_when_no_suggestions():
    state = _state(retry_count=1, review_suggestions="")
    assert _build_retry_context(state) == ""


def test_T3_retry_ctx_populated_when_retry_and_suggestions():
    state = _state(
        retry_count=2, review_suggestions="Fix proper nouns and add missing table row."
    )
    ctx = _build_retry_context(state)
    assert "PREVIOUS ATTEMPT HAD ISSUES" in ctx
    assert "Fix proper nouns" in ctx


# ─────────────────────────────────────────────────────────────────────────────
# _select_reference_images
# ─────────────────────────────────────────────────────────────────────────────


def test_T4_select_images_empty_batches():
    assert _select_reference_images([]) == []


def test_T5_select_images_single_batch_max_five():
    batches = [{"images_b64": ["a", "b", "c", "d", "e", "f"]}]
    imgs = _select_reference_images(batches)
    assert imgs == ["a", "b", "c"]  # first 3 from batch 1


def test_T6_select_images_two_batches_combines():
    batches = [
        {"images_b64": ["a", "b", "c", "d"]},
        {"images_b64": ["x", "y", "z"]},
    ]
    imgs = _select_reference_images(batches)
    # first 3 from batch 1 + last 2 from last batch
    assert imgs == ["a", "b", "c", "x", "y"]


def test_T7_select_images_skips_empty_strings():
    batches = [
        {"images_b64": ["", "b", "c"]},
        {"images_b64": ["x", ""]},
    ]
    imgs = _select_reference_images(batches)
    assert "" not in imgs
    assert "b" in imgs
    assert "x" in imgs


def test_T8_select_images_caps_at_max():
    batches = [
        {"images_b64": ["a", "b", "c"]},
        {"images_b64": ["x", "y", "z"]},
    ]
    imgs = _select_reference_images(batches)
    assert len(imgs) <= 5


# ─────────────────────────────────────────────────────────────────────────────
# _parse_reformat_response
# ─────────────────────────────────────────────────────────────────────────────


def test_T9_parse_plain_json():
    raw = json.dumps({"header": "Section 1", "fir_contents": "Narrative text"})
    result = _parse_reformat_response(raw)
    assert result == {"header": "Section 1", "fir_contents": "Narrative text"}


def test_T10_parse_fenced_json():
    raw = '```json\n{"header": "Title", "accused": "John Doe"}\n```'
    result = _parse_reformat_response(raw)
    assert result["header"] == "Title"
    assert result["accused"] == "John Doe"


def test_T11_parse_null_values_skipped():
    raw = json.dumps({"header": "Title", "occurrence": None, "place": ""})
    result = _parse_reformat_response(raw)
    assert "header" in result
    # null is skipped (not string), empty string is kept (intentional clear)
    assert "occurrence" not in result
    assert "place" in result


def test_T12_parse_non_string_values_skipped():
    raw = json.dumps({"header": "Title", "score": 42, "valid": True})
    result = _parse_reformat_response(raw)
    assert "header" in result
    assert "score" not in result
    assert "valid" not in result


def test_T13_parse_stray_text_around_json():
    raw = 'Here is the reformatted output:\n{"header": "Court Name"}\nDone.'
    result = _parse_reformat_response(raw)
    assert result["header"] == "Court Name"


# ─────────────────────────────────────────────────────────────────────────────
# reformat_sections node
# ─────────────────────────────────────────────────────────────────────────────


def _make_claude_resp(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(data))]
    return resp


@pytest.mark.asyncio
async def test_T14_empty_detected_sections_returns_empty():
    state = _state(detected_sections={})
    result = await reformat_sections(state)
    assert result["formatted_sections"] == {}


@pytest.mark.asyncio
async def test_T15_all_sections_reformatted():
    sections = {
        "header": "raw header",
        "fir_contents": "raw narrative",
        "accused": "raw accused",
        "action_taken": "raw action",
    }
    reformatted = {
        "header": "Clean Header",
        "fir_contents": "Clean narrative",
        "accused": "Clean accused",
        "action_taken": "Clean action",
    }
    state = _state(detected_sections=sections, document_template="fir_ncrb")

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_resp(reformatted))

    with patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="prompt {retry_context} {sections_json}",
    ):
        result = await reformat_sections(state)

    assert result["formatted_sections"]["header"] == "Clean Header"
    assert result["formatted_sections"]["fir_contents"] == "Clean narrative"


@pytest.mark.asyncio
async def test_T16_group_failure_falls_back_to_original():
    """If Claude call fails for one group, original detected text is used."""
    sections = {f"sec_{i}": f"raw_{i}" for i in range(6)}
    state = _state(detected_sections=sections, document_template="generic")

    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("API error")
        # Second group succeeds
        group_keys = list(sections.keys())[4:]
        return _make_claude_resp({k: f"clean_{k}" for k in group_keys})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=side_effect)

    with patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="prompt {retry_context} {sections_json}",
    ):
        result = await reformat_sections(state)

    formatted = result["formatted_sections"]
    # First group (failed) → original text preserved
    assert formatted["sec_0"] == "raw_0"
    assert formatted["sec_3"] == "raw_3"
    # Second group (succeeded) → reformatted
    assert formatted["sec_4"] == "clean_sec_4"


@pytest.mark.asyncio
async def test_T17_retry_context_injected_in_prompt():
    """When retry_count > 0, review_suggestions appear in prompt."""
    sections = {"header": "raw"}
    state = _state(
        detected_sections=sections,
        document_template="fir_ncrb",
        retry_count=1,
        review_suggestions="Fix the accused table.",
    )

    captured_content = []

    async def capture(*args, **kwargs):
        captured_content.extend(kwargs.get("messages", [{}])[0].get("content", []))
        return _make_claude_resp({"header": "Clean"})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=capture)

    with patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="ctx={retry_context} secs={sections_json}",
    ):
        await reformat_sections(state)

    prompt_text = captured_content[0]["text"]
    assert "PREVIOUS ATTEMPT HAD ISSUES" in prompt_text
    assert "Fix the accused table." in prompt_text


@pytest.mark.asyncio
async def test_T18_no_images_sent_when_no_page_batches():
    """When page_batches is empty, no image content blocks are added."""
    sections = {"header": "raw"}
    state = _state(
        detected_sections=sections, document_template="generic", page_batches=[]
    )

    captured_content = []

    async def capture(*args, **kwargs):
        msgs = kwargs.get("messages", [])
        if msgs:
            captured_content.extend(msgs[0].get("content", []))
        return _make_claude_resp({"header": "Clean"})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=capture)

    with patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="prompt {retry_context} {sections_json}",
    ):
        await reformat_sections(state)

    image_blocks = [b for b in captured_content if b.get("type") == "image"]
    assert image_blocks == []


@pytest.mark.asyncio
async def test_T19_images_sent_when_page_batches_present():
    """When page_batches exist, image content blocks are appended."""
    sections = {"header": "raw"}
    state = _state(
        detected_sections=sections,
        document_template="fir_ncrb",
        page_batches=[
            {"page_start": 1, "page_end": 5, "images_b64": ["aaa", "bbb", "ccc"]}
        ],
    )

    captured_content = []

    async def capture(*args, **kwargs):
        msgs = kwargs.get("messages", [])
        if msgs:
            captured_content.extend(msgs[0].get("content", []))
        return _make_claude_resp({"header": "Clean"})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=capture)

    with patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="prompt {retry_context} {sections_json}",
    ):
        await reformat_sections(state)

    image_blocks = [b for b in captured_content if b.get("type") == "image"]
    assert len(image_blocks) > 0


@pytest.mark.asyncio
async def test_T20_missing_prompt_file_fallback():
    """If prompt file missing, formatted_sections falls back to detected_sections."""
    sections = {"header": "raw", "accused": "raw accused"}
    state = _state(detected_sections=sections, document_template="fir_ncrb")

    with patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        side_effect=FileNotFoundError("missing"),
    ):
        result = await reformat_sections(state)

    # Falls back to original detected text
    assert result["formatted_sections"] == sections


@pytest.mark.asyncio
async def test_T21_sections_per_call_grouping():
    """Sections are grouped into SECTIONS_PER_CALL=4 chunks."""
    from app.agents.typing.nodes.reformat import SECTIONS_PER_CALL

    # 9 sections → 3 groups (4, 4, 1)
    sections = {f"s{i}": f"raw{i}" for i in range(9)}
    state = _state(detected_sections=sections, document_template="generic")

    call_count = 0

    async def count_calls(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        msgs = kwargs.get("messages", [{}])
        content = msgs[0].get("content", [{}])
        prompt_text = content[0].get("text", "{}")
        # Extract the sections JSON from the prompt
        secs_start = prompt_text.find("{")
        secs_json = prompt_text[secs_start:]
        secs = json.loads(secs_json)
        return _make_claude_resp({k: f"clean_{k}" for k in secs})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=count_calls)

    with patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="{sections_json}",
    ):
        result = await reformat_sections(state)

    expected_calls = (9 + SECTIONS_PER_CALL - 1) // SECTIONS_PER_CALL  # ceil(9/4) = 3
    assert call_count == expected_calls
    assert len(result["formatted_sections"]) == 9


# ─────────────────────────────────────────────────────────────────────────────
# Graph integration: Nodes 1-4 chained
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T22_graph_nodes_1_to_4_live():
    """Nodes 1-4 run; formatted_sections key is present in final state."""
    from app.agents.typing.graph import typing_graph

    state = _state(
        document_id="doc-graph-s4",
        presigned_url="",
        ocr_raw_text="Sample OCR text for FIR document.",
        document_type="fir",
    )

    assess_resp = MagicMock()
    assess_resp.content = [
        MagicMock(
            text=json.dumps(
                {
                    "quality_score": 0.85,
                    "issues": [],
                    "language": "hindi_devanagari",
                    "visual_vs_ocr_gaps": [],
                }
            )
        )
    ]

    detect_resp = MagicMock()
    detect_resp.content = [
        MagicMock(
            text=json.dumps(
                {
                    "header": "FIR Header",
                    "fir_contents": "Narrative text",
                }
            )
        )
    ]

    reformat_resp = MagicMock()
    reformat_resp.content = [
        MagicMock(
            text=json.dumps(
                {
                    "header": "Clean FIR Header",
                    "fir_contents": "Clean Narrative",
                }
            )
        )
    ]

    responses = [assess_resp, detect_resp, reformat_resp]
    call_idx = 0

    async def mock_create(*args, **kwargs):
        nonlocal call_idx
        resp = responses[min(call_idx, len(responses) - 1)]
        call_idx += 1
        return resp

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
        "app.agents.typing.nodes.assess.load_agent_prompt", return_value="assess prompt"
    ), patch(
        "app.agents.typing.nodes.detect.load_agent_prompt", return_value="detect prompt"
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="reformat {retry_context} {sections_json}",
    ):
        result = await typing_graph.ainvoke(state)

    assert "formatted_sections" in result
    # Node 4 ran (either cleaned or fallback to detected)
    assert isinstance(result["formatted_sections"], dict)


@pytest.mark.asyncio
async def test_T23_formatted_sections_present_after_reformat_failure():
    """Even if reformat node's Claude call fails for all groups, formatted_sections is set."""
    state = _state(
        document_id="doc-reformat-fail",
        detected_sections={"header": "raw header", "accused": "raw accused"},
        document_template="fir_ncrb",
        page_batches=[],
    )

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("Claude down"))

    with patch(
        "app.agents.typing.nodes.reformat.get_anthropic_client",
        return_value=mock_client,
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="prompt {retry_context} {sections_json}",
    ):
        result = await reformat_sections(state)

    # All groups failed → original text preserved
    assert result["formatted_sections"]["header"] == "raw header"
    assert result["formatted_sections"]["accused"] == "raw accused"
