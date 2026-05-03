from fastapi import APIRouter

from app.core.dependencies import CurrentUser
from app.core.exceptions import NotFoundError
from app.workers.job_store import get_job_status

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get(
    "/{job_id}",
    summary="Poll background job status",
    description="Poll OCR, extraction, or export job. Terminal states: completed | failed.",
)
async def get_job(job_id: str, current_user: CurrentUser):
    status = await get_job_status(job_id)
    if not status:
        raise NotFoundError("Job not found or expired")
    return {"success": True, "data": status}