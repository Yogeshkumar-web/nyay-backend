import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.documents.models import Document, OcrStatus
from app.features.documents.repository import DocumentRepository
from app.features.documents.schemas import (
    ConfirmUploadRequest,
    DocumentResponse,
    PresignUploadRequest,
    PresignUploadResponse,
    UpdateDocumentRequest,
    ViewUrlResponse,
)
from app.features.users.models import User

# Allowed MIME types
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}


def _get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = DocumentRepository(db)
        self.case_repo = CaseRepository(db)

    async def _require_case_access(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_access(case_id, user.id):
            raise NotFoundError("Case not found")

    async def _require_case_edit(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_edit(case_id, user.id):
            raise ForbiddenError("You do not have edit access to this case")

    # ── Presign upload ─────────────────────────────────────────────────────────

    async def presign_upload(
        self,
        case_id: uuid.UUID,
        data: PresignUploadRequest,
        current_user: User,
    ) -> PresignUploadResponse:
        await self._require_case_edit(case_id, current_user)

        if data.mime_type not in ALLOWED_MIME_TYPES:
            raise ValidationError(
                f"Unsupported file type: {data.mime_type}. "
                f"Allowed: PDF, JPG, PNG, TIFF, WEBP"
            )

        # Build R2 key: cases/{case_id}/docs/{document_id}/{filename}
        document_id = uuid.uuid4()
        safe_filename = data.filename.replace(" ", "_")
        r2_key = f"cases/{case_id}/docs/{document_id}/{safe_filename}"

        # Create DB record first (status=pending)
        doc = await self.repo.create(
            case_id=case_id,
            uploaded_by=current_user.id,
            original_filename=data.filename,
            r2_key=r2_key,
            r2_bucket=settings.R2_BUCKET_NAME,
            mime_type=data.mime_type,
            file_size_bytes=data.file_size_bytes,
            document_type=data.document_type,
        )
        # Override the auto-generated id with our pre-made one for the key
        doc.id = document_id
        await self.db.flush()

        # Generate presigned PUT URL
        r2 = _get_r2_client()
        upload_url = r2.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.R2_BUCKET_NAME,
                "Key": r2_key,
                "ContentType": data.mime_type,
                "ContentLength": data.file_size_bytes,
            },
            ExpiresIn=900,  # 15 minutes
        )

        return PresignUploadResponse(
            upload_url=upload_url,
            document_id=doc.id,
            r2_key=r2_key,
        )

    # ── Confirm upload ─────────────────────────────────────────────────────────

    async def confirm_upload(
        self,
        document_id: uuid.UUID,
        data: ConfirmUploadRequest,
        current_user: User,
    ) -> DocumentResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        doc = await self.repo.confirm_upload(doc, data.is_scanned)

        # Trigger OCR async task
        if data.is_scanned:
            from app.workers.ocr_tasks import run_ocr_on_document
            run_ocr_on_document.delay(str(document_id))
        else:
            # Not scanned — OCR not required
            await self.repo.set_ocr_status(doc, OcrStatus.not_required)

        return DocumentResponse.model_validate(doc)

    # ── List documents ─────────────────────────────────────────────────────────

    async def list_documents(
        self, case_id: uuid.UUID, current_user: User
    ) -> list[DocumentResponse]:
        await self._require_case_access(case_id, current_user)
        docs = await self.repo.list_for_case(case_id)
        return [DocumentResponse.model_validate(d) for d in docs]

    # ── Get document ───────────────────────────────────────────────────────────

    async def get_document(
        self, document_id: uuid.UUID, current_user: User
    ) -> DocumentResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_case_access(doc.case_id, current_user)
        return DocumentResponse.model_validate(doc)

    # ── Update metadata ────────────────────────────────────────────────────────

    async def update_document(
        self,
        document_id: uuid.UUID,
        data: UpdateDocumentRequest,
        current_user: User,
    ) -> DocumentResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_case_edit(doc.case_id, current_user)
        doc = await self.repo.update(doc, data)
        return DocumentResponse.model_validate(doc)

    # ── Presigned view URL ─────────────────────────────────────────────────────

    async def get_view_url(
        self, document_id: uuid.UUID, current_user: User
    ) -> ViewUrlResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_case_access(doc.case_id, current_user)

        r2 = _get_r2_client()
        expires_in = 900  # 15 minutes
        url = r2.generate_presigned_url(
            "get_object",
            Params={"Bucket": doc.r2_bucket, "Key": doc.r2_key},
            ExpiresIn=expires_in,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        return ViewUrlResponse(url=url, expires_at=expires_at)

    # ── Delete document ────────────────────────────────────────────────────────

    async def delete_document(
        self, document_id: uuid.UUID, current_user: User
    ) -> None:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_case_edit(doc.case_id, current_user)

        # Delete from R2
        try:
            r2 = _get_r2_client()
            r2.delete_object(Bucket=doc.r2_bucket, Key=doc.r2_key)
        except Exception:
            # R2 delete failure should not block DB deletion
            pass

        await self.repo.delete(doc)