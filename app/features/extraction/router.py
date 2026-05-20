import uuid
import logging

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.dependencies import CurrentUser, DB
from app.features.extraction.schemas import (
    ReviewExtractionRequest,
    ReviewTypedVersionRequest,
    SaveTypedVersionRequest,
)
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


# ─────────────────────────────
# Typed Versions
# ─────────────────────────────


@router.post(
    "/documents/{document_id}/type",
    status_code=status.HTTP_201_CREATED,
    summary="Trigger 'Type This Document' job",
)
async def trigger_typing(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExtractionService(db)

    result = await service.trigger_typing(document_id, current_user)

    logger.info(
        "typing_trigger",
        extra={"user_id": str(current_user.id), "document_id": str(document_id)},
    )

    return {
        "success": True,
        "data": result,
    }


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

    logger.info(
        "typed_fetch",
        extra={"user_id": str(current_user.id), "document_id": str(document_id)},
    )

    return {
        "success": True,
        "data": {"typed_version": result.model_dump()},
    }


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

    result = await service.review_typed_version(
        document_id,
        body,
        current_user,
    )

    logger.info(
        "typed_review",
        extra={"user_id": str(current_user.id), "document_id": str(document_id)},
    )

    return {
        "success": True,
        "data": {"typed_version": result.model_dump()},
    }


@router.patch(
    "/documents/{document_id}/typed-version",
    summary="Save Tiptap editor content (auto-save and manual save)",
)
async def save_typed_version(
    document_id: uuid.UUID,
    body: SaveTypedVersionRequest,
    current_user: CurrentUser,
    db: DB,
):
    """
    Called by the Tiptap editor on every debounced auto-save and on manual Save click.
    Always sets status = 'edited'. Also marks Document.review_status = 'reviewed'.
    """
    service = ExtractionService(db)

    result = await service.save_typed_version(document_id, body, current_user)

    logger.info(
        "typed_save",
        extra={
            "user_id": str(current_user.id),
            "document_id": str(document_id),
            "content_length": len(body.typed_content),
        },
    )

    return {
        "success": True,
        "data": {"typed_version": result.model_dump()},
    }


class SavePushContextRequest(BaseModel):
    html_content: str


@router.post(
    "/documents/{document_id}/run-ai-extraction",
    summary="Stream AI-generated structured HTML extraction (SSE)",
)
async def run_ai_extraction(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    """
    SSE stream: AI reads the document content and produces structured HTML.
    - Uses TypedVersion content → OCR text → PDF direct extraction (priority order).
    - Saves the result as TypedVersion when done.
    - Client reads chunked SSE: 'data: <html_chunk>\\n\\n' then 'data: [DONE]\\n\\n'.
    """
    service = ExtractionService(db)

    async def generate():
        async for chunk in service.run_ai_extraction_stream(document_id, current_user):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/documents/{document_id}/save-push-context",
    summary="Save HTML content as TypedVersion AND push to case context atomically",
)
async def save_push_context(
    document_id: uuid.UUID,
    body: SavePushContextRequest,
    current_user: CurrentUser,
    db: DB,
):
    """
    Saves html_content as the TypedVersion, then pushes it to context_json.
    Replaces the two-step save-then-push workflow with a single action.
    """
    service = ExtractionService(db)
    result = await service.save_and_push_to_context(
        document_id, body.html_content, current_user
    )
    return {"success": True, "data": result}


@router.post(
    "/documents/{document_id}/typed-version/accept",
    summary="Mark typed version as final (editor becomes read-only)",
)
async def accept_typed_version(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    """
    Called when user clicks 'Mark as Final'. Content is not changed.
    Sets status = 'accepted'. Frontend should make editor read-only after this.
    Can be reversed by saving again (which sets status back to 'edited').
    """
    service = ExtractionService(db)

    result = await service.finalize_typed_version(document_id, current_user)

    logger.info(
        "typed_accept",
        extra={"user_id": str(current_user.id), "document_id": str(document_id)},
    )

    return {
        "success": True,
        "data": {"typed_version": result.model_dump()},
    }
