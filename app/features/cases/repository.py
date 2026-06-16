import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.features.cases.models import (
    Case,
    CaseAccess,
    CaseNumber,
    CaseSection,
    CaseStatus,
    CaseType,
    Party,
)


class CaseRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data, lawyer_id: uuid.UUID) -> Case:
        payload = data.model_dump(
            exclude={"case_numbers", "sections", "parties"}, exclude_none=True
        )
        case = Case(lawyer_id=lawyer_id, **payload)
        self.db.add(case)
        await self.db.flush()
        return case

    async def get_by_id(self, case_id: uuid.UUID) -> Optional[Case]:
        result = await self.db.execute(
            select(Case)
            .options(
                selectinload(Case.case_numbers),
                selectinload(Case.sections),
                selectinload(Case.parties),
            )
            .where(Case.id == case_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_simple(self, case_id: uuid.UUID) -> Optional[Case]:
        result = await self.db.execute(select(Case).where(Case.id == case_id))
        return result.scalar_one_or_none()

    async def can_access(self, case_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(Case.id).where(Case.id == case_id, Case.lawyer_id == user_id)
        )
        if result.scalar_one_or_none():
            return True

        result = await self.db.execute(
            select(CaseAccess.id).where(
                CaseAccess.case_id == case_id,
                CaseAccess.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def can_edit(self, case_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(Case.id).where(Case.id == case_id, Case.lawyer_id == user_id)
        )
        if result.scalar_one_or_none():
            return True

        result = await self.db.execute(
            select(CaseAccess.id).where(
                CaseAccess.case_id == case_id,
                CaseAccess.user_id == user_id,
                CaseAccess.can_edit.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_access(self, case_id: uuid.UUID, user_id: uuid.UUID):
        result = await self.db.execute(
            select(CaseAccess).where(
                CaseAccess.case_id == case_id,
                CaseAccess.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def grant_access(self, data, case_id: uuid.UUID, granted_by: uuid.UUID):
        existing = await self.get_access(case_id, data.user_id)
        if existing:
            existing.can_edit = data.can_edit
            existing.granted_by = granted_by
            return existing

        access = CaseAccess(
            case_id=case_id,
            user_id=data.user_id,
            granted_by=granted_by,
            can_edit=data.can_edit,
        )
        self.db.add(access)
        await self.db.flush()
        return access

    async def revoke_access(self, case_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        access = await self.get_access(case_id, user_id)
        if not access:
            return False

        await self.db.delete(access)
        return True

    async def list_for_lawyer(
        self,
        user_id: uuid.UUID,
        *,
        page: int,
        limit: int,
        status: Optional[CaseStatus],
        case_type: Optional[CaseType],
        search: Optional[str],
    ) -> tuple[list[Case], int]:
        filters = []

        if status:
            filters.append(Case.status == status)
        if case_type:
            filters.append(Case.case_type == case_type)
        if search:
            term = f"%{search}%"
            matching_case_numbers = select(CaseNumber.case_id).where(
                CaseNumber.case_number.ilike(term)
            )
            filters.append(
                or_(
                    Case.case_title.ilike(term),
                    Case.petitioner_name.ilike(term),
                    Case.respondent_name.ilike(term),
                    Case.id.in_(matching_case_numbers),
                )
            )

        access_subq = select(CaseAccess.case_id).where(CaseAccess.user_id == user_id)
        base_query = select(Case).where(
            and_(
                or_(Case.lawyer_id == user_id, Case.id.in_(access_subq)),
                *filters,
            )
        )

        count_query = select(func.count()).select_from(base_query.subquery())
        total = (await self.db.execute(count_query)).scalar_one()

        result = await self.db.execute(
            base_query.order_by(Case.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def update(self, case: Case, data) -> Case:
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(case, key, value)
        return case

    async def archive(self, case: Case):
        case.status = CaseStatus.archived

    async def add_case_number(self, case_id: uuid.UUID, data) -> CaseNumber:
        number = CaseNumber(case_id=case_id, **data.model_dump(exclude_none=True))
        self.db.add(number)
        await self.db.flush()
        return number

    async def get_case_number(self, number_id: uuid.UUID):
        result = await self.db.execute(
            select(CaseNumber).where(CaseNumber.id == number_id)
        )
        return result.scalar_one_or_none()

    async def update_case_number(self, number: CaseNumber, data):
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(number, key, value)
        return number

    async def delete_case_number(self, number: CaseNumber):
        await self.db.delete(number)

    async def add_section(
        self, case_id: uuid.UUID, data, added_by: uuid.UUID
    ) -> CaseSection:
        section = CaseSection(
            case_id=case_id, added_by=added_by, **data.model_dump(exclude_none=True)
        )
        self.db.add(section)
        await self.db.flush()
        return section

    async def get_section(self, section_id: uuid.UUID):
        result = await self.db.execute(
            select(CaseSection).where(CaseSection.id == section_id)
        )
        return result.scalar_one_or_none()

    async def remove_section(self, section: CaseSection, removed_by: uuid.UUID):
        section.is_active = False
        section.removed_by = removed_by
        section.removed_at = datetime.utcnow()
        return section

    async def get_sections_history(self, case_id: uuid.UUID) -> list[CaseSection]:
        result = await self.db.execute(
            select(CaseSection)
            .where(CaseSection.case_id == case_id)
            .order_by(CaseSection.added_at.asc())
        )
        return list(result.scalars().all())

    async def add_party(self, case_id: uuid.UUID, data) -> Party:
        party = Party(case_id=case_id, **data.model_dump(exclude_none=True))
        self.db.add(party)
        await self.db.flush()
        return party

    async def get_party(self, party_id: uuid.UUID):
        result = await self.db.execute(select(Party).where(Party.id == party_id))
        return result.scalar_one_or_none()

    async def update_party(self, party: Party, data):
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(party, key, value)
        return party

    async def delete_party(self, party: Party):
        await self.db.delete(party)
