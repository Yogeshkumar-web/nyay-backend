import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.features.cases.models import (
    Case, CaseAccess, CaseNumber, CaseSection, CaseStatus, CaseType, Party
)
from app.features.cases.schemas import (
    CaseCreateRequest, CaseUpdateRequest,
    CaseNumberCreateRequest, CaseNumberUpdateRequest,
    CaseSectionCreateRequest,
    CaseAccessGrantRequest,
    PartyCreateRequest, PartyUpdateRequest,
)


class CaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Cases ──────────────────────────────────────────────────────────────

    async def create(self, data: CaseCreateRequest, lawyer_id: uuid.UUID) -> Case:
        case = Case(
            lawyer_id=lawyer_id,
            **data.model_dump(exclude_none=True),
        )
        self.session.add(case)
        await self.session.flush()
        return case

    async def get_by_id(self, case_id: uuid.UUID) -> Case | None:
        result = await self.session.execute(
            select(Case)
            .options(
                selectinload(Case.case_numbers),
                selectinload(Case.case_sections),
                selectinload(Case.parties),
            )
            .where(Case.id == case_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_simple(self, case_id: uuid.UUID) -> Case | None:
        """Lightweight fetch without eager-loading relations."""
        result = await self.session.execute(select(Case).where(Case.id == case_id))
        return result.scalar_one_or_none()

    async def list_for_lawyer(
        self,
        lawyer_id: uuid.UUID,
        *,
        page: int = 1,
        limit: int = 20,
        status: Optional[CaseStatus] = None,
        case_type: Optional[CaseType] = None,
        search: Optional[str] = None,
    ) -> tuple[list[Case], int]:
        """
        Returns (cases, total_count).
        Includes cases directly owned by lawyer + cases where munshi access exists.
        """
        # Base query: cases the lawyer owns OR has access to
        base = (
            select(Case)
            .outerjoin(CaseAccess, CaseAccess.case_id == Case.id)
            .where(
                or_(
                    Case.lawyer_id == lawyer_id,
                    CaseAccess.user_id == lawyer_id,
                )
            )
            .distinct()
        )

        if status:
            base = base.where(Case.status == status)
        if case_type:
            base = base.where(Case.case_type == case_type)
        if search:
            search_term = f"%{search}%"
            base = base.where(
                or_(
                    Case.case_title.ilike(search_term),
                    Case.petitioner_name.ilike(search_term),
                    Case.respondent_name.ilike(search_term),
                )
            )

        # Count query
        count_result = await self.session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar_one()

        # Paginated results
        offset = (page - 1) * limit
        result = await self.session.execute(
            base.order_by(Case.updated_at.desc()).offset(offset).limit(limit)
        )
        cases = list(result.scalars().all())
        return cases, total

    async def update(self, case: Case, data: CaseUpdateRequest) -> Case:
        update_data = data.model_dump(exclude_none=True)
        for key, value in update_data.items():
            setattr(case, key, value)
        case.updated_at = datetime.utcnow()
        await self.session.flush()
        return case

    async def archive(self, case: Case) -> Case:
        case.status = CaseStatus.archived
        case.updated_at = datetime.utcnow()
        await self.session.flush()
        return case

    # ── Case Access ────────────────────────────────────────────────────────

    async def get_access(self, case_id: uuid.UUID, user_id: uuid.UUID) -> CaseAccess | None:
        result = await self.session.execute(
            select(CaseAccess).where(
                CaseAccess.case_id == case_id,
                CaseAccess.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def can_access(self, case_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Returns True if user is the lawyer owner or has CaseAccess."""
        case = await self.get_by_id_simple(case_id)
        if not case:
            return False
        if case.lawyer_id == user_id:
            return True
        access = await self.get_access(case_id, user_id)
        return access is not None

    async def can_edit(self, case_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        case = await self.get_by_id_simple(case_id)
        if not case:
            return False
        if case.lawyer_id == user_id:
            return True
        access = await self.get_access(case_id, user_id)
        return access is not None and access.can_edit

    async def grant_access(
        self, data: CaseAccessGrantRequest, case_id: uuid.UUID, granted_by: uuid.UUID
    ) -> CaseAccess:
        # Upsert pattern: update if exists
        existing = await self.get_access(case_id, data.user_id)
        if existing:
            existing.can_edit = data.can_edit
            await self.session.flush()
            return existing

        access = CaseAccess(
            case_id=case_id,
            user_id=data.user_id,
            granted_by=granted_by,
            can_edit=data.can_edit,
        )
        self.session.add(access)
        await self.session.flush()
        return access

    async def revoke_access(self, case_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        access = await self.get_access(case_id, user_id)
        if not access:
            return False
        await self.session.delete(access)
        await self.session.flush()
        return True

    # ── Case Numbers ───────────────────────────────────────────────────────

    async def add_case_number(
        self, case_id: uuid.UUID, data: CaseNumberCreateRequest
    ) -> CaseNumber:
        # If new number is primary, unset all existing primary flags for this case
        if data.is_primary:
            await self._unset_primary_case_numbers(case_id)

        number = CaseNumber(case_id=case_id, **data.model_dump(exclude_none=True))
        self.session.add(number)
        await self.session.flush()
        return number

    async def get_case_number(self, number_id: uuid.UUID) -> CaseNumber | None:
        result = await self.session.execute(
            select(CaseNumber).where(CaseNumber.id == number_id)
        )
        return result.scalar_one_or_none()

    async def update_case_number(
        self, number: CaseNumber, data: CaseNumberUpdateRequest
    ) -> CaseNumber:
        if data.is_primary:
            await self._unset_primary_case_numbers(number.case_id)

        update_data = data.model_dump(exclude_none=True)
        for key, value in update_data.items():
            setattr(number, key, value)
        await self.session.flush()
        return number

    async def delete_case_number(self, number: CaseNumber) -> None:
        await self.session.delete(number)
        await self.session.flush()

    async def _unset_primary_case_numbers(self, case_id: uuid.UUID) -> None:
        result = await self.session.execute(
            select(CaseNumber).where(
                CaseNumber.case_id == case_id, CaseNumber.is_primary == True  # noqa: E712
            )
        )
        for num in result.scalars():
            num.is_primary = False
        await self.session.flush()

    # ── Case Sections ──────────────────────────────────────────────────────

    async def add_section(
        self, case_id: uuid.UUID, data: CaseSectionCreateRequest, added_by: uuid.UUID
    ) -> CaseSection:
        section = CaseSection(
            case_id=case_id,
            added_by=added_by,
            **data.model_dump(exclude_none=True),
        )
        self.session.add(section)
        await self.session.flush()
        return section

    async def get_section(self, section_id: uuid.UUID) -> CaseSection | None:
        result = await self.session.execute(
            select(CaseSection).where(CaseSection.id == section_id)
        )
        return result.scalar_one_or_none()

    async def remove_section(
        self, section: CaseSection, removed_by: uuid.UUID
    ) -> CaseSection:
        """Soft-delete: preserve history."""
        section.is_active = False
        section.removed_by = removed_by
        section.removed_at = datetime.utcnow()
        await self.session.flush()
        return section

    async def get_sections_history(self, case_id: uuid.UUID) -> list[CaseSection]:
        """All sections including removed ones, newest first."""
        result = await self.session.execute(
            select(CaseSection)
            .where(CaseSection.case_id == case_id)
            .order_by(CaseSection.added_at.desc())
        )
        return list(result.scalars().all())

    # ── Parties ────────────────────────────────────────────────────────────

    async def add_party(self, case_id: uuid.UUID, data: PartyCreateRequest) -> Party:
        party = Party(case_id=case_id, **data.model_dump(exclude_none=True))
        self.session.add(party)
        await self.session.flush()
        return party

    async def get_party(self, party_id: uuid.UUID) -> Party | None:
        result = await self.session.execute(
            select(Party).where(Party.id == party_id)
        )
        return result.scalar_one_or_none()

    async def update_party(self, party: Party, data: PartyUpdateRequest) -> Party:
        update_data = data.model_dump(exclude_none=True)
        for key, value in update_data.items():
            setattr(party, key, value)
        party.updated_at = datetime.utcnow()
        await self.session.flush()
        return party

    async def delete_party(self, party: Party) -> None:
        await self.session.delete(party)
        await self.session.flush()