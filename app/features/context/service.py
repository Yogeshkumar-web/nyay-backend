import uuid
import json
import logging
import asyncio
from pathlib import Path
from typing import Tuple, Dict, Any, List

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.context.repository import ContextRepository
from app.features.context.schemas import CaseContextResponse, CaseSummaryResponse
from app.features.extraction.models import ExtractionResult, ReviewStatus
from app.features.users.models import User

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 12000
MAX_AI_RETRIES = 3
AI_TIMEOUT = 60


# ─────────────────────────────
# Prompt cache
# ─────────────────────────────
_PROMPT_CACHE = {}


def _load_prompt(path: str) -> str:
    if path in _PROMPT_CACHE:
        return _PROMPT_CACHE[path]

    prompt_file = Path(__file__).parent.parent.parent.parent / "prompts" / path
    content = prompt_file.read_text(encoding="utf-8")
    _PROMPT_CACHE[path] = content
    return content


# ─────────────────────────────
# Token estimation
# ─────────────────────────────
def _estimate_tokens(text: str) -> int:
    return len(text) // 4


# ─────────────────────────────
# AI call — Gemini (dev) / Claude (prod)
# ─────────────────────────────


async def _call_gemini(system_prompt: str, user_text: str) -> str:
    from google import genai
    from google.genai import types as genai_types

    model = settings.GEMINI_MODEL or "gemini-2.5-flash"
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    loop = asyncio.get_running_loop()

    for attempt in range(1, MAX_AI_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=model,
                        contents=user_text,
                        config=genai_types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            max_output_tokens=3000,
                        ),
                    ),
                ),
                timeout=AI_TIMEOUT,
            )
            return response.text
        except Exception as e:
            logger.warning(f"Gemini summary attempt {attempt} failed: {e}")
            if attempt == MAX_AI_RETRIES:
                raise
            await asyncio.sleep(2 * attempt)


async def _call_claude_ai(system_prompt: str, user_text: str) -> str:
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    loop = asyncio.get_running_loop()

    for attempt in range(1, MAX_AI_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=3000,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_text}],
                    ),
                ),
                timeout=AI_TIMEOUT,
            )
            return response.content[0].text
        except Exception as e:
            logger.warning(f"Claude summary attempt {attempt} failed: {e}")
            if attempt == MAX_AI_RETRIES:
                raise
            await asyncio.sleep(2 * attempt)


async def _call_ai(system_prompt: str, user_text: str) -> str:
    """Route to Gemini (dev) or Claude (prod) based on AI_PROVIDER setting."""
    if settings.AI_PROVIDER == "gemini":
        return await _call_gemini(system_prompt, user_text)
    return await _call_claude_ai(system_prompt, user_text)


# ─────────────────────────────
# Service
# ─────────────────────────────
class ContextService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ContextRepository(db)
        self.case_repo = CaseRepository(db)

    # ─────────────────────────────
    # Access guards
    # ─────────────────────────────
    async def _require_access(self, case_id: uuid.UUID, user: User):
        if not await self.case_repo.can_access(case_id, user.id):
            raise NotFoundError("Case not found")

    async def _require_edit(self, case_id: uuid.UUID, user: User):
        if not await self.case_repo.can_edit(case_id, user.id):
            raise ForbiddenError("No edit access")

    # ─────────────────────────────
    # Get
    # ─────────────────────────────
    async def get_context(self, case_id: uuid.UUID, user: User):
        await self._require_access(case_id, user)

        context = await self.repo.get_context(case_id)
        if not context:
            return CaseContextResponse(
                id=uuid.uuid4(),
                case_id=case_id,
                context_json={},
                pushed_documents={},
                token_estimate=0,
                version=1,
                last_updated_at=__import__("datetime").datetime.utcnow(),
            )

        return CaseContextResponse.model_validate(context)

    # ─────────────────────────────
    # Push
    # ─────────────────────────────
    async def push_to_context(
        self,
        case_id: uuid.UUID,
        document_ids: List[uuid.UUID],
        user: User,
    ) -> Tuple[CaseContextResponse, CaseSummaryResponse]:
        await self._require_edit(case_id, user)

        if not document_ids:
            raise ValidationError("document_ids required")

        # ── Batch fetch (NO N+1) ──
        stmt = select(ExtractionResult).where(
            ExtractionResult.document_id.in_(document_ids),
            ExtractionResult.review_status.in_(
                [ReviewStatus.accepted, ReviewStatus.edited]
            ),
        )

        rows = (await self.db.execute(stmt)).scalars().all()

        if not rows:
            raise ValidationError("No valid extractions")

        # ── Load existing context ──
        existing = await self.repo.get_context(case_id)
        context_json = existing.context_json if existing else {}
        pushed_docs = existing.pushed_documents if existing else {}

        # ── Merge ──
        for row in rows:
            doc_id = str(row.document_id)

            context_json[doc_id] = {
                "fields": row.extracted_fields,
                "review_status": row.review_status.value,
            }

            pushed_docs[doc_id] = {
                "added_at": __import__("datetime").datetime.utcnow().isoformat()
            }

        # ── Token control ──
        context_str = json.dumps(context_json, ensure_ascii=False)
        tokens = _estimate_tokens(context_str)

        if tokens > MAX_CONTEXT_TOKENS:
            raise ValidationError("Context too large, trim required")

        # ── Save ──
        context = await self.repo.upsert_context(
            case_id=case_id,
            context_json=context_json,
            pushed_documents=pushed_docs,
            token_estimate=tokens,
        )

        # ── Summary ──
        summary = await self._generate_summary(case_id, context_json)

        logger.info(
            "context_push",
            extra={"case_id": str(case_id), "docs": len(rows)},
        )

        return (
            CaseContextResponse.model_validate(context),
            CaseSummaryResponse.model_validate(summary),
        )

    # ─────────────────────────────
    # Get Summary
    # ─────────────────────────────
    async def get_summary(self, case_id: uuid.UUID, user: User) -> CaseSummaryResponse:
        await self._require_access(case_id, user)

        summary = await self.repo.get_summary(case_id)
        if not summary:
            raise NotFoundError("No summary found for this case")

        # Compute is_stale: compare current context hash vs summary's hash
        context = await self.repo.get_context(case_id)
        is_stale = False
        if context and context.context_json:
            current_hash = ContextRepository.hash_context(context.context_json)
            is_stale = current_hash != summary.context_snapshot_hash

        response = CaseSummaryResponse.model_validate(summary)
        response.is_stale = is_stale
        return response

    # ─────────────────────────────
    # Regenerate Summary
    # ─────────────────────────────
    async def regenerate_summary(
        self, case_id: uuid.UUID, user: User
    ) -> CaseSummaryResponse:
        await self._require_edit(case_id, user)

        context = await self.repo.get_context(case_id)
        if not context or not context.context_json:
            raise ValidationError("No context found. Push documents to context first.")

        summary = await self._generate_summary(case_id, context.context_json)

        logger.info("summary_regenerated", extra={"case_id": str(case_id)})

        response = CaseSummaryResponse.model_validate(summary)
        response.is_stale = False  # freshly generated, never stale
        return response

    # ─────────────────────────────
    # Update context document content
    # ─────────────────────────────
    async def update_document_content(
        self,
        case_id: uuid.UUID,
        document_id: str,
        html_content: str,
        user: User,
    ) -> None:
        """
        Update the HTML content of a specific pushed document in context_json.
        This is called when the user edits a document in the context panel and saves.
        """
        await self._require_edit(case_id, user)

        context = await self.repo.get_context(case_id)
        if not context:
            raise ValidationError("No context found for this case")

        context_json = dict(context.context_json or {})

        if document_id not in context_json:
            raise ValidationError("Document not found in context. Push it first.")

        entry = dict(context_json[document_id])
        entry["content"] = html_content
        entry["content_type"] = "html"
        entry["updated_at"] = __import__("datetime").datetime.utcnow().isoformat()
        context_json[document_id] = entry

        token_estimate = len(json.dumps(context_json, ensure_ascii=False)) // 4

        await self.repo.upsert_context(
            case_id=case_id,
            context_json=context_json,
            pushed_documents=dict(context.pushed_documents or {}),
            token_estimate=token_estimate,
        )

        await self.db.commit()

    # ─────────────────────────────
    # Internal: Generate Summary via AI
    # ─────────────────────────────
    async def _generate_summary(self, case_id: uuid.UUID, context_json: Dict[str, Any]):
        system_prompt = _load_prompt("summary/case_summary.txt")
        context_str = json.dumps(context_json, ensure_ascii=False)

        raw = await _call_ai(
            system_prompt,
            f"Generate a case summary based on the following extracted document fields:\n\n{context_str}",
        )

        context_hash = ContextRepository.hash_context(context_json)

        return await self.repo.upsert_summary(
            case_id=case_id,
            summary_text=raw,
            context_snapshot_hash=context_hash,
        )
