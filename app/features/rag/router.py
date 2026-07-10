from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, Query, UploadFile

from app.core.dependencies import DB, LawyerUser
from app.core.exceptions import ValidationError
from app.features.documents.digital_extractor import DOCX_MIME, PDF_MIME
from app.features.rag.repository import RagRepository
from app.features.rag.schemas import (
    CitationVerificationRequest,
    DraftAssemblyRequest,
    RagGenerateSectionRequest,
    RagRetrieveRequest,
    VerifiedCitationCreate,
)
from app.features.rag.service import RagService

router = APIRouter(prefix="/rag", tags=["RAG"])

RAG_INGEST_MIME_TYPES = {
    PDF_MIME,
    DOCX_MIME,
    "image/jpeg",
    "image/png",
}


@router.post("/knowledge-base/ingest", status_code=202)
async def ingest_private_knowledge_base_file(
    current_user: LawyerUser,
    db: DB,
    file: UploadFile = File(...),
    case_id: uuid.UUID | None = Form(default=None),
):
    mime_type = file.content_type or ""
    if mime_type not in RAG_INGEST_MIME_TYPES:
        raise ValidationError("Unsupported knowledge base document type.")

    file_bytes = await file.read()
    service = RagService(RagRepository(db))
    result = await service.ingest_knowledge_base_file(
        lawyer_id=current_user.id,
        case_id=case_id,
        file_bytes=file_bytes,
        mime_type=mime_type,
        original_filename=file.filename,
    )
    return {
        "success": True,
        "data": {
            "document_id": result.document.id,
            "duplicate": result.duplicate,
            "chunk_count": result.chunk_count,
            "processing_status": result.document.processing_status,
        },
    }


@router.post("/retrieve")
async def retrieve_private_context(
    body: RagRetrieveRequest,
    current_user: LawyerUser,
    db: DB,
):
    service = RagService(RagRepository(db))
    result = await service.retrieve(lawyer_id=current_user.id, request=body)
    return {"success": True, "data": result.model_dump()}


@router.post("/generate-section")
async def generate_section_from_private_context(
    body: RagGenerateSectionRequest,
    current_user: LawyerUser,
    db: DB,
):
    service = RagService(RagRepository(db))
    result = await service.generate_section(lawyer_id=current_user.id, request=body)
    return {"success": True, "data": result.model_dump()}


@router.post("/verified-citations", status_code=201)
async def create_verified_citation(
    body: VerifiedCitationCreate,
    current_user: LawyerUser,
    db: DB,
):
    service = RagService(RagRepository(db))
    result = await service.create_verified_citation(
        lawyer_id=current_user.id,
        request=body,
    )
    return {"success": True, "data": result.model_dump()}


@router.get("/verified-citations")
async def list_verified_citations(
    current_user: LawyerUser,
    db: DB,
    limit: int = Query(default=100, ge=1, le=500),
):
    service = RagService(RagRepository(db))
    result = await service.list_verified_citations(
        lawyer_id=current_user.id,
        limit=limit,
    )
    return {"success": True, "data": [item.model_dump() for item in result]}


@router.post("/citations/verify")
async def verify_citations(
    body: CitationVerificationRequest,
    current_user: LawyerUser,
    db: DB,
):
    service = RagService(RagRepository(db))
    result = await service.verify_citations(lawyer_id=current_user.id, request=body)
    return {"success": True, "data": result.model_dump()}


@router.post("/assemble-draft")
async def assemble_draft(
    body: DraftAssemblyRequest,
    current_user: LawyerUser,
    db: DB,
):
    service = RagService(RagRepository(db))
    result = await service.assemble_draft(request=body)
    return {"success": True, "data": result.model_dump()}


@router.get("/observability/events")
async def list_observability_events(
    current_user: LawyerUser,
    db: DB,
    case_id: uuid.UUID | None = Query(default=None),
    draft_id: uuid.UUID | None = Query(default=None),
    event_type: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
):
    service = RagService(RagRepository(db))
    result = await service.list_observability_events(
        lawyer_id=current_user.id,
        case_id=case_id,
        draft_id=draft_id,
        event_type=event_type,
        limit=limit,
    )
    return {"success": True, "data": [item.model_dump() for item in result]}


@router.get("/observability/drafts/{draft_id}/trace")
async def get_draft_trace(
    draft_id: uuid.UUID,
    current_user: LawyerUser,
    db: DB,
):
    service = RagService(RagRepository(db))
    result = await service.get_draft_trace(
        lawyer_id=current_user.id,
        draft_id=draft_id,
    )
    return {"success": True, "data": result.model_dump()}
