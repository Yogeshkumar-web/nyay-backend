import uuid
import re
import hashlib
from datetime import datetime, timedelta, timezone
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.documents.models import (
    Document,
    DocumentType,
    DocumentPageStatus,
    DocReviewStatus,
    OcrStatus,
    ProcessingRunStatus,
    ProcessingStatus,
    TypedRevisionStatus,
    UploadStatus,
)
from app.features.documents.canonical_document import (
    CANONICAL_SCHEMA_VERSION,
    build_canonical_document,
)
from app.features.documents.repository import DocumentRepository
from app.features.documents.schemas import (
    ConfirmUploadRequest,
    ConfirmUploadResponse,
    DocumentResponse,
    DocumentPageProgressResponse,
    ProcessingProgressResponse,
    ProcessingRunResponse,
    PresignUploadRequest,
    PresignUploadResponse,
    SaveReviewRequest,
    SaveTypedRevisionRequest,
    TypedRevisionResponse,
    ApproveTypedRevisionResponse,
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
def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
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
        doc.updated_at = _utcnow()
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
        doc.updated_at = _utcnow()
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

        if _utcnow() - updated_at < timedelta(
            seconds=settings.DOCUMENT_PROCESSING_STALE_AFTER_SECONDS
        ):
            return False

        logger.warning("Marking stale OCR job as failed for document %s", doc.id)
        message = "OCR worker did not finish in time. Retry OCR."
        await self.repo.set_ocr_status(
            doc,
            OcrStatus.failed,
            error=message,
        )
        await self.repo.fail_processing(doc, error=message)
        job_id = doc.processing_job_id or doc.ocr_job_id
        if job_id:
            from app.workers.job_store import JobStatus, set_job_status

            await set_job_status(job_id, JobStatus.failed, error=message)
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
    ) -> ConfirmUploadResponse:
        doc = await self.repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_case_edit(doc.case_id, current_user)

        await self._require_storage_object_available(doc)
        doc = await self.repo.confirm_upload(doc, data.is_scanned)
        await self.db.commit()

        active_run = (
            await self.repo.get_processing_run(doc.active_processing_run_id)
            if doc.active_processing_run_id
            else None
        )
        if active_run is None or active_run.status == ProcessingRunStatus.failed:
            queued = await self._queue_processing(
                doc,
                started_by=current_user.id,
                force=False,
            )
            active_run = await self.repo.get_processing_run(
                uuid.UUID(queued["processing_run_id"])
            )
        if active_run is None:
            raise ValidationError("Document processing could not be queued.")

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

        return ConfirmUploadResponse(
            document=DocumentResponse.model_validate(doc),
            processing_run=ProcessingRunResponse.model_validate(active_run),
        )

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

        return await self._queue_processing(
            doc,
            started_by=current_user.id,
            force=True,
            trigger="full_reprocess",
        )

    async def _queue_processing(
        self,
        doc: Document,
        *,
        started_by: uuid.UUID,
        force: bool = False,
        trigger: str | None = None,
    ) -> dict:
        from app.workers.document_processing_tasks import process_document
        from app.workers.job_store import JobStatus, set_job_status

        doc = await self._ensure_upload_confirmed_from_storage(doc)
        if doc.processing_status in {
            ProcessingStatus.queued,
            ProcessingStatus.processing,
        }:
            return {"job_id": doc.processing_job_id, "already_running": True}

        job_id = str(uuid.uuid4())
        await self.repo.start_processing(doc, job_id=job_id)
        await self.repo.start_ocr(doc, job_id=job_id)
        run = await self.repo.create_processing_run(
            doc,
            job_id=job_id,
            started_by=started_by,
            status=ProcessingRunStatus.queued,
            metadata={
                "pipeline": "document_pipeline_v2",
                "trigger": trigger or ("manual" if force else "upload_confirmation"),
            },
        )
        await self.db.commit()

        try:
            await set_job_status(
                job_id,
                JobStatus.pending,
                result={"document_id": str(doc.id), "processing_run_id": str(run.id)},
            )
            process_document.apply_async(args=[str(doc.id)], task_id=job_id)
        except Exception as exc:
            logger.exception("Failed to queue document processing for %s", doc.id)
            message = "Document worker is not available. Start Redis/Celery and retry."
            await self.repo.fail_processing(doc, error=message)
            await self.repo.set_ocr_status(doc, OcrStatus.failed, error=message)
            await self.repo.update_processing_run(
                run,
                status=ProcessingRunStatus.failed,
                error=message,
            )
            await self.db.commit()
            raise ValidationError(message) from exc
        return {"job_id": job_id, "processing_run_id": str(run.id)}

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

        await self._queue_processing(doc, started_by=current_user.id, force=True)
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

    async def get_processing_progress(
        self,
        document_id: uuid.UUID,
        current_user: User,
        run_id: uuid.UUID | None = None,
    ) -> ProcessingProgressResponse:
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("Document not found")
        await self._require_case_access(doc.case_id, current_user)
        selected_run_id = run_id or doc.active_processing_run_id
        if selected_run_id is None:
            raise NotFoundError("Document processing run not found")
        run = await self.repo.get_processing_run(selected_run_id)
        if run is None or run.document_id != doc.id:
            raise NotFoundError("Document processing run not found")
        pages = await self.repo.list_pages_for_run(run.id)
        payload = ProcessingProgressResponse.model_validate(run)
        payload.pages = [
            DocumentPageProgressResponse.model_validate(page) for page in pages
        ]
        return payload

    async def retry_page(
        self,
        document_id: uuid.UUID,
        page_number: int,
        current_user: User,
    ) -> dict:
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("Document not found")
        await self._require_case_edit(doc.case_id, current_user)
        if doc.active_processing_run_id is None:
            raise NotFoundError("Document processing run not found")
        page = await self.repo.get_run_page_for_update(
            doc.active_processing_run_id,
            page_number,
        )
        if page is None:
            raise NotFoundError("Document page not found")
        if page.status != DocumentPageStatus.failed:
            raise ValidationError("Only a failed page can be retried.")
        from app.workers.document_processing_tasks import retry_document_page
        from app.workers.job_store import JobStatus, set_job_status

        job_id = str(uuid.uuid4())
        retry_stage = (
            ProcessingRunStatus.typing_pages
            if page.extracted_text is not None
            else ProcessingRunStatus.extracting_pages
        )
        page.status = (
            DocumentPageStatus.typing
            if page.extracted_text is not None
            else DocumentPageStatus.extracting
        )
        page.error = None
        page.attempt_count += 1
        run = await self.repo.get_processing_run(doc.active_processing_run_id)
        if run is None:
            raise NotFoundError("Document processing run not found")
        await self.repo.update_processing_run(run, status=retry_stage, error=None)
        doc.processing_status = ProcessingStatus.processing
        doc.processing_error = None
        await self.db.commit()
        try:
            await set_job_status(
                job_id,
                JobStatus.pending,
                result={
                    "document_id": str(doc.id),
                    "processing_run_id": str(run.id),
                    "page_number": page_number,
                },
            )
            retry_document_page.apply_async(
                args=[str(doc.id), str(run.id), page_number],
                task_id=job_id,
            )
        except Exception as exc:
            page.status = DocumentPageStatus.failed
            page.error = "Document page retry could not be queued."
            await self.repo.update_processing_run(
                run,
                status=ProcessingRunStatus.failed,
                error=page.error,
            )
            doc.processing_status = ProcessingStatus.failed
            doc.processing_error = page.error
            await self.db.commit()
            raise ValidationError(page.error) from exc
        return {
            "job_id": job_id,
            "processing_run_id": str(run.id),
            "page_number": page_number,
        }

    async def get_typed_revision(
        self,
        document_id: uuid.UUID,
        current_user: User,
    ) -> TypedRevisionResponse:
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("Document not found")
        await self._require_case_access(doc.case_id, current_user)
        revision = await self.repo.get_latest_typed_revision(document_id)
        if revision is None:
            raise NotFoundError("Typed version not found")
        await self.ensure_canonical_structure(doc, revision)
        await self.db.commit()
        return TypedRevisionResponse.model_validate(revision)

    async def ensure_canonical_structure(self, doc: Document, revision):
        if (
            revision.canonical_structure
            and revision.canonical_schema_version >= CANONICAL_SCHEMA_VERSION
            and revision.canonical_structure.get("source_markdown_hash")
            == hashlib.sha256(revision.content_markdown.encode("utf-8")).hexdigest()
        ):
            return revision
        canonical = build_canonical_document(
            revision.content_markdown,
            document_type=doc.document_type.value,
            page_evidence=(doc.source_artifact or {}).get("page_evidence"),
        )
        revision.canonical_structure = canonical.model_dump(mode="json")
        revision.canonical_schema_version = canonical.schema_version
        if doc.document_type == DocumentType.other and canonical.document_type == "fir":
            doc.document_type = DocumentType.fir
        await self.db.flush()
        return revision

    async def save_typed_revision(
        self,
        document_id: uuid.UUID,
        data: SaveTypedRevisionRequest,
        current_user: User,
    ) -> TypedRevisionResponse:
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("Document not found")
        await self._require_case_edit(doc.case_id, current_user)
        revision = await self.repo.get_typed_revision(data.revision_id)
        if revision is None or revision.document_id != doc.id:
            raise NotFoundError("Typed version not found")
        if revision.lock_version != data.lock_version:
            latest = await self.repo.get_latest_typed_revision(doc.id)
            raise ConflictError(
                "This document was changed in another session.",
                details={
                    "latest_revision_id": str(latest.id) if latest else None,
                    "latest_lock_version": latest.lock_version if latest else None,
                },
            )
        content = data.content_markdown.strip()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        canonical = build_canonical_document(
            content,
            document_type=doc.document_type.value,
            page_evidence=(doc.source_artifact or {}).get("page_evidence"),
        )
        if revision.status == TypedRevisionStatus.approved:
            revision = await self.repo.create_typed_revision(
                doc,
                content_markdown=content,
                content_hash=content_hash,
                canonical_structure=canonical.model_dump(mode="json"),
                canonical_schema_version=canonical.schema_version,
                created_by=current_user.id,
                processing_run_id=revision.processing_run_id,
                parent_revision_id=revision.id,
            )
        else:
            revision = await self.repo.update_typed_draft(
                revision,
                content_markdown=content,
                content_hash=content_hash,
                canonical_structure=canonical.model_dump(mode="json"),
                canonical_schema_version=canonical.schema_version,
            )
            doc.latest_typed_revision_id = revision.id
            doc.review_status = DocReviewStatus.review_required
            doc.processing_status = ProcessingStatus.ready_for_review
            doc.reviewed_content = content

        from app.features.extraction.repository import ExtractionRepository
        from app.features.extraction.models import ReviewStatus

        legacy = await ExtractionRepository(self.db).create_or_update_typed_version(
            document_id=doc.id,
            typed_content=content,
            agent_notes="canonical_revision_v2",
        )
        legacy.status = ReviewStatus.edited
        legacy.reviewed_by = current_user.id
        await self.db.commit()
        return TypedRevisionResponse.model_validate(revision)

    async def approve_typed_revision(
        self,
        document_id: uuid.UUID,
        current_user: User,
    ) -> ApproveTypedRevisionResponse:
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("Document not found")
        await self._require_case_edit(doc.case_id, current_user)
        revision = await self.repo.get_latest_typed_revision(document_id)
        if revision is None or not revision.content_markdown.strip():
            raise ValidationError("Save typed document content before approval.")
        await self.ensure_canonical_structure(doc, revision)
        if revision.status == TypedRevisionStatus.approved:
            event = await self.repo.get_approval_event(doc.id)
            if event is None:
                raise ValidationError("Document approval event is missing.")
            return ApproveTypedRevisionResponse(
                revision=TypedRevisionResponse.model_validate(revision),
                event_id=event.id,
            )
        if doc.active_processing_run_id:
            pages = await self.repo.list_pages_for_run(doc.active_processing_run_id)
            failed = [page.page_number for page in pages if page.status == DocumentPageStatus.failed]
            if failed:
                raise ValidationError(
                    "Failed pages must be retried before approval.",
                    details={"failed_pages": failed},
                )
        now = datetime.now(timezone.utc)
        event_payload = {
            "event_type": "document.typed_version.approved",
            "document_id": str(doc.id),
            "case_id": str(doc.case_id),
            "approved_revision_id": str(revision.id),
            "processing_run_id": str(revision.processing_run_id) if revision.processing_run_id else None,
            "content_hash": revision.content_hash,
            "canonical_schema_version": revision.canonical_schema_version,
            "unresolved_block_count": revision.canonical_structure.get(
                "unresolved_block_count", 0
            ),
            "approved_by": str(current_user.id),
            "approved_at": now.isoformat(),
        }
        event = await self.repo.approve_typed_revision(
            doc,
            revision,
            approved_by=current_user.id,
            event_payload=event_payload,
        )
        if doc.active_processing_run_id:
            run = await self.repo.get_processing_run(doc.active_processing_run_id)
            if run:
                await self.repo.update_processing_run(
                    run,
                    status=ProcessingRunStatus.approved,
                )
        from app.features.extraction.repository import ExtractionRepository
        from app.features.extraction.models import ReviewStatus

        legacy = await ExtractionRepository(self.db).get_typed_version(doc.id)
        if legacy:
            legacy.status = ReviewStatus.accepted
            legacy.reviewed_by = current_user.id
            legacy.reviewed_at = _utcnow()
        await self.db.commit()
        return ApproveTypedRevisionResponse(
            revision=TypedRevisionResponse.model_validate(revision),
            event_id=event.id,
        )

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

            # Step 2: only an explicitly approved canonical revision is ready
            # for downstream Case KB and drafting workflows.
            typed_ok = doc.approved_typed_revision_id is not None
            if ocr_ok and not typed_ok:
                missing.append("typed_approval")
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
