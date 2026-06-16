import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from app.features.documents.models import (
    DocReviewStatus,
    DocumentType,
    OcrStatus,
    ProcessingRoute,
    ProcessingStatus,
    UploadStatus,
)

# ── Requests ──────────────────────────────────────────────────────────────────


class PresignUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=500)
    mime_type: str = Field(..., min_length=1, max_length=100)
    document_type: DocumentType = DocumentType.other
    file_size_bytes: int = Field(..., gt=0, le=52_428_800)  # max 50MB


class ConfirmUploadRequest(BaseModel):
    # Kept for backward compatibility. The backend classifies the stored file.
    is_scanned: bool | None = None


class UpdateDocumentRequest(BaseModel):
    document_type: Optional[DocumentType] = None
    display_name: Optional[str] = Field(None, max_length=500)


class SaveReviewRequest(BaseModel):
    content: str = Field(..., min_length=1)


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
    ocr_raw_text: Optional[str] = None
    ocr_language: Optional[str]
    page_count: Optional[int]
    ocr_job_id: Optional[str] = None
    ocr_error: Optional[str] = None
    ocr_provider: Optional[str] = None
    ocr_started_at: Optional[datetime] = None
    ocr_completed_at: Optional[datetime] = None
    is_scanned: bool
    processing_route: ProcessingRoute = ProcessingRoute.pending
    processing_status: ProcessingStatus = ProcessingStatus.pending
    processing_job_id: Optional[str] = None
    processing_error: Optional[str] = None
    processing_started_at: Optional[datetime] = None
    processing_completed_at: Optional[datetime] = None
    source_text: Optional[str] = None
    source_artifact: Optional[dict] = None
    classification_details: Optional[dict] = None
    reviewed_content: Optional[str] = None
    review_status: DocReviewStatus = DocReviewStatus.pending
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
