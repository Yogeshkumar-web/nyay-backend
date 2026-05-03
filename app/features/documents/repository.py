import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.documents.models import Document, DocumentType, OcrStatus, UploadStatus
from app.features.documents.schemas import UpdateDocumentRequest


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        case_id: uuid.UUID,
        uploaded_by: uuid.UUID,
        original_filename: str,
        r2_key: str,
        r2_bucket: str,
        mime_type: str,
        file_size_bytes: int,
        document_type: DocumentType,
    ) -> Document:
        doc = Document(
            case_id=case_id,
            uploaded_by=uploaded_by,
            original_filename=original_filename,
            r2_key=r2_key,
            r2_bucket=r2_bucket,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            document_type=document_type,
            upload_status=UploadStatus.pending,
            ocr_status=OcrStatus.pending,
        )
        self.session.add(doc)
        await self.session.flush()
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

    async def confirm_upload(self, doc: Document, is_scanned: bool) -> Document:
        doc.upload_status = UploadStatus.uploaded
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
    ) -> Document:
        doc.ocr_status = status
        if raw_text is not None:
            doc.ocr_raw_text = raw_text
        if language is not None:
            doc.ocr_language = language
        if page_count is not None:
            doc.page_count = page_count
        doc.updated_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def delete(self, doc: Document) -> None:
        await self.session.delete(doc)
        await self.session.flush()