import uuid
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.dependencies import CurrentUser, DB
from app.features.context.service import ContextService

router = APIRouter(tags=["Context"])

logger = logging.getLogger(__name__)

# ─────────────────────────────
# Get Context
# ─────────────────────────────
@router.get("/cases/{case_id}/context", summary="Get case context")
async def get_context(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)

    context = await service.get_context(case_id, current_user)

    logger.info(
        "context_fetch",
        extra={"user_id": str(current_user.id), "case_id": str(case_id)},
    )

    return {
        "success": True,
        "data": {
            "context": context.model_dump(),
            "version": context.version,
        },
    }


# ─────────────────────────────
# Update Context Document Content
# ─────────────────────────────


class UpdateContextDocumentRequest(BaseModel):
    html_content: str


@router.patch(
    "/cases/{case_id}/context/documents/{document_id}",
    summary="Update a document's HTML content in context (in-context editing)",
)
async def update_context_document(
    case_id: uuid.UUID,
    document_id: str,
    body: UpdateContextDocumentRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = ContextService(db)
    await service.update_document_content(
        case_id, document_id, body.html_content, current_user
    )
    return {"success": True, "data": {}}


# ─────────────────────────────
# Clear Context
# ─────────────────────────────
@router.post("/cases/{case_id}/context/clear", summary="Clear case context")
async def clear_context(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)

    context = await service.clear_context(case_id, current_user)

    logger.warning(
        "context_cleared",
        extra={"user_id": str(current_user.id), "case_id": str(case_id)},
    )

    return {
        "success": True,
        "data": {
            "context": context.model_dump(),
            "version": context.version,
        },
    }


# ─────────────────────────────
# Get Summary
# ─────────────────────────────
@router.get("/cases/{case_id}/summary", summary="Get case summary")
async def get_summary(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)

    summary = await service.get_summary(case_id, current_user)

    logger.info(
        "summary_fetch",
        extra={"user_id": str(current_user.id), "case_id": str(case_id)},
    )

    return {
        "success": True,
        "data": {
            "summary": summary.model_dump(),
        },
    }


# ─────────────────────────────
# Regenerate Summary
# ─────────────────────────────
@router.post("/cases/{case_id}/summary/regenerate", summary="Regenerate case summary")
async def regenerate_summary(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)

    summary = await service.regenerate_summary(case_id, current_user)

    logger.warning(
        "summary_regenerated",
        extra={"user_id": str(current_user.id), "case_id": str(case_id)},
    )

    return {
        "success": True,
        "data": {
            "summary": summary.model_dump(),
        },
    }
