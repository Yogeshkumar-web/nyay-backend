import time
import logging
import hashlib
from typing import Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.security import decode_access_token

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
RATE_LIMITED_PREFIXES = [
    "/api/v1/cases/",  # drafts
    "/api/v1/courtroom-sessions/",  # chat
    "/api/v1/documents/",  # extraction retry
]

MAX_AI_CALLS_PER_HOUR = 30
WINDOW_SECONDS = 3600


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _is_rate_limited_path(path: str) -> bool:
    """
    Safer prefix-based matching.
    """
    return any(path.startswith(prefix) for prefix in RATE_LIMITED_PREFIXES)


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ")
    return None


def _get_user_identifier(token: str) -> str:
    """
    Prefer user_id from JWT.
    Fallback to hashed token.
    """
    payload = decode_access_token(token)
    if payload and payload.get("sub"):
        return f"user:{payload['sub']}"

    # fallback (should rarely happen)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return f"token:{token_hash}"


# ─────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.redis = None

    async def _get_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(settings.REDIS_URL)
        return self.redis

    async def dispatch(self, request: Request, call_next):
        # Only POST requests (AI-heavy endpoints)
        if request.method != "POST":
            return await call_next(request)

        if not _is_rate_limited_path(request.url.path):
            return await call_next(request)

        token = _extract_token(request)
        if not token:
            return await call_next(request)

        user_key = _get_user_identifier(token)

        # rolling window bucket
        current_window = int(time.time() // WINDOW_SECONDS)
        redis_key = f"rate_limit:{user_key}:{current_window}"

        try:
            r = await self._get_redis()

            current = await r.incr(redis_key)

            if current == 1:
                await r.expire(redis_key, WINDOW_SECONDS)

            if current > MAX_AI_CALLS_PER_HOUR:
                logger.warning(
                    f"Rate limit exceeded for {user_key} on {request.url.path}"
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Max {MAX_AI_CALLS_PER_HOUR} AI calls/hour.",
                )

        except HTTPException:
            raise

        except Exception as e:
            # Fail open (never block users if Redis fails)
            logger.error(f"Rate limiter error: {e}")

        return await call_next(request)
