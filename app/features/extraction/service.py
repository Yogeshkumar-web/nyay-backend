import json
import uuid
from typing import Optional

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.documents.models import DocumentType
from app.features.documents.repository import DocumentRepository
from app.features.extraction.models import ExtractionResult, ReviewStatus, TypedVersion
from app.features.extraction.repository import ExtractionRepository
from app.features.extraction.schemas import (
    ExtractionResultResponse,
    ReviewExtractionRequest,
    ReviewTypedVersionRequest,
    TypedVersionResponse,
)
from app.features.users.models import User

# ── Prompt loader ─────────────────────────────────────────────────────────────

def _load_prompt(path: str) -> str:
    from pathlib import Path
    prompt_file = Path(__file__).parent.parent.parent.parent / "prompts" / path
    return prompt_file.read_text(encoding="utf-8")


# Document type → prompt file mapping
EXTRACTION_PROMPT_MAP: dict[DocumentType, str] = {
    DocumentType.fir: "extraction/fir.txt",
    DocumentType.chargesheet: "extraction/chargesheet.txt",
    DocumentType.bail_rejection_order: "extraction/bail_rejection.txt",
    DocumentType.bail_order: "extraction/bail_order.txt",
    DocumentType.court_order: "extraction/court_order.txt",
    DocumentType.summon: "extraction/court_order.txt",
    DocumentType.judgment: "extraction/court_order.txt",
    DocumentType.affidavit: "extraction/court_order.txt",
    DocumentType.counter_affidavit: "extraction/court_order.txt",
    DocumentType.rejoinder: "extraction/court_order.txt",
    DocumentType.vakalatnama: "extraction/court_order.txt",
    DocumentType.other: "extraction/court_order.txt",
}


# ── Claude API caller ─────────────────────────────────────────────────────────

def _get_claude_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


async def _extract_fields_via_claude(
    ocr_text: str,
    document_type: DocumentType,
) -> tuple[dict, str, float]:
    """
    Calls Claude to extract structured fields from OCR text.
    Returns: (extracted_fields, raw_response, confidence_score)
    """
    prompt_path = EXTRACTION_PROMPT_MAP.get(document_type, "extraction/court_order.txt")
    system_prompt = _load_prompt(prompt_path)

    client = _get_claude_client()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Extract fields from this document:\n\n{ocr_text}",
            }
        ],
    )

    raw_response = message.content[0].text

    # Parse JSON from response
    try:
        # Strip markdown code fences if present
        clean = raw_response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        extracted_fields = json.loads(clean.strip())
    except json.JSONDecodeError:
        extracted_fields = {"raw_text": raw_response}

    # Simple confidence: 1.0 if all fields populated, lower if many are null
    total = len(extracted_fields)
    filled = sum(1 for v in extracted_fields.values() if v not in (None, "", [], {}))
    confidence = round(filled / total, 3) if total > 0 else 0.5

    return extracted_fields, raw_response, confidence


async def _type_document_via_claude(ocr_text: str) -> tuple[str, str]:
    """
    Calls Claude to convert OCR text to HC-formatted typed version.
    Returns: (typed_content, raw_response)
    """
    system_prompt = _load_prompt("typing/hc_format.txt")

    client = _get_claude_client()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Convert this OCR text to HC-formatted typed version:\n\n{ocr_text}",
            }
        ],
    )

    raw_response = message.content[0].text
    return raw_response, raw_response


# ── Extraction Service ────────────────────────────────────────────────────────

class ExtractionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ExtractionRepository(db)
        self.doc_repo = DocumentRepository(db)
        self.case_repo = CaseRepository(db)

    async def _require_access(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_access(case_id, user.id):
            raise NotFoundError("Case not found")

    async def _require_edit(self, case_id: uuid.UUID, user: User) -> None:
        if not await self.case_repo.can_edit(case_id, user.id):
            raise ForbiddenError("You do not have edit access to this case")

    # ── Get extraction ─────────────────────────────────────────────────────────

    async def get_extraction(
        self, document_id: uuid.UUID, current_user: User
    ) -> ExtractionResultResponse:
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_access(doc.case_id, current_user)

        extraction = await self.repo.get_by_document(document_id)
        if not extraction:
            raise NotFoundError("Extraction result not found")

        return ExtractionResultResponse.model_validate(extraction)

    # ── Review extraction ──────────────────────────────────────────────────────

    async def review_extraction(
        self,
        extraction_id: uuid.UUID,
        data: ReviewExtractionRequest,
        current_user: User,
    ) -> ExtractionResultResponse:
        extraction = await self.repo.get_by_id(extraction_id)
        if not extraction:
            raise NotFoundError("Extraction result not found")

        await self._require_edit(extraction.case_id, current_user)

        extraction = await self.repo.review(
            extraction,
            extracted_fields=data.extracted_fields,
            review_status=data.review_status,
            reviewed_by=current_user.id,
        )
        return ExtractionResultResponse.model_validate(extraction)

    # ── Retry extraction ───────────────────────────────────────────────────────

    async def retry_extraction(
        self, document_id: uuid.UUID, current_user: User
    ) -> dict:
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_edit(doc.case_id, current_user)

        if not doc.ocr_raw_text:
            raise ValidationError("OCR not completed yet — cannot retry extraction")

        from app.workers.extraction_tasks import run_extraction
        task = run_extraction.delay(str(document_id))
        return {"job_id": task.id}

    # ── Typed version ──────────────────────────────────────────────────────────

    async def trigger_typing(
        self, document_id: uuid.UUID, current_user: User
    ) -> dict:
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_edit(doc.case_id, current_user)

        if not doc.ocr_raw_text:
            raise ValidationError("OCR not completed yet")

        from app.workers.typing_tasks import run_type_document
        task = run_type_document.delay(str(document_id))
        return {"job_id": task.id}

    async def get_typed_version(
        self, document_id: uuid.UUID, current_user: User
    ) -> TypedVersionResponse:
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_access(doc.case_id, current_user)

        typed = await self.repo.get_typed_version(document_id)
        if not typed:
            raise NotFoundError("Typed version not found")

        return TypedVersionResponse.model_validate(typed)

    async def review_typed_version(
        self,
        document_id: uuid.UUID,
        data: ReviewTypedVersionRequest,
        current_user: User,
    ) -> TypedVersionResponse:
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._require_edit(doc.case_id, current_user)

        typed = await self.repo.get_typed_version(document_id)
        if not typed:
            raise NotFoundError("Typed version not found")

        typed = await self.repo.review_typed_version(
            typed,
            typed_content=data.typed_content,
            status=data.status,
            reviewed_by=current_user.id,
        )
        return TypedVersionResponse.model_validate(typed)