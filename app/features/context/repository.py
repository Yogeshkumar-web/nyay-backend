import hashlib
import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.context.models import CaseContext, CaseSummary


class ContextRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── CaseContext ────────────────────────────────────────────────────────────

    async def get_context(self, case_id: uuid.UUID) -> Optional[CaseContext]:
        result = await self.session.execute(
            select(CaseContext).where(CaseContext.case_id == case_id)
        )
        return result.scalar_one_or_none()

    async def upsert_context(
        self,
        case_id: uuid.UUID,
        context_json: dict,
        pushed_document_ids: list[uuid.UUID],
        token_estimate: Optional[int] = None,
    ) -> CaseContext:
        existing = await self.get_context(case_id)
        if existing:
            existing.context_json = context_json
            existing.pushed_document_ids = pushed_document_ids
            existing.token_estimate = token_estimate
            existing.last_updated_at = datetime.utcnow()
            await self.session.flush()
            return existing

        context = CaseContext(
            case_id=case_id,
            context_json=context_json,
            pushed_document_ids=pushed_document_ids,
            token_estimate=token_estimate,
        )
        self.session.add(context)
        await self.session.flush()
        return context

    async def clear_context(self, case_id: uuid.UUID) -> None:
        context = await self.get_context(case_id)
        if context:
            context.context_json = {}
            context.pushed_document_ids = []
            context.token_estimate = None
            context.last_updated_at = datetime.utcnow()
            await self.session.flush()

    # ── CaseSummary ────────────────────────────────────────────────────────────

    async def get_summary(self, case_id: uuid.UUID) -> Optional[CaseSummary]:
        result = await self.session.execute(
            select(CaseSummary).where(CaseSummary.case_id == case_id)
        )
        return result.scalar_one_or_none()

    async def upsert_summary(
        self,
        case_id: uuid.UUID,
        summary_text: str,
        context_snapshot_hash: str,
    ) -> CaseSummary:
        existing = await self.get_summary(case_id)
        if existing:
            existing.summary_text = summary_text
            existing.context_snapshot_hash = context_snapshot_hash
            existing.generated_at = datetime.utcnow()
            await self.session.flush()
            return existing

        summary = CaseSummary(
            case_id=case_id,
            summary_text=summary_text,
            context_snapshot_hash=context_snapshot_hash,
        )
        self.session.add(summary)
        await self.session.flush()
        return summary

    # ── Hash helper ────────────────────────────────────────────────────────────

    @staticmethod
    def hash_context(context_json: dict) -> str:
        serialized = json.dumps(context_json, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode()).hexdigest()