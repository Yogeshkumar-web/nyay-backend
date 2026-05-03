import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import decode_access_token
from app.core.exceptions import AuthRequiredError, AuthInvalidError, ForbiddenError, NotFoundError
from app.features.users.models import User
from app.features.users.repository import UserRepository
from app.features.cases.repository import CaseRepository


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthRequiredError()

    token = authorization.removeprefix("Bearer ")
    payload = decode_access_token(token)
    if not payload:
        raise AuthInvalidError()

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise AuthInvalidError()

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise AuthInvalidError()

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if not user:
        raise AuthInvalidError("User no longer exists")
    if not user.is_active:
        raise ForbiddenError("Account is deactivated")

    return user


async def require_lawyer(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in ("lawyer", "admin"):
        raise ForbiddenError("Lawyer account required")
    return current_user


async def get_case_with_access(
    case_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dependency that validates the current user has read access to the case.
    Injects the case object into the route handler.
    """
    from app.features.cases.models import Case
    repo = CaseRepository(db)

    if not await repo.can_access(case_id, current_user.id):
        raise NotFoundError("Case not found")

    case = await repo.get_by_id_simple(case_id)
    if not case:
        raise NotFoundError("Case not found")

    return case


# Typed aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
LawyerUser = Annotated[User, Depends(require_lawyer)]
DB = Annotated[AsyncSession, Depends(get_db)]