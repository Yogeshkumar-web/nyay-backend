import uuid
import logging
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.documents.models import (
    Document,
    DocumentDocxExport,
    DocumentPage,
    DocumentPageStatus,
    DocumentProcessingRun,
    DocReviewStatus,
    OcrStatus,
    ProcessingRunStatus,
    ProcessingRoute,
    ProcessingStatus,
    UploadStatus,
)
from app.features.documents.schemas import UpdateDocumentRequest

logger = logging.getLogger(__name__)


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> Document:
        doc = Document(**kwargs)
        self.session.add(doc)
        await self.session.flush()
        logger.info(f"Document created: {doc.id}")
        return doc

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        result = await self.session.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()

    async def get_processing_run_by_job_id(
        self,
        job_id: str,
    ) -> DocumentProcessingRun | None:
        result = await self.session.execute(
            select(DocumentProcessingRun).where(DocumentProcessingRun.job_id == job_id)
        )
        return result.scalar_one_or_none()

    async def list_for_case(self, case_id: uuid.UUID) -> list[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.case_id == case_id)
            .order_by(Document.created_at.desc())
        )
        return list(result.scalars().all())

    async def update(self, doc: Document, data: UpdateDocumentRequest) -> Document:
        update_data = data.model_dump(exclude_none=True)

        for key, value in update_data.items():
            setattr(doc, key, value)

        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def confirm_upload(self, doc: Document, is_scanned: bool | None) -> Document:
        if doc.upload_status == UploadStatus.uploaded:
            if is_scanned is not None and doc.is_scanned != is_scanned:
                doc.is_scanned = is_scanned
                doc.updated_at = datetime.utcnow()
                await self.session.flush()
            return doc  # idempotent

        doc.upload_status = UploadStatus.uploaded
        if is_scanned is not None:
            doc.is_scanned = is_scanned
        doc.updated_at = datetime.utcnow()

        await self.session.flush()
        return doc

    async def set_ocr_status(
        self,
        doc: Document,
        status: OcrStatus,
        *,
        raw_text: Optional[str] = None,
        language: Optional[str] = None,
        page_count: Optional[int] = None,
        error: Optional[str] = None,
        provider: Optional[str] = None,
        artifact: Optional[dict] = None,
        job_id: Optional[str] = None,
    ) -> Document:
        doc.ocr_status = status

        if raw_text is not None:
            doc.ocr_raw_text = raw_text

        if language is not None:
            doc.ocr_language = language

        if page_count is not None:
            doc.page_count = page_count

        if error is not None:
            doc.ocr_error = error

        if provider is not None:
            doc.ocr_provider = provider

        if artifact is not None:
            doc.ocr_artifact = artifact

        if job_id is not None:
            doc.ocr_job_id = job_id

        if status == OcrStatus.completed:
            doc.ocr_error = None
            doc.ocr_completed_at = datetime.utcnow()
        elif status == OcrStatus.failed:
            doc.ocr_completed_at = datetime.utcnow()

        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def start_ocr(self, doc: Document, *, job_id: str | None = None) -> Document:
        doc.ocr_status = OcrStatus.processing
        doc.is_scanned = True
        doc.ocr_raw_text = None
        doc.ocr_language = None
        doc.page_count = None
        doc.ocr_job_id = job_id
        doc.ocr_error = None
        doc.ocr_provider = None
        doc.ocr_artifact = None
        doc.ocr_started_at = datetime.utcnow()
        doc.ocr_completed_at = None
        doc.reviewed_content = None
        doc.review_status = DocReviewStatus.pending
        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def start_processing(
        self,
        doc: Document,
        *,
        job_id: str,
    ) -> Document:
        doc.processing_status = ProcessingStatus.processing
        doc.processing_job_id = job_id
        doc.processing_error = None
        doc.processing_started_at = datetime.utcnow()
        doc.processing_completed_at = None
        doc.source_text = None
        doc.source_artifact = None
        doc.classification_details = None
        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def create_processing_run(
        self,
        doc: Document,
        *,
        job_id: str,
        started_by: uuid.UUID,
        status: ProcessingRunStatus = ProcessingRunStatus.ocr_running,
        metadata: dict | None = None,
    ) -> DocumentProcessingRun:
        run = DocumentProcessingRun(
            document_id=doc.id,
            case_id=doc.case_id,
            started_by=started_by,
            job_id=job_id,
            status=status,
            metadata_=metadata or {},
            metrics={},
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def update_processing_run(
        self,
        run: DocumentProcessingRun | None,
        *,
        status: ProcessingRunStatus,
        error: str | None = None,
        metrics: dict | None = None,
        metadata: dict | None = None,
    ) -> DocumentProcessingRun | None:
        if run is None:
            return None
        run.status = status
        run.error = error
        if metrics is not None:
            run.metrics = metrics
        if metadata is not None:
            run.metadata_ = metadata
        if status in {
            ProcessingRunStatus.ready_for_review,
            ProcessingRunStatus.reviewed,
            ProcessingRunStatus.docx_ready,
            ProcessingRunStatus.failed,
        }:
            run.completed_at = datetime.now(UTC)
        run.updated_at = datetime.now(UTC)
        await self.session.flush()
        return run

    async def replace_run_pages(
        self,
        doc: Document,
        run: DocumentProcessingRun,
        pages: list[dict],
    ) -> list[DocumentPage]:
        existing = await self.session.execute(
            select(DocumentPage).where(DocumentPage.processing_run_id == run.id)
        )
        for page in existing.scalars().all():
            await self.session.delete(page)
        created: list[DocumentPage] = []
        for page_data in pages:
            page = DocumentPage(
                document_id=doc.id,
                processing_run_id=run.id,
                page_number=page_data["page_number"],
                source_filename=page_data["source_filename"],
                mime_type=page_data["mime_type"],
                checksum_sha256=page_data["checksum_sha256"],
                size_bytes=page_data["size_bytes"],
                status=page_data.get("status", DocumentPageStatus.split),
            )
            self.session.add(page)
            created.append(page)
        await self.session.flush()
        return created

    async def list_pages_for_run(
        self,
        run_id: uuid.UUID,
    ) -> list[DocumentPage]:
        result = await self.session.execute(
            select(DocumentPage)
            .where(DocumentPage.processing_run_id == run_id)
            .order_by(DocumentPage.page_number)
        )
        return list(result.scalars().all())

    async def mark_run_pages_ocr_running(
        self,
        run: DocumentProcessingRun,
    ) -> None:
        pages = await self.list_pages_for_run(run.id)
        for page in pages:
            page.status = DocumentPageStatus.ocr_running
            page.error = None
            page.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def update_run_page_ocr_results(
        self,
        run: DocumentProcessingRun,
        page_results: list[dict],
    ) -> None:
        pages = await self.list_pages_for_run(run.id)
        pages_by_number = {page.page_number: page for page in pages}
        for result in page_results:
            page_number = int(result["page_number"])
            page = pages_by_number.get(page_number)
            if page is None:
                continue
            page.status = DocumentPageStatus.ocr_completed
            page.ocr_text = str(result.get("text") or "")
            page.ocr_artifact = result
            page.error = None
            page.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def complete_processing(
        self,
        doc: Document,
        *,
        route: ProcessingRoute,
        source_text: str,
        source_artifact: dict,
        classification_details: dict,
        is_scanned: bool,
    ) -> Document:
        doc.processing_route = route
        doc.processing_status = ProcessingStatus.completed
        doc.processing_error = None
        doc.processing_completed_at = datetime.utcnow()
        doc.source_text = source_text
        doc.source_artifact = source_artifact
        doc.classification_details = classification_details
        doc.is_scanned = is_scanned
        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def fail_processing(self, doc: Document, *, error: str) -> Document:
        doc.processing_status = ProcessingStatus.failed
        doc.processing_error = error
        doc.processing_completed_at = datetime.utcnow()
        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def skip_ocr(self, doc: Document) -> Document:
        doc.ocr_status = OcrStatus.not_required
        doc.is_scanned = False
        doc.ocr_raw_text = None
        doc.ocr_language = None
        doc.page_count = None
        doc.ocr_error = None
        doc.ocr_provider = None
        doc.ocr_artifact = None
        doc.ocr_started_at = None
        doc.ocr_completed_at = None
        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def save_review(self, doc: Document, content: str) -> Document:
        doc.reviewed_content = content
        doc.review_status = DocReviewStatus.reviewed
        doc.updated_at = datetime.utcnow()

        await self.session.flush()
        return doc

    async def mark_pushed(self, doc: Document) -> Document:
        doc.review_status = DocReviewStatus.pushed
        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def record_docx_export(
        self,
        doc: Document,
        *,
        exported_by: uuid.UUID,
        filename: str,
        size_bytes: int,
        checksum_sha256: str,
        metadata: dict,
    ) -> DocumentDocxExport:
        export = DocumentDocxExport(
            document_id=doc.id,
            case_id=doc.case_id,
            exported_by=exported_by,
            filename=filename,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            generator="python_docx_reviewed_document_v1",
            export_metadata=metadata,
        )
        self.session.add(export)
        await self.session.flush()
        return export

    async def delete(self, doc: Document) -> None:
        await self.session.delete(doc)
        await self.session.flush()
