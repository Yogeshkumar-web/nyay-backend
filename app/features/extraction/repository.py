import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.extraction.models import (
    ExtractionResult, ExtractionStatus, ReviewStatus, TypedVersion
)


class ExtractionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── ExtractionResult ──────────────────────────────────────────────────────

    async def create(
        self,
        *,
        document_id: uuid.UUID,
        case_id: uuid.UUID,
    ) -> ExtractionResult:
        result = ExtractionResult(
            document_id=document_id,
            case_id=case_id,
            extraction_status=ExtractionStatus.pending,
            review_status=ReviewStatus.pending,
        )
        self.session.add(result)
        await self.session.flush()
        return result

    async def get_by_document(self, document_id: uuid.UUID) -> ExtractionResult | None:
        result = await self.session.execute(
            select(ExtractionResult).where(
                ExtractionResult.document_id == document_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, extraction_id: uuid.UUID) -> ExtractionResult | None:
        result = await self.session.execute(
            select(ExtractionResult).where(ExtractionResult.id == extraction_id)
        )
        return result.scalar_one_or_none()

    async def set_extraction_complete(
        self,
        extraction: ExtractionResult,
        *,
        extracted_fields: dict,
        raw_ai_response: str,
        confidence_score: Optional[float] = None,
    ) -> ExtractionResult:
        extraction.extracted_fields = extracted_fields
        extraction.raw_ai_response = raw_ai_response
        extraction.confidence_score = confidence_score
        extraction.extraction_status = ExtractionStatus.completed
        extraction.updated_at = datetime.utcnow()
        await self.session.flush()
        return extraction

    async def set_extraction_failed(
        self, extraction: ExtractionResult
    ) -> ExtractionResult:
        extraction.extraction_status = ExtractionStatus.failed
        extraction.updated_at = datetime.utcnow()
        await self.session.flush()
        return extraction

    async def review(
        self,
        extraction: ExtractionResult,
        *,
        extracted_fields: dict,
        review_status: ReviewStatus,
        reviewed_by: uuid.UUID,
    ) -> ExtractionResult:
        # Store diff in user_edits
        original = extraction.extracted_fields or {}
        diff = {
            k: v for k, v in extracted_fields.items()
            if original.get(k) != v
        }
        extraction.extracted_fields = extracted_fields
        extraction.user_edits = diff if diff else None
        extraction.review_status = review_status
        extraction.reviewed_by = reviewed_by
        extraction.reviewed_at = datetime.utcnow()
        extraction.updated_at = datetime.utcnow()
        await self.session.flush()
        return extraction

    # ── TypedVersion ──────────────────────────────────────────────────────────

    async def create_typed_version(
        self,
        *,
        document_id: uuid.UUID,
        typed_content: str,
        raw_ai_response: str,
    ) -> TypedVersion:
        typed = TypedVersion(
            document_id=document_id,
            typed_content=typed_content,
            raw_ai_response=raw_ai_response,
            status=ReviewStatus.pending,
        )
        self.session.add(typed)
        await self.session.flush()
        return typed

    async def get_typed_version(self, document_id: uuid.UUID) -> TypedVersion | None:
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
        return typed