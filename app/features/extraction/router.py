import uuid
import logging

from fastapi import APIRouter

from app.core.dependencies import CurrentUser, DB
from app.features.extraction.schemas import ReviewExtractionRequest
from app.features.extraction.service import ExtractionService

router = APIRouter(tags=["Extraction"])

logger = logging.getLogger(__name__)


# ─────────────────────────────
# Extraction Results
# ─────────────────────────────


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

    logger.info(
        "extraction_fetch",
        extra={"user_id": str(current_user.id), "document_id": str(document_id)},
    )

    return {
        "success": True,
        "data": {"extraction_result": result.model_dump()},
    }

@router.post(
    "/documents/{document_id}/extraction/retry",
    summary="Manually trigger or re-run extraction",
)
async def retry_extraction(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)

    result = await service.trigger_extraction(document_id, current_user)

    logger.warning(
        "extraction_retry",
        extra={"user_id": str(current_user.id), "document_id": str(document_id)},
    )

    return {
        "success": True,
        "data": result,
    }


@router.post(
    "/documents/{document_id}/extraction/run",
    summary="Manually trigger extraction",
)
async def run_extraction(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)
    result = await service.trigger_extraction(document_id, current_user)
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

    result = await service.review_extraction(
        extraction_id,
        body,
        current_user,
    )

    logger.info(
        "extraction_review",
        extra={
            "user_id": str(current_user.id),
            "extraction_id": str(extraction_id),
        },
    )

    return {
        "success": True,
        "data": {"extraction_result": result.model_dump()},
    }
