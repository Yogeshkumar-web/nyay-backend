import uuid
import logging
import asyncio
import hashlib

from fastapi import APIRouter
from fastapi.responses import Response

from app.core.dependencies import CurrentUser, DB
from app.core.exceptions import ValidationError
from app.features.drafts.export import generate_reviewed_document_docx
from app.features.documents.schemas import (
    ConfirmUploadRequest,
    PresignUploadRequest,
    SaveTypedRevisionRequest,
    UpdateDocumentRequest,
)
from app.features.documents.service import DocumentService

router = APIRouter(tags=["Documents"])
logger = logging.getLogger(__name__)


def _require_reviewed_content_for_docx(content: str | None) -> str:
    if not content or not content.strip():
        raise ValidationError("Approve typed text before downloading DOCX.")
    return content


# ── Presign ────────────────────────────────────────────────────────────────────


@router.post(
    "/cases/{case_id}/documents/presign",
    status_code=201,
    summary="Get R2 presigned upload URL",
)
async def presign_upload(
    case_id: uuid.UUID,
    body: PresignUploadRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.presign_upload(case_id, body, current_user)
    return {"success": True, "data": result.model_dump()}


# ── Confirm ────────────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/confirm-upload",
    summary="Confirm upload complete",
)
async def confirm_upload(
    document_id: uuid.UUID,
    body: ConfirmUploadRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.confirm_upload(document_id, body, current_user)
    return {"success": True, "data": result.model_dump()}


# ── List ───────────────────────────────────────────────────────────────────────


@router.get(
    "/cases/{case_id}/documents",
    summary="List all documents for a case",
)
async def list_documents(
    case_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    docs = await service.list_documents(case_id, current_user)
    return {"success": True, "data": {"documents": [d.model_dump() for d in docs]}}


@router.get(
    "/cases/{case_id}/documents/readiness",
    summary="Check readiness of all documents in a case",
)
async def get_documents_readiness(
    case_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.get_documents_readiness(case_id, current_user)
    return {"success": True, "data": result}


# ── Detail ─────────────────────────────────────────────────────────────────────


@router.get(
    "/documents/{document_id}",
    summary="Get document detail",
)
async def get_document(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    doc = await service.get_document(document_id, current_user)
    return {"success": True, "data": {"document": doc.model_dump()}}


# ── Update metadata ────────────────────────────────────────────────────────────


@router.patch(
    "/documents/{document_id}",
    summary="Update document metadata (type, display name)",
)
async def update_document(
    document_id: uuid.UUID,
    body: UpdateDocumentRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    doc = await service.update_document(document_id, body, current_user)
    return {"success": True, "data": {"document": doc.model_dump()}}


@router.post(
    "/documents/{document_id}/processing-runs",
    summary="Create a full document reprocessing run",
)
async def create_processing_run(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.run_ocr(document_id, current_user)
    return {"success": True, "data": result}


@router.get(
    "/documents/{document_id}/processing-progress",
    summary="Get active document processing progress",
)
async def get_processing_progress(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    result = await DocumentService(db).get_processing_progress(document_id, current_user)
    return {"success": True, "data": {"processing_run": result.model_dump()}}


@router.get(
    "/documents/{document_id}/processing-runs/{run_id}",
    summary="Get one document processing run and page progress",
)
async def get_processing_run(
    document_id: uuid.UUID,
    run_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    result = await DocumentService(db).get_processing_progress(
        document_id, current_user, run_id
    )
    return {"success": True, "data": {"processing_run": result.model_dump()}}


@router.post(
    "/documents/{document_id}/pages/{page_number}/retry",
    summary="Retry a failed document page in a new auditable run",
)
async def retry_failed_page(
    document_id: uuid.UUID,
    page_number: int,
    current_user: CurrentUser,
    db: DB,
):
    result = await DocumentService(db).retry_page(
        document_id, page_number, current_user
    )
    return {"success": True, "data": result}


@router.get(
    "/documents/{document_id}/typed-version",
    summary="Get the canonical typed document revision",
)
async def get_typed_revision(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    result = await DocumentService(db).get_typed_revision(document_id, current_user)
    return {"success": True, "data": {"typed_revision": result.model_dump()}}


@router.patch(
    "/documents/{document_id}/typed-version",
    summary="Save the canonical typed document revision",
)
async def save_typed_revision(
    document_id: uuid.UUID,
    body: SaveTypedRevisionRequest,
    current_user: CurrentUser,
    db: DB,
):
    result = await DocumentService(db).save_typed_revision(
        document_id, body, current_user
    )
    return {"success": True, "data": {"typed_revision": result.model_dump()}}


@router.post(
    "/documents/{document_id}/typed-version/approve",
    summary="Approve the canonical typed document revision",
)
async def approve_typed_revision(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    result = await DocumentService(db).approve_typed_revision(document_id, current_user)
    return {"success": True, "data": result.model_dump()}


# ── Code-generated download ───────────────────────────────────────────────────


@router.get(
    "/documents/{document_id}/download/docx",
    summary="Download reviewed document text as code-formatted DOCX",
)
async def download_reviewed_document_docx(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    doc = await service.get_document(document_id, current_user)
    persisted_doc = await service.repo.get_by_id(document_id)
    if persisted_doc is None:
        raise ValidationError("Document not found.")
    approved_revision = await service.repo.get_approved_typed_revision(persisted_doc)
    if approved_revision is not None:
        await service.ensure_canonical_structure(persisted_doc, approved_revision)
    content = _require_reviewed_content_for_docx(
        approved_revision.content_markdown if approved_revision else None
    )

    title = doc.display_name or doc.original_filename
    safe_title = title.replace(" ", "_").replace("/", "_")[:80]
    structured = (doc.source_artifact or {}).get("structured_extraction")
    file_bytes = await asyncio.to_thread(
        generate_reviewed_document_docx,
        content,
        title,
        structured_extraction=structured,
        canonical_document=approved_revision.canonical_structure,
    )
    checksum = hashlib.sha256(file_bytes).hexdigest()
    await service.repo.record_docx_export(
        doc,
        exported_by=current_user.id,
        filename=f"{safe_title}.docx",
        size_bytes=len(file_bytes),
        checksum_sha256=checksum,
        metadata={
            "source": "document_review_download",
            "review_status": doc.review_status.value,
            "approved_revision_id": str(approved_revision.id),
            "approved_content_hash": approved_revision.content_hash,
            "has_structured_extraction": structured is not None,
            "canonical_schema_version": approved_revision.canonical_schema_version,
            "reviewed_content_chars": len(content),
        },
        approved_revision_id=approved_revision.id,
    )
    await db.commit()
    return Response(
        content=file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.docx"'},
    )


# ── Presigned view URL ─────────────────────────────────────────────────────────


@router.get(
    "/documents/{document_id}/view-url",
    summary="Get presigned URL to view/download document (15 min expiry)",
)
async def get_view_url(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.get_view_url(document_id, current_user)
    return {"success": True, "data": result.model_dump()}


# ── Delete ─────────────────────────────────────────────────────────────────────


@router.delete(
    "/documents/{document_id}",
    summary="Delete document from DB + R2",
)
async def delete_document(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    await service.delete_document(document_id, current_user)
    return {"success": True, "data": {}}
