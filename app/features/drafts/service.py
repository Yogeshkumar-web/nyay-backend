import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.context.repository import ContextRepository
from app.features.drafts.models import Draft, DraftStatus, DraftType
from app.features.drafts.repository import DraftRepository
from app.features.drafts.schemas import (
    CreateDraftRequest, DraftResponse, UpdateDraftRequest
)
from app.features.users.models import User


def _load_prompt(path: str) -> str:
    prompt_file = Path(__file__).parent.parent.parent.parent / "prompts" / path
    return prompt_file.read_text(encoding="utf-8")


DRAFT_PROMPT_MAP: dict[DraftType, str] = {
    DraftType.bail_application: "drafts/bail_application.txt",
    DraftType.anticipatory_bail: "drafts/anticipatory_bail.txt",
    DraftType.writ_petition: "drafts/writ_petition.txt",
    DraftType.pil: "drafts/writ_petition.txt",
    DraftType.criminal_revision: "drafts/criminal_revision.txt",
    DraftType.quashing_petition: "drafts/quashing_petition.txt",
    DraftType.affidavit: "drafts/bail_application.txt",
    DraftType.counter_affidavit: "drafts/bail_application.txt",
    DraftType.rejoinder: "drafts/bail_application.txt",
    DraftType.synopsis: "drafts/bail_application.txt",
    DraftType.other: "drafts/bail_application.txt",
}

DRAFT_TYPE_TITLES: dict[DraftType, str] = {
    DraftType.bail_application: "Bail Application",
    DraftType.anticipatory_bail: "Anticipatory Bail Application",
    DraftType.writ_petition: "Writ Petition",
    DraftType.pil: "Public Interest Litigation",
    DraftType.criminal_revision: "Criminal Revision",
    DraftType.quashing_petition: "Quashing Petition",
    DraftType.affidavit: "Affidavit",
    DraftType.counter_affidavit: "Counter Affidavit",
    DraftType.rejoinder: "Rejoinder",
    DraftType.synopsis: "Synopsis",
    DraftType.other: "Legal Draft",
}


class DraftService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = DraftRepository(db)
        self.case_repo = CaseRepository(db)
        self.context_repo = ContextRepository(db)

    async def _require_access(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_access(case_id, user.id):
            raise NotFoundError("Case not found")

    async def _require_edit(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_edit(case_id, user.id):
            raise ForbiddenError("You do not have edit access")

    # ── Create draft ───────────────────────────────────────────────────────────

    async def create_draft(
        self, case_id: uuid.UUID, data: CreateDraftRequest, user: User
    ) -> DraftResponse:
        await self._require_edit(case_id, user)

        # Check context exists
        context = await self.context_repo.get_context(case_id)
        if not context or not context.context_json:
            raise ValidationError("No context found. Push documents to context first.")

        # Check summary is not stale
        summary = await self.context_repo.get_summary(case_id)
        if summary:
            current_hash = ContextRepository.hash_context(context.context_json)
            if current_hash != summary.context_snapshot_hash:
                raise ValidationError(
                    "Summary is stale. Regenerate summary before generating a draft."
                )

        title = data.title or DRAFT_TYPE_TITLES.get(data.draft_type, "Legal Draft")
        prompt_path = DRAFT_PROMPT_MAP.get(data.draft_type, "drafts/bail_application.txt")

        draft = await self.repo.create(
            case_id=case_id,
            created_by=user.id,
            draft_type=data.draft_type,
            title=title,
            ai_prompt_used=prompt_path,
        )
        return DraftResponse.model_validate(draft)

    # ── Stream draft ───────────────────────────────────────────────────────────

    async def stream_draft(
        self, draft_id: uuid.UUID, user: User
    ) -> AsyncGenerator[str, None]:
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_access(draft.case_id, user)

        context = await self.context_repo.get_context(draft.case_id)
        summary = await self.context_repo.get_summary(draft.case_id)

        import json
        system_prompt = _load_prompt(draft.ai_prompt_used or "drafts/bail_application.txt")

        context_block = json.dumps(context.context_json if context else {}, ensure_ascii=False)
        summary_block = summary.summary_text if summary else "No summary available."

        user_message = (
            f"Case Summary:\n{summary_block}\n\n"
            f"Extracted Facts:\n{context_block}\n\n"
            f"Generate a {draft.title}."
        )

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        full_content = ""

        with client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                full_content += text
                yield f"data: {text}\n\n"

        # Save completed content
        await self.repo.set_content(draft, full_content, DraftStatus.ready)
        yield "data: [DONE]\n\n"

    # ── List / Get / Update / Fork / Archive ───────────────────────────────────

    async def list_drafts(self, case_id: uuid.UUID, user: User) -> list[DraftResponse]:
        await self._require_access(case_id, user)
        drafts = await self.repo.list_for_case(case_id)
        return [DraftResponse.model_validate(d) for d in drafts]

    async def get_draft(self, draft_id: uuid.UUID, user: User) -> DraftResponse:
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")
        await self._require_access(draft.case_id, user)
        return DraftResponse.model_validate(draft)

    async def update_draft(
        self, draft_id: uuid.UUID, data: UpdateDraftRequest, user: User
    ) -> DraftResponse:
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")
        await self._require_edit(draft.case_id, user)
        draft = await self.repo.update(draft, data)
        return DraftResponse.model_validate(draft)

    async def fork_draft(self, draft_id: uuid.UUID, user: User) -> DraftResponse:
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")
        await self._require_edit(draft.case_id, user)
        new_draft = await self.repo.fork(draft, user.id)
        return DraftResponse.model_validate(new_draft)

    async def archive_draft(self, draft_id: uuid.UUID, user: User) -> None:
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")
        await self._require_edit(draft.case_id, user)
        await self.repo.archive(draft)