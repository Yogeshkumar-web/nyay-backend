"""
Sprint 1 Tests — Node 1: PDF Fetch + Batching

Test matrix:
  [pdf_utils]
  T1  Real in-memory PDF (3 pages) → correct batch count + page numbering
  T2  Large PDF (25 pages) → ceil(25/10) = 3 batches, sizes [10, 10, 5]
  T3  Single-page PDF → 1 batch with 1 image
  T4  Corrupt bytes → returns [] gracefully (no exception)
  T5  Empty bytes → returns [] gracefully

  [fetch_and_batch_pages node]
  T6  Valid URL → page_batches populated, fetch_error=None
  T7  403 Forbidden (expired URL) → fetch_error set, page_batches=[]
  T8  Network timeout → fetch_error set, page_batches=[]
  T9  No presigned_url (empty string) → fetch_error set immediately
  T10 PDF fetch OK but corrupt bytes → fetch_error set, page_batches=[]

  [graph integration]
  T11 Full graph ainvoke with Node 1 live → state flows through all nodes
"""
import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import fitz  # pymupdf
import httpx
import pytest

from app.agents.shared.pdf_utils import PageBatch, pdf_bytes_to_batches
from app.agents.typing.graph import build_typing_graph
from app.agents.typing.nodes.fetch_pages import fetch_and_batch_pages
from app.agents.typing.state import default_state


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_pdf(num_pages: int) -> bytes:
    """Create a minimal valid PDF with `num_pages` pages using pymupdf."""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text((72, 72), f"Test page {i + 1}", fontsize=14)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_state(**overrides):
    """Return a default state with optional field overrides."""
    base = default_state(
        ocr_raw_text="Sample OCR text for testing purposes.",
        document_type="fir",
        document_id="test-doc-uuid-1234",
        presigned_url="https://r2.example.com/test.pdf",
        total_pages=0,
    )
    return {**base, **overrides}


# ─────────────────────────────────────────────────────────────────────────────
# T1 – T5 : pdf_utils unit tests (no network, no mocking)
# ─────────────────────────────────────────────────────────────────────────────


def test_T1_three_page_pdf_one_batch():
    """T1: 3-page PDF → 1 batch with 3 images, correct page numbering."""
    pdf = _make_pdf(3)
    batches = pdf_bytes_to_batches(pdf)

    assert len(batches) == 1
    b = batches[0]
    assert b.batch_number == 1
    assert b.page_start == 1
    assert b.page_end == 3
    assert len(b.images_b64) == 3
    # Verify images are valid base64 PNG
    for img in b.images_b64:
        raw = base64.b64decode(img)
        assert raw[:4] == b"\x89PNG", "Expected PNG magic bytes"


def test_T2_large_pdf_three_batches():
    """T2: 25-page PDF → 3 batches: [10, 10, 5]."""
    pdf = _make_pdf(25)
    batches = pdf_bytes_to_batches(pdf)

    assert len(batches) == 3
    assert batches[0].batch_number == 1
    assert batches[0].page_start == 1
    assert batches[0].page_end == 10
    assert len(batches[0].images_b64) == 10

    assert batches[1].batch_number == 2
    assert batches[1].page_start == 11
    assert batches[1].page_end == 20
    assert len(batches[1].images_b64) == 10

    assert batches[2].batch_number == 3
    assert batches[2].page_start == 21
    assert batches[2].page_end == 25
    assert len(batches[2].images_b64) == 5


def test_T3_single_page_pdf():
    """T3: 1-page PDF → exactly 1 batch with 1 image."""
    pdf = _make_pdf(1)
    batches = pdf_bytes_to_batches(pdf)

    assert len(batches) == 1
    assert len(batches[0].images_b64) == 1


def test_T4_corrupt_bytes_returns_empty():
    """T4: Corrupt bytes → empty list, no exception raised."""
    corrupt = b"this is not a pdf at all %@#!"
    batches = pdf_bytes_to_batches(corrupt)
    assert batches == []


def test_T5_empty_bytes_returns_empty():
    """T5: Empty bytes → empty list, no exception raised."""
    batches = pdf_bytes_to_batches(b"")
    assert batches == []


def test_T2_batches_are_named_tuples():
    """Extra: batches are PageBatch NamedTuples and serializable to dict."""
    pdf = _make_pdf(2)
    batches = pdf_bytes_to_batches(pdf)
    assert isinstance(batches[0], PageBatch)
    d = batches[0]._asdict()
    assert set(d.keys()) == {"batch_number", "page_start", "page_end", "images_b64"}


# ─────────────────────────────────────────────────────────────────────────────
# T6 – T10 : fetch_and_batch_pages node tests (httpx mocked)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T6_valid_url_returns_batches():
    """T6: Valid URL → page_batches populated, fetch_error=None."""
    pdf_bytes = _make_pdf(5)
    state = _make_state()

    mock_response = MagicMock()
    mock_response.content = pdf_bytes
    mock_response.raise_for_status = MagicMock()  # no-op

    with patch("app.agents.typing.nodes.fetch_pages.httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(
            return_value=mock_client_instance
        )
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await fetch_and_batch_pages(state)

    assert result["fetch_error"] is None
    assert result["batch_count"] == 1
    assert len(result["page_batches"]) == 1
    assert result["page_batches"][0]["page_start"] == 1
    assert result["page_batches"][0]["page_end"] == 5
    assert result["total_pages"] == 5  # updated from actual PDF


@pytest.mark.asyncio
async def test_T7_http_403_sets_fetch_error():
    """T7: 403 Forbidden (expired presigned URL) → fetch_error set, batches empty."""
    state = _make_state()

    mock_response = MagicMock()
    mock_response.status_code = 403

    http_error = httpx.HTTPStatusError(
        "403 Forbidden", request=MagicMock(), response=mock_response
    )

    with patch("app.agents.typing.nodes.fetch_pages.httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=http_error)
        MockClient.return_value.__aenter__ = AsyncMock(
            return_value=mock_client_instance
        )
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await fetch_and_batch_pages(state)

    assert result["fetch_error"] is not None
    assert "403" in result["fetch_error"]
    assert result["page_batches"] == []
    assert result["batch_count"] == 0


@pytest.mark.asyncio
async def test_T8_network_timeout_sets_fetch_error():
    """T8: Network timeout → fetch_error set, batches empty."""
    state = _make_state()

    with patch("app.agents.typing.nodes.fetch_pages.httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        MockClient.return_value.__aenter__ = AsyncMock(
            return_value=mock_client_instance
        )
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await fetch_and_batch_pages(state)

    assert result["fetch_error"] is not None
    assert (
        "Timeout" in result["fetch_error"] or "timeout" in result["fetch_error"].lower()
    )
    assert result["page_batches"] == []
    assert result["batch_count"] == 0


@pytest.mark.asyncio
async def test_T9_empty_presigned_url_sets_fetch_error():
    """T9: Empty presigned_url → fetch_error set immediately, no HTTP call made."""
    state = _make_state(presigned_url="")

    with patch("app.agents.typing.nodes.fetch_pages.httpx.AsyncClient") as MockClient:
        result = await fetch_and_batch_pages(state)
        MockClient.assert_not_called()  # should not even attempt HTTP call

    assert result["fetch_error"] is not None
    assert result["page_batches"] == []
    assert result["batch_count"] == 0


@pytest.mark.asyncio
async def test_T10_corrupt_pdf_bytes_sets_fetch_error():
    """T10: HTTP 200 but corrupt PDF bytes → fetch_error set, batches empty."""
    state = _make_state()

    mock_response = MagicMock()
    mock_response.content = b"not a real pdf"
    mock_response.raise_for_status = MagicMock()

    with patch("app.agents.typing.nodes.fetch_pages.httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(
            return_value=mock_client_instance
        )
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await fetch_and_batch_pages(state)

    assert result["fetch_error"] is not None
    assert result["page_batches"] == []
    assert result["batch_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# T11 : Graph integration test — Node 1 live, rest passthrough
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T11_graph_integration_node1_live():
    """
    T11: Full graph.ainvoke() with Node 1 live.
    Verifies state flows through all 6 nodes and final state is correct.
    """
    pdf_bytes = _make_pdf(12)
    state = _make_state()

    mock_response = MagicMock()
    mock_response.content = pdf_bytes
    mock_response.raise_for_status = MagicMock()

    graph = build_typing_graph()

    with patch("app.agents.typing.nodes.fetch_pages.httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(
            return_value=mock_client_instance
        )
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        final = await graph.ainvoke(state)

    # Node 1 ran correctly
    assert final["fetch_error"] is None
    assert (
        final["batch_count"] == 2
    )  # 12 pages → 2 batches of 10 + 2... wait: ceil(12/10) = 2
    assert len(final["page_batches"]) == 2
    assert final["page_batches"][0]["page_start"] == 1
    assert final["page_batches"][0]["page_end"] == 10
    assert final["page_batches"][1]["page_start"] == 11
    assert final["page_batches"][1]["page_end"] == 12
    assert final["total_pages"] == 12

    # State preserved through passthrough nodes
    assert final["document_type"] == "fir"
    assert final["document_id"] == "test-doc-uuid-1234"
    assert final["ocr_raw_text"] == "Sample OCR text for testing purposes."
