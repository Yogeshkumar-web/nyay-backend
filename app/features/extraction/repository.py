import uuid
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.features.extraction.models import (
    ExtractionResult,
    ExtractionStatus,
    ReviewStatus,
    TypedVersion,
)

logger = logging.getLogger(__name__)


class ExtractionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ─────────────────────────────────────────────
    # ExtractionResult
    # ─────────────────────────────────────────────

    async def create_or_get(
        self,
        *,
        document_id: uuid.UUID,
        case_id: uuid.UUID,
    ) -> ExtractionResult:
        """
        Idempotent creation.
        """
        existing = await self.get_by_document(document_id)
        if existing:
            return existing

        result = ExtractionResult(
            document_id=document_id,
            case_id=case_id,
            extraction_status=ExtractionStatus.pending,
            review_status=ReviewStatus.pending,
        )

        self.session.add(result)
        await self.session.flush()

        logger.info(f"Extraction created: {result.id}")
        return result

    async def get_by_document(
        self, document_id: uuid.UUID
    ) -> Optional[ExtractionResult]:
        result = await self.session.execute(
            select(ExtractionResult).where(ExtractionResult.document_id == document_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, extraction_id: uuid.UUID) -> Optional[ExtractionResult]:
        result = await self.session.execute(
            select(ExtractionResult).where(ExtractionResult.id == extraction_id)
        )
        return result.scalar_one_or_none()

    async def mark_processing(self, extraction: ExtractionResult) -> ExtractionResult:
        """
        Lock state: prevents double-processing.
        """
        if extraction.extraction_status == ExtractionStatus.processing:
            raise RuntimeError("Already processing")

        extraction.extraction_status = ExtractionStatus.processing
        extraction.review_status = ReviewStatus.pending
        extraction.user_edits = None
        extraction.reviewed_by = None
        extraction.reviewed_at = None
        extraction.updated_at = datetime.utcnow()

        await self.session.flush()
        return extraction

    async def set_extraction_complete(
        self,
        extraction: ExtractionResult,
        *,
        extracted_fields: dict,
        raw_ai_response: str,
        confidence_score: Optional[float] = None,
        formatted_content: Optional[str] = None,
    ) -> ExtractionResult:
        if extraction.extraction_status != ExtractionStatus.processing:
            raise RuntimeError("Invalid state transition")

        extraction.extracted_fields = extracted_fields
        extraction.raw_ai_response = raw_ai_response
        extraction.confidence_score = confidence_score
        extraction.formatted_content = formatted_content
        extraction.extraction_status = ExtractionStatus.completed
        extraction.updated_at = datetime.utcnow()

        await self.session.flush()

        logger.info(f"Extraction completed: {extraction.id}")
        return extraction

    async def set_extraction_failed(
        self, extraction: ExtractionResult
    ) -> ExtractionResult:
        extraction.extraction_status = ExtractionStatus.failed
        extraction.updated_at = datetime.utcnow()

        await self.session.flush()

        logger.warning(f"Extraction failed: {extraction.id}")
        return extraction

    async def review(
        self,
        extraction: ExtractionResult,
        *,
        extracted_fields: dict,
        review_status: ReviewStatus,
        reviewed_by: uuid.UUID,
        formatted_content: Optional[str] = None,
    ) -> ExtractionResult:
        original = extraction.extracted_fields or {}

        diff = {k: v for k, v in extracted_fields.items() if original.get(k) != v}

        extraction.extracted_fields = extracted_fields
        extraction.user_edits = diff if diff else None
        extraction.review_status = review_status
        extraction.reviewed_by = reviewed_by
        extraction.reviewed_at = datetime.utcnow()
        extraction.updated_at = datetime.utcnow()

        # Persist user's reviewed content when provided.
        if formatted_content is not None:
            extraction.formatted_content = formatted_content

        await self.session.flush()

        logger.info(f"Extraction reviewed: {extraction.id}")
        return extraction

    # ─────────────────────────────────────────────
    # TypedVersion
    # ─────────────────────────────────────────────

    async def create_or_update_typed_version(
        self,
        *,
        document_id: uuid.UUID,
        typed_content: str,
        agent_notes: str = "",
        raw_ai_response: str | None = None,
    ) -> TypedVersion:
        """
        Idempotent typed version.

        `agent_notes` holds the pipe-delimited audit trail from the Typing Agent
        (processing_notes). `raw_ai_response` is no longer written here — kept
        on the model only for backward-compat with older rows.
        """

        values = {
            "document_id": document_id,
            "typed_content": typed_content,
            "agent_notes": agent_notes,
            "status": ReviewStatus.pending,
        }
        if raw_ai_response is not None:
            values["raw_ai_response"] = raw_ai_response

        update_values = {
            "typed_content": typed_content,
            "agent_notes": agent_notes,
            "updated_at": func.now(),
        }
        if raw_ai_response is not None:
            update_values["raw_ai_response"] = raw_ai_response

        stmt = (
            insert(TypedVersion)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[TypedVersion.document_id],
                set_=update_values,
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

        typed = await self.get_typed_version(document_id)
        if typed is None:
            raise RuntimeError("TypedVersion upsert did not return a row")

        logger.info("Typed upserted: %s", typed.id)
        return typed

    async def get_typed_version(self, document_id: uuid.UUID) -> Optional[TypedVersion]:
        result = await self.session.execute(
            select(TypedVersion).where(TypedVersion.document_id == document_id)
        )
        return result.scalar_one_or_none()

    async def review_typed_version(
        self,
        typed: TypedVersion,
        *,
        typed_content: str,
        status: ReviewStatus,
        reviewed_by: uuid.UUID,
    ) -> TypedVersion:
        typed.typed_content = typed_content
        typed.status = status
        typed.reviewed_by = reviewed_by
        typed.reviewed_at = datetime.utcnow()
        typed.updated_at = datetime.utcnow()

        await self.session.flush()

        logger.info(f"Typed reviewed: {typed.id}")
        return typed
