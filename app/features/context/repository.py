import hashlib
import json
import uuid
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.context.models import (
    CaseContext,
    CaseSummary,
    CaseContextVersion,
)

logger = logging.getLogger(__name__)


class ContextRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ─────────────────────────────
    # CaseContext
    # ─────────────────────────────

    async def get_context(self, case_id: uuid.UUID) -> Optional[CaseContext]:
        result = await self.session.execute(
            select(CaseContext).where(CaseContext.case_id == case_id)
        )
        return result.scalar_one_or_none()

    async def upsert_context(
        self,
        *,
        case_id: uuid.UUID,
        context_json: dict,
        pushed_documents: dict,
        token_estimate: Optional[int],
    ) -> CaseContext:
        """
        Idempotent + versioned + race-safe upsert
        """

        existing = await self.get_context(case_id)

        # ── HASH for idempotency ──
        new_hash = self.hash_context(context_json)

        if existing:
            old_hash = self.hash_context(existing.context_json)

            # ✔ No-op if same content
            if new_hash == old_hash:
                logger.info(f"context_no_change: {case_id}")
                return existing

            # ── Save version snapshot ──
            version_row = CaseContextVersion(
                case_id=case_id,
                version=existing.version,
                context_snapshot=existing.context_json,
                token_estimate=existing.token_estimate,
            )
            self.session.add(version_row)

            # ── Update current ──
            existing.context_json = context_json
            existing.pushed_documents = pushed_documents
            existing.token_estimate = token_estimate
            existing.version += 1
            existing.last_updated_at = datetime.utcnow()

            await self.session.flush()

            logger.info(f"context_updated v{existing.version}: {case_id}")
            return existing

        # ── CREATE ──
        context = CaseContext(
            case_id=case_id,
            context_json=context_json,
            pushed_documents=pushed_documents,
            token_estimate=token_estimate,
            version=1,
        )

        self.session.add(context)
        await self.session.flush()

        logger.info(f"context_created: {case_id}")
        return context

    async def clear_context(self, case_id: uuid.UUID) -> None:
        context = await self.get_context(case_id)
        if not context:
            return

        # save version before clearing
        version_row = CaseContextVersion(
            case_id=case_id,
            version=context.version,
            context_snapshot=context.context_json,
            token_estimate=context.token_estimate,
        )
        self.session.add(version_row)

        # Preserve the system-managed _case key so case details are never lost
        case_info = context.context_json.get("_case") if context.context_json else None
        context.context_json = {"_case": case_info} if case_info else {}
        context.pushed_documents = {}
        context.token_estimate = (
            len(json.dumps(context.context_json, ensure_ascii=False)) // 4
            if context.context_json
            else None
        )
        context.version += 1
        context.last_updated_at = datetime.utcnow()

        await self.session.flush()

        logger.warning(f"context_cleared: {case_id}")

    # ─────────────────────────────
    # CaseSummary
    # ─────────────────────────────

    async def get_summary(self, case_id: uuid.UUID) -> Optional[CaseSummary]:
        result = await self.session.execute(
            select(CaseSummary).where(CaseSummary.case_id == case_id)
        )
        return result.scalar_one_or_none()

    async def upsert_summary(
        self,
        *,
        case_id: uuid.UUID,
        summary_text: str,
        context_snapshot_hash: str,
        token_count: Optional[int] = None,
    ) -> CaseSummary:
        existing = await self.get_summary(case_id)

        if existing:
            # ✔ skip if same context hash
            if existing.context_snapshot_hash == context_snapshot_hash:
                logger.info(f"summary_no_change: {case_id}")
                return existing

            existing.summary_text = summary_text
            existing.context_snapshot_hash = context_snapshot_hash
            existing.token_count = token_count
            existing.generated_at = datetime.utcnow()

            await self.session.flush()

            logger.info(f"summary_updated: {case_id}")
            return existing

        summary = CaseSummary(
            case_id=case_id,
            summary_text=summary_text,
            context_snapshot_hash=context_snapshot_hash,
            token_count=token_count,
        )

        self.session.add(summary)
        await self.session.flush()

        logger.info(f"summary_created: {case_id}")
        return summary

    # ─────────────────────────────
    # Hash helper
    # ─────────────────────────────

    @staticmethod
    def hash_context(context_json: dict) -> str:
        serialized = json.dumps(
            context_json,
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()
