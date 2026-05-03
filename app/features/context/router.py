import uuid

from fastapi import APIRouter

from app.core.dependencies import CurrentUser, DB
from app.features.context.schemas import PushToContextRequest
from app.features.context.service import ContextService

router = APIRouter(tags=["Context"])


@router.get("/cases/{case_id}/context", summary="Get case context")
async def get_context(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)
    context = await service.get_context(case_id, current_user)
    return {"success": True, "data": {"context": context.model_dump()}}


@router.post("/cases/{case_id}/context/push", summary="Push extractions to context")
async def push_to_context(
    case_id: uuid.UUID, body: PushToContextRequest, current_user: CurrentUser, db: DB
):
    service = ContextService(db)
    context, summary = await service.push_to_context(case_id, body.document_ids, current_user)
    return {"success": True, "data": {"context": context.model_dump(), "summary": summary.model_dump()}}


@router.post("/cases/{case_id}/context/clear", summary="Clear case context")
async def clear_context(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)
    context = await service.clear_context(case_id, current_user)
    return {"success": True, "data": {"context": context.model_dump()}}


@router.get("/cases/{case_id}/summary", summary="Get case summary")
async def get_summary(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)
    summary = await service.get_summary(case_id, current_user)
    return {"success": True, "data": {"summary": summary.model_dump()}}


@router.post("/cases/{case_id}/summary/regenerate", summary="Regenerate case summary")
async def regenerate_summary(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = ContextService(db)
    summary = await service.regenerate_summary(case_id, current_user)
    return {"success": True, "data": {"summary": summary.model_dump()}}