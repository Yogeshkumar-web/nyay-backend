import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from app.features.documents.models import DocumentType, OcrStatus, UploadStatus


# ── Requests ──────────────────────────────────────────────────────────────────

class PresignUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=500)
    mime_type: str = Field(..., min_length=1, max_length=100)
    document_type: DocumentType = DocumentType.other
    file_size_bytes: int = Field(..., gt=0, le=52_428_800)  # max 50MB


class ConfirmUploadRequest(BaseModel):
    is_scanned: bool = False


class UpdateDocumentRequest(BaseModel):
    document_type: Optional[DocumentType] = None
    display_name: Optional[str] = Field(None, max_length=500)


# ── Responses ─────────────────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    uploaded_by: uuid.UUID
    original_filename: str
    r2_key: str
    r2_bucket: str
    mime_type: str
    file_size_bytes: int
    document_type: DocumentType
    display_name: Optional[str]
    upload_status: UploadStatus
    ocr_status: OcrStatus
    ocr_language: Optional[str]
    page_count: Optional[int]
    is_scanned: bool
    created_at: datetime
    updated_at: datetime


class PresignUploadResponse(BaseModel):
    upload_url: str
    document_id: uuid.UUID
    r2_key: str
    expires_in_seconds: int = 900  # 15 minutes


class ViewUrlResponse(BaseModel):
    url: str
    expires_at: datetime