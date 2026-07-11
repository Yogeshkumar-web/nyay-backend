from types import SimpleNamespace
import uuid
from datetime import datetime, timedelta

import fitz
import pytest

from app.features.documents.models import DocReviewStatus
from app.features.documents.models import OcrStatus
from app.features.documents.models import ProcessingStatus
from app.features.documents.models import UploadStatus
from app.features.documents.service import DocumentService
from app.features.documents.digital_extractor import (
    PageSplit,
    build_single_image_page,
    split_pdf_pages,
)
from app.features.documents.document_processing_service import OcrResult
from app.features.documents.models import DocumentPageStatus
from app.features.documents.models import ProcessingRunStatus
from app.features.documents.page_stitching import stitch_ocr_pages
from app.features.documents.repository import DocumentRepository
from app.features.documents.sarvam_vision import SarvamVisionOcrProvider
from app.features.documents.structured_extraction import (
    SarvamStructuredExtractor,
    StructuredExtractionResult,
)


def test_processing_run_status_contains_refactor_stages():
    assert [status.value for status in ProcessingRunStatus] == [
        "uploaded",
        "splitting_pages",
        "ocr_running",
        "stitching_pages",
        "structured_extraction",
        "ready_for_review",
        "reviewed",
        "docx_ready",
        "failed",
    ]


def test_document_page_status_contains_split_stages():
    assert [status.value for status in DocumentPageStatus] == [
        "pending",
        "split",
        "ocr_running",
        "ocr_completed",
        "failed",
    ]


def test_split_pdf_pages_returns_single_page_artifacts_in_order():
    pdf = fitz.open()
    try:
        pdf.new_page()
        pdf.new_page()
        pdf_bytes = pdf.tobytes()
    finally:
        pdf.close()

    pages = split_pdf_pages(pdf_bytes)

    assert [page.page_number for page in pages] == [1, 2]
    assert [page.filename for page in pages] == ["page_0001.png", "page_0002.png"]
    assert all(page.mime_type == "image/png" for page in pages)
    assert all(page.content.startswith(b"\x89PNG\r\n\x1a\n") for page in pages)
    assert all(len(page.checksum) == 64 for page in pages)


def test_build_single_image_page_preserves_original_image_bytes():
    page = build_single_image_page(b"\x89PNG\r\n\x1a\nfake", "image/png")

    assert page.page_number == 1
    assert page.filename == "page_0001.png"
    assert page.mime_type == "image/png"
    assert page.content == b"\x89PNG\r\n\x1a\nfake"
    assert page.size_bytes == len(page.content)


def test_document_page_model_has_no_persistent_split_page_location():
    from app.features.documents.models import DocumentPage

    assert "page_r2_key" not in DocumentPage.__table__.columns
    assert "r2_bucket" not in DocumentPage.__table__.columns


@pytest.mark.asyncio
async def test_confirm_upload_does_not_auto_queue_processing():
    repo = DocumentRepository(_FakeSession())
    doc = SimpleNamespace(
        upload_status=UploadStatus.pending,
        is_scanned=False,
        processing_status=ProcessingStatus.pending,
        processing_job_id=None,
        ocr_status=OcrStatus.pending,
        updated_at=None,
    )

    await repo.confirm_upload(doc, is_scanned=True)  # type: ignore[arg-type]

    assert doc.upload_status == UploadStatus.uploaded
    assert doc.is_scanned is True
    assert doc.processing_status == ProcessingStatus.pending
    assert doc.processing_job_id is None
    assert doc.ocr_status == OcrStatus.pending


def test_stitch_ocr_pages_preserves_order_markers_and_review_flags():
    stitched = stitch_ocr_pages(
        [
            {
                "page_number": 2,
                "source_filename": "page_0002.png",
                "text": "Second page",
                "uncertain_words": ["नाम"],
                "ocr_issue_flags": ["low_confidence"],
            },
            {
                "page_number": 1,
                "source_filename": "page_0001.png",
                "text": "First page",
            },
        ]
    )

    assert stitched.text == (
        "[[PAGE 1 START]]\n"
        "First page\n"
        "[[PAGE 1 END]]\n\n"
        "[[PAGE 2 START]]\n"
        "Second page\n"
        "[[PAGE 2 END]]"
    )
    assert stitched.pages[1]["uncertain_words"] == ["नाम"]
    assert stitched.pages[1]["issue_flags"] == ["low_confidence"]


def test_stitch_ocr_pages_removes_only_synthetic_line_numbers():
    synthetic_text = "\n".join(f"{number}. line {number}" for number in range(1, 10))
    real_paragraphs = "\n".join(
        [
            "7. Real numbered paragraph",
            "8. Another real paragraph",
            "9. Third paragraph",
            "10. Fourth paragraph",
            "11. Fifth paragraph",
            "12. Sixth paragraph",
        ]
    )

    synthetic = stitch_ocr_pages([{"page_number": 1, "text": synthetic_text}])
    real = stitch_ocr_pages([{"page_number": 1, "text": real_paragraphs}])

    assert "1. line 1" not in synthetic.text
    assert "line 1" in synthetic.text
    assert "page_1:synthetic_line_numbers_removed" in synthetic.warnings
    assert "7. Real numbered paragraph" in real.text
    assert "12. Sixth paragraph" in real.text


def test_structured_extraction_schema_validates_required_shape():
    result = StructuredExtractionResult.model_validate(
        {
            "document_type": "victim_statement",
            "pages": [{"page_number": 1, "summary": "Statement begins"}],
            "statements": [
                {
                    "text": "Victim statement text",
                    "statement_type": "victim_statement",
                    "source_pages": [1],
                }
            ],
            "dates": [
                {
                    "raw_text": "10/01/2026",
                    "normalized_date": "2026-01-10",
                    "context": "incident date",
                    "source_pages": [1],
                }
            ],
            "names": [{"raw_text": "A", "role": "victim", "source_pages": [1]}],
            "unclear_words": [],
            "warnings": ["verify spelling"],
            "formatting_plan": [
                {
                    "block_type": "paragraph",
                    "text": "Victim statement text",
                    "source_pages": [1],
                }
            ],
        }
    )

    assert result.document_type == "victim_statement"
    assert result.pages[0].page_number == 1


@pytest.mark.asyncio
async def test_sarvam_structured_extractor_returns_validated_artifact():
    extractor = _FakeStructuredExtractor(api_key="test")

    artifact = await extractor.extract(
        "[[PAGE 1 START]]\nVictim statement text\n[[PAGE 1 END]]",
        document_type_hint="chargesheet",
        page_artifact={
            "stitching": {
                "pages": [
                    {
                        "page_number": 1,
                        "text": "Victim statement text",
                        "warnings": ["low_confidence"],
                    }
                ]
            }
        },
    )

    assert artifact["provider"] == "sarvam_chat_completions"
    assert artifact["result"]["document_type"] == "victim_statement"
    assert artifact["result"]["statements"][0]["source_pages"] == [1]
    assert "reasoning_content" not in artifact["raw_response"]["choices"][0]["message"]


@pytest.mark.asyncio
async def test_sarvam_page_processing_batches_at_ten_pages():
    provider = _FakeSarvamVisionProvider(api_key="test")
    pages = [
        PageSplit(
            page_number=number,
            filename=f"page_{number:04d}.png",
            mime_type="image/png",
            content=b"\x89PNG\r\n\x1a\nfake" + bytes([number]),
            checksum=f"{number:064d}"[-64:],
            size_bytes=12,
        )
        for number in range(1, 12)
    ]

    result = await provider.process_pages(pages, batch_size=10)

    assert result.text
    assert result.page_count == 11
    assert result.artifact["batch_count"] == 2
    assert [batch["source_filenames"] for batch in result.artifact["batches"]] == [
        [f"page_{number:04d}.png" for number in range(1, 11)],
        ["page_0011.png"],
    ]
    assert [page["page_number"] for page in result.artifact["pages"]] == list(
        range(1, 12)
    )


@pytest.mark.asyncio
async def test_update_run_page_ocr_results_persists_per_page_output():
    pages = [
        SimpleNamespace(
            page_number=1,
            status=DocumentPageStatus.ocr_running,
            ocr_text=None,
            ocr_artifact=None,
            error="old",
            updated_at=None,
        ),
        SimpleNamespace(
            page_number=2,
            status=DocumentPageStatus.ocr_running,
            ocr_text=None,
            ocr_artifact=None,
            error=None,
            updated_at=None,
        ),
    ]
    repo = DocumentRepository(_FakePageSession(pages))
    run = SimpleNamespace(id=uuid.uuid4())

    await repo.update_run_page_ocr_results(
        run,  # type: ignore[arg-type]
        [
            {"page_number": 2, "text": "Second", "confidence": 0.91},
            {"page_number": 1, "text": "First", "confidence": 0.95},
        ],
    )

    assert [page.status for page in pages] == [
        DocumentPageStatus.ocr_completed,
        DocumentPageStatus.ocr_completed,
    ]
    assert [page.ocr_text for page in pages] == ["First", "Second"]
    assert pages[0].ocr_artifact == {
        "page_number": 1,
        "text": "First",
        "confidence": 0.95,
    }
    assert pages[0].error is None
    assert pages[0].updated_at is not None


@pytest.mark.asyncio
async def test_manual_processing_queues_worker(monkeypatch):
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        uploaded_by=uuid.uuid4(),
        upload_status=UploadStatus.uploaded,
        processing_status=ProcessingStatus.pending,
        processing_job_id=None,
        processing_error=None,
        processing_started_at=None,
        processing_completed_at=None,
        source_text=None,
        source_artifact=None,
        classification_details=None,
        ocr_status=OcrStatus.pending,
        is_scanned=False,
        ocr_raw_text=None,
        ocr_language=None,
        page_count=None,
        ocr_job_id=None,
        ocr_error=None,
        ocr_provider=None,
        ocr_artifact=None,
        ocr_started_at=None,
        ocr_completed_at=None,
        reviewed_content=None,
        review_status=DocReviewStatus.pending,
        updated_at=None,
    )
    fake_repo = _FakeQueueRepo(doc)
    fake_db = _FakeDb()
    service = DocumentService(fake_db)  # type: ignore[arg-type]
    service.repo = fake_repo  # type: ignore[assignment]

    queued: dict[str, object] = {}
    statuses: list[tuple[str, object, dict | None]] = []

    from app.workers import document_processing_tasks, job_store

    def fake_apply_async(*, args, task_id):
        queued["args"] = args
        queued["task_id"] = task_id

    async def fake_set_job_status(job_id, status, result=None, error=None):
        statuses.append((job_id, status, result))

    monkeypatch.setattr(document_processing_tasks.process_document, "apply_async", fake_apply_async)
    monkeypatch.setattr(job_store, "set_job_status", fake_set_job_status)

    result = await service._queue_processing(
        doc,  # type: ignore[arg-type]
        started_by=doc.uploaded_by,
        force=True,
    )

    assert result["job_id"] == queued["task_id"]
    assert queued["args"] == [str(doc.id)]
    assert result["processing_run_id"] == str(fake_repo.run.id)
    assert fake_db.commit_count == 1
    assert doc.processing_status == ProcessingStatus.processing
    assert doc.ocr_status == OcrStatus.processing
    assert statuses[0][0] == result["job_id"]
    assert statuses[0][2] == {
        "document_id": str(doc.id),
        "processing_run_id": str(fake_repo.run.id),
    }


@pytest.mark.asyncio
async def test_stale_processing_marks_document_and_job_failed(monkeypatch):
    job_updates: list[tuple[str, object, str | None]] = []
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        ocr_status=OcrStatus.processing,
        processing_status=ProcessingStatus.processing,
        processing_job_id=str(uuid.uuid4()),
        ocr_job_id=None,
        updated_at=datetime.utcnow() - timedelta(seconds=180),
    )
    service = DocumentService(_FakeDb())  # type: ignore[arg-type]
    service.repo = _FakeStaleRepo()  # type: ignore[assignment]

    from app.workers import job_store

    async def fake_set_job_status(job_id, status, result=None, error=None):
        job_updates.append((job_id, status, error))

    monkeypatch.setattr(job_store, "set_job_status", fake_set_job_status)

    changed = await service._recover_stale_ocr(doc)  # type: ignore[arg-type]

    assert changed is True
    assert doc.ocr_status == OcrStatus.failed
    assert doc.processing_status == ProcessingStatus.failed
    assert doc.ocr_error == "OCR worker did not finish in time. Retry OCR."
    assert doc.processing_error == "OCR worker did not finish in time. Retry OCR."
    assert job_updates == [
        (
            doc.processing_job_id,
            job_store.JobStatus.failed,
            "OCR worker did not finish in time. Retry OCR.",
        )
    ]


@pytest.mark.asyncio
async def test_update_processing_run_marks_terminal_timestamp():
    repo = DocumentRepository(_FakeSession())
    run = SimpleNamespace(
        status=ProcessingRunStatus.ocr_running,
        error=None,
        metrics={},
        metadata_={},
        completed_at=None,
        updated_at=None,
    )

    await repo.update_processing_run(
        run,
        status=ProcessingRunStatus.ready_for_review,
        metrics={"page_count": 2},
        metadata={"processing_route": "scanned_ocr"},
    )

    assert run.status == ProcessingRunStatus.ready_for_review
    assert run.error is None
    assert run.metrics == {"page_count": 2}
    assert run.metadata_ == {"processing_route": "scanned_ocr"}
    assert run.completed_at is not None
    assert run.updated_at is not None


@pytest.mark.asyncio
async def test_update_processing_run_keeps_non_terminal_open():
    repo = DocumentRepository(_FakeSession())
    run = SimpleNamespace(
        status=ProcessingRunStatus.uploaded,
        error=None,
        metrics={},
        metadata_={},
        completed_at=None,
        updated_at=None,
    )

    await repo.update_processing_run(run, status=ProcessingRunStatus.ocr_running)

    assert run.status == ProcessingRunStatus.ocr_running
    assert run.completed_at is None


class _FakeSession:
    async def execute(self, _stmt):
        return _FakeResult()

    def add(self, _obj):
        return None

    async def flush(self):
        return None


class _FakePageSession:
    def __init__(self, pages):
        self.pages = pages

    async def execute(self, _stmt):
        return _FakePageResult(self.pages)

    async def flush(self):
        return None


class _FakePageResult:
    def __init__(self, pages):
        self.pages = pages

    def scalars(self):
        return self

    def all(self):
        return self.pages


class _FakeDb:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1


class _FakeQueueRepo:
    def __init__(self, doc):
        self.doc = doc
        self.run = SimpleNamespace(id=uuid.uuid4())

    async def start_processing(self, doc, *, job_id):
        doc.processing_status = ProcessingStatus.processing
        doc.processing_job_id = job_id
        return doc

    async def start_ocr(self, doc, *, job_id=None):
        doc.ocr_status = OcrStatus.processing
        doc.ocr_job_id = job_id
        doc.is_scanned = True
        return doc

    async def create_processing_run(self, doc, *, job_id, started_by, status, metadata):
        self.run.job_id = job_id
        self.run.started_by = started_by
        self.run.status = status
        self.run.metadata_ = metadata
        return self.run

    async def fail_processing(self, doc, *, error):
        doc.processing_status = ProcessingStatus.failed
        doc.processing_error = error
        return doc

    async def set_ocr_status(self, doc, status, *, error=None):
        doc.ocr_status = status
        doc.ocr_error = error
        return doc

    async def update_processing_run(self, run, *, status, error=None):
        run.status = status
        run.error = error
        return run


class _FakeStaleRepo:
    async def set_ocr_status(self, doc, status, *, error=None):
        doc.ocr_status = status
        doc.ocr_error = error
        return doc

    async def fail_processing(self, doc, *, error):
        doc.processing_status = ProcessingStatus.failed
        doc.processing_error = error
        return doc


class _FakeSarvamVisionProvider(SarvamVisionOcrProvider):
    async def _process_prepared_input(self, prepared):
        pages = [
            {
                "page_number": page_number,
                "source": "sarvam_vision",
                "source_filename": filename,
                "text": f"Text for page {page_number}",
            }
            for page_number, filename in zip(
                prepared.original_page_numbers or (),
                prepared.source_filenames,
                strict=False,
            )
        ]
        return OcrResult(
            text="\n\n".join(page["text"] for page in pages),
            language="hi-IN",
            page_count=len(pages),
            artifact={
                "schema_version": 1,
                "provider": "sarvam_vision",
                "job": {"job_id": f"job-{prepared.filename}"},
                "download_manifest": {},
                "output_files": ["output.json"],
                "json_payload": {"pages": pages},
                "pages": pages,
            },
        )


class _FakeStructuredExtractor(SarvamStructuredExtractor):
    async def _post_chat_completion(self, payload):
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        return {
            "id": "chatcmpl-test",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"document_type":"victim_statement",'
                            '"pages":[{"page_number":1,"summary":"Statement"}],'
                            '"statements":[{"text":"Victim statement text",'
                            '"statement_type":"victim_statement",'
                            '"source_pages":[1]}],'
                            '"dates":[],"names":[],"unclear_words":[],'
                            '"warnings":["low_confidence"],'
                            '"formatting_plan":[{"block_type":"paragraph",'
                            '"text":"Victim statement text","source_pages":[1]}]}'
                        ),
                        "reasoning_content": "hidden",
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []
