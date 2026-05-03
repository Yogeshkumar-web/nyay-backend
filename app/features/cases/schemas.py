import uuid
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from app.features.cases.models import (
    BenchType, CaseNumberType, CaseStage, CaseStatus, CaseType, PartyType
)


# ─────────────────────────────────────────────────────────────
# Case
# ─────────────────────────────────────────────────────────────

class CaseCreateRequest(BaseModel):
    case_title: str = Field(..., min_length=3, max_length=500)
    case_type: CaseType
    bench_type: BenchType = BenchType.single_bench
    petitioner_name: str = Field(..., min_length=2, max_length=500)
    respondent_name: str = Field(..., min_length=2, max_length=500)
    act_name: Optional[str] = Field(None, max_length=255)
    court_number: Optional[str] = Field(None, max_length=20)
    brief_facts: Optional[str] = None
    filing_date: Optional[date] = None
    next_hearing_date: Optional[date] = None
    lower_court_decision_date: Optional[date] = None
    bail_rejection_date: Optional[date] = None
    limitation_expiry_date: Optional[date] = None
    notes: Optional[str] = None


class CaseUpdateRequest(BaseModel):
    case_title: Optional[str] = Field(None, min_length=3, max_length=500)
    case_type: Optional[CaseType] = None
    bench_type: Optional[BenchType] = None
    stage: Optional[CaseStage] = None
    status: Optional[CaseStatus] = None
    court_number: Optional[str] = Field(None, max_length=20)
    petitioner_name: Optional[str] = Field(None, min_length=2, max_length=500)
    respondent_name: Optional[str] = Field(None, min_length=2, max_length=500)
    act_name: Optional[str] = Field(None, max_length=255)
    brief_facts: Optional[str] = None
    filing_date: Optional[date] = None
    next_hearing_date: Optional[date] = None
    lower_court_decision_date: Optional[date] = None
    bail_rejection_date: Optional[date] = None
    limitation_expiry_date: Optional[date] = None
    notes: Optional[str] = None


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lawyer_id: uuid.UUID
    case_title: str
    case_type: CaseType
    bench_type: BenchType
    stage: CaseStage
    status: CaseStatus
    court_number: Optional[str]
    petitioner_name: str
    respondent_name: str
    act_name: Optional[str]
    brief_facts: Optional[str]
    filing_date: Optional[date]
    next_hearing_date: Optional[date]
    lower_court_decision_date: Optional[date]
    bail_rejection_date: Optional[date]
    limitation_expiry_date: Optional[date]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


class CaseDetailResponse(CaseResponse):
    case_numbers: list["CaseNumberResponse"] = []
    sections: list["CaseSectionResponse"] = []
    parties: list["PartyResponse"] = []


class CaseListResponse(BaseModel):
    cases: list[CaseResponse]
    pagination: "PaginationMeta"


# ─────────────────────────────────────────────────────────────
# Case Number
# ─────────────────────────────────────────────────────────────

class CaseNumberCreateRequest(BaseModel):
    number_type: CaseNumberType
    case_number: str = Field(..., min_length=1, max_length=200)
    court_name: Optional[str] = Field(None, max_length=255)
    year: Optional[int] = Field(None, ge=1900, le=2100)
    is_primary: bool = False
    notes: Optional[str] = Field(None, max_length=500)


class CaseNumberUpdateRequest(BaseModel):
    number_type: Optional[CaseNumberType] = None
    case_number: Optional[str] = Field(None, min_length=1, max_length=200)
    court_name: Optional[str] = Field(None, max_length=255)
    year: Optional[int] = Field(None, ge=1900, le=2100)
    is_primary: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=500)


class CaseNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    number_type: CaseNumberType
    case_number: str
    court_name: Optional[str]
    year: Optional[int]
    is_primary: bool
    notes: Optional[str]
    created_at: datetime


# ─────────────────────────────────────────────────────────────
# Case Section
# ─────────────────────────────────────────────────────────────

class CaseSectionCreateRequest(BaseModel):
    section: str = Field(..., min_length=1, max_length=200)
    act_name: str = Field(..., min_length=1, max_length=200)
    notes: Optional[str] = Field(None, max_length=500)


class CaseSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    section: str
    act_name: str
    is_active: bool
    added_by: uuid.UUID
    added_at: datetime
    removed_by: Optional[uuid.UUID]
    removed_at: Optional[datetime]
    notes: Optional[str]


# ─────────────────────────────────────────────────────────────
# Case Access
# ─────────────────────────────────────────────────────────────

class CaseAccessGrantRequest(BaseModel):
    user_id: uuid.UUID
    can_edit: bool = True


class CaseAccessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    user_id: uuid.UUID
    granted_by: uuid.UUID
    can_edit: bool
    created_at: datetime


# ─────────────────────────────────────────────────────────────
# Party
# ─────────────────────────────────────────────────────────────

class PartyCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=500)
    party_type: PartyType
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None


class PartyUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=500)
    party_type: Optional[PartyType] = None
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None


class PartyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    name: str
    party_type: PartyType
    address: Optional[str]
    phone: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


# ─────────────────────────────────────────────────────────────
# Pagination
# ─────────────────────────────────────────────────────────────

class PaginationMeta(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int


# ─────────────────────────────────────────────────────────────
# Shared API envelope
# ─────────────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    success: bool = True
    data: dict | list | None = None
    message: Optional[str] = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail


# Update forward refs
CaseDetailResponse.model_rebuild()
CaseListResponse.model_rebuild()