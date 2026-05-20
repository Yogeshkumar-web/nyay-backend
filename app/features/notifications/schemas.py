import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.features.notifications.models import NotificationType

# ============================================================
# RESPONSE SCHEMA
# ============================================================


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID

    notification_type: NotificationType

    title: str = Field(..., max_length=300)
    message: Optional[str]

    # ------------------------------------------------------------
    # STATE
    # ------------------------------------------------------------
    is_read: bool
    read_at: Optional[datetime]

    # ------------------------------------------------------------
    # LINKING (NEW STANDARD)
    # ------------------------------------------------------------
    linked_entity_type: Optional[str]
    linked_entity_id: Optional[uuid.UUID]

    # ------------------------------------------------------------
    # BACKWARD COMPATIBILITY (legacy)
    # ------------------------------------------------------------
    link: Optional[str]

    # ------------------------------------------------------------
    # METADATA
    # ------------------------------------------------------------
    created_at: datetime
