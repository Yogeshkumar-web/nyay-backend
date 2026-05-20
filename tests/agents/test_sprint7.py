"""
Sprint 7 tests — typing_tasks.py wiring

Covers:
- _generate_presigned_get_url()  (unit)
- _typing_task_async()           (integration, all deps mocked)
  • document not found
  • no OCR text
  • state built with correct fields
  • graph output saved to TypedVersion
  • empty HTML → fallback to OCR text
  • graph exception → job failed
  • job status transitions (processing → completed / failed)
  • processing_notes stored in agent_notes
"""
import uuid
import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.typing_tasks import _generate_presigned_get_url, _typing_task_async
from app.workers.job_store import JobStatus


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

DOC_ID = str(uuid.uuid4())
JOB_ID = "job-test-sprint7"


def _make_doc(
    *,
    ocr_raw_text="Sample OCR text from Google Document AI.",
    document_type_value="fir",
    page_count=5,
    r2_bucket="vakil-bucket",
    r2_key="docs/test.pdf",
):
    """Return a mock Document ORM object."""
    doc = MagicMock()
    doc.id = uuid.UUID(DOC_ID)
    doc.ocr_raw_text = ocr_raw_text
    doc.document_type = MagicMock()
    doc.document_type.value = document_type_value
    doc.page_count = page_count
    doc.r2_bucket = r2_bucket
    doc.r2_key = r2_key
    return doc


def _make_graph_result(
    *,
    final_html='<div class="typed-document typed-fir"><h2>FIR Header</h2><p>Content</p></div>',
    processing_notes="ts=2026-01-01 | quality=0.90 | retries=0 | review=pass",
    retry_count=0,
    review_passed=True,
):
    """Return a mock graph result state dict."""
    return {
        "final_html": final_html,
        "processing_notes": processing_notes,
        "retry_count": retry_count,
        "review_passed": review_passed,
    }


@contextmanager
def _mock_task_env(
    *,
    doc=None,  # None → document not found
    graph_result=None,  # None → use default success result
    graph_exc=None,  # Exception instance → graph raises this
    presigned_url="https://r2.example.com/test.pdf",
):
    """
    Context manager that wires all mocks needed for _typing_task_async.
    Yields a namespace with individual mocks for fine-grained assertions.
    """
    if graph_result is None and graph_exc is None:
        graph_result = _make_graph_result()

    # Engine mock
    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()

    # Session mock (async context manager)
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=session_cm)

    # DocumentRepository mock
    mock_doc_repo = AsyncMock()
    mock_doc_repo.get_by_id = AsyncMock(return_value=doc)

    # ExtractionRepository mock
    mock_ext_repo = AsyncMock()
    mock_ext_repo.create_or_update_typed_version = AsyncMock()

    # Graph mock
    mock_graph = MagicMock()
    if graph_exc:
        mock_graph.ainvoke = AsyncMock(side_effect=graph_exc)
    else:
        mock_graph.ainvoke = AsyncMock(return_value=graph_result)

    # set_job_status mock
    mock_set_job = AsyncMock()

    class _Mocks:
        engine = mock_engine
        session = mock_session
        doc_repo = mock_doc_repo
        ext_repo = mock_ext_repo
        graph = mock_graph
        set_job = mock_set_job

    with patch("app.workers.typing_tasks.set_job_status", mock_set_job), patch(
        "sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine
    ), patch(
        "sqlalchemy.ext.asyncio.async_sessionmaker", return_value=session_factory
    ), patch(
        "app.features.documents.repository.DocumentRepository",
        return_value=mock_doc_repo,
    ), patch(
        "app.features.extraction.repository.ExtractionRepository",
        return_value=mock_ext_repo,
    ), patch("app.agents.typing.graph.typing_graph", mock_graph), patch(
        "app.workers.typing_tasks._generate_presigned_get_url",
        return_value=presigned_url,
    ), patch("app.core.config.settings") as mock_settings:
        mock_settings.DATABASE_URL = "postgresql+asyncpg://test:test@localhost/test"
        mock_settings.R2_ACCOUNT_ID = "test_account"
        mock_settings.R2_ACCESS_KEY_ID = "test_key"
        mock_settings.R2_SECRET_ACCESS_KEY = "test_secret"

        yield _Mocks()


# ─────────────────────────────────────────────────────────────────────────────
# _generate_presigned_get_url
# ─────────────────────────────────────────────────────────────────────────────


def test_T1_presign_returns_url_on_success():
    mock_r2 = MagicMock()
    mock_r2.generate_presigned_url.return_value = "https://r2.example.com/signed"

    with patch("boto3.client", return_value=mock_r2), patch(
        "app.core.config.settings"
    ) as mock_settings:
        mock_settings.R2_ACCOUNT_ID = "acct"
        mock_settings.R2_ACCESS_KEY_ID = "key"
        mock_settings.R2_SECRET_ACCESS_KEY = "secret"

        result = _generate_presigned_get_url(
            "my-bucket", "docs/test.pdf", expires_in=300
        )

    assert result == "https://r2.example.com/signed"
    mock_r2.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "my-bucket", "Key": "docs/test.pdf"},
        ExpiresIn=300,
    )


def test_T2_presign_returns_empty_string_on_exception():
    with patch("boto3.client", side_effect=RuntimeError("No credentials")), patch(
        "app.core.config.settings"
    ) as mock_settings:
        mock_settings.R2_ACCOUNT_ID = "acct"
        mock_settings.R2_ACCESS_KEY_ID = "key"
        mock_settings.R2_SECRET_ACCESS_KEY = "secret"

        result = _generate_presigned_get_url("bucket", "key")

    assert result == ""


def test_T3_presign_r2_generate_fails_returns_empty():
    mock_r2 = MagicMock()
    mock_r2.generate_presigned_url.side_effect = Exception("R2 error")

    with patch("boto3.client", return_value=mock_r2), patch(
        "app.core.config.settings"
    ) as mock_settings:
        mock_settings.R2_ACCOUNT_ID = "acct"
        mock_settings.R2_ACCESS_KEY_ID = "key"
        mock_settings.R2_SECRET_ACCESS_KEY = "secret"

        result = _generate_presigned_get_url("b", "k")

    assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# _typing_task_async — failure cases
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T4_document_not_found_returns_failed():
    with _mock_task_env(doc=None):
        result = await _typing_task_async(JOB_ID, DOC_ID)

    assert result["status"] == "failed"
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_T5_document_not_found_sets_job_failed():
    with _mock_task_env(doc=None) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    # job_status must go: processing → failed
    calls = m.set_job.call_args_list
    statuses = [c.args[1] for c in calls]
    assert JobStatus.processing in statuses
    assert JobStatus.failed in statuses


@pytest.mark.asyncio
async def test_T6_no_ocr_text_returns_failed():
    doc = _make_doc(ocr_raw_text=None)
    doc.ocr_raw_text = None
    with _mock_task_env(doc=doc):
        result = await _typing_task_async(JOB_ID, DOC_ID)

    assert result["status"] == "failed"
    assert "ocr" in result["error"].lower()


@pytest.mark.asyncio
async def test_T7_graph_exception_returns_failed():
    doc = _make_doc()
    with _mock_task_env(doc=doc, graph_exc=RuntimeError("LangGraph crashed")):
        result = await _typing_task_async(JOB_ID, DOC_ID)

    assert result["status"] == "failed"
    assert "LangGraph crashed" in result["error"]


@pytest.mark.asyncio
async def test_T8_graph_exception_sets_job_failed():
    doc = _make_doc()
    with _mock_task_env(doc=doc, graph_exc=ValueError("bad state")) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    statuses = [c.args[1] for c in m.set_job.call_args_list]
    assert JobStatus.failed in statuses


# ─────────────────────────────────────────────────────────────────────────────
# _typing_task_async — state building
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T9_graph_called_with_correct_ocr_text():
    doc = _make_doc(ocr_raw_text="Specific OCR content for the FIR.")
    with _mock_task_env(doc=doc) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    invoked_state = m.graph.ainvoke.call_args.args[0]
    assert invoked_state["ocr_raw_text"] == "Specific OCR content for the FIR."


@pytest.mark.asyncio
async def test_T10_graph_called_with_document_type_string():
    doc = _make_doc(document_type_value="chargesheet")
    with _mock_task_env(doc=doc) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    invoked_state = m.graph.ainvoke.call_args.args[0]
    assert invoked_state["document_type"] == "chargesheet"


@pytest.mark.asyncio
async def test_T11_graph_called_with_presigned_url():
    doc = _make_doc()
    with _mock_task_env(doc=doc, presigned_url="https://r2.cf.com/signed?x=1") as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    invoked_state = m.graph.ainvoke.call_args.args[0]
    assert invoked_state["presigned_url"] == "https://r2.cf.com/signed?x=1"


@pytest.mark.asyncio
async def test_T12_graph_called_with_total_pages_from_doc():
    doc = _make_doc(page_count=12)
    with _mock_task_env(doc=doc) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    invoked_state = m.graph.ainvoke.call_args.args[0]
    assert invoked_state["total_pages"] == 12


@pytest.mark.asyncio
async def test_T13_page_count_none_becomes_zero():
    doc = _make_doc(page_count=None)
    with _mock_task_env(doc=doc) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    invoked_state = m.graph.ainvoke.call_args.args[0]
    assert invoked_state["total_pages"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# _typing_task_async — successful save
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T14_final_html_saved_to_typed_version():
    doc = _make_doc()
    html = '<div class="typed-document typed-fir"><h2>Header</h2><p>Content</p></div>'
    graph_result = _make_graph_result(final_html=html)

    with _mock_task_env(doc=doc, graph_result=graph_result) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    m.ext_repo.create_or_update_typed_version.assert_called_once()
    call_kwargs = m.ext_repo.create_or_update_typed_version.call_args.kwargs
    assert call_kwargs["typed_content"] == html
    assert call_kwargs["document_id"] == uuid.UUID(DOC_ID)


@pytest.mark.asyncio
async def test_T15_processing_notes_saved_to_agent_notes():
    doc = _make_doc()
    notes = "ts=2026-01-01 | quality=0.88 | retries=1 | review=pass"
    graph_result = _make_graph_result(processing_notes=notes)

    with _mock_task_env(doc=doc, graph_result=graph_result) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    call_kwargs = m.ext_repo.create_or_update_typed_version.call_args.kwargs
    assert call_kwargs["agent_notes"] == notes


@pytest.mark.asyncio
async def test_T16_session_committed_on_success():
    doc = _make_doc()
    with _mock_task_env(doc=doc) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    m.session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_T17_job_completed_on_success():
    doc = _make_doc()
    graph_result = _make_graph_result(retry_count=1, review_passed=True)

    with _mock_task_env(doc=doc, graph_result=graph_result) as m:
        result = await _typing_task_async(JOB_ID, DOC_ID)

    assert result["status"] == "completed"
    # Find the "completed" call
    completed_call = next(
        c for c in m.set_job.call_args_list if c.args[1] == JobStatus.completed
    )
    result_payload = completed_call.kwargs["result"]
    assert result_payload["retry_count"] == 1
    assert result_payload["review_passed"] is True
    assert "html_length" in result_payload


# ─────────────────────────────────────────────────────────────────────────────
# _typing_task_async — empty HTML fallback
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T18_empty_final_html_falls_back_to_ocr_text():
    doc = _make_doc(ocr_raw_text="Raw OCR backup text")
    graph_result = _make_graph_result(final_html="")

    with _mock_task_env(doc=doc, graph_result=graph_result) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    call_kwargs = m.ext_repo.create_or_update_typed_version.call_args.kwargs
    assert "Raw OCR backup text" in call_kwargs["typed_content"]


@pytest.mark.asyncio
async def test_T19_placeholder_html_falls_back_to_ocr_text():
    """<p></p> is treated as empty — fallback triggered."""
    doc = _make_doc(ocr_raw_text="OCR fallback")
    graph_result = _make_graph_result(final_html="<p></p>")

    with _mock_task_env(doc=doc, graph_result=graph_result) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    call_kwargs = m.ext_repo.create_or_update_typed_version.call_args.kwargs
    assert "OCR fallback" in call_kwargs["typed_content"]


@pytest.mark.asyncio
async def test_T20_fallback_note_appended_to_processing_notes():
    doc = _make_doc()
    graph_result = _make_graph_result(final_html="", processing_notes="quality=0.5")

    with _mock_task_env(doc=doc, graph_result=graph_result) as m:
        await _typing_task_async(JOB_ID, DOC_ID)

    call_kwargs = m.ext_repo.create_or_update_typed_version.call_args.kwargs
    assert "fallback:empty_html" in call_kwargs["agent_notes"]


# ─────────────────────────────────────────────────────────────────────────────
# _typing_task_async — presigned URL with empty string (R2 failed)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_T21_empty_presigned_url_still_invokes_graph():
    """If R2 presign fails, graph still runs with empty presigned_url (text-only fallback)."""
    doc = _make_doc()
    with _mock_task_env(doc=doc, presigned_url="") as m:
        result = await _typing_task_async(JOB_ID, DOC_ID)

    # Graph was still called
    m.graph.ainvoke.assert_called_once()
    invoked_state = m.graph.ainvoke.call_args.args[0]
    assert invoked_state["presigned_url"] == ""
    # And task completed (Node 1 handles empty URL gracefully)
    assert result["status"] == "completed"
