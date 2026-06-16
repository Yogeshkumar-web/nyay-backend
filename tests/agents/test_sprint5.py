"""
Sprint 5 tests — Node 5: self_review + conditional retry edge

Covers:
- _parse_review_response()
- review_router()
- self_review() node (Claude mocked)
- Graph integration: Nodes 1-5 with retry loop
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.typing.nodes.review import (
    MAX_RETRIES,
    _parse_review_response,
    review_router,
    self_review,
)
from app.agents.typing.state import default_state


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


def _state(**overrides):
    s = default_state(
        ocr_raw_text="Original OCR text for review testing.",
        document_type="fir",
        document_id="test-doc-s5",
        presigned_url="",
        total_pages=3,
    )
    s.update(overrides)
    return s


def _claude_resp(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(data))]
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# _parse_review_response
# ─────────────────────────────────────────────────────────────────────────────


def test_T1_parse_passed_true():
    raw = json.dumps({"passed": True, "issues": [], "suggestions": ""})
    result = _parse_review_response(raw)
    assert result["passed"] is True
    assert result["issues"] == []
    assert result["suggestions"] == ""


def test_T2_parse_passed_false_with_issues():
    raw = json.dumps(
        {
            "passed": False,
            "issues": ["Name mismatch on page 2", "Missing accused row"],
            "suggestions": "Fix accused table and verify name spelling.",
        }
    )
    result = _parse_review_response(raw)
    assert result["passed"] is False
    assert len(result["issues"]) == 2
    assert "Fix accused table" in result["suggestions"]


def test_T3_parse_fenced_json():
    raw = '```json\n{"passed": true, "issues": [], "suggestions": "All good."}\n```'
    result = _parse_review_response(raw)
    assert result["passed"] is True


def test_T4_parse_issues_string_coerced_to_list():
    raw = json.dumps(
        {"passed": False, "issues": "Name error found", "suggestions": "Fix it."}
    )
    result = _parse_review_response(raw)
    assert isinstance(result["issues"], list)
    assert result["issues"] == ["Name error found"]


def test_T5_parse_issues_non_list_non_str_coerced_to_empty():
    raw = json.dumps({"passed": True, "issues": 42, "suggestions": ""})
    result = _parse_review_response(raw)
    assert result["issues"] == []


def test_T6_parse_suggestions_non_str_coerced():
    raw = json.dumps({"passed": False, "issues": [], "suggestions": None})
    result = _parse_review_response(raw)
    assert isinstance(result["suggestions"], str)


def test_T7_parse_bad_json_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_review_response("this is not json at all")


def test_T8_parse_stray_text_around_json():
    raw = 'Review complete.\n{"passed": true, "issues": [], "suggestions": ""}\nEnd.'
    result = _parse_review_response(raw)
    assert result["passed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# review_router
# ─────────────────────────────────────────────────────────────────────────────


def test_T9_router_passed_true_returns_continue():
    state = _state(review_passed=True, retry_count=0)
    assert review_router(state) == "continue"


def test_T10_router_failed_retry_count_zero_returns_retry():
    state = _state(review_passed=False, retry_count=1)  # already incremented by node
    assert review_router(state) == "retry"


def test_T11_router_failed_retry_count_at_max_returns_continue():
    state = _state(review_passed=False, retry_count=MAX_RETRIES)
    assert review_router(state) == "continue"


def test_T12_router_failed_retry_count_exceeds_max_returns_continue():
    state = _state(review_passed=False, retry_count=MAX_RETRIES + 5)
    assert review_router(state) == "continue"


def test_T13_router_failed_retry_count_one_below_max_returns_retry():
    state = _state(review_passed=False, retry_count=MAX_RETRIES - 1)
    assert review_router(state) == "retry"


# ─────────────────────────────────────────────────────────────────────────────
# self_review node
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T14_empty_formatted_sections_auto_pass():
    """No Claude call when formatted_sections is empty — auto-pass."""
    state = _state(formatted_sections={})
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock()

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ):
        result = await self_review(state)

    assert result["review_passed"] is True
    assert result["review_issues"] == []
    mock_client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_T15_claude_says_passed_review_passed_true():
    state = _state(formatted_sections={"header": "Clean Header"}, retry_count=0)
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_claude_resp({"passed": True, "issues": [], "suggestions": ""})
    )

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="ocr={original_ocr} fmt={formatted_json}",
    ):
        result = await self_review(state)

    assert result["review_passed"] is True
    assert result["retry_count"] == 0  # unchanged — passed, no retry


@pytest.mark.asyncio
async def test_T16_claude_says_failed_retry_count_incremented():
    """Failed review with retry_count=0 → increments to 1."""
    state = _state(formatted_sections={"header": "Wrong Header"}, retry_count=0)
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_claude_resp(
            {
                "passed": False,
                "issues": ["Name mismatch"],
                "suggestions": "Fix the party name on line 1.",
            }
        )
    )

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="ocr={original_ocr} fmt={formatted_json}",
    ):
        result = await self_review(state)

    assert result["review_passed"] is False
    assert result["retry_count"] == 1  # incremented
    assert "Fix the party name" in result["review_suggestions"]
    assert result["review_issues"] == ["Name mismatch"]


@pytest.mark.asyncio
async def test_T17_failed_at_max_retries_count_not_incremented_further():
    """Failed review when retry_count already at MAX_RETRIES → count stays at MAX."""
    state = _state(
        formatted_sections={"header": "Still Wrong"},
        retry_count=MAX_RETRIES,
    )
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_claude_resp(
            {
                "passed": False,
                "issues": ["Still wrong"],
                "suggestions": "Fix everything.",
            }
        )
    )

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="ocr={original_ocr} fmt={formatted_json}",
    ):
        result = await self_review(state)

    assert result["review_passed"] is False
    assert result["retry_count"] == MAX_RETRIES  # NOT incremented past max


@pytest.mark.asyncio
async def test_T18_claude_api_failure_returns_fallback_passed():
    """Any Claude exception → fallback passed=True (avoid infinite retry)."""
    state = _state(formatted_sections={"header": "Text"})
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("Network error"))

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch("app.agents.typing.nodes.review.load_agent_prompt", return_value="prompt"):
        result = await self_review(state)

    assert result["review_passed"] is True
    assert "review_fallback" in result["processing_notes"]


@pytest.mark.asyncio
async def test_T19_missing_prompt_file_returns_fallback_passed():
    """Missing prompt file → fallback passed=True."""
    state = _state(formatted_sections={"header": "Text"})

    with patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        side_effect=FileNotFoundError("missing"),
    ):
        result = await self_review(state)

    assert result["review_passed"] is True
    assert "review_fallback" in result["processing_notes"]


@pytest.mark.asyncio
async def test_T20_no_images_sent_when_no_page_batches():
    """No image blocks when page_batches is empty."""
    state = _state(formatted_sections={"header": "Text"}, page_batches=[])
    captured = []

    async def capture(*args, **kwargs):
        msgs = kwargs.get("messages", [])
        if msgs:
            captured.extend(msgs[0].get("content", []))
        return _claude_resp({"passed": True, "issues": [], "suggestions": ""})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=capture)

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="ocr={original_ocr} fmt={formatted_json}",
    ):
        await self_review(state)

    image_blocks = [b for b in captured if b.get("type") == "image"]
    assert image_blocks == []


@pytest.mark.asyncio
async def test_T21_images_sent_when_page_batches_present():
    """Image blocks sent when page_batches exist."""
    state = _state(
        formatted_sections={"header": "Text"},
        page_batches=[{"page_start": 1, "page_end": 3, "images_b64": ["aaa", "bbb"]}],
    )
    captured = []

    async def capture(*args, **kwargs):
        msgs = kwargs.get("messages", [])
        if msgs:
            captured.extend(msgs[0].get("content", []))
        return _claude_resp({"passed": True, "issues": [], "suggestions": ""})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=capture)

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="ocr={original_ocr} fmt={formatted_json}",
    ):
        await self_review(state)

    image_blocks = [b for b in captured if b.get("type") == "image"]
    assert len(image_blocks) > 0


@pytest.mark.asyncio
async def test_T22_formatted_json_in_prompt():
    """formatted_sections is serialised as JSON inside the prompt."""
    state = _state(formatted_sections={"accused": "John Doe"}, page_batches=[])
    captured_text = []

    async def capture(*args, **kwargs):
        msgs = kwargs.get("messages", [])
        if msgs:
            for block in msgs[0].get("content", []):
                if block.get("type") == "text":
                    captured_text.append(block["text"])
        return _claude_resp({"passed": True, "issues": [], "suggestions": ""})

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=capture)

    with patch(
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="ocr={original_ocr} fmt={formatted_json}",
    ):
        await self_review(state)

    assert any("John Doe" in t for t in captured_text)


# ─────────────────────────────────────────────────────────────────────────────
# Graph integration
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T23_graph_review_passes_first_time():
    """
    Nodes 1-5: review passes on first attempt → no retry → assemble_html passthrough.
    review_passed=True in final state.
    """
    from app.agents.typing.graph import typing_graph

    state = _state(
        document_id="doc-s5-pass",
        ocr_raw_text="FIR OCR text content.",
        document_type="fir",
        presigned_url="",
    )

    assess_resp = _claude_resp(
        {
            "quality_score": 0.9,
            "issues": [],
            "language": "hindi_devanagari",
            "visual_vs_ocr_gaps": [],
        }
    )
    detect_resp = _claude_resp({"header": "FIR Header", "fir_contents": "Narrative"})
    reformat_resp = _claude_resp(
        {"header": "Clean Header", "fir_contents": "Clean Narrative"}
    )
    review_resp = _claude_resp({"passed": True, "issues": [], "suggestions": ""})

    responses = [assess_resp, detect_resp, reformat_resp, review_resp]
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
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.assess.load_agent_prompt", return_value="assess prompt"
    ), patch(
        "app.agents.typing.nodes.detect.load_agent_prompt", return_value="detect prompt"
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="reformat {retry_context} {sections_json}",
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="review {original_ocr} {formatted_json}",
    ):
        result = await typing_graph.ainvoke(state)

    assert result["review_passed"] is True
    assert result["retry_count"] == 0
    assert isinstance(result["formatted_sections"], dict)


@pytest.mark.asyncio
async def test_T24_graph_review_fails_once_then_passes():
    """
    review fails on first pass → retry → reformat runs again → review passes.
    Final state: review_passed=True, retry_count=1.
    """
    from app.agents.typing.graph import typing_graph

    state = _state(
        document_id="doc-s5-retry",
        ocr_raw_text="FIR OCR text content.",
        document_type="fir",
        presigned_url="",
    )

    assess_resp = _claude_resp(
        {
            "quality_score": 0.8,
            "issues": [],
            "language": "hindi_devanagari",
            "visual_vs_ocr_gaps": [],
        }
    )
    detect_resp = _claude_resp({"header": "Raw Header", "fir_contents": "Raw Content"})
    reformat1_resp = _claude_resp(
        {"header": "First Pass Header", "fir_contents": "First Pass Content"}
    )
    review_fail = _claude_resp(
        {"passed": False, "issues": ["Name wrong"], "suggestions": "Fix name."}
    )
    reformat2_resp = _claude_resp(
        {"header": "Fixed Header", "fir_contents": "Fixed Content"}
    )
    review_pass = _claude_resp({"passed": True, "issues": [], "suggestions": ""})

    responses = [
        assess_resp,
        detect_resp,
        reformat1_resp,
        review_fail,
        reformat2_resp,
        review_pass,
    ]
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
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.assess.load_agent_prompt", return_value="assess prompt"
    ), patch(
        "app.agents.typing.nodes.detect.load_agent_prompt", return_value="detect prompt"
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="reformat {retry_context} {sections_json}",
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="review {original_ocr} {formatted_json}",
    ):
        result = await typing_graph.ainvoke(state)

    assert result["review_passed"] is True
    assert result["retry_count"] == 1  # one retry happened


@pytest.mark.asyncio
async def test_T25_graph_review_fails_max_retries_still_terminates():
    """
    review keeps failing — after MAX_RETRIES the graph must still terminate
    (router forces "continue"). No infinite loop.
    """
    from app.agents.typing.graph import typing_graph

    state = _state(
        document_id="doc-s5-maxretry",
        ocr_raw_text="FIR OCR text.",
        document_type="fir",
        presigned_url="",
    )

    assess_resp = _claude_resp(
        {
            "quality_score": 0.7,
            "issues": [],
            "language": "hindi_devanagari",
            "visual_vs_ocr_gaps": [],
        }
    )
    detect_resp = _claude_resp({"header": "Raw", "fir_contents": "Raw"})
    reformat_resp = _claude_resp({"header": "Pass", "fir_contents": "Pass"})
    review_fail = _claude_resp(
        {"passed": False, "issues": ["Still wrong"], "suggestions": "Fix it."}
    )

    # Always fail review and always return same reformat
    responses = [assess_resp, detect_resp] + [reformat_resp, review_fail] * (
        MAX_RETRIES + 2
    )
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
        "app.agents.typing.nodes.review.get_anthropic_client", return_value=mock_client
    ), patch(
        "app.agents.typing.nodes.assess.load_agent_prompt", return_value="assess prompt"
    ), patch(
        "app.agents.typing.nodes.detect.load_agent_prompt", return_value="detect prompt"
    ), patch(
        "app.agents.typing.nodes.reformat.load_agent_prompt",
        return_value="reformat {retry_context} {sections_json}",
    ), patch(
        "app.agents.typing.nodes.review.load_agent_prompt",
        return_value="review {original_ocr} {formatted_json}",
    ):
        result = await typing_graph.ainvoke(state)

    # Graph terminated — even though review never passed
    assert result["retry_count"] == MAX_RETRIES
    assert result["review_passed"] is False
    # final_html should still be present (assemble_html is passthrough → empty string)
    assert "final_html" in result
