import uuid
from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUser, DB
from app.features.exports.service import ExportService
from app.features.drafts.schemas import DraftExportResponse, TriggerExportRequest

router = APIRouter(tags=["Exports"])


# ============================================================
# TRIGGER EXPORT
# ============================================================


@router.post(
    "/drafts/{draft_id}/export",
    status_code=202,
    summary="Trigger export generation",
)
async def trigger_export(
    draft_id: uuid.UUID,
    body: TriggerExportRequest,
    current_user: CurrentUser,
    db: DB,
):
    try:
        service = ExportService(db)
        result = await service.trigger_export(
            draft_id,
            body.format,
            current_user,
        )

        return {
            "success": True,
            "data": result,  # expected: { export_id, job_id }
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# GET EXPORT METADATA
# ============================================================


@router.get(
    "/exports/{export_id}",
    summary="Get export details",
)
async def get_export(
    export_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = ExportService(db)

    export = await service.get_export(export_id, current_user)

    if not export:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Export not found",
        )

    return {
        "success": True,
        "data": {"export": DraftExportResponse.model_validate(export).model_dump()},
    }


# ============================================================
# GET DOWNLOAD URL
# ============================================================


@router.get(
    "/exports/{export_id}/download",
    summary="Get presigned download URL",
)
async def get_export_download(
    export_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    try:
        service = ExportService(db)
        data = await service.get_presigned_url(export_id, current_user)

        # Defensive check (important for expired exports)
        if not data or "url" not in data:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Export expired or unavailable",
            )

        return {
            "success": True,
            "data": data,  # { url, expires_at }
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
