import uuid

from fastapi import APIRouter

from app.core.dependencies import CurrentUser, DB
from app.features.documents.schemas import (
    ConfirmUploadRequest,
    PresignUploadRequest,
    UpdateDocumentRequest,
)
from app.features.documents.service import DocumentService

router = APIRouter(tags=["Documents"])


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
    summary="Confirm upload complete → triggers OCR job if scanned",
)
async def confirm_upload(
    document_id: uuid.UUID,
    body: ConfirmUploadRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = DocumentService(db)
    doc = await service.confirm_upload(document_id, body, current_user)
    return {"success": True, "data": {"document": doc.model_dump()}}


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