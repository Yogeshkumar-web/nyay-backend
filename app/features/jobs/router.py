import re
from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUser
from app.workers.job_store import get_job_status

router = APIRouter(prefix="/jobs", tags=["Jobs"])


# ============================================================
# VALIDATION
# ============================================================

JOB_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-]{10,100}$")


def _validate_job_id(job_id: str) -> None:
    if not JOB_ID_PATTERN.match(job_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job_id format",
        )


# ============================================================
# GET JOB STATUS
# ============================================================


@router.get(
    "/{job_id}",
    summary="Poll background job status",
    description="Poll OCR, extraction, or export job. Terminal states: completed | failed.",
)
async def get_job(job_id: str, current_user: CurrentUser):
    _validate_job_id(job_id)

    status_data = await get_job_status(job_id)

    if not status_data:
        # Could be expired or invalid
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or expired",
        )

    # Optional: future enhancement → ownership check
    # if status_data.get("user_id") != current_user.id:
    #     raise HTTPException(status_code=403, detail="Not authorized")

    return {
        "success": True,
        "data": {
            "status": status_data.get("status"),
            "result": status_data.get("result"),
            "error": status_data.get("error"),
            "updated_at": status_data.get("updated_at"),
        },
    }
