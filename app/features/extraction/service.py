import json
import uuid
import logging
import asyncio
import queue
import threading
from datetime import datetime
from typing import Tuple, AsyncGenerator

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.cases.repository import CaseRepository
from app.features.documents.repository import DocumentRepository
from app.features.documents.models import DocReviewStatus, DocumentType, OcrStatus
from app.features.extraction.models import ExtractionStatus
from app.features.extraction.repository import ExtractionRepository
from app.features.extraction.schemas import (
    ExtractionResultResponse,
    SaveTypedVersionRequest,
    TypedVersionResponse,
)
from app.features.users.models import User

logger = logging.getLogger(__name__)

MAX_AI_RETRIES = 3
AI_TIMEOUT = 60  # Gemini can be slower on first call


# ─────────────────────────────
# Prompt Cache (IMPORTANT)
# ─────────────────────────────
_PROMPT_CACHE = {}


def _load_prompt(path: str) -> str:
    if path in _PROMPT_CACHE:
        return _PROMPT_CACHE[path]

    from pathlib import Path

    prompt_file = Path(__file__).parent.parent.parent.parent / "prompts" / path
    if not prompt_file.exists() and path != "extraction/court_order.txt":
        logger.warning("Prompt %s not found; using court_order fallback", path)
        return _load_prompt("extraction/court_order.txt")

    content = prompt_file.read_text(encoding="utf-8")

    _PROMPT_CACHE[path] = content
    return content


EXTRACTION_PROMPT_MAP: dict[DocumentType, str] = {
    DocumentType.fir: "extraction/fir.txt",
    DocumentType.chargesheet: "extraction/chargesheet.txt",
    DocumentType.bail_rejection_order: "extraction/bail_rejection.txt",
    DocumentType.bail_order: "extraction/bail_order.txt",
    DocumentType.court_order: "extraction/court_order.txt",
    DocumentType.summon: "extraction/court_order.txt",
    DocumentType.judgment: "extraction/court_order.txt",
    DocumentType.affidavit: "extraction/affidavit.txt",
    DocumentType.counter_affidavit: "extraction/counter_affidavit.txt",
    DocumentType.rejoinder: "extraction/rejoinder.txt",
    DocumentType.vakalatnama: "extraction/vakalatnama.txt",
    DocumentType.other: "extraction/court_order.txt",
}

# Document-type-specific typing prompts.
# Types not listed here fall back to the generic HC format.
TYPING_PROMPT_MAP: dict[DocumentType, str] = {
    DocumentType.fir: "typing/fir_format.txt",
}

# HTML extraction prompts — produce Tiptap-ready HTML with structured sections
HTML_EXTRACTION_PROMPT_MAP: dict[DocumentType, str] = {
    DocumentType.fir: "typing/fir_html.txt",
    DocumentType.chargesheet: "typing/generic_document_html.txt",
    DocumentType.bail_rejection_order: "typing/generic_document_html.txt",
    DocumentType.bail_order: "typing/generic_document_html.txt",
    DocumentType.court_order: "typing/generic_document_html.txt",
    DocumentType.summon: "typing/generic_document_html.txt",
    DocumentType.judgment: "typing/generic_document_html.txt",
    DocumentType.affidavit: "typing/generic_document_html.txt",
    DocumentType.counter_affidavit: "typing/generic_document_html.txt",
    DocumentType.rejoinder: "typing/generic_document_html.txt",
    DocumentType.vakalatnama: "typing/generic_document_html.txt",
    DocumentType.other: "typing/generic_document_html.txt",
}


# ─────────────────────────────
# AI Helpers — provider-switchable
# ─────────────────────────────


async def _call_gemini(system_prompt: str, user_text: str) -> str:
    """
    Call Google Gemini API (used in development).
    Uses the new `google-genai` SDK (google-generativeai is deprecated).
    Model is configured via settings.GEMINI_MODEL:
      - "gemini-1.5-flash"  → free tier (15 RPM, 1500 RPD) ← default
      - "gemini-2.0-flash"  → paid tier (faster)
    """
    from google import genai  # lazy import — optional dep
    from google.genai import types as genai_types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    model = settings.GEMINI_MODEL or "gemini-1.5-flash"

    for attempt in range(1, MAX_AI_RETRIES + 1):
        try:
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=model,
                        contents=user_text,
                        config=genai_types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            max_output_tokens=2000,
                        ),
                    ),
                ),
                timeout=AI_TIMEOUT,
            )
            return response.text

        except Exception as e:
            logger.warning(f"Gemini attempt {attempt} failed: {e}")
            if attempt == MAX_AI_RETRIES:
                raise
            await asyncio.sleep(2 * attempt)

    raise RuntimeError("Gemini: unreachable")


async def _call_claude(system_prompt: str, user_text: str) -> str:
    """Call Anthropic Claude API (used in production)."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    for attempt in range(1, MAX_AI_RETRIES + 1):
        try:
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=1500,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_text}],
                    ),
                ),
                timeout=AI_TIMEOUT,
            )
            return response.content[0].text

        except Exception as e:
            logger.warning(f"Claude attempt {attempt} failed: {e}")
            if attempt == MAX_AI_RETRIES:
                raise
            await asyncio.sleep(2 * attempt)

    raise RuntimeError("Claude: unreachable")


async def _call_ai(system_prompt: str, user_text: str) -> str:
    """
    Unified AI dispatcher.
    Reads settings.AI_PROVIDER at call time so it can be overridden per-request in tests.
      "gemini"  → Google Gemini 2.0 Flash  (development)
      "claude"  → Anthropic Claude Sonnet  (production)
    """
    provider = (settings.AI_PROVIDER or "gemini").lower()
    if provider == "claude":
        return await _call_claude(system_prompt, user_text)
    return await _call_gemini(system_prompt, user_text)


def _safe_json_parse(raw: str) -> dict:
    try:
        clean = raw.strip()

        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]

        return json.loads(clean.strip())

    except Exception:
        return {"raw_text": raw}


def _confidence_score(data: dict) -> float:
    if not data:
        return 0.0

    total = len(data)
    filled = sum(1 for v in data.values() if v not in (None, "", [], {}))
    return round(filled / total, 3)


async def _extract_fields_via_ai(
    ocr_text: str,
    document_type: DocumentType,
) -> Tuple[dict, str, float]:
    prompt_path = EXTRACTION_PROMPT_MAP.get(
        document_type,
        "extraction/court_order.txt",
    )
    system_prompt = _load_prompt(prompt_path)
    raw = await _call_ai(system_prompt, f"Extract fields:\n\n{ocr_text}")
    fields = _safe_json_parse(raw)
    return fields, raw, _confidence_score(fields)


async def _type_document_via_ai(
    ocr_text: str,
    document_type: DocumentType | None = None,
) -> Tuple[str, str]:
    prompt_path = (
        TYPING_PROMPT_MAP.get(document_type, "typing/hc_format.txt")
        if document_type is not None
        else "typing/hc_format.txt"
    )
    system_prompt = _load_prompt(prompt_path)
    logger.debug(
        "Typing document document_type=%s prompt=%s",
        document_type,
        prompt_path,
    )
    raw = await _call_ai(
        system_prompt,
        f"Convert this OCR text into a clean typed court document:\n\n{ocr_text}",
    )
    return raw.strip(), raw


def _get_r2_client_extraction():
    """Minimal R2 client for text extraction (avoids circular import with documents/service)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


async def _extract_pdf_text(r2_bucket: str, r2_key: str) -> str:
    """Download PDF from R2 and extract text via PyMuPDF (digital PDFs only)."""
    import io
    import fitz  # PyMuPDF

    loop = asyncio.get_running_loop()

    def _do_extract():
        r2 = _get_r2_client_extraction()
        obj = r2.get_object(Bucket=r2_bucket, Key=r2_key)
        pdf_bytes = obj["Body"].read()
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n\n".join(p for p in pages if p.strip())

    return await loop.run_in_executor(None, _do_extract)


async def _stream_ai_html_gemini(
    system_prompt: str, user_text: str
) -> AsyncGenerator[str, None]:
    """Stream HTML tokens from Gemini (sync SDK in thread)."""
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
                contents=user_text,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=6000,
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


async def _stream_ai_html_claude(
    system_prompt: str, user_text: str
) -> AsyncGenerator[str, None]:
    """Stream HTML tokens from Claude."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    with client.messages.stream(
        model="claude-sonnet-4-5",
        max_tokens=6000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    ) as stream:
        for chunk in stream.text_stream:
            yield chunk


def _get_html_ai_stream(
    system_prompt: str, user_text: str
) -> AsyncGenerator[str, None]:
    """Route to Gemini (dev) or Claude (prod)."""
    provider = (settings.AI_PROVIDER or "gemini").lower()
    if provider == "claude":
        return _stream_ai_html_claude(system_prompt, user_text)
    return _stream_ai_html_gemini(system_prompt, user_text)


def _fields_to_html(fields: dict, doc_type: str) -> str:
    """
    Convert extracted JSON fields to an HTML string suitable for Tiptap.
    Grouped into logical sections based on field name patterns.
    """
    if not fields:
        return "<p><em>No fields extracted.</em></p>"

    # Section groupings — order matters
    SECTIONS: list[tuple[str, list[str]]] = [
        (
            "Key Identifiers",
            [
                "fir_number",
                "case_number",
                "court_number",
                "diary_number",
                "registration_number",
                "order_number",
            ],
        ),
        (
            "Court Details",
            [
                "court_name",
                "court_type",
                "bench_type",
                "judge_name",
                "court_district",
                "police_station",
                "jurisdiction",
            ],
        ),
        (
            "Applicable Law",
            [
                "sections",
                "acts",
                "offences",
                "charges",
                "ipc_sections",
                "crpc_sections",
                "applicable_sections",
            ],
        ),
        (
            "Parties",
            [
                "accused",
                "complainant",
                "petitioner",
                "respondent",
                "victim",
                "witness",
                "advocate",
                "applicant",
            ],
        ),
        (
            "Key Dates",
            [
                "incident_date",
                "arrest_date",
                "filing_date",
                "hearing_date",
                "order_date",
                "judgment_date",
                "date_of_fir",
                "bail_date",
            ],
        ),
        (
            "Summary",
            [
                "brief_facts",
                "allegations",
                "grounds",
                "summary",
                "order_summary",
                "observations",
                "decision",
                "directions",
            ],
        ),
    ]

    def _render_value(v) -> str:
        if isinstance(v, list):
            items = "".join(f"<li>{item}</li>" for item in v if item)
            return f"<ul>{items}</ul>" if items else ""
        if isinstance(v, dict):
            rows = "".join(
                f"<p><strong>{k.replace('_',' ').title()}:</strong> {val}</p>"
                for k, val in v.items()
                if val
            )
            return rows
        return str(v) if v not in (None, "", [], {}) else ""

    def _label(key: str) -> str:
        return key.replace("_", " ").title()

    html_parts: list[str] = [
        f"<h2>Extracted Fields — {doc_type.replace('_', ' ').title()}</h2>"
    ]

    rendered_keys: set[str] = set()

    for section_title, keys in SECTIONS:
        section_items: list[str] = []
        for key in keys:
            # Fuzzy match: field name contains the key or key contains field name
            matched = {k for k in fields if key in k.lower() or k.lower() in key}
            for k in matched:
                if k in rendered_keys:
                    continue
                val_html = _render_value(fields[k])
                if val_html:
                    section_items.append(
                        f"<p><strong>{_label(k)}:</strong> {val_html}</p>"
                    )
                rendered_keys.add(k)

        if section_items:
            html_parts.append(f"<h3>{section_title}</h3>")
            html_parts.extend(section_items)

    # Remaining fields not matched by any section
    remaining = [
        f"<p><strong>{_label(k)}:</strong> {_render_value(v)}</p>"
        for k, v in fields.items()
        if k not in rendered_keys and _render_value(v)
    ]
    if remaining:
        html_parts.append("<h3>Other Details</h3>")
        html_parts.extend(remaining)

    return "\n".join(html_parts)


# ─────────────────────────────
# Service
# ─────────────────────────────
class ExtractionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ExtractionRepository(db)
        self.doc_repo = DocumentRepository(db)
        self.case_repo = CaseRepository(db)

    # ─────────────────────────────
    # Access Guards
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
    async def get_extraction(self, document_id, user):
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_access(doc.case_id, user)

        extraction = await self.repo.get_by_document(document_id)
        if not extraction:
            raise NotFoundError("Extraction not found")

        return ExtractionResultResponse.model_validate(extraction)

    # ─────────────────────────────
    # Review
    # ─────────────────────────────
    async def review_extraction(self, extraction_id, data, user):
        extraction = await self.repo.get_by_id(extraction_id)
        if not extraction:
            raise NotFoundError("Extraction not found")

        await self._require_edit(extraction.case_id, user)

        extraction = await self.repo.review(
            extraction,
            extracted_fields=data.extracted_fields,
            review_status=data.review_status,
            reviewed_by=user.id,
            formatted_content=data.formatted_content,
        )

        await self.db.commit()
        return ExtractionResultResponse.model_validate(extraction)

    # ─────────────────────────────
    # Retry
    # ─────────────────────────────
    async def trigger_extraction(self, document_id, user):
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_edit(doc.case_id, user)

        if not doc.ocr_raw_text:
            raise ValidationError("OCR not completed")

        extraction = await self.repo.get_by_document(document_id)
        if extraction and extraction.extraction_status == ExtractionStatus.processing:
            raise ValidationError("Extraction is already running for this document")

        from app.workers.extraction_tasks import run_extraction

        try:
            task = run_extraction.delay(str(document_id))
        except Exception as exc:
            logger.exception(
                "Failed to queue extraction task for document %s", document_id
            )
            raise ValidationError(
                "Extraction worker is not available. Start Redis/Celery and retry."
            ) from exc

        logger.warning(f"Extraction triggered: {document_id}")

        return {"job_id": task.id}

    async def retry_extraction(self, document_id, user):
        return await self.trigger_extraction(document_id, user)

    # ─────────────────────────────
    # Core Extraction (Worker calls this)
    # ─────────────────────────────
    async def process_extraction(self, document_id: uuid.UUID):
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc or not doc.ocr_raw_text:
            raise RuntimeError("Invalid document")

        extraction = await self.repo.create_or_get(
            document_id=document_id,
            case_id=doc.case_id,
        )

        await self.repo.mark_processing(extraction)

        fields, raw, confidence = await _extract_fields_via_ai(
            doc.ocr_raw_text,
            doc.document_type,
        )

        # Generate HTML for the Tiptap review editor
        try:
            formatted_content = _fields_to_html(fields, doc.document_type.value)
        except Exception as fmt_exc:
            logger.warning(f"_fields_to_html failed (non-critical): {fmt_exc}")
            formatted_content = None

        extraction = await self.repo.set_extraction_complete(
            extraction,
            extracted_fields=fields,
            raw_ai_response=raw,
            confidence_score=confidence,
            formatted_content=formatted_content,
        )

        await self.db.commit()

        logger.info(f"Extraction done: {document_id}")

    # ─────────────────────────────
    # Typed Version
    # ─────────────────────────────
    async def trigger_typing(self, document_id, user):
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_edit(doc.case_id, user)

        if not doc.ocr_raw_text:
            raise ValidationError("OCR not completed")

        from app.workers.typing_tasks import run_type_document

        try:
            task = run_type_document.delay(str(document_id))
        except Exception as exc:
            logger.exception("Failed to queue typing task for document %s", document_id)
            raise ValidationError(
                "Typing worker is not available. Start Redis/Celery and retry."
            ) from exc

        return {"job_id": task.id}

    async def get_typed_version(self, document_id, user):
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_access(doc.case_id, user)

        typed = await self.repo.get_typed_version(document_id)
        if not typed:
            raise NotFoundError("Typed version not found")

        return TypedVersionResponse.model_validate(typed)

    async def review_typed_version(self, document_id, data, user):
        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_edit(doc.case_id, user)

        typed = await self.repo.get_typed_version(document_id)
        if not typed:
            raise NotFoundError("Typed version not found")

        typed = await self.repo.review_typed_version(
            typed,
            typed_content=data.typed_content,
            status=data.status,
            reviewed_by=user.id,
        )

        await self.db.commit()
        return TypedVersionResponse.model_validate(typed)

    async def save_typed_version(
        self,
        document_id: uuid.UUID,
        data: SaveTypedVersionRequest,
        user: User,
    ) -> TypedVersionResponse:
        """
        Tiptap auto-save and manual save endpoint.

        - Always sets TypedVersion.status = 'edited'
        - Sets Document.review_status = 'reviewed' so the document
          is marked as having user-reviewed content.
        - Idempotent: creates TypedVersion if it doesn't exist yet
          (handles skip-OCR path or any race condition gracefully).
        """
        from app.features.extraction.models import ReviewStatus

        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_edit(doc.case_id, user)

        typed = await self.repo.get_typed_version(document_id)

        if typed is None:
            # Lazy creation — covers skip-OCR path or missed auto-create
            typed = await self.repo.create_or_update_typed_version(
                document_id=document_id,
                typed_content=data.typed_content,
                raw_ai_response="",
            )
            # Manually set reviewed_by since create_or_update doesn't accept it
            typed.reviewed_by = user.id
            await self.db.flush()
        else:
            typed = await self.repo.review_typed_version(
                typed,
                typed_content=data.typed_content,
                status=ReviewStatus.edited,
                reviewed_by=user.id,
            )

        # Mark Document as having user-reviewed content
        if doc.review_status != DocReviewStatus.pushed:
            # Don't downgrade 'pushed' back to 'reviewed'
            doc.review_status = DocReviewStatus.reviewed
            await self.db.flush()

        await self.db.commit()
        return TypedVersionResponse.model_validate(typed)

    async def finalize_typed_version(
        self,
        document_id: uuid.UUID,
        user: User,
    ) -> TypedVersionResponse:
        """
        Mark TypedVersion as accepted — editor becomes read-only in frontend.
        Content is not changed. Can be undone by calling save again.
        """
        from app.features.extraction.models import ReviewStatus

        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_edit(doc.case_id, user)

        typed = await self.repo.get_typed_version(document_id)
        if not typed:
            raise NotFoundError(
                "No typed version found. Open the editor and save content first."
            )

        if not typed.typed_content or not typed.typed_content.strip():
            raise ValidationError(
                "Cannot accept an empty document. Add content in the editor first."
            )

        typed = await self.repo.review_typed_version(
            typed,
            typed_content=typed.typed_content,  # keep existing content unchanged
            status=ReviewStatus.accepted,
            reviewed_by=user.id,
        )

        await self.db.commit()
        return TypedVersionResponse.model_validate(typed)

    # ─────────────────────────────
    # AI HTML Extraction (streaming)
    # ─────────────────────────────

    async def run_ai_extraction_stream(
        self,
        document_id: uuid.UUID,
        user: User,
    ) -> AsyncGenerator[str, None]:
        """
        Stream structured HTML extracted from the document content.
        Uses document-type-specific prompts (FIR, court order, etc.).
        Saves result as TypedVersion on completion.

        Text priority:
          1. Existing TypedVersion content (user-edited OCR)
          2. Raw OCR text
          3. Direct PDF text extraction via PyMuPDF (for digital/skip-OCR docs)
        """
        import re as _re
        from app.features.extraction.models import ReviewStatus

        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_edit(doc.case_id, user)

        # ── 1. Get best text input ──────────────────────────────
        typed = await self.repo.get_typed_version(document_id)
        if typed and typed.typed_content and typed.typed_content.strip():
            # Strip HTML tags to get plain text for the AI
            input_text = _re.sub(r"<[^>]+>", " ", typed.typed_content).strip()
        elif doc.ocr_raw_text and doc.ocr_raw_text.strip():
            input_text = doc.ocr_raw_text.strip()
        elif (
            doc.ocr_status == OcrStatus.not_required
            and doc.mime_type == "application/pdf"
        ):
            # Digital PDF — extract text directly
            try:
                input_text = await _extract_pdf_text(doc.r2_bucket, doc.r2_key)
            except Exception as exc:
                logger.error(
                    "PDF text extraction failed for doc %s: %s", document_id, exc
                )
                yield "event: error\ndata: Could not extract text from the PDF. Check that the document is a searchable PDF.\n\n"
                return
        else:
            yield "event: error\ndata: No text content available. Run OCR first or ensure the document is a searchable digital PDF.\n\n"
            return

        if not input_text.strip():
            yield "event: error\ndata: Document appears to be empty or has no extractable text.\n\n"
            return

        # ── 2. Load prompt ──────────────────────────────────────
        prompt_path = HTML_EXTRACTION_PROMPT_MAP.get(
            doc.document_type, "typing/generic_document_html.txt"
        )
        system_prompt = _load_prompt(prompt_path)
        user_message = f"Convert this document into structured HTML:\n\n{input_text}"

        # ── 3. Stream HTML ──────────────────────────────────────
        full_html = ""
        try:
            async for chunk in _get_html_ai_stream(system_prompt, user_message):
                full_html += chunk
                # Escape newlines so SSE data lines are single-line
                escaped = chunk.replace("\n", "\\n")
                yield f"data: {escaped}\n\n"
        except Exception as exc:
            logger.error("AI extraction stream failed for doc %s: %s", document_id, exc)
            yield f"event: error\ndata: {str(exc)}\n\n"
            return

        # ── 4. Save as TypedVersion ─────────────────────────────
        try:
            existing = await self.repo.get_typed_version(document_id)
            if existing is None:
                existing = await self.repo.create_or_update_typed_version(
                    document_id=document_id,
                    typed_content=full_html,
                    raw_ai_response=full_html,
                )
            else:
                existing = await self.repo.review_typed_version(
                    existing,
                    typed_content=full_html,
                    status=ReviewStatus.edited,
                    reviewed_by=user.id,
                )

            if doc.review_status != DocReviewStatus.pushed:
                doc.review_status = DocReviewStatus.reviewed
            await self.db.commit()
        except Exception as exc:
            logger.error(
                "Failed to save AI extraction result for doc %s: %s", document_id, exc
            )

        yield "data: [DONE]\n\n"

    # ─────────────────────────────
    # Save TypedVersion + Push to Context
    # ─────────────────────────────

    async def save_and_push_to_context(
        self,
        document_id: uuid.UUID,
        html_content: str,
        user: User,
    ) -> dict:
        """
        Atomically:
          1. Save html_content as TypedVersion (status=edited)
          2. Push the HTML to context_json[doc_id] as a rich-text entry

        This replaces the old push_to_context which pushed raw text.
        """
        import json as _json
        from app.features.extraction.models import ReviewStatus
        from app.features.context.repository import ContextRepository

        doc = await self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")

        await self._require_edit(doc.case_id, user)

        if not html_content or not html_content.strip():
            raise ValidationError("html_content cannot be empty")

        # ── Save TypedVersion ───────────────────────────────────
        typed = await self.repo.get_typed_version(document_id)
        if typed is None:
            typed = await self.repo.create_or_update_typed_version(
                document_id=document_id,
                typed_content=html_content,
                raw_ai_response="",
            )
        else:
            typed = await self.repo.review_typed_version(
                typed,
                typed_content=html_content,
                status=ReviewStatus.edited,
                reviewed_by=user.id,
            )

        # ── Push to context ─────────────────────────────────────
        context_repo = ContextRepository(self.db)
        existing_ctx = await context_repo.get_context(doc.case_id)
        context_json = dict(existing_ctx.context_json) if existing_ctx else {}
        pushed_docs = dict(existing_ctx.pushed_documents) if existing_ctx else {}

        doc_id_str = str(doc.id)
        context_json[doc_id_str] = {
            "document_type": doc.document_type.value,
            "document_title": doc.display_name or doc.original_filename,
            "content": html_content,
            "content_type": "html",
            "pushed_at": datetime.utcnow().isoformat(),
        }
        pushed_docs[doc_id_str] = {
            "document_type": doc.document_type.value,
            "filename": doc.display_name or doc.original_filename,
            "added_at": datetime.utcnow().isoformat(),
            "source": "ai_extraction",
        }

        token_estimate = len(_json.dumps(context_json, ensure_ascii=False)) // 4
        await context_repo.upsert_context(
            case_id=doc.case_id,
            context_json=context_json,
            pushed_documents=pushed_docs,
            token_estimate=token_estimate,
        )

        # Mark document as pushed
        doc.review_status = DocReviewStatus.pushed
        await self.db.commit()

        logger.info(
            "save_and_push_to_context: doc=%s case=%s", document_id, doc.case_id
        )
        return {
            "case_id": str(doc.case_id),
            "document_id": doc_id_str,
        }
