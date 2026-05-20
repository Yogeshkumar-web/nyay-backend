import uuid
import logging
from typing import Annotated, Optional

from fastapi import Cookie, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import decode_access_token
from app.core.exceptions import (
    AuthRequiredError,
    AuthInvalidError,
    ForbiddenError,
    NotFoundError,
)
from app.features.users.models import User, UserRole
from app.features.users.repository import UserRepository
from app.features.cases.repository import CaseRepository

# Optional Redis (fail-safe: works even if Redis is down)
try:
    import redis.asyncio as aioredis
    from app.core.config import settings

    redis_client = aioredis.from_url(settings.REDIS_URL)
except Exception:
    redis_client = None

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Helper: Cache Layer (non-breaking, optional)
# ─────────────────────────────────────────────────────────────
async def _get_cached_user(user_id: uuid.UUID) -> Optional[User]:
    if not redis_client:
        return None

    try:
        data = await redis_client.get(f"user:{user_id}")
        if data:
            # NOTE: simple approach — you can serialize JSON later
            return None  # skip deserialization for now (safe fallback)
    except Exception as e:
        logger.warning(f"Redis get error: {e}")

    return None


async def _set_cached_user(user: User) -> None:
    if not redis_client:
        return

    try:
        # NOTE: lightweight cache (ID only for now)
        await redis_client.set(
            f"user:{user.id}",
            str(user.id),
            ex=60,  # 60 seconds TTL
        )
    except Exception as e:
        logger.warning(f"Redis set error: {e}")


# ─────────────────────────────────────────────────────────────
# Core Dependency: Get Current User
# ─────────────────────────────────────────────────────────────
async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Cookie()] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract token → validate → fetch user (with caching).
    """

    token: Optional[str] = None

    # Header priority
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")

    # Cookie fallback
    elif access_token:
        token = access_token

    if not token:
        raise AuthRequiredError()

    payload = decode_access_token(token)
    if not payload:
        logger.warning("Invalid access token")
        raise AuthInvalidError()

    user_id_str = payload.get("sub")
    if not user_id_str:
        logger.warning("Missing user_id in token")
        raise AuthInvalidError()

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        logger.warning("Invalid UUID in token")
        raise AuthInvalidError()

    # ── Try cache first ─────────────────────────
    user = await _get_cached_user(user_id)

    # ── Fallback to DB ─────────────────────────
    if not user:
        repo = UserRepository(db)
        user = await repo.get_by_id(user_id)

        if not user:
            logger.warning(f"User not found: {user_id}")
            raise AuthInvalidError("User no longer exists")

        await _set_cached_user(user)

    # ── Business checks ────────────────────────
    if not user.is_active:
        logger.warning(f"Inactive user access attempt: {user_id}")
        raise ForbiddenError("Account is deactivated")

    return user


# ─────────────────────────────────────────────────────────────
# Role-based Dependency
# ─────────────────────────────────────────────────────────────
async def require_lawyer(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in (UserRole.lawyer, UserRole.admin):
        raise ForbiddenError("Lawyer account required")
    return current_user


# ─────────────────────────────────────────────────────────────
# Case Access Dependency
# ─────────────────────────────────────────────────────────────
async def get_case_with_access(
    case_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CaseRepository(db)

    has_access = await repo.can_access(case_id, current_user.id)
    if not has_access:
        raise NotFoundError("Case not found")

    case = await repo.get_by_id_simple(case_id)
    if not case:
        raise NotFoundError("Case not found")

    return case


# ─────────────────────────────────────────────────────────────
# Typed Aliases (Clean Routes)
# ─────────────────────────────────────────────────────────────
CurrentUser = Annotated[User, Depends(get_current_user)]
LawyerUser = Annotated[User, Depends(require_lawyer)]
DB = Annotated[AsyncSession, Depends(get_db)]

# import uuid
# from typing import Annotated

# from fastapi import Cookie, Depends, Header
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.db.session import get_db
# from app.core.security import decode_access_token
# from app.core.exceptions import AuthRequiredError, AuthInvalidError, ForbiddenError, NotFoundError
# from app.features.users.models import User, UserRole
# from app.features.users.repository import UserRepository
# from app.features.cases.repository import CaseRepository


# async def get_current_user(
#     authorization: Annotated[str | None, Header()] = None,
#     access_token: Annotated[str | None, Cookie()] = None,
#     db: AsyncSession = Depends(get_db),
# ) -> User:
#     """
#     Extracts the access token from one of two places (in priority order):
#       1. Authorization: Bearer <token>  header  (API / Swagger clients)
#       2. access_token HttpOnly cookie           (browser clients)
#     """
#     token: str | None = None

#     # Priority 1 — Bearer header
#     if authorization and authorization.startswith("Bearer "):
#         token = authorization.removeprefix("Bearer ")

#     # Priority 2 — HttpOnly cookie
#     elif access_token:
#         token = access_token

#     if not token:
#         raise AuthRequiredError()

#     payload = decode_access_token(token)
#     if not payload:
#         raise AuthInvalidError()

#     user_id_str = payload.get("sub")
#     if not user_id_str:
#         raise AuthInvalidError()

#     try:
#         user_id = uuid.UUID(user_id_str)
#     except ValueError:
#         raise AuthInvalidError()

#     repo = UserRepository(db)
#     user = await repo.get_by_id(user_id)
#     if not user:
#         raise AuthInvalidError("User no longer exists")
#     if not user.is_active:
#         raise ForbiddenError("Account is deactivated")

#     return user


# async def require_lawyer(
#     current_user: User = Depends(get_current_user),
# ) -> User:
#     if current_user.role not in (UserRole.lawyer, UserRole.admin):
#         raise ForbiddenError("Lawyer account required")
#     return current_user


# async def get_case_with_access(
#     case_id: uuid.UUID,
#     current_user: User = Depends(get_current_user),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Validates the current user has read access to the requested case.
#     Injects the case object into the route handler.
#     """
#     repo = CaseRepository(db)

#     if not await repo.can_access(case_id, current_user.id):
#         raise NotFoundError("Case not found")

#     case = await repo.get_by_id_simple(case_id)
#     if not case:
#         raise NotFoundError("Case not found")

#     return case


# # ── Typed aliases for cleaner route signatures ────────────────────────────────
# CurrentUser = Annotated[User, Depends(get_current_user)]
# LawyerUser = Annotated[User, Depends(require_lawyer)]
# DB = Annotated[AsyncSession, Depends(get_db)]
