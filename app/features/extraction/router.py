import uuid

from fastapi import APIRouter

from app.core.dependencies import CurrentUser, DB
from app.features.extraction.schemas import ReviewExtractionRequest, ReviewTypedVersionRequest
from app.features.extraction.service import ExtractionService

router = APIRouter(tags=["Extraction"])


# ── Extraction Results ─────────────────────────────────────────────────────────

@router.get(
    "/documents/{document_id}/extraction",
    summary="Get extraction result for a document",
)
async def get_extraction(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)
    result = await service.get_extraction(document_id, current_user)
    return {"success": True, "data": {"extraction_result": result.model_dump()}}


@router.post(
    "/documents/{document_id}/extraction/retry",
    summary="Retry failed extraction",
)
async def retry_extraction(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)
    result = await service.retry_extraction(document_id, current_user)
    return {"success": True, "data": result}


@router.patch(
    "/extraction/{extraction_id}",
    summary="Accept / edit / reject extraction result",
)
async def review_extraction(
    extraction_id: uuid.UUID,
    body: ReviewExtractionRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)
    result = await service.review_extraction(extraction_id, body, current_user)
    return {"success": True, "data": {"extraction_result": result.model_dump()}}


# ── Typed Versions ─────────────────────────────────────────────────────────────

@router.post(
    "/documents/{document_id}/type",
    status_code=201,
    summary="Trigger 'Type This Document' job",
)
async def trigger_typing(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)
    result = await service.trigger_typing(document_id, current_user)
    return {"success": True, "data": result}


@router.get(
    "/documents/{document_id}/typed",
    summary="Get typed version of a document",
)
async def get_typed_version(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)
    result = await service.get_typed_version(document_id, current_user)
    return {"success": True, "data": {"typed_version": result.model_dump()}}


@router.patch(
    "/typed/{document_id}",
    summary="Accept / edit / reject typed version",
)
async def review_typed_version(
    document_id: uuid.UUID,
    body: ReviewTypedVersionRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)
    result = await service.review_typed_version(document_id, body, current_user)
    return {"success": True, "data": {"typed_version": result.model_dump()}}