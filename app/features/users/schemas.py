import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.features.users.models import AuthProvider, Designation, UserRole


# ─────────────────────────────────────────────
# Response
# ─────────────────────────────────────────────
class LawyerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    auth_provider: AuthProvider
    is_email_verified: bool
    is_active: bool
    is_profile_complete: bool

    enrollment_number: Optional[str] = None
    designation: Optional[Designation] = None
    chamber_number: Optional[str] = None
    court_name: Optional[str] = None

    office_address_line1: Optional[str] = None
    office_address_line2: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None

    mobile_number: Optional[str] = None
    alternate_mobile: Optional[str] = None

    default_district: Optional[str] = None

    last_login_at: Optional[datetime] = None
    created_at: datetime


# ─────────────────────────────────────────────
# Update
# ─────────────────────────────────────────────
class LawyerProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=200)

    enrollment_number: Optional[str] = Field(None, max_length=100)
    designation: Optional[Designation] = None

    chamber_number: Optional[str] = Field(None, max_length=100)

    court_name: Optional[str] = Field(None, max_length=300)

    office_address_line1: Optional[str] = Field(None, max_length=300)
    office_address_line2: Optional[str] = Field(None, max_length=300)

    city: Optional[str] = Field(None, max_length=100)
    district: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)

    mobile_number: Optional[str] = Field(None, min_length=10, max_length=20)
    alternate_mobile: Optional[str] = Field(None, max_length=20)

    default_district: Optional[str] = Field(None, max_length=100)

    # ── Normalize strings ───────────────────────
    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v


# ─────────────────────────────────────────────
# Munshi
# ─────────────────────────────────────────────
class MunshiResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    role: UserRole
    mobile_number: Optional[str] = None
    is_active: bool
