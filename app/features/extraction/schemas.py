import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.features.extraction.models import ExtractionStatus, ReviewStatus


# ─────────────────────────────
# Helpers
# ─────────────────────────────
def _strip(v: Optional[str]) -> Optional[str]:
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    return v


# ─────────────────────────────
# Extraction Result
# ─────────────────────────────
class ExtractionResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    case_id: uuid.UUID

    extracted_fields: dict[str, Any]

    # Review content shown in the form-based extraction reviewer.
    # Populated by backend after extraction; updated when user saves edits.
    formatted_content: Optional[str] = None

    confidence_score: Optional[float] = Field(None, ge=0, le=1)

    extraction_status: ExtractionStatus
    review_status: ReviewStatus

    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]

    user_edits: Optional[dict[str, Any]]

    created_at: datetime
    updated_at: datetime


class ReviewExtractionRequest(BaseModel):
    extracted_fields: dict[str, Any] = Field(..., min_length=1)

    review_status: ReviewStatus

    # Optional reviewed content saved by the user in ExtractionReviewEditor
    formatted_content: Optional[str] = None

    @field_validator("extracted_fields")
    @classmethod
    def validate_fields(cls, v):
        if not isinstance(v, dict):
            raise ValueError("Invalid extracted_fields")

        # basic sanity: no empty keys
        for k in v.keys():
            if not k or not str(k).strip():
                raise ValueError("Invalid field key")

        return v


# ─────────────────────────────
# Typed Version
# ─────────────────────────────
class TypedVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID

    typed_content: str
    agent_notes: Optional[str] = None

    status: ReviewStatus

    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]

    created_at: datetime
    updated_at: datetime


class ReviewTypedVersionRequest(BaseModel):
    typed_content: str = Field(..., min_length=1, max_length=20000)

    status: ReviewStatus

    @field_validator("typed_content", mode="before")
    @classmethod
    def normalize_content(cls, v):
        return _strip(v)


class SaveTypedVersionRequest(BaseModel):
    """
    Used for text auto-save and manual save.
    Status is always set to 'edited' automatically — caller does not choose it.
    No max_length: legal documents (FIR, chargesheet, affidavit) can be very long.
    """

    typed_content: str = Field(..., min_length=1)

    @field_validator("typed_content", mode="before")
    @classmethod
    def normalize_content(cls, v):
        return _strip(v)
