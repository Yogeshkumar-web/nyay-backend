import uuid
from typing import Optional

from fastapi import APIRouter, Query

from app.core.dependencies import CurrentUser, DB
from app.features.cases.models import CaseStatus, CaseType
from app.features.cases.schemas import (
    CaseCreateRequest, CaseUpdateRequest,
    CaseNumberCreateRequest, CaseNumberUpdateRequest,
    CaseSectionCreateRequest,
    CaseAccessGrantRequest,
    PartyCreateRequest, PartyUpdateRequest,
)
from app.features.cases.service import CaseService

router = APIRouter(prefix="/cases", tags=["Cases"])

# ── Case Numbers ───────────────────────────────────────────────────────────
case_numbers_router = APIRouter(prefix="/case-numbers", tags=["Case Numbers"])

@case_numbers_router.patch(
    "/{number_id}",
    summary="Update case number",
)

async def update_case_number(
    number_id: uuid.UUID,
    body: CaseNumberUpdateRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    number = await service.update_case_number(number_id, body, current_user)
    return {"success": True, "data": {"case_number": number.model_dump()}}
 
 
@case_numbers_router.delete(
    "/{number_id}",
    summary="Delete case number",
)
async def delete_case_number(
    number_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    await service.delete_case_number(number_id, current_user)
    return {"success": True, "data": {}}
 
 
# ── Case Sections ──────────────────────────────────────────────────────────
case_sections_router = APIRouter(prefix="/case-sections", tags=["Case Sections"])
 
 
@case_sections_router.delete(
    "/{section_id}",
    summary="Soft-remove section",
    description="Preserves history. Section is marked inactive but not deleted.",
)
async def remove_section(
    section_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    section = await service.remove_section(section_id, current_user)
    return {"success": True, "data": {"section": section.model_dump()}}
 
 
# ── Parties ────────────────────────────────────────────────────────────────
parties_router = APIRouter(prefix="/parties", tags=["Parties"])
 
 
@parties_router.patch(
    "/{party_id}",
    summary="Update party",
)
async def update_party(
    party_id: uuid.UUID,
    body: PartyUpdateRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    party = await service.update_party(party_id, body, current_user)
    return {"success": True, "data": {"party": party.model_dump()}}
 
 
@parties_router.delete(
    "/{party_id}",
    summary="Delete party",
)
async def delete_party(
    party_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    await service.delete_party(party_id, current_user)
    return {"success": True, "data": {}}


# ── Cases ──────────────────────────────────────────────────────────────────

@router.post(
    "",
    status_code=201,
    summary="Create a new case",
    description="Creates a case owned by the authenticated lawyer.",
)
async def create_case(
    body: CaseCreateRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    case = await service.create_case(body, current_user)
    return {"success": True, "data": {"case": case.model_dump()}}


@router.get(
    "",
    summary="List cases",
    description="Returns paginated cases accessible to the current user.",
)
async def list_cases(
    current_user: CurrentUser,
    db: DB,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    status: Optional[CaseStatus] = Query(default=None),
    case_type: Optional[CaseType] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=200),
):
    service = CaseService(db)
    cases, pagination = await service.list_cases(
        current_user,
        page=page,
        limit=limit,
        status=status,
        case_type=case_type,
        search=search,
    )
    return {
        "success": True,
        "data": [c.model_dump() for c in cases],
        "pagination": pagination.model_dump(),
    }


@router.get(
    "/{case_id}",
    summary="Get case detail",
    description="Returns full case with numbers, sections, parties. Returns 404 for cases the user cannot access.",
)
async def get_case(
    case_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    case = await service.get_case(case_id, current_user)
    return {"success": True, "data": {"case": case.model_dump()}}


@router.patch(
    "/{case_id}",
    summary="Update case",
)
async def update_case(
    case_id: uuid.UUID,
    body: CaseUpdateRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    case = await service.update_case(case_id, body, current_user)
    return {"success": True, "data": {"case": case.model_dump()}}


@router.delete(
    "/{case_id}",
    status_code=200,
    summary="Archive case",
    description="Soft-deletes by setting status=archived. Only the owning lawyer can archive.",
)
async def archive_case(
    case_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    await service.archive_case(case_id, current_user)
    return {"success": True, "data": {}}


# ── Case Access ────────────────────────────────────────────────────────────

@router.post(
    "/{case_id}/access",
    status_code=201,
    summary="Grant munshi access",
)
async def grant_access(
    case_id: uuid.UUID,
    body: CaseAccessGrantRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    access = await service.grant_access(case_id, body, current_user)
    return {"success": True, "data": {"access": access.model_dump()}}


@router.delete(
    "/{case_id}/access/{user_id}",
    summary="Revoke munshi access",
)
async def revoke_access(
    case_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    await service.revoke_access(case_id, user_id, current_user)
    return {"success": True, "data": {}}


# ── Case Numbers ───────────────────────────────────────────────────────────

@router.post(
    "/{case_id}/numbers",
    status_code=201,
    summary="Add case number",
)
async def add_case_number(
    case_id: uuid.UUID,
    body: CaseNumberCreateRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    number = await service.add_case_number(case_id, body, current_user)
    return {"success": True, "data": {"case_number": number.model_dump()}}


# ── Case Sections ──────────────────────────────────────────────────────────

@router.post(
    "/{case_id}/sections",
    status_code=201,
    summary="Add section",
)
async def add_section(
    case_id: uuid.UUID,
    body: CaseSectionCreateRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    section = await service.add_section(case_id, body, current_user)
    return {"success": True, "data": {"section": section.model_dump()}}


@router.get(
    "/{case_id}/sections/history",
    summary="Section history",
    description="Returns all sections including removed ones.",
)
async def get_sections_history(
    case_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    sections = await service.get_sections_history(case_id, current_user)
    return {"success": True, "data": {"sections": [s.model_dump() for s in sections]}}


# ── Parties ────────────────────────────────────────────────────────────────

@router.post(
    "/{case_id}/parties",
    status_code=201,
    summary="Add party",
)
async def add_party(
    case_id: uuid.UUID,
    body: PartyCreateRequest,
    current_user: CurrentUser,
    db: DB,
):
    service = CaseService(db)
    party = await service.add_party(case_id, body, current_user)
    return {"success": True, "data": {"party": party.model_dump()}}