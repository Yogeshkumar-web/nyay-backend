import uuid
import math
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.cases.models import Case, CaseAccess, CaseNumber, CaseSection, CaseStatus, CaseType, Party
from app.features.users.models import User
from app.features.cases.schemas import (
    CaseCreateRequest, CaseUpdateRequest, CaseDetailResponse, CaseResponse,
    CaseNumberCreateRequest, CaseNumberUpdateRequest, CaseNumberResponse,
    CaseSectionCreateRequest, CaseSectionResponse,
    CaseAccessGrantRequest, CaseAccessResponse,
    PartyCreateRequest, PartyUpdateRequest, PartyResponse,
    PaginationMeta,
)
from app.features.cases.repository import CaseRepository
from app.features.users.repository import UserRepository
from app.core.exceptions import (
    NotFoundError, ForbiddenError, ConflictError, ValidationError
)


class CaseService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = CaseRepository(db)
        self.user_repo = UserRepository(db)

    # ── Access guard ───────────────────────────────────────────────────────

    async def _require_case_edit_access(
        self, case_id: uuid.UUID, user: User
    ) -> Case:
        """Fetch case and verify user has edit permission. Raises on failure."""
        case = await self.repo.get_by_id_simple(case_id)
        if not case:
            raise NotFoundError("Case not found")
        if not await self.repo.can_edit(case_id, user.id):
            raise ForbiddenError("You do not have edit access to this case")
        return case

    async def _require_lawyer_ownership(
        self, case_id: uuid.UUID, user: User
    ) -> Case:
        """Only the owning lawyer can perform this action."""
        case = await self.repo.get_by_id_simple(case_id)
        if not case:
            raise NotFoundError("Case not found")
        if case.lawyer_id != user.id:
            raise ForbiddenError("Only the owning lawyer can perform this action")
        return case

    # ── Cases ──────────────────────────────────────────────────────────────

    async def create_case(
        self, data: CaseCreateRequest, current_user: User
    ) -> CaseDetailResponse:
        case = await self.repo.create(data, current_user.id)
        # Eager-load for response
        case = await self.repo.get_by_id(case.id)
        return CaseDetailResponse.model_validate(case)

    async def list_cases(
        self,
        current_user: User,
        *,
        page: int = 1,
        limit: int = 20,
        status: Optional[CaseStatus] = None,
        case_type: Optional[CaseType] = None,
        search: Optional[str] = None,
    ) -> tuple[list[CaseResponse], PaginationMeta]:
        cases, total = await self.repo.list_for_lawyer(
            current_user.id,
            page=page,
            limit=limit,
            status=status,
            case_type=case_type,
            search=search,
        )
        pagination = PaginationMeta(
            page=page,
            limit=limit,
            total=total,
            total_pages=math.ceil(total / limit) if total else 0,
        )
        return [CaseResponse.model_validate(c) for c in cases], pagination

    async def get_case(
        self, case_id: uuid.UUID, current_user: User
    ) -> CaseDetailResponse:
        if not await self.repo.can_access(case_id, current_user.id):
            # Return 404 intentionally — don't reveal existence of other lawyers' cases
            raise NotFoundError("Case not found")
        case = await self.repo.get_by_id(case_id)
        if not case:
            raise NotFoundError("Case not found")
        return CaseDetailResponse.model_validate(case)

    async def update_case(
        self, case_id: uuid.UUID, data: CaseUpdateRequest, current_user: User
    ) -> CaseResponse:
        case = await self._require_case_edit_access(case_id, current_user)
        case = await self.repo.update(case, data)
        return CaseResponse.model_validate(case)

    async def archive_case(
        self, case_id: uuid.UUID, current_user: User
    ) -> None:
        # Only owning lawyer can archive
        case = await self._require_lawyer_ownership(case_id, current_user)
        await self.repo.archive(case)

    # ── Case Access (munshi management) ────────────────────────────────────

    async def grant_access(
        self, case_id: uuid.UUID, data: CaseAccessGrantRequest, current_user: User
    ) -> CaseAccessResponse:
        case = await self._require_lawyer_ownership(case_id, current_user)

        # Verify target user exists and is a munshi
        target_user = await self.user_repo.get_by_id(data.user_id)
        if not target_user:
            raise NotFoundError("User not found")
        if target_user.role not in ("munshi", "lawyer"):
            raise ValidationError("Access can only be granted to munshi accounts")

        # Cannot grant access to self
        if data.user_id == current_user.id:
            raise ValidationError("Cannot grant access to yourself")

        access = await self.repo.grant_access(data, case_id, current_user.id)
        return CaseAccessResponse.model_validate(access)

    async def revoke_access(
        self, case_id: uuid.UUID, user_id: uuid.UUID, current_user: User
    ) -> None:
        await self._require_lawyer_ownership(case_id, current_user)
        removed = await self.repo.revoke_access(case_id, user_id)
        if not removed:
            raise NotFoundError("Access record not found")

    # ── Case Numbers ───────────────────────────────────────────────────────

    async def add_case_number(
        self, case_id: uuid.UUID, data: CaseNumberCreateRequest, current_user: User
    ) -> CaseNumberResponse:
        await self._require_case_edit_access(case_id, current_user)
        number = await self.repo.add_case_number(case_id, data)
        return CaseNumberResponse.model_validate(number)

    async def update_case_number(
        self, number_id: uuid.UUID, data: CaseNumberUpdateRequest, current_user: User
    ) -> CaseNumberResponse:
        number = await self.repo.get_case_number(number_id)
        if not number:
            raise NotFoundError("Case number not found")
        await self._require_case_edit_access(number.case_id, current_user)
        number = await self.repo.update_case_number(number, data)
        return CaseNumberResponse.model_validate(number)

    async def delete_case_number(
        self, number_id: uuid.UUID, current_user: User
    ) -> None:
        number = await self.repo.get_case_number(number_id)
        if not number:
            raise NotFoundError("Case number not found")
        await self._require_case_edit_access(number.case_id, current_user)
        await self.repo.delete_case_number(number)

    # ── Case Sections ──────────────────────────────────────────────────────

    async def add_section(
        self, case_id: uuid.UUID, data: CaseSectionCreateRequest, current_user: User
    ) -> CaseSectionResponse:
        await self._require_case_edit_access(case_id, current_user)
        section = await self.repo.add_section(case_id, data, current_user.id)
        return CaseSectionResponse.model_validate(section)

    async def remove_section(
        self, section_id: uuid.UUID, current_user: User
    ) -> CaseSectionResponse:
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundError("Section not found")
        if not section.is_active:
            raise ValidationError("Section is already removed")
        await self._require_case_edit_access(section.case_id, current_user)
        section = await self.repo.remove_section(section, current_user.id)
        return CaseSectionResponse.model_validate(section)

    async def get_sections_history(
        self, case_id: uuid.UUID, current_user: User
    ) -> list[CaseSectionResponse]:
        if not await self.repo.can_access(case_id, current_user.id):
            raise NotFoundError("Case not found")
        sections = await self.repo.get_sections_history(case_id)
        return [CaseSectionResponse.model_validate(s) for s in sections]

    # ── Parties ────────────────────────────────────────────────────────────

    async def add_party(
        self, case_id: uuid.UUID, data: PartyCreateRequest, current_user: User
    ) -> PartyResponse:
        await self._require_case_edit_access(case_id, current_user)
        party = await self.repo.add_party(case_id, data)
        return PartyResponse.model_validate(party)

    async def update_party(
        self, party_id: uuid.UUID, data: PartyUpdateRequest, current_user: User
    ) -> PartyResponse:
        party = await self.repo.get_party(party_id)
        if not party:
            raise NotFoundError("Party not found")
        await self._require_case_edit_access(party.case_id, current_user)
        party = await self.repo.update_party(party, data)
        return PartyResponse.model_validate(party)

    async def delete_party(
        self, party_id: uuid.UUID, current_user: User
    ) -> None:
        party = await self.repo.get_party(party_id)
        if not party:
            raise NotFoundError("Party not found")
        await self._require_case_edit_access(party.case_id, current_user)
        await self.repo.delete_party(party)