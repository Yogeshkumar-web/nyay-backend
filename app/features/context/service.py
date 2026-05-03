import uuid
from pathlib import Path
from typing import Optional

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.context.models import CaseContext, CaseSummary
from app.features.context.repository import ContextRepository
from app.features.context.schemas import CaseContextResponse, CaseSummaryResponse
from app.features.extraction.models import ReviewStatus
from app.features.extraction.repository import ExtractionRepository
from app.features.users.models import User


def _load_prompt(path: str) -> str:
    prompt_file = Path(__file__).parent.parent.parent.parent / "prompts" / path
    return prompt_file.read_text(encoding="utf-8")


def _estimate_tokens(text: str) -> int:
    """Rough estimate: 1 token ≈ 4 characters."""
    return len(text) // 4


class ContextService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ContextRepository(db)
        self.case_repo = CaseRepository(db)
        self.ext_repo = ExtractionRepository(db)

    async def _require_access(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_access(case_id, user.id):
            raise NotFoundError("Case not found")

    async def _require_edit(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_edit(case_id, user.id):
            raise ForbiddenError("You do not have edit access to this case")

    # ── Get context ────────────────────────────────────────────────────────────

    async def get_context(self, case_id: uuid.UUID, user: User) -> CaseContextResponse:
        await self._require_access(case_id, user)
        context = await self.repo.get_context(case_id)
        if not context:
            # Return empty context
            return CaseContextResponse(
                id=uuid.uuid4(),
                case_id=case_id,
                context_json={},
                pushed_document_ids=[],
                token_estimate=0,
                last_updated_at=__import__('datetime').datetime.utcnow(),
            )
        return CaseContextResponse.model_validate(context)

    # ── Push to context ────────────────────────────────────────────────────────

    async def push_to_context(
        self,
        case_id: uuid.UUID,
        document_ids: list[uuid.UUID],
        user: User,
    ) -> tuple[CaseContextResponse, CaseSummaryResponse]:
        await self._require_edit(case_id, user)

        if not document_ids:
            raise ValidationError("At least one document_id required")

        # Fetch accepted/edited extractions for these documents
        context_json: dict = {}
        valid_doc_ids = []

        for doc_id in document_ids:
            extraction = await self.ext_repo.get_by_document(doc_id)
            if not extraction:
                continue
            if extraction.review_status not in (ReviewStatus.accepted, ReviewStatus.edited):
                continue

            # Merge fields into context
            context_json[str(doc_id)] = {
                "document_id": str(doc_id),
                "review_status": extraction.review_status.value,
                "fields": extraction.extracted_fields,
            }
            valid_doc_ids.append(doc_id)

        if not context_json:
            raise ValidationError(
                "No accepted/edited extractions found for provided documents. "
                "Review and accept extractions first."
            )

        # Estimate tokens
        import json
        context_str = json.dumps(context_json, ensure_ascii=False)
        token_estimate = _estimate_tokens(context_str)

        # Save context
        context = await self.repo.upsert_context(
            case_id=case_id,
            context_json=context_json,
            pushed_document_ids=valid_doc_ids,
            token_estimate=token_estimate,
        )

        # Auto-generate summary
        summary = await self._generate_summary(case_id, context_json)

        return (
            CaseContextResponse.model_validate(context),
            CaseSummaryResponse.model_validate(summary),
        )

    # ── Clear context ──────────────────────────────────────────────────────────

    async def clear_context(self, case_id: uuid.UUID, user: User) -> CaseContextResponse:
        await self._require_edit(case_id, user)
        await self.repo.clear_context(case_id)
        return await self.get_context(case_id, user)

    # ── Summary ────────────────────────────────────────────────────────────────

    async def get_summary(self, case_id: uuid.UUID, user: User) -> CaseSummaryResponse:
        await self._require_access(case_id, user)

        summary = await self.repo.get_summary(case_id)
        if not summary:
            raise NotFoundError("No summary yet. Push documents to context first.")

        # Check staleness
        context = await self.repo.get_context(case_id)
        is_stale = False
        if context:
            current_hash = ContextRepository.hash_context(context.context_json)
            is_stale = current_hash != summary.context_snapshot_hash

        result = CaseSummaryResponse.model_validate(summary)
        result.is_stale = is_stale
        return result

    async def regenerate_summary(self, case_id: uuid.UUID, user: User) -> CaseSummaryResponse:
        await self._require_edit(case_id, user)

        context = await self.repo.get_context(case_id)
        if not context or not context.context_json:
            raise ValidationError("No context found. Push documents first.")

        summary = await self._generate_summary(case_id, context.context_json)
        return CaseSummaryResponse.model_validate(summary)

    async def _generate_summary(self, case_id: uuid.UUID, context_json: dict) -> CaseSummary:
        import json

        system_prompt = _load_prompt("summary/case_summary.txt")
        context_str = json.dumps(context_json, ensure_ascii=False, indent=2)

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Generate case summary from:\n\n{context_str}"}],
        )

        summary_text = message.content[0].text
        context_hash = ContextRepository.hash_context(context_json)

        summary = await self.repo.upsert_summary(
            case_id=case_id,
            summary_text=summary_text,
            context_snapshot_hash=context_hash,
        )
        return summary