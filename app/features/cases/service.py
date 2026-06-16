import json as _json
import logging
import math
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.models import CaseStatus, CaseType
from app.features.cases.repository import CaseRepository
from app.features.cases.schemas import (
    CaseAccessGrantRequest,
    CaseAccessResponse,
    CaseCreateRequest,
    CaseDetailResponse,
    CaseNumberCreateRequest,
    CaseNumberResponse,
    CaseNumberUpdateRequest,
    CaseResponse,
    CaseSectionCreateRequest,
    CaseSectionResponse,
    CaseUpdateRequest,
    PaginationMeta,
    PartyCreateRequest,
    PartyResponse,
    PartyUpdateRequest,
)
from app.features.notifications.models import NotificationType
from app.features.notifications.service import NotificationService
from app.features.users.models import User, UserRole
from app.features.users.repository import UserRepository

logger = logging.getLogger(__name__)


# ============================================================
# CONTEXT SYNC HELPER
# ============================================================


def _build_case_context(case: CaseDetailResponse) -> dict:
    """Serialize the essential case fields into a dict for context_json['_case']."""
    return {
        "title": case.case_title,
        "type": case.case_type.value if case.case_type else None,
        "bench": case.bench_type.value if case.bench_type else None,
        "stage": case.stage.value if case.stage else None,
        "status": case.status.value if case.status else None,
        "petitioner": case.petitioner_name,
        "respondent": case.respondent_name,
        "act": case.act_name,
        "court_number": case.court_number,
        "brief_facts": case.brief_facts,
        "filing_date": str(case.filing_date) if case.filing_date else None,
        "next_hearing_date": str(case.next_hearing_date)
        if case.next_hearing_date
        else None,
        "lower_court_decision_date": str(case.lower_court_decision_date)
        if case.lower_court_decision_date
        else None,
        "bail_rejection_date": str(case.bail_rejection_date)
        if case.bail_rejection_date
        else None,
        "limitation_expiry_date": str(case.limitation_expiry_date)
        if case.limitation_expiry_date
        else None,
        "notes": case.notes,
        "case_numbers": [
            {
                "type": n.number_type.value,
                "number": n.case_number,
                "court": n.court_name,
                "year": n.year,
                "is_primary": n.is_primary,
            }
            for n in case.case_numbers
        ],
        "sections": [
            {"section": s.section, "act": s.act_name}
            for s in case.sections
            if s.is_active
        ],
        "parties": [
            {
                "name": p.name,
                "type": p.party_type.value,
                "address": p.address,
                "phone": p.phone,
            }
            for p in case.parties
        ],
    }


class CaseService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = CaseRepository(db)
        self.user_repo = UserRepository(db)

    async def _sync_case_to_context(self, case_id: uuid.UUID) -> None:
        """Write (or update) the _case key in context_json with fresh case details.

        Called after every mutation that changes case data so context always stays
        in sync without the user needing to manually push anything.
        """
        from app.features.context.repository import ContextRepository

        case = await self.repo.get_by_id(case_id)
        if not case:
            return

        case_detail = CaseDetailResponse.model_validate(case)
        case_ctx = _build_case_context(case_detail)

        context_repo = ContextRepository(self.db)
        existing = await context_repo.get_context(case_id)

        if existing:
            context_json = dict(existing.context_json)
            pushed_docs = dict(existing.pushed_documents)
        else:
            context_json = {}
            pushed_docs = {}

        context_json["_case"] = case_ctx
        tokens = len(_json.dumps(context_json, ensure_ascii=False)) // 4

        await context_repo.upsert_context(
            case_id=case_id,
            context_json=context_json,
            pushed_documents=pushed_docs,
            token_estimate=tokens,
        )
        logger.info("case_context_synced: %s", case_id)

    async def _require_case_edit_access(self, case_id: uuid.UUID, user: User):
        case = await self.repo.get_by_id_simple(case_id)
        if not case:
            raise NotFoundError("Case not found")

        if case.lawyer_id != user.id:
            access = await self.repo.get_access(case_id, user.id)
            if not access or not access.can_edit:
                raise ForbiddenError("No edit access")

        return case

    async def _require_lawyer_ownership(self, case_id: uuid.UUID, user: User):
        case = await self.repo.get_by_id_simple(case_id)
        if not case:
            raise NotFoundError("Case not found")

        if case.lawyer_id != user.id:
            raise ForbiddenError("Only owner allowed")

        return case

    async def create_case(self, data: CaseCreateRequest, current_user: User):
        case = await self.repo.create(data, current_user.id)

        for number in data.case_numbers:
            await self.repo.add_case_number(case.id, number)
        for section in data.sections:
            await self.repo.add_section(case.id, section, current_user.id)
        for party in data.parties:
            await self.repo.add_party(case.id, party)

        notification_svc = NotificationService(self.db)
        await notification_svc.create(
            user_id=current_user.id,
            notification_type=NotificationType.case_created,
            title="Case Created",
            message=f"Successfully created case: {case.case_title}",
            link=f"/cases/{case.id}",
            dedup_key=f"case_created_{case.id}",
            linked_entity_type="Case",
            linked_entity_id=case.id,
        )

        await self.db.commit()
        created = await self.repo.get_by_id(case.id)
        await self._sync_case_to_context(case.id)
        await self.db.commit()
        logger.info("Case created: %s", case.id)
        return CaseDetailResponse.model_validate(created)

    async def list_cases(
        self,
        current_user: User,
        *,
        page: int = 1,
        limit: int = 20,
        status: Optional[CaseStatus] = None,
        case_type: Optional[CaseType] = None,
        search: Optional[str] = None,
    ):
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
        return [CaseResponse.model_validate(case) for case in cases], pagination

    async def get_case(self, case_id: uuid.UUID, current_user: User):
        if not await self.repo.can_access(case_id, current_user.id):
            raise NotFoundError("Case not found")

        case = await self.repo.get_by_id(case_id)
        if not case:
            raise NotFoundError("Case not found")

        return CaseDetailResponse.model_validate(case)

    async def update_case(
        self, case_id: uuid.UUID, data: CaseUpdateRequest, current_user: User
    ):
        case = await self._require_case_edit_access(case_id, current_user)
        await self.repo.update(case, data)
        await self.db.commit()

        updated = await self.repo.get_by_id(case_id)
        await self._sync_case_to_context(case_id)
        await self.db.commit()
        logger.info("Case updated: %s", case_id)
        return CaseDetailResponse.model_validate(updated)

    async def archive_case(self, case_id: uuid.UUID, current_user: User):
        case = await self._require_lawyer_ownership(case_id, current_user)
        await self.repo.archive(case)
        await self.db.commit()
        logger.info("Case archived: %s", case_id)

    async def grant_access(
        self, case_id: uuid.UUID, data: CaseAccessGrantRequest, current_user: User
    ):
        await self._require_lawyer_ownership(case_id, current_user)

        target = await self.user_repo.get_by_id(data.user_id)
        if not target:
            raise NotFoundError("User not found")
        if target.role not in (UserRole.munshi, UserRole.lawyer):
            raise ValidationError("Invalid role")
        if data.user_id == current_user.id:
            raise ValidationError("Cannot grant self access")

        access = await self.repo.grant_access(data, case_id, current_user.id)
        await self.db.commit()
        logger.info("Access granted: %s -> %s", case_id, data.user_id)
        return CaseAccessResponse.model_validate(access)

    async def revoke_access(
        self, case_id: uuid.UUID, user_id: uuid.UUID, current_user: User
    ):
        await self._require_lawyer_ownership(case_id, current_user)

        removed = await self.repo.revoke_access(case_id, user_id)
        if not removed:
            raise NotFoundError("Access not found")

        await self.db.commit()

    async def add_case_number(
        self, case_id: uuid.UUID, data: CaseNumberCreateRequest, current_user: User
    ):
        await self._require_case_edit_access(case_id, current_user)
        number = await self.repo.add_case_number(case_id, data)
        await self.db.commit()
        await self._sync_case_to_context(case_id)
        await self.db.commit()
        return CaseNumberResponse.model_validate(number)

    async def update_case_number(
        self, number_id: uuid.UUID, data: CaseNumberUpdateRequest, current_user: User
    ):
        number = await self.repo.get_case_number(number_id)
        if not number:
            raise NotFoundError("Case number not found")

        await self._require_case_edit_access(number.case_id, current_user)
        number = await self.repo.update_case_number(number, data)
        await self.db.commit()
        await self._sync_case_to_context(number.case_id)
        await self.db.commit()
        return CaseNumberResponse.model_validate(number)

    async def delete_case_number(self, number_id: uuid.UUID, current_user: User):
        number = await self.repo.get_case_number(number_id)
        if not number:
            raise NotFoundError("Case number not found")

        case_id = number.case_id
        await self._require_case_edit_access(case_id, current_user)
        await self.repo.delete_case_number(number)
        await self.db.commit()
        await self._sync_case_to_context(case_id)
        await self.db.commit()

    async def add_section(
        self, case_id: uuid.UUID, data: CaseSectionCreateRequest, current_user: User
    ):
        await self._require_case_edit_access(case_id, current_user)
        section = await self.repo.add_section(case_id, data, current_user.id)
        await self.db.commit()
        await self._sync_case_to_context(case_id)
        await self.db.commit()
        return CaseSectionResponse.model_validate(section)

    async def remove_section(self, section_id: uuid.UUID, current_user: User):
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundError("Section not found")
        if not section.is_active:
            raise ValidationError("Already removed")

        case_id = section.case_id
        await self._require_case_edit_access(case_id, current_user)
        section = await self.repo.remove_section(section, current_user.id)
        await self.db.commit()
        await self._sync_case_to_context(case_id)
        await self.db.commit()
        return CaseSectionResponse.model_validate(section)

    async def get_sections_history(self, case_id: uuid.UUID, current_user: User):
        if not await self.repo.can_access(case_id, current_user.id):
            raise NotFoundError("Case not found")

        sections = await self.repo.get_sections_history(case_id)
        return [CaseSectionResponse.model_validate(section) for section in sections]

    async def add_party(
        self, case_id: uuid.UUID, data: PartyCreateRequest, current_user: User
    ):
        await self._require_case_edit_access(case_id, current_user)
        party = await self.repo.add_party(case_id, data)
        await self.db.commit()
        await self._sync_case_to_context(case_id)
        await self.db.commit()
        return PartyResponse.model_validate(party)

    async def update_party(
        self, party_id: uuid.UUID, data: PartyUpdateRequest, current_user: User
    ):
        party = await self.repo.get_party(party_id)
        if not party:
            raise NotFoundError("Party not found")

        await self._require_case_edit_access(party.case_id, current_user)
        party = await self.repo.update_party(party, data)
        await self.db.commit()
        await self._sync_case_to_context(party.case_id)
        await self.db.commit()
        return PartyResponse.model_validate(party)

    async def delete_party(self, party_id: uuid.UUID, current_user: User):
        party = await self.repo.get_party(party_id)
        if not party:
            raise NotFoundError("Party not found")

        case_id = party.case_id
        await self._require_case_edit_access(case_id, current_user)
        await self.repo.delete_party(party)
        await self.db.commit()
        await self._sync_case_to_context(case_id)
        await self.db.commit()
