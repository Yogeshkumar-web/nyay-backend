import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.features.drafts.models import DraftStatus, DraftType, ExportFormat

# ============================================================
# CONSTANTS (Centralized Limits)
# ============================================================

MAX_TITLE_LENGTH = 500
MAX_CONTENT_LENGTH = 200_000  # ~200KB safe for Tiptap HTML
MAX_INSTRUCTIONS_LENGTH = 5_000  # Prevent prompt abuse


# ============================================================
# REQUEST SCHEMAS
# ============================================================


class CreateDraftRequest(BaseModel):
    draft_type: DraftType
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)
    additional_instructions: Optional[str] = Field(
        default=None,
        max_length=MAX_INSTRUCTIONS_LENGTH,
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v = v.strip()
            if not v:
                raise ValueError("Title cannot be empty or whitespace")
        return v

    @field_validator("additional_instructions")
    @classmethod
    def sanitize_instructions(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v = v.strip()
            if not v:
                return None
        return v


class UpdateDraftRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=MAX_CONTENT_LENGTH)
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)
    status: Optional[DraftStatus] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v = v.strip()
            if not v:
                raise ValueError("Title cannot be empty")
        return v

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v = v.strip()
            if not v:
                return None
        return v


class ReviewDraftRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_LENGTH)
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)

    @field_validator("content")
    @classmethod
    def validate_reviewed_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Reviewed content cannot be empty")
        return v

    @field_validator("title")
    @classmethod
    def validate_review_title(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v = v.strip()
            if not v:
                raise ValueError("Title cannot be empty")
        return v


# ============================================================
# RESPONSE SCHEMAS
# ============================================================


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    created_by: uuid.UUID
    draft_type: DraftType
    title: str
    content: Optional[str]
    version: int
    status: DraftStatus
    generated_by_ai: bool
    parent_draft_id: Optional[uuid.UUID]
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    final_accepted_at: Optional[datetime]
    exported_by: Optional[uuid.UUID]
    exported_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class DraftExportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    export_format: ExportFormat
    r2_key: str
    file_size_bytes: Optional[int]
    expires_at: Optional[datetime]
    created_at: datetime


# ============================================================
# EXPORT REQUEST
# ============================================================


class TriggerExportRequest(BaseModel):
    format: ExportFormat
