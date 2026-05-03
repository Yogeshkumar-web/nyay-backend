import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.drafts.models import Draft, DraftExport, DraftStatus, DraftType
from app.features.drafts.schemas import UpdateDraftRequest


class DraftRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        case_id: uuid.UUID,
        created_by: uuid.UUID,
        draft_type: DraftType,
        title: str,
        ai_prompt_used: Optional[str] = None,
    ) -> Draft:
        draft = Draft(
            case_id=case_id,
            created_by=created_by,
            draft_type=draft_type,
            title=title,
            status=DraftStatus.generating,
            ai_prompt_used=ai_prompt_used,
        )
        self.session.add(draft)
        await self.session.flush()
        return draft

    async def get_by_id(self, draft_id: uuid.UUID) -> Optional[Draft]:
        result = await self.session.execute(select(Draft).where(Draft.id == draft_id))
        return result.scalar_one_or_none()

    async def list_for_case(self, case_id: uuid.UUID) -> list[Draft]:
        result = await self.session.execute(
            select(Draft)
            .where(Draft.case_id == case_id, Draft.status != DraftStatus.archived)
            .order_by(Draft.created_at.desc())
        )
        return list(result.scalars().all())

    async def update(self, draft: Draft, data: UpdateDraftRequest) -> Draft:
        update_data = data.model_dump(exclude_none=True)
        for key, value in update_data.items():
            setattr(draft, key, value)
        draft.updated_at = datetime.utcnow()
        await self.session.flush()
        return draft

    async def set_content(self, draft: Draft, content: str, status: DraftStatus) -> Draft:
        draft.content = content
        draft.status = status
        draft.updated_at = datetime.utcnow()
        await self.session.flush()
        return draft

    async def fork(self, draft: Draft, created_by: uuid.UUID) -> Draft:
        new_draft = Draft(
            case_id=draft.case_id,
            created_by=created_by,
            draft_type=draft.draft_type,
            title=f"{draft.title} (v{draft.version + 1})",
            content=draft.content,
            version=draft.version + 1,
            status=DraftStatus.editing,
            generated_by_ai=False,
            parent_draft_id=draft.id,
        )
        self.session.add(new_draft)
        await self.session.flush()
        return new_draft

    async def archive(self, draft: Draft) -> Draft:
        draft.status = DraftStatus.archived
        draft.updated_at = datetime.utcnow()
        await self.session.flush()
        return draft

    # ── Exports ────────────────────────────────────────────────────────────────

    async def create_export(
        self,
        *,
        draft_id: uuid.UUID,
        exported_by: uuid.UUID,
        export_format: str,
        r2_key: str,
        r2_bucket: str,
    ) -> DraftExport:
        from app.features.drafts.models import ExportFormat
        export = DraftExport(
            draft_id=draft_id,
            exported_by=exported_by,
            export_format=ExportFormat(export_format),
            r2_key=r2_key,
            r2_bucket=r2_bucket,
        )
        self.session.add(export)
        await self.session.flush()
        return export

    async def get_export(self, export_id: uuid.UUID) -> Optional[DraftExport]:
        result = await self.session.execute(
            select(DraftExport).where(DraftExport.id == export_id)
        )
        return result.scalar_one_or_none()