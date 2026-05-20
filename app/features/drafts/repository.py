import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.drafts.models import (
    Draft,
    DraftExport,
    DraftStatus,
    DraftType,
    ExportFormat,
)
from app.features.drafts.schemas import UpdateDraftRequest


class DraftRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ============================================================
    # Draft CRUD
    # ============================================================

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
            version=1,
            revision=1,
        )
        self.session.add(draft)
        await self.session.flush()
        return draft

    async def get_by_id(
        self,
        draft_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[Draft]:
        stmt = select(Draft).where(Draft.id == draft_id)

        if for_update:
            stmt = stmt.with_for_update()

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_case(self, case_id: uuid.UUID) -> List[Draft]:
        result = await self.session.execute(
            select(Draft)
            .where(
                Draft.case_id == case_id,
                Draft.status != DraftStatus.archived,
            )
            .order_by(Draft.created_at.desc())
        )
        return list(result.scalars())

    # ============================================================
    # Update Logic (SAFE)
    # ============================================================

    async def update(
        self,
        draft: Draft,
        data: UpdateDraftRequest,
    ) -> Draft:
        update_data = data.model_dump(exclude_none=True)

        # ❗ Prevent illegal updates
        if draft.status == DraftStatus.archived:
            raise ValueError("Cannot update archived draft")

        if draft.status == DraftStatus.final:
            raise ValueError("Final draft is immutable. Fork instead.")

        for key, value in update_data.items():
            setattr(draft, key, value)

        # Increment revision (optimistic concurrency tracking)
        draft.revision += 1
        draft.updated_at = datetime.utcnow()

        await self.session.flush()
        return draft

    async def set_content(
        self,
        draft: Draft,
        content: str,
        status: DraftStatus,
    ) -> Draft:
        if draft.status == DraftStatus.archived:
            raise ValueError("Cannot modify archived draft")

        # Validate status transitions
        allowed_transitions = {
            DraftStatus.generating: {DraftStatus.ready, DraftStatus.archived},
            DraftStatus.ready: {
                DraftStatus.editing,
                DraftStatus.final,
                DraftStatus.archived,
            },
            DraftStatus.editing: {DraftStatus.final, DraftStatus.archived},
            DraftStatus.final: set(),
        }

        if status not in allowed_transitions.get(draft.status, set()):
            raise ValueError(f"Invalid status transition: {draft.status} → {status}")

        draft.content = content
        draft.status = status
        draft.revision += 1
        draft.updated_at = datetime.utcnow()

        await self.session.flush()
        return draft

    # ============================================================
    # Versioning (CRITICAL)
    # ============================================================

    async def fork(
        self,
        draft: Draft,
        created_by: uuid.UUID,
    ) -> Draft:
        # Lock parent to avoid race condition
        locked_draft = await self.get_by_id(draft.id, for_update=True)
        if not locked_draft:
            raise ValueError("Draft not found")

        # Find latest version in chain
        result = await self.session.execute(
            select(Draft.version)
            .where(Draft.parent_draft_id == locked_draft.id)
            .order_by(Draft.version.desc())
            .limit(1)
        )
        latest_version = result.scalar_one_or_none()

        next_version = (latest_version or locked_draft.version) + 1

        new_draft = Draft(
            case_id=locked_draft.case_id,
            created_by=created_by,
            draft_type=locked_draft.draft_type,
            title=f"{locked_draft.title} (v{next_version})",
            content=locked_draft.content,
            version=next_version,
            revision=1,
            status=DraftStatus.editing,
            generated_by_ai=False,
            parent_draft_id=locked_draft.id,
        )

        self.session.add(new_draft)
        await self.session.flush()
        return new_draft

    async def archive(self, draft: Draft) -> Draft:
        if draft.status == DraftStatus.archived:
            return draft

        draft.status = DraftStatus.archived
        draft.revision += 1
        draft.updated_at = datetime.utcnow()

        await self.session.flush()
        return draft

    # ============================================================
    # EXPORTS
    # ============================================================

    async def create_export(
        self,
        *,
        draft_id: uuid.UUID,
        exported_by: uuid.UUID,
        export_format: str,
        r2_key: str,
        r2_bucket: str,
    ) -> DraftExport:
        try:
            fmt = ExportFormat(export_format)
        except ValueError:
            raise ValueError("Invalid export format")

        export = DraftExport(
            draft_id=draft_id,
            exported_by=exported_by,
            export_format=fmt,
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
