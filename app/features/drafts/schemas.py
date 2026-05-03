import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from app.features.drafts.models import DraftStatus, DraftType, ExportFormat


class CreateDraftRequest(BaseModel):
    draft_type: DraftType
    title: Optional[str] = None
    additional_instructions: Optional[str] = None


class UpdateDraftRequest(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = Field(None, max_length=500)
    status: Optional[DraftStatus] = None


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


class TriggerExportRequest(BaseModel):
    format: ExportFormat