import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.dependencies import CurrentUser, DB
from app.features.drafts.schemas import CreateDraftRequest, UpdateDraftRequest
from app.features.drafts.service import DraftService

router = APIRouter(tags=["Drafts"])


@router.post("/cases/{case_id}/drafts", status_code=201, summary="Create draft")
async def create_draft(
    case_id: uuid.UUID, body: CreateDraftRequest, current_user: CurrentUser, db: DB
):
    service = DraftService(db)
    draft = await service.create_draft(case_id, body, current_user)
    return {"success": True, "data": {"draft": draft.model_dump()}}


@router.get("/cases/{case_id}/drafts", summary="List drafts for case")
async def list_drafts(case_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = DraftService(db)
    drafts = await service.list_drafts(case_id, current_user)
    return {"success": True, "data": {"drafts": [d.model_dump() for d in drafts]}}


@router.get("/drafts/{draft_id}", summary="Get draft")
async def get_draft(draft_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = DraftService(db)
    draft = await service.get_draft(draft_id, current_user)
    return {"success": True, "data": {"draft": draft.model_dump()}}


@router.get("/drafts/{draft_id}/stream", summary="Stream draft generation (SSE)")
async def stream_draft(draft_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = DraftService(db)
    return StreamingResponse(
        service.stream_draft(draft_id, current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/drafts/{draft_id}", summary="Update draft content")
async def update_draft(
    draft_id: uuid.UUID, body: UpdateDraftRequest, current_user: CurrentUser, db: DB
):
    service = DraftService(db)
    draft = await service.update_draft(draft_id, body, current_user)
    return {"success": True, "data": {"draft": draft.model_dump()}}


@router.post("/drafts/{draft_id}/version", status_code=201, summary="Fork draft as new version")
async def fork_draft(draft_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = DraftService(db)
    draft = await service.fork_draft(draft_id, current_user)
    return {"success": True, "data": {"draft": draft.model_dump()}}


@router.delete("/drafts/{draft_id}", summary="Archive draft")
async def archive_draft(draft_id: uuid.UUID, current_user: CurrentUser, db: DB):
    service = DraftService(db)
    await service.archive_draft(draft_id, current_user)
    return {"success": True, "data": {}}