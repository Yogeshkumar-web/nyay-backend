import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip(v: Optional[str]) -> Optional[str]:
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    return v


# ─────────────────────────────
# Response Models
# ─────────────────────────────


class CauseListingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    listing_date: date
    court_number: str
    serial_number: Optional[int]

    case_number: Optional[str]
    case_title: Optional[str]
    petitioner: Optional[str]
    respondent: Optional[str]
    advocate_name: Optional[str]
    case_type_raw: Optional[str]
    remarks: Optional[str]

    matched_case_id: Optional[uuid.UUID]
    scraped_at: datetime
    created_at: datetime


# ─────────────────────────────
# Request Models
# ─────────────────────────────


class RefreshRequest(BaseModel):
    """Admin-only: trigger a background scrape."""

    date: Optional[date] = None

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        if v and v.year < 2000:
            raise ValueError("Invalid date")
        return v


class ScrapeRequest(BaseModel):
    """Any authenticated user: trigger a background scrape for a specific date."""

    date: Optional[date] = None

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        if v and v.year < 2000:
            raise ValueError("Invalid date")
        return v


# ─────────────────────────────
# Query validation
# ─────────────────────────────


class CauseListingQueryParams(BaseModel):
    date: Optional[date] = None
    court_number: Optional[str] = Field(None, max_length=20)
    search: Optional[str] = Field(None, max_length=200)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)

    @field_validator("court_number", "search", mode="before")
    @classmethod
    def normalize_strings(cls, v):
        return _strip(v)
