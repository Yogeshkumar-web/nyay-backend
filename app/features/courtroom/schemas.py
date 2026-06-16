import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.features.courtroom.models import SessionType, SessionStatus

# ============================================================
# CONSTANTS
# ============================================================

MAX_MESSAGE_LENGTH = 5000


# ============================================================
# MESSAGE SCHEMA
# ============================================================


class Message(BaseModel):
    role: str = Field(..., pattern="^(lawyer|ai)$")
    content: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    timestamp: datetime


class WeakPoint(BaseModel):
    point: str = Field(..., min_length=1)
    suggestion: str = Field(..., min_length=1)
    identified_at: datetime


# ============================================================
# RESPONSE
# ============================================================


class CourtroomSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    lawyer_id: uuid.UUID

    session_type: SessionType
    title: Optional[str]

    messages: List[Message]
    weak_points_identified: List[WeakPoint]

    turn_count: int
    status: SessionStatus

    created_at: datetime
    updated_at: datetime


# ============================================================
# REQUESTS
# ============================================================


class CreateSessionRequest(BaseModel):
    session_type: SessionType
    title: Optional[str] = Field(default=None, max_length=300)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, v: Optional[str]):
        if v:
            v = v.strip()
            if not v:
                return None
        return v


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, v: str):
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        return v


# ============================================================
# END SESSION RESPONSE
# ============================================================


class EndSessionResponse(BaseModel):
    session: CourtroomSessionResponse
    weak_points: List[WeakPoint]
