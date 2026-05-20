import logging
import uuid
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.dependencies import CurrentUser, DB
from app.core.exceptions import AppError
from app.features.courtroom.schemas import (
    CourtroomSessionResponse,
    CreateSessionRequest,
    SendMessageRequest,
)
from app.features.courtroom.service import CourtroomService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Courtroom Strategy"])


# ============================================================
# CREATE SESSION
# ============================================================


@router.post(
    "/cases/{case_id}/courtroom-sessions",
    status_code=201,
    summary="Start a new practice session",
)
async def create_session(
    case_id: uuid.UUID,
    body: CreateSessionRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CourtroomService(db)
    session = await service.create_session(
        case_id=case_id,
        lawyer_id=current_user.id,
        session_type=body.session_type,
        title=body.title,
    )
    return {
        "success": True,
        "data": {
            "session": CourtroomSessionResponse.model_validate(session).model_dump()
        },
    }


# ============================================================
# LIST SESSIONS
# ============================================================


@router.get("/cases/{case_id}/courtroom-sessions", summary="List sessions for case")
async def list_sessions(
    case_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
):
    service = CourtroomService(db)
    sessions = await service.get_sessions_for_case(
        case_id,
        current_user.id,
        page=page,
        limit=limit,
    )
    return {
        "success": True,
        "data": {
            "sessions": [
                CourtroomSessionResponse.model_validate(s).model_dump()
                for s in sessions
            ],
            "pagination": {"page": page, "limit": limit},
        },
    }


# ============================================================
# GET SESSION
# ============================================================


@router.get("/courtroom-sessions/{session_id}", summary="Get session detail")
async def get_session(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CourtroomService(db)
    session = await service.get_session(session_id, current_user.id)
    return {
        "success": True,
        "data": {
            "session": CourtroomSessionResponse.model_validate(session).model_dump()
        },
    }


# ============================================================
# SEND MESSAGE (SSE STREAM)
# ============================================================


@router.post(
    "/courtroom-sessions/{session_id}/message",
    summary="Send message and stream AI response",
)
async def send_message(
    session_id: uuid.UUID,
    body: SendMessageRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CourtroomService(db)

    async def event_generator():
        try:
            async for chunk in service.send_message_stream(
                session_id,
                body.content,
                current_user.id,
            ):
                yield chunk
        except AppError as e:
            yield f"event: error\ndata: {e.message}\n\n"
        except Exception as e:
            logger.error("Courtroom stream error: %s", e)
            yield "event: error\ndata: An error occurred\n\n"

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
# END SESSION
# ============================================================


@router.post(
    "/courtroom-sessions/{session_id}/end",
    summary="End session and generate weak-point analysis",
)
async def end_session(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CourtroomService(db)
    session = await service.end_session(session_id, current_user.id)
    return {
        "success": True,
        "data": {
            "session": CourtroomSessionResponse.model_validate(session).model_dump(),
            "weak_points": session.weak_points_identified,
        },
    }
