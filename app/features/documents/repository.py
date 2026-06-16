import uuid
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.documents.models import (
    Document,
    DocReviewStatus,
    OcrStatus,
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

    async def delete(self, doc: Document) -> None:
        await self.session.delete(doc)
        await self.session.flush()
