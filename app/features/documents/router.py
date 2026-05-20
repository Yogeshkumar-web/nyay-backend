import uuid
import logging

from fastapi import APIRouter

from app.core.dependencies import CurrentUser, DB
from app.core.exceptions import AppError
from app.features.documents.models import UploadStatus
from app.features.documents.schemas import (
    ConfirmUploadRequest,
    PresignUploadRequest,
    SaveReviewRequest,
    UpdateDocumentRequest,
)
from app.features.documents.service import DocumentService

router = APIRouter(tags=["Documents"])
logger = logging.getLogger(__name__)


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
    try:
        doc = await service.confirm_upload(document_id, body, current_user)
        return {"success": True, "data": {"document": doc.model_dump()}}
    except AppError:
        raise
    except Exception:
        logger.exception(
            "Confirm upload failed after document %s was uploaded", document_id
        )
        await db.rollback()

        existing = await service.repo.get_by_id(document_id)
        if existing and existing.upload_status == UploadStatus.uploaded:
            doc = await service.get_document(document_id, current_user)
            return {"success": True, "data": {"document": doc.model_dump()}}

        raise


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


# ── Manual OCR trigger ────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/run-ocr",
    summary="Manually trigger OCR on a document",
)
async def run_ocr(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.run_ocr(document_id, current_user)
    return {"success": True, "data": result}


# ── Save review ────────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/skip-ocr",
    summary="Mark OCR as not required for a typed/digital document",
)
async def skip_ocr(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    doc = await service.skip_ocr(document_id, current_user)
    return {"success": True, "data": {"document": doc.model_dump()}}


@router.patch(
    "/documents/{document_id}/review",
    summary="Save lawyer-reviewed/edited OCR content",
)
async def save_review(
    document_id: uuid.UUID,
    body: SaveReviewRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    doc = await service.save_review(document_id, body, current_user)
    return {"success": True, "data": {"document": doc.model_dump()}}


# ── Push to context ────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/push-context",
    summary="Push reviewed document content into case context for drafting",
)
async def push_to_context(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.push_to_context(document_id, current_user)
    return {"success": True, "data": result}


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


# ── Typing Agent ──────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/run-typing",
    summary="Enqueue the LangGraph Typing Agent for this document",
)
async def run_typing(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    result = await service.run_typing(document_id, current_user)
    return {"success": True, "data": result}


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
