import uuid
import json
from pathlib import Path
from typing import AsyncGenerator

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.context.repository import ContextRepository
from app.features.drafts.models import Draft, DraftStatus, DraftType
from app.features.drafts.repository import DraftRepository
from app.features.drafts.schemas import (
    CreateDraftRequest,
    DraftResponse,
    ReviewDraftRequest,
    UpdateDraftRequest,
)
from app.features.rag.repository import RagRepository
from app.features.rag.schemas import (
    CitedJudgment,
    DraftAssemblyRequest,
    DraftAssemblySection,
    RagObservabilityEventCreate,
    RagGenerateSectionRequest,
)
from app.features.rag.service import RagService
from app.features.users.models import User


# ============================================================
# AI STREAMING HELPERS
# ============================================================


async def _stream_gemini(
    system_prompt: str, user_message: str
) -> AsyncGenerator[str, None]:
    """Stream tokens from Gemini — runs sync SDK in thread to avoid blocking event loop."""
    import asyncio
    import queue
    import threading
    from google import genai
    from google.genai import types as genai_types

    model = settings.GEMINI_MODEL or "gemini-2.5-flash"
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    q: queue.Queue = queue.Queue()
    _DONE = object()

    def _run():
        try:
            response = client.models.generate_content_stream(
                model=model,
                contents=user_message,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=4000,
                ),
            )
            for chunk in response:
                if chunk.text:
                    q.put(chunk.text)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(_DONE)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, q.get)
        if item is _DONE:
            break
        if isinstance(item, Exception):
            raise item
        yield item


async def _stream_claude(
    system_prompt: str, user_message: str
) -> AsyncGenerator[str, None]:
    """Stream tokens from Anthropic Claude."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    with client.messages.stream(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for chunk in stream.text_stream:
            yield chunk


def _get_ai_stream(system_prompt: str, user_message: str) -> AsyncGenerator[str, None]:
    """Route to Gemini (dev) or Claude (prod) based on AI_PROVIDER setting."""
    if settings.AI_PROVIDER == "gemini":
        return _stream_gemini(system_prompt, user_message)
    return _stream_claude(system_prompt, user_message)


# ============================================================
# PROMPT MAPS (FIXED)
# ============================================================

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


# ============================================================
# UTIL
# ============================================================


def _load_prompt(path: str) -> str:
    prompt_file = Path(__file__).parent.parent.parent.parent / "prompts" / path
    if not prompt_file.exists():
        raise RuntimeError(f"Prompt file not found: {path}")
    return prompt_file.read_text(encoding="utf-8")


def _build_advocate_block(user: "User") -> str:
    """Build a structured advocate-info block to prepend to every draft prompt."""
    lines = ["Appearing Advocate Information:"]
    if user.full_name:
        lines.append(f"  Name: {user.full_name}")
    if user.enrollment_number:
        lines.append(f"  Enrollment No.: {user.enrollment_number}")
    if user.designation:
        designation_labels = {
            "advocate": "Advocate",
            "aor": "Advocate-on-Record (AoR)",
            "senior_advocate": "Senior Advocate",
        }
        lines.append(
            f"  Designation: {designation_labels.get(user.designation.value, user.designation.value)}"
        )
    if user.chamber_number:
        lines.append(f"  Chamber No.: {user.chamber_number}")
    if user.court_name:
        lines.append(f"  Court: {user.court_name}")
    if user.mobile_number:
        lines.append(f"  Mobile: {user.mobile_number}")
    return "\n".join(lines)


def _build_user_message(
    summary: str, context_json: dict, title: str, user: "User | None" = None
) -> str:
    advocate_block = f"\n{_build_advocate_block(user)}\n" if user else ""
    return (
        f"Case Summary:\n{summary}\n\n"
        f"Extracted Facts (JSON):\n{json.dumps(context_json, ensure_ascii=False)}\n"
        f"{advocate_block}\n"
        f"Generate a professional {title}.\n"
        f"Follow proper High Court formatting."
    )


def _flatten_context_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(_flatten_context_text(item) for item in value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            text = _flatten_context_text(item)
            if text:
                parts.append(f"{key}: {text}")
        return "\n".join(parts)
    return str(value)


def _extract_sections_invoked(value: object) -> list[str]:
    matches: list[str] = []

    def walk(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                key_text = str(key).lower()
                if any(token in key_text for token in ("section", "act", "ipc", "crpc")):
                    text = _flatten_context_text(nested)
                    if text:
                        matches.append(text)
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)
    seen: set[str] = set()
    deduped: list[str] = []
    for match in matches:
        normalized = " ".join(match.split())
        if normalized and normalized.lower() not in seen:
            deduped.append(normalized)
            seen.add(normalized.lower())
    return deduped[:20]


def _unique_citations(citations: list[CitedJudgment]) -> list[CitedJudgment]:
    seen: set[str] = set()
    unique: list[CitedJudgment] = []
    for citation in citations:
        key = "|".join(
            [
                citation.case_name.lower().strip(),
                (citation.citation or "").lower().strip(),
                str(citation.year or ""),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(citation)
    return unique


def _compact_sse_payload(content: str) -> str:
    return " ".join(line.strip() for line in content.splitlines() if line.strip())


# ============================================================
# SERVICE
# ============================================================


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

    # ------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------

    async def create_draft(
        self, case_id: uuid.UUID, data: CreateDraftRequest, user: User
    ) -> DraftResponse:
        await self._require_edit(case_id, user)

        context = await self.context_repo.get_context(case_id)
        if not context or not context.context_json:
            raise ValidationError("No context found. Push documents first.")

        summary = await self.context_repo.get_summary(case_id)
        if summary:
            current_hash = ContextRepository.hash_context(context.context_json)
            if current_hash != summary.context_snapshot_hash:
                raise ValidationError("Summary is stale. Regenerate first.")

        title = data.title or DRAFT_TYPE_TITLES.get(data.draft_type, "Legal Draft")

        prompt_path = DRAFT_PROMPT_MAP.get(
            data.draft_type, "drafts/bail_application.txt"
        )

        draft = await self.repo.create(
            case_id=case_id,
            created_by=user.id,
            draft_type=data.draft_type,
            title=title,
            ai_prompt_used=prompt_path,
        )

        return DraftResponse.model_validate(draft)

    # ------------------------------------------------------------
    # STREAM
    # ------------------------------------------------------------

    async def stream_draft(
        self, draft_id: uuid.UUID, user: User
    ) -> AsyncGenerator[str, None]:
        draft = await self.repo.get_by_id(draft_id, for_update=True)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_access(draft.case_id, user)

        if draft.status != DraftStatus.generating:
            raise ValidationError("Draft is not in generating state")

        context = await self.context_repo.get_context(draft.case_id)
        summary = await self.context_repo.get_summary(draft.case_id)

        if draft.draft_type == DraftType.anticipatory_bail:
            try:
                full_content = await self._generate_rag_anticipatory_bail_draft(
                    draft=draft,
                    user=user,
                    context_json=context.context_json if context else {},
                    summary_text=summary.summary_text if summary else "",
                )
                yield f"data: {_compact_sse_payload(full_content)}\n\n"
                await self.repo.set_content(draft, full_content, DraftStatus.ready)
                yield "data: [DONE]\n\n"
            except Exception as e:
                await self.repo.set_content(
                    draft,
                    "RAG draft generation failed.",
                    DraftStatus.editing,
                )
                yield f"event: error\ndata: {str(e)}\n\n"
            return

        system_prompt = _load_prompt(
            draft.ai_prompt_used or "drafts/bail_application.txt"
        )

        user_message = _build_user_message(
            summary.summary_text if summary else "No summary available.",
            context.context_json if context else {},
            draft.title,
            user=user,
        )

        full_content = ""

        try:
            async for chunk in _get_ai_stream(system_prompt, user_message):
                full_content += chunk
                yield f"data: {chunk}\n\n"

        except Exception as e:
            await self.repo.set_content(
                draft,
                full_content or "Draft generation failed.",
                DraftStatus.editing,
            )
            yield f"event: error\ndata: {str(e)}\n\n"
            return

        await self.repo.set_content(draft, full_content, DraftStatus.ready)
        yield "data: [DONE]\n\n"

    async def _generate_rag_anticipatory_bail_draft(
        self,
        *,
        draft: Draft,
        user: User,
        context_json: dict,
        summary_text: str,
    ) -> str:
        rag = RagService(RagRepository(self.db))
        case_facts = "\n\n".join(
            part
            for part in (
                summary_text.strip(),
                _flatten_context_text(context_json).strip(),
            )
            if part
        )
        if len(case_facts) < 20:
            raise ValidationError("Not enough reviewed case facts for RAG draft generation.")

        sections_invoked = _extract_sections_invoked(context_json)
        generated_sections: list[DraftAssemblySection] = []
        citations: list[CitedJudgment] = []

        for section in ("facts", "grounds", "prayer"):
            response = await rag.generate_section(
                lawyer_id=user.id,
                request=RagGenerateSectionRequest(
                    case_facts=case_facts,
                    sections_invoked=sections_invoked,
                    target_section=section,
                    case_id=draft.case_id,
                    draft_id=draft.id,
                    draft_type=draft.draft_type.value,
                ),
            )
            if response.status != "generated" or not response.content:
                raise ValidationError(
                    response.refusal_reason
                    or f"RAG generation refused for {section} section."
                )
            generated_sections.append(
                DraftAssemblySection(
                    section=section,
                    content=response.content,
                    source_chunk_ids=response.source_chunk_ids,
                    confidence_score=response.confidence_score,
                )
            )
            citations.extend(response.cited_judgments)

        assembly = await rag.assemble_draft(
            request=DraftAssemblyRequest(
                case_metadata={
                    "draft_title": draft.title,
                    "case_id": str(draft.case_id),
                    "lawyer_id": str(user.id),
                    "sections_invoked": ", ".join(sections_invoked),
                },
                sections=generated_sections,
                verified_citations=_unique_citations(citations),
            )
        )
        return assembly.html

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------

    async def list_drafts(self, case_id: uuid.UUID, user: User):
        await self._require_access(case_id, user)
        drafts = await self.repo.list_for_case(case_id)
        return [DraftResponse.model_validate(d) for d in drafts]

    async def get_draft(self, draft_id: uuid.UUID, user: User):
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_access(draft.case_id, user)
        return DraftResponse.model_validate(draft)

    async def update_draft(
        self, draft_id: uuid.UUID, data: UpdateDraftRequest, user: User
    ):
        draft = await self.repo.get_by_id(draft_id, for_update=True)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_edit(draft.case_id, user)

        previous_content = draft.content or ""
        draft = await self.repo.update(draft, data)
        if data.content is not None and data.content != previous_content:
            await RagRepository(self.db).create_observability_event(
                RagObservabilityEventCreate(
                    lawyer_id=user.id,
                    case_id=draft.case_id,
                    draft_id=draft.id,
                    event_type="lawyer_edit_detected",
                    severity="info",
                    metrics={
                        "previous_content_chars": len(previous_content),
                        "new_content_chars": len(data.content),
                        "delta_chars": len(data.content) - len(previous_content),
                    },
                    metadata={"status": draft.status.value},
                )
            )
        return DraftResponse.model_validate(draft)

    async def review_draft(
        self, draft_id: uuid.UUID, data: ReviewDraftRequest, user: User
    ):
        draft = await self.repo.get_by_id(draft_id, for_update=True)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_edit(draft.case_id, user)

        draft = await self.repo.mark_reviewed_final(
            draft,
            reviewed_by=user.id,
            content=data.content,
            title=data.title,
        )
        await RagRepository(self.db).create_observability_event(
            RagObservabilityEventCreate(
                lawyer_id=user.id,
                case_id=draft.case_id,
                draft_id=draft.id,
                event_type="draft_reviewed",
                severity="info",
                metrics={"content_chars": len(data.content), "revision": draft.revision},
                metadata={"status": draft.status.value},
            )
        )
        return DraftResponse.model_validate(draft)

    async def mark_exported(self, draft_id: uuid.UUID, user: User):
        draft = await self.repo.get_by_id(draft_id, for_update=True)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_edit(draft.case_id, user)

        draft = await self.repo.mark_exported(draft, exported_by=user.id)
        await RagRepository(self.db).create_observability_event(
            RagObservabilityEventCreate(
                lawyer_id=user.id,
                case_id=draft.case_id,
                draft_id=draft.id,
                event_type="draft_exported",
                severity="info",
                metrics={"revision": draft.revision},
                metadata={"status": draft.status.value},
            )
        )
        return DraftResponse.model_validate(draft)

    async def fork_draft(self, draft_id: uuid.UUID, user: User):
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_edit(draft.case_id, user)

        new_draft = await self.repo.fork(draft, user.id)
        return DraftResponse.model_validate(new_draft)

    async def archive_draft(self, draft_id: uuid.UUID, user: User):
        draft = await self.repo.get_by_id(draft_id)
        if not draft:
            raise NotFoundError("Draft not found")

        await self._require_edit(draft.case_id, user)

        await self.repo.archive(draft)
