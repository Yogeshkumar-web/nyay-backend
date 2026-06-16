import uuid
import re
import html
import json
from datetime import datetime, timedelta, timezone
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.documents.models import (
    Document,
    OcrStatus,
    ProcessingStatus,
    UploadStatus,
)
from app.features.documents.repository import DocumentRepository
from app.features.documents.schemas import (
    ConfirmUploadRequest,
    DocumentResponse,
    PresignUploadRequest,
    PresignUploadResponse,
    SaveReviewRequest,
    UpdateDocumentRequest,
    ViewUrlResponse,
)
from app.features.users.models import User
from app.features.notifications.service import NotificationService
from app.features.notifications.models import NotificationType

logger = logging.getLogger(__name__)

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
OCR_STALE_AFTER_SECONDS = 120
MAX_CONTEXT_TOKENS = 80_000


def _has_meaningful_content(content: str | None) -> bool:
    if not content:
        return False
    text = re.sub(r"<[^>]+>", " ", content)
    text = html.unescape(text).replace("\xa0", " ")
    return bool(text.strip())


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _format_validation_error(exc: ValidationError) -> str:
    message = exc.message
    details = exc.details or {}
    missing = details.get("missing")
    if isinstance(missing, list) and missing:
        message = f"{message}. Missing: {', '.join(str(item) for item in missing)}"
    credential_error = details.get("credential_error")
    if credential_error:
        message = f"{message}. Credential error: {credential_error}"
    return message


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

    def _storage_object_exists(self, doc: Document) -> bool:
        r2 = _get_r2_client()
        try:
            r2.head_object(Bucket=doc.r2_bucket, Key=doc.r2_key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            logger.exception("Failed to verify storage object for document %s", doc.id)
            raise ValidationError(
                "Could not verify document upload. Retry in a moment."
            ) from exc
        except Exception as exc:
            logger.exception("Failed to verify storage object for document %s", doc.id)
            raise ValidationError(
                "Could not verify document upload. Retry in a moment."
            ) from exc

    async def _require_storage_object_available(self, doc: Document) -> None:
        if self._storage_object_exists(doc):
            return

        doc.upload_status = UploadStatus.failed
        doc.updated_at = datetime.utcnow()
        await self.db.flush()
        raise ValidationError(
            "Document file is missing from storage. Re-upload the document."
        )

    async def _ensure_upload_confirmed_from_storage(
        self,
        doc: Document,
        *,
        verify_existing: bool = False,
    ) -> Document:
        if doc.upload_status == UploadStatus.uploaded:
            if verify_existing:
                await self._require_storage_object_available(doc)
            return doc

        await self._require_storage_object_available(doc)

        logger.info("Self-healing upload_status for document %s from R2", doc.id)
        doc.upload_status = UploadStatus.uploaded
        doc.updated_at = datetime.utcnow()
        await self.db.flush()
        return doc

    async def _recover_stale_ocr(self, doc: Document) -> bool:
        if doc.ocr_status != OcrStatus.processing:
            return False

        updated_at = doc.updated_at
        if updated_at is None:
            return False
        if updated_at.tzinfo is not None:
            updated_at = updated_at.replace(tzinfo=None)

        if datetime.utcnow() - updated_at < timedelta(seconds=OCR_STALE_AFTER_SECONDS):
            return False

        logger.warning("Marking stale OCR job as failed for document %s", doc.id)
        await self.repo.set_ocr_status(
            doc,
            OcrStatus.failed,
            error="OCR worker did not finish in time. Retry OCR.",
        )
        return True

    # ── Presign upload ─────────────────────────────────────────────────────────

    async def presign_upload(
        self,
        case_id: uuid.UUID,
        data: PresignUploadRequest,
        current_user: User,
    ) -> PresignUploadResponse:
        await self._require_case_edit(case_id, current_user)

        if data.mime_type not in ALLOWED_MIME_TYPES:
            raise ValidationError("Unsupported file type")

        if data.file_size_bytes > MAX_FILE_SIZE:
            raise ValidationError("File too large")

        document_id = uuid.uuid4()

        # safer filename sanitization
        safe_filename = re.sub(r"[^a-zA-Z0-9._-]", "_", data.filename.strip())
        r2_key = f"cases/{case_id}/docs/{document_id}/{safe_filename}"

        doc = await self.repo.create(
            id=document_id,
            case_id=case_id,
            uploaded_by=current_user.id,
            original_filename=data.filename,
            r2_key=r2_key,
            r2_bucket=settings.R2_BUCKET_NAME,
            mime_type=data.mime_type,
            file_size_bytes=data.file_size_bytes,
            document_type=data.document_type,
        )

        r2 = _get_r2_client()

        try:
            upload_url = r2.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": settings.R2_BUCKET_NAME,
                    "Key": r2_key,
                    "ContentType": data.mime_type,
                },
                ExpiresIn=900,
            )
        except Exception as exc:
            logger.exception("Presign URL generation failed")
            raise ValidationError("Failed to generate upload URL") from exc

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

        await self._require_storage_object_available(doc)
        doc = await self.repo.confirm_upload(doc, data.is_scanned)
        await self.db.commit()

        try:
            await self._queue_processing(doc)
        except ValidationError:
            logger.exception(
                "Automatic processing queue failed for uploaded document %s", doc.id
            )
        response = DocumentResponse.model_validate(doc)

        # ─────────────────────────────
        # Create notification
        # ─────────────────────────────
        try:
            notification_svc = NotificationService(self.db)
            await notification_svc.create(
                user_id=current_user.id,
                notification_type=NotificationType.info,
                title="Document Uploaded",
                message=f"Document {doc.original_filename} uploaded successfully.",
                link=f"/cases/{doc.case_id}?tab=documents",
                dedup_key=f"document_uploaded_{doc.id}",
                linked_entity_type="Document",
                linked_entity_id=doc.id,
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            logger.exception(
                "Failed to create upload notification for document %s", doc.id
            )

        return response

    # ── Run OCR ───────────────────────────────────────────────────────────────

    async def run_ocr(
        self,
        document_id: uuid.UUID,
        current_user: User,
    ) -> dict:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        return await self._queue_processing(doc, force=True)

    async def _queue_processing(self, doc: Document, *, force: bool = False) -> dict:
        from app.workers.document_processing_tasks import process_document
        from app.workers.job_store import JobStatus, set_job_status

        doc = await self._ensure_upload_confirmed_from_storage(doc)
        if doc.processing_status == ProcessingStatus.processing:
            return {"job_id": doc.processing_job_id, "already_running": True}

        job_id = str(uuid.uuid4())
        await self.repo.start_processing(doc, job_id=job_id)
        await self.repo.start_ocr(doc, job_id=job_id)
        await self.db.commit()

        try:
            await set_job_status(
                job_id,
                JobStatus.pending,
                result={"document_id": str(doc.id)},
            )
            process_document.apply_async(args=[str(doc.id)], task_id=job_id)
        except Exception as exc:
            logger.exception("Failed to queue document processing for %s", doc.id)
            message = "Document worker is not available. Start Redis/Celery and retry."
            await self.repo.fail_processing(doc, error=message)
            await self.repo.set_ocr_status(doc, OcrStatus.failed, error=message)
            await self.db.commit()
            raise ValidationError(message) from exc
        return {"job_id": job_id}

    # ── List documents ─────────────────────────────────────────────────────────

    async def skip_ocr(
        self,
        document_id: uuid.UUID,
        current_user: User,
    ) -> DocumentResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        await self._queue_processing(doc, force=True)
        return DocumentResponse.model_validate(doc)

    async def list_documents(
        self, case_id: uuid.UUID, current_user: User
    ) -> list[DocumentResponse]:
        await self._require_case_access(case_id, current_user)
        docs = await self.repo.list_for_case(case_id)
        changed = False
        for doc in docs:
            changed = await self._recover_stale_ocr(doc) or changed
        if changed:
            await self.db.commit()
        return [DocumentResponse.model_validate(d) for d in docs]

    # ── Get document ───────────────────────────────────────────────────────────

    async def get_document(
        self, document_id: uuid.UUID, current_user: User
    ) -> DocumentResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_access(doc.case_id, current_user)
        if await self._recover_stale_ocr(doc):
            await self.db.commit()
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

        doc = await self._ensure_upload_confirmed_from_storage(
            doc,
            verify_existing=True,
        )
        await self.db.commit()

        r2 = _get_r2_client()
        expires_in = 900

        url = r2.generate_presigned_url(
            "get_object",
            Params={"Bucket": doc.r2_bucket, "Key": doc.r2_key},
            ExpiresIn=expires_in,
        )

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        return ViewUrlResponse(url=url, expires_at=expires_at)

    # ── Delete document ────────────────────────────────────────────────────────

    async def delete_document(self, document_id: uuid.UUID, current_user: User) -> None:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        try:
            r2 = _get_r2_client()
            r2.delete_object(Bucket=doc.r2_bucket, Key=doc.r2_key)
        except Exception:
            logger.warning("R2 delete failed, continuing DB cleanup")

        await self.repo.delete(doc)

    # ── Save review ────────────────────────────────────────────────────────────

    async def save_review(
        self,
        document_id: uuid.UUID,
        data: SaveReviewRequest,
        current_user: User,
    ) -> DocumentResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        doc = await self.repo.save_review(doc, data.content)
        await self.db.commit()
        return DocumentResponse.model_validate(doc)

    # ── Push to context ────────────────────────────────────────────────────────

    async def push_to_context(
        self,
        document_id: uuid.UUID,
        current_user: User,
    ) -> dict:
        from app.features.context.repository import ContextRepository

        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        content_to_push = doc.reviewed_content or doc.source_text or doc.ocr_raw_text
        if not _has_meaningful_content(content_to_push):
            if doc.ocr_status == OcrStatus.processing:
                raise ValidationError(
                    "OCR is still running. Wait for it to finish first."
                )
            if doc.ocr_status == OcrStatus.failed:
                detail = f": {doc.ocr_error}" if doc.ocr_error else ""
                raise ValidationError(
                    f"OCR failed{detail}. Retry OCR or save text manually."
                )
            if doc.ocr_status == OcrStatus.not_required:
                raise ValidationError(
                    "Save reviewed text before pushing this typed document."
                )
            raise ValidationError(
                "No content to push. Complete OCR or save reviewed text first."
            )
            raise ValidationError("No content to push — OCR must complete first")

        context_repo = ContextRepository(self.db)
        existing = await context_repo.get_context(doc.case_id)
        context_json = dict(existing.context_json or {}) if existing else {}
        pushed_documents = dict(existing.pushed_documents or {}) if existing else {}

        existing_docs = context_json.get("documents", [])
        if not isinstance(existing_docs, list):
            existing_docs = []
        doc_id_str = str(doc.id)

        existing_docs = [
            d
            for d in existing_docs
            if isinstance(d, dict) and d.get("document_id") != doc_id_str
        ]

        existing_docs.append(
            {
                "document_id": doc_id_str,
                "document_type": doc.document_type.value,
                "filename": doc.display_name or doc.original_filename,
                "content": content_to_push,
            }
        )

        context_json["documents"] = existing_docs
        pushed_documents[doc_id_str] = {
            "document_type": doc.document_type.value,
            "filename": doc.display_name or doc.original_filename,
            "added_at": datetime.utcnow().isoformat(),
            "source": "document_review",
            "content_chars": len(content_to_push),
        }

        token_estimate = _estimate_tokens(json.dumps(context_json, ensure_ascii=False))
        if token_estimate > MAX_CONTEXT_TOKENS:
            raise ValidationError("Context too large, trim required")

        await context_repo.upsert_context(
            case_id=doc.case_id,
            context_json=context_json,
            pushed_documents=pushed_documents,
            token_estimate=token_estimate,
        )

        await self.repo.mark_pushed(doc)
        await self.db.commit()

        return {
            "case_id": str(doc.case_id),
            "document_id": str(doc.id),
        }

    # ── Document Readiness ─────────────────────────────────────────────────────

    async def get_documents_readiness(
        self,
        case_id: uuid.UUID,
        current_user: User,
    ) -> dict:
        """
        Returns readiness status for every document in the case.
        A document is 'ready' when:
          1. ocr_status = completed OR not_required
          2. TypedVersion exists AND status in (edited, accepted)
          3. ExtractionResult review_status in (accepted, edited)  ← optional
        """
        from app.features.extraction.repository import ExtractionRepository
        from app.features.extraction.models import ReviewStatus

        await self._require_case_access(case_id, current_user)

        docs = await self.repo.list_for_case(case_id)
        ext_repo = ExtractionRepository(self.db)

        document_statuses = []
        total_ready = 0
        pending_ocr = 0
        pending_typed = 0
        pending_extraction = 0

        for doc in docs:
            missing: list[str] = []

            # Step 1: OCR
            ocr_ok = doc.ocr_status in (OcrStatus.completed, OcrStatus.not_required)
            if not ocr_ok:
                missing.append("ocr")
                pending_ocr += 1

            # Step 2: TypedVersion saved
            typed_ok = doc.processing_route.value == "digital_extract"
            if ocr_ok and not typed_ok:
                tv = await ext_repo.get_typed_version(doc.id)
                typed_ok = tv is not None and tv.status in (
                    ReviewStatus.edited,
                    ReviewStatus.accepted,
                )
                if not typed_ok:
                    missing.append("typed_content")
                    pending_typed += 1

            # Step 3: Extraction reviewed (optional — only required if extraction ran)
            ex_ok = True
            if typed_ok:
                ex = await ext_repo.get_by_document(doc.id)
                if ex is not None:
                    # Extraction ran — require it to be reviewed
                    ex_reviewed = ex.review_status in (
                        ReviewStatus.accepted,
                        ReviewStatus.edited,
                    )
                    if not ex_reviewed:
                        ex_ok = False
                        missing.append("extraction_review")
                        pending_extraction += 1
                # If no extraction result → extraction not run → skip requirement

            is_ready = ocr_ok and typed_ok and ex_ok

            if is_ready:
                total_ready += 1

            document_statuses.append(
                {
                    "id": str(doc.id),
                    "name": doc.display_name or doc.original_filename,
                    "document_type": doc.document_type.value,
                    "ocr_status": doc.ocr_status.value,
                    "is_ready": is_ready,
                    "missing_steps": missing,
                }
            )

        total = len(docs)
        return {
            "total": total,
            "ready": total_ready,
            "all_ready": total > 0 and total_ready == total,
            "pending_ocr": pending_ocr,
            "pending_typed": pending_typed,
            "pending_extraction": pending_extraction,
            "documents": document_statuses,
        }

    # ── Typing Agent ───────────────────────────────────────────────────────────

    async def run_typing(
        self,
        document_id: uuid.UUID,
        current_user: User,
    ) -> dict:
        """
        Enqueue the Typing Agent (LangGraph) for this document.
        Returns a job_id the caller can poll via GET /jobs/{job_id}.
        """
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        if not doc.ocr_raw_text:
            raise ValidationError(
                "OCR must complete before running the Typing Agent. "
                "Run OCR first or skip it for digital documents."
            )

        job_id = str(uuid.uuid4())

        from app.workers.typing_tasks import run_type_document
        from app.workers.job_store import JobStatus, set_job_status

        try:
            await set_job_status(
                job_id,
                JobStatus.pending,
                result={"document_id": str(document_id)},
            )
            run_type_document.apply_async(args=[str(document_id)], task_id=job_id)
        except Exception as exc:
            logger.exception("Failed to queue Typing task for document %s", document_id)
            raise ValidationError(
                "Typing worker is not available. Start Redis/Celery and retry."
            ) from exc

        logger.info(
            "Typing task queued | job_id=%s | document_id=%s", job_id, document_id
        )
        return {"job_id": job_id}
