"""
Redis-based job status store.
No DB table — lightweight key/value with TTL.

Schema per job:
{
  "status": "pending" | "processing" | "completed" | "failed",
  "result": { ... } | null,
  "error": "message" | null,
  "updated_at": "ISO string"
}
"""
import enum
import json
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings

JOB_TTL_SECONDS = 86400  # 24 hours


class JobStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


def _redis_key(job_id: str) -> str:
    return f"job:{job_id}"


async def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def set_job_status(
    job_id: str,
    status: JobStatus,
    *,
    result: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    r = await _get_redis()
    payload = {
        "status": status.value,
        "result": result,
        "error": error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await r.setex(_redis_key(job_id), JOB_TTL_SECONDS, json.dumps(payload))
    await r.aclose()


async def get_job_status(job_id: str) -> Optional[dict]:
    r = await _get_redis()
    raw = await r.get(_redis_key(job_id))
    await r.aclose()
    if not raw:
        return None
    return json.loads(raw)
