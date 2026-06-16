import asyncio
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, Response

from app.core.dependencies import CurrentUser, DB
from app.features.drafts.schemas import CreateDraftRequest, UpdateDraftRequest
from app.features.drafts.service import DraftService
from app.features.drafts.export import generate_pdf, generate_docx

router = APIRouter(tags=["Drafts"])


# ============================================================
# CREATE DRAFT
# ============================================================


@router.post("/cases/{case_id}/drafts", status_code=201, summary="Create draft")
async def create_draft(
    case_id: uuid.UUID,
    body: CreateDraftRequest,
    current_user: CurrentUser,
    db: DB,
):
    try:
        service = DraftService(db)
        draft = await service.create_draft(case_id, body, current_user)

        return {
            "success": True,
            "data": {"draft": draft.model_dump()},
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# LIST DRAFTS
# ============================================================


@router.get("/cases/{case_id}/drafts", summary="List drafts for case")
async def list_drafts(
    case_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    try:
        service = DraftService(db)
        drafts = await service.list_drafts(case_id, current_user)

        return {
            "success": True,
            "data": {"drafts": [d.model_dump() for d in drafts]},
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# GET DRAFT
# ============================================================


@router.get("/drafts/{draft_id}", summary="Get draft")
async def get_draft(
    draft_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DraftService(db)
    draft = await service.get_draft(draft_id, current_user)

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    return {
        "success": True,
        "data": {"draft": draft.model_dump()},
    }


# ============================================================
# STREAM DRAFT (SSE)
# ============================================================


@router.get("/drafts/{draft_id}/stream", summary="Stream draft generation (SSE)")
async def stream_draft(
    draft_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = DraftService(db)

    async def event_generator():
        try:
            async for chunk in service.stream_draft(draft_id, current_user):
                yield chunk

        except Exception as e:
            # Ensure SSE client gets structured error event
            yield f"event: error\ndata: {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# UPDATE DRAFT
# ============================================================


@router.patch("/drafts/{draft_id}", summary="Update draft content")
async def update_draft(
    draft_id: uuid.UUID,
    body: UpdateDraftRequest,
    current_user: CurrentUser,
    db: DB,
):
    try:
        service = DraftService(db)
        draft = await service.update_draft(draft_id, body, current_user)

        return {
            "success": True,
            "data": {"draft": draft.model_dump()},
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# FORK DRAFT
# ============================================================


@router.post(
    "/drafts/{draft_id}/version",
    status_code=201,
    summary="Fork draft as new version",
)
async def fork_draft(
    draft_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    try:
        service = DraftService(db)
        draft = await service.fork_draft(draft_id, current_user)

        return {
            "success": True,
            "data": {"draft": draft.model_dump()},
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# ARCHIVE DRAFT
# ============================================================


@router.delete("/drafts/{draft_id}", summary="Archive draft")
async def archive_draft(
    draft_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    try:
        service = DraftService(db)
        await service.archive_draft(draft_id, current_user)

        return {"success": True, "data": {}}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# DIRECT DOWNLOAD (PDF / DOCX) — synchronous, no Celery
# ============================================================


@router.get(
    "/drafts/{draft_id}/download/{fmt}", summary="Download draft as PDF or DOCX"
)
async def download_draft(
    draft_id: uuid.UUID,
    fmt: str,
    current_user: CurrentUser,
    db: DB,
):
    if fmt not in ("pdf", "docx"):
        raise HTTPException(status_code=400, detail="Format must be pdf or docx")

    service = DraftService(db)
    draft = await service.get_draft(draft_id, current_user)

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    content = draft.content or ""
    safe_title = draft.title.replace(" ", "_")[:80]

    if fmt == "pdf":
        file_bytes = await asyncio.to_thread(generate_pdf, content, draft.title)
        return Response(
            content=file_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
        )
    else:
        file_bytes = await asyncio.to_thread(generate_docx, content, draft.title)
        return Response(
            content=file_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_title}.docx"'
            },
        )
