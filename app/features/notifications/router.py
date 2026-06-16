import uuid
from fastapi import APIRouter, Depends, Path, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.dependencies import get_current_user
from app.features.users.models import User
from app.features.notifications.schemas import NotificationResponse
from app.features.notifications.service import NotificationService

router = APIRouter(tags=["Notifications"])


# ============================================================
# GET NOTIFICATIONS (WITH PAGINATION)
# ============================================================


@router.get("/notifications", summary="Get notifications")
async def get_notifications(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        service = NotificationService(db)

        notifications, unread_count = await service.get_for_user(
            user.id,
            page=page,
            limit=limit,
        )

        return {
            "success": True,
            "data": {
                "notifications": [
                    NotificationResponse.model_validate(n).model_dump()
                    for n in notifications
                ],
                "unread_count": unread_count,
                "pagination": {
                    "page": page,
                    "limit": limit,
                },
            },
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# MARK SINGLE AS READ
# ============================================================


@router.patch(
    "/notifications/{notification_id}/read",
    summary="Mark notification as read",
)
async def mark_read(
    notification_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        service = NotificationService(db)
        await service.mark_read(notification_id, user.id)

        return {"success": True, "data": {}}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# MARK ALL AS READ
# ============================================================


@router.patch(
    "/notifications/read-all",
    summary="Mark all notifications as read",
)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        service = NotificationService(db)
        await service.mark_all_read(user.id)

        return {"success": True, "data": {}}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
