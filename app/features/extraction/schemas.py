import uuid
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict

from app.features.extraction.models import ExtractionStatus, ReviewStatus


class ExtractionResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    case_id: uuid.UUID
    extracted_fields: dict[str, Any]
    confidence_score: Optional[float]
    extraction_status: ExtractionStatus
    review_status: ReviewStatus
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    user_edits: Optional[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class ReviewExtractionRequest(BaseModel):
    extracted_fields: dict[str, Any]
    review_status: ReviewStatus


class TypedVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    typed_content: str
    status: ReviewStatus
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ReviewTypedVersionRequest(BaseModel):
    typed_content: str
    status: ReviewStatus