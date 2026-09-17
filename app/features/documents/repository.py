import uuid
import logging
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.documents.models import (
    Document,
    DocumentDocxExport,
    DocumentDomainEvent,
    DocumentPage,
    DocumentPageStatus,
    DocumentProcessingRun,
    DocumentTypedRevision,
    DocReviewStatus,
    OcrStatus,
    ProcessingRunStatus,
    ProcessingRoute,
    ProcessingStatus,
    TypedRevisionStatus,
    UploadStatus,
)
from app.features.documents.schemas import UpdateDocumentRequest

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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

    async def get_processing_run(
        self,
        run_id: uuid.UUID,
    ) -> DocumentProcessingRun | None:
        result = await self.session.execute(
            select(DocumentProcessingRun).where(DocumentProcessingRun.id == run_id)
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

        doc.updated_at = _utcnow()
        await self.session.flush()
        return doc

    async def confirm_upload(self, doc: Document, is_scanned: bool | None) -> Document:
        if doc.upload_status == UploadStatus.uploaded:
            if is_scanned is not None and doc.is_scanned != is_scanned:
                doc.is_scanned = is_scanned
                doc.updated_at = _utcnow()
                await self.session.flush()
            return doc  # idempotent

        doc.upload_status = UploadStatus.uploaded
        if is_scanned is not None:
            doc.is_scanned = is_scanned
        doc.updated_at = _utcnow()

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
            doc.ocr_completed_at = _utcnow()
        elif status == OcrStatus.failed:
            doc.ocr_completed_at = _utcnow()

        doc.updated_at = _utcnow()
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
        doc.ocr_started_at = _utcnow()
        doc.ocr_completed_at = None
        if doc.approved_typed_revision_id is None:
            doc.reviewed_content = None
            doc.review_status = DocReviewStatus.pending
        doc.updated_at = _utcnow()
        await self.session.flush()
        return doc

    async def start_processing(
        self,
        doc: Document,
        *,
        job_id: str,
    ) -> Document:
        doc.processing_status = ProcessingStatus.queued
        doc.processing_job_id = job_id
        doc.processing_error = None
        doc.processing_started_at = _utcnow()
        doc.processing_completed_at = None
        doc.source_text = None
        doc.source_artifact = None
        doc.classification_details = None
        doc.updated_at = _utcnow()
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
        doc.active_processing_run_id = run.id
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
            run.completed_at = _utcnow()
        run.updated_at = _utcnow()
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
                classification=page_data.get("classification"),
                extraction_method=page_data.get("extraction_method"),
                embedded_text=page_data.get("embedded_text"),
                classification_artifact=page_data.get("classification_artifact", {}),
                content_hashes=page_data.get("content_hashes", {}),
            )
            self.session.add(page)
            created.append(page)
        await self.session.flush()
        return created

    async def save_page_extraction(
        self,
        page: DocumentPage,
        *,
        extracted_text: str,
        extraction_method,
        provider_key: str,
        provider_version: str | None,
        provider_job_id: str | None,
        language: str | None,
        confidence: float | None,
        warnings: list[str],
        provider_artifact: dict,
        content_hashes: dict,
    ) -> None:
        page.status = DocumentPageStatus.extracted
        page.extracted_text = extracted_text
        page.ocr_text = extracted_text if extraction_method.value == "vision_ocr" else None
        page.extraction_method = extraction_method
        page.provider_key = provider_key
        page.provider_version = provider_version
        page.provider_job_id = provider_job_id
        page.language = language
        page.confidence = confidence
        page.warnings = warnings
        page.provider_artifact = provider_artifact
        page.content_hashes = {**(page.content_hashes or {}), **content_hashes}
        page.error = None
        page.updated_at = _utcnow()
        await self.session.flush()

    async def save_page_typing(
        self,
        page: DocumentPage,
        *,
        typed_markdown: str,
        warnings: list[str],
        provider_key: str,
        provider_version: str | None,
        provider_artifact: dict,
        typed_hash: str,
    ) -> None:
        page.status = DocumentPageStatus.typed
        page.typed_markdown = typed_markdown
        page.warnings = list(dict.fromkeys([*(page.warnings or []), *warnings]))
        page.provider_artifact = {
            **(page.provider_artifact or {}),
            "typing": provider_artifact,
        }
        page.content_hashes = {**(page.content_hashes or {}), "typed": typed_hash}
        page.updated_at = _utcnow()
        await self.session.flush()

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

    async def get_run_page(
        self,
        run_id: uuid.UUID,
        page_number: int,
    ) -> DocumentPage | None:
        result = await self.session.execute(
            select(DocumentPage).where(
                DocumentPage.processing_run_id == run_id,
                DocumentPage.page_number == page_number,
            )
        )
        return result.scalar_one_or_none()

    async def get_run_page_for_update(
        self,
        run_id: uuid.UUID,
        page_number: int,
    ) -> DocumentPage | None:
        result = await self.session.execute(
            select(DocumentPage)
            .where(
                DocumentPage.processing_run_id == run_id,
                DocumentPage.page_number == page_number,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def mark_unfinished_pages_failed(
        self,
        run: DocumentProcessingRun,
        *,
        error: str,
    ) -> None:
        pages = await self.list_pages_for_run(run.id)
        failed_statuses = {
            DocumentPageStatus.pending,
            DocumentPageStatus.inventoried,
            DocumentPageStatus.split,
            DocumentPageStatus.classified,
            DocumentPageStatus.extracting,
            DocumentPageStatus.ocr_running,
        }
        if run.status == ProcessingRunStatus.typing_pages:
            failed_statuses |= {
                DocumentPageStatus.extracted,
                DocumentPageStatus.ocr_completed,
                DocumentPageStatus.typing,
            }
        for page in pages:
            if page.status not in failed_statuses:
                continue
            page.status = DocumentPageStatus.failed
            page.error = error
            page.attempt_count += 1
            page.updated_at = _utcnow()
        run.failed_pages = sum(
            page.status == DocumentPageStatus.failed for page in pages
        )
        await self.session.flush()

    async def mark_run_pages_ocr_running(
        self,
        run: DocumentProcessingRun,
    ) -> None:
        pages = await self.list_pages_for_run(run.id)
        for page in pages:
            page.status = DocumentPageStatus.ocr_running
            page.error = None
            page.updated_at = _utcnow()
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
            page.updated_at = _utcnow()
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
        doc.processing_completed_at = _utcnow()
        doc.source_text = source_text
        doc.source_artifact = source_artifact
        doc.classification_details = classification_details
        doc.is_scanned = is_scanned
        doc.updated_at = _utcnow()
        await self.session.flush()
        return doc

    async def fail_processing(self, doc: Document, *, error: str) -> Document:
        doc.processing_status = ProcessingStatus.failed
        doc.processing_error = error
        doc.processing_completed_at = _utcnow()
        doc.updated_at = _utcnow()
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
        doc.updated_at = _utcnow()
        await self.session.flush()
        return doc

    async def save_review(self, doc: Document, content: str) -> Document:
        doc.reviewed_content = content
        doc.review_status = DocReviewStatus.reviewed
        doc.updated_at = _utcnow()

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
        approved_revision_id: uuid.UUID | None = None,
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
            approved_revision_id=approved_revision_id,
        )
        self.session.add(export)
        await self.session.flush()
        return export

    async def get_latest_typed_revision(
        self,
        document_id: uuid.UUID,
    ) -> DocumentTypedRevision | None:
        result = await self.session.execute(
            select(DocumentTypedRevision)
            .where(DocumentTypedRevision.document_id == document_id)
            .order_by(DocumentTypedRevision.revision_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_typed_revision(
        self,
        revision_id: uuid.UUID,
    ) -> DocumentTypedRevision | None:
        result = await self.session.execute(
            select(DocumentTypedRevision).where(DocumentTypedRevision.id == revision_id)
        )
        return result.scalar_one_or_none()

    async def get_approved_typed_revision(
        self,
        doc: Document,
    ) -> DocumentTypedRevision | None:
        if doc.approved_typed_revision_id is None:
            return None
        return await self.get_typed_revision(doc.approved_typed_revision_id)

    async def create_typed_revision(
        self,
        doc: Document,
        *,
        content_markdown: str,
        content_hash: str,
        canonical_structure: dict,
        canonical_schema_version: int,
        created_by: uuid.UUID,
        processing_run_id: uuid.UUID | None,
        parent_revision_id: uuid.UUID | None = None,
    ) -> DocumentTypedRevision:
        max_result = await self.session.execute(
            select(func.max(DocumentTypedRevision.revision_number)).where(
                DocumentTypedRevision.document_id == doc.id
            )
        )
        revision = DocumentTypedRevision(
            document_id=doc.id,
            processing_run_id=processing_run_id,
            parent_revision_id=parent_revision_id,
            revision_number=(max_result.scalar_one_or_none() or 0) + 1,
            content_markdown=content_markdown,
            content_hash=content_hash,
            canonical_structure=canonical_structure,
            canonical_schema_version=canonical_schema_version,
            status=TypedRevisionStatus.draft,
            created_by=created_by,
        )
        self.session.add(revision)
        await self.session.flush()
        doc.latest_typed_revision_id = revision.id
        doc.review_status = DocReviewStatus.review_required
        doc.reviewed_content = content_markdown
        await self.session.flush()
        return revision

    async def update_typed_draft(
        self,
        revision: DocumentTypedRevision,
        *,
        content_markdown: str,
        content_hash: str,
        canonical_structure: dict,
        canonical_schema_version: int,
    ) -> DocumentTypedRevision:
        revision.content_markdown = content_markdown
        revision.content_hash = content_hash
        revision.canonical_structure = canonical_structure
        revision.canonical_schema_version = canonical_schema_version
        revision.lock_version += 1
        revision.updated_at = _utcnow()
        await self.session.flush()
        return revision

    async def approve_typed_revision(
        self,
        doc: Document,
        revision: DocumentTypedRevision,
        *,
        approved_by: uuid.UUID,
        event_payload: dict,
    ) -> DocumentDomainEvent:
        previous = await self.get_approved_typed_revision(doc)
        if previous is not None and previous.id != revision.id:
            previous.status = TypedRevisionStatus.superseded
        revision.status = TypedRevisionStatus.approved
        revision.approved_by = approved_by
        revision.approved_at = _utcnow()
        revision.updated_at = _utcnow()
        doc.latest_typed_revision_id = revision.id
        doc.approved_typed_revision_id = revision.id
        doc.review_status = DocReviewStatus.approved
        doc.processing_status = ProcessingStatus.approved
        doc.reviewed_content = revision.content_markdown
        event = DocumentDomainEvent(
            document_id=doc.id,
            event_type="document.typed_version.approved",
            payload=event_payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_approval_event(
        self,
        document_id: uuid.UUID,
    ) -> DocumentDomainEvent | None:
        result = await self.session.execute(
            select(DocumentDomainEvent)
            .where(
                DocumentDomainEvent.document_id == document_id,
                DocumentDomainEvent.event_type == "document.typed_version.approved",
            )
            .order_by(DocumentDomainEvent.occurred_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def delete(self, doc: Document) -> None:
        await self.session.delete(doc)
        await self.session.flush()
