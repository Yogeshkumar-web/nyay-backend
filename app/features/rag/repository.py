import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.rag.models import (
    DraftSectionSource,
    RagChunk,
    RagDocument,
    RagProcessingStatus,
    RagQueryLog,
    RagVerifiedCitation,
)
from app.features.rag.schemas import RagChunkCreate, RagDocumentCreate


class RagRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_document_by_hash(
        self,
        *,
        lawyer_id: uuid.UUID,
        file_hash: str,
    ) -> RagDocument | None:
        result = await self.session.execute(
            select(RagDocument).where(
                RagDocument.lawyer_id == lawyer_id,
                RagDocument.file_hash == file_hash,
            )
        )
        return result.scalar_one_or_none()

    async def create_document(self, data: RagDocumentCreate) -> RagDocument:
        document = RagDocument(
            lawyer_id=data.lawyer_id,
            case_id=data.case_id,
            source_document_id=data.source_document_id,
            draft_type=data.draft_type,
            file_hash=data.file_hash,
            original_filename=data.original_filename,
            source_kind=data.source_kind,
            metadata_=data.metadata,
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def set_document_status(
        self,
        document: RagDocument,
        status: RagProcessingStatus,
        *,
        error: str | None = None,
    ) -> RagDocument:
        document.processing_status = status.value
        document.processing_error = error
        await self.session.flush()
        return document

    async def create_chunk(self, data: RagChunkCreate) -> RagChunk:
        chunk = RagChunk(
            document_id=data.document_id,
            lawyer_id=data.lawyer_id,
            case_id=data.case_id,
            draft_type=data.draft_type,
            section=data.section,
            chunk_text=data.chunk_text,
            summary=data.summary,
            keywords=data.keywords,
            embedding=data.embedding,
            confidence_score=data.confidence_score,
            token_count=data.token_count,
            metadata_=data.metadata,
        )
        self.session.add(chunk)
        await self.session.flush()
        return chunk

    async def list_chunks_for_lawyer(
        self,
        *,
        lawyer_id: uuid.UUID,
        draft_type: str = "anticipatory_bail",
        case_id: uuid.UUID | None = None,
        section: str | None = None,
        limit: int = 20,
    ) -> list[RagChunk]:
        stmt = select(RagChunk).where(
            RagChunk.lawyer_id == lawyer_id,
            RagChunk.draft_type == draft_type,
        )
        if case_id is not None:
            stmt = stmt.where(RagChunk.case_id == case_id)
        if section is not None:
            stmt = stmt.where(RagChunk.section == section)
        result = await self.session.execute(stmt.limit(limit))
        return list(result.scalars().all())

    async def log_query(
        self,
        *,
        lawyer_id: uuid.UUID,
        query_text: str,
        case_id: uuid.UUID | None = None,
        filters: dict | None = None,
        confidence_score: float | None = None,
        generated: bool = False,
        chunks_used: Sequence[uuid.UUID] | None = None,
    ) -> RagQueryLog:
        log = RagQueryLog(
            lawyer_id=lawyer_id,
            case_id=case_id,
            query_text=query_text,
            filters=filters or {},
            confidence_score=confidence_score,
            generated=generated,
            chunks_used=list(chunks_used) if chunks_used else [],
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_verified_citation(
        self,
        *,
        lawyer_id: uuid.UUID,
        normalized_key: str,
    ) -> RagVerifiedCitation | None:
        result = await self.session.execute(
            select(RagVerifiedCitation).where(
                RagVerifiedCitation.lawyer_id == lawyer_id,
                RagVerifiedCitation.normalized_key == normalized_key,
                RagVerifiedCitation.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def create_draft_section_source(
        self,
        *,
        draft_id: uuid.UUID,
        lawyer_id: uuid.UUID,
        section: str,
        source_chunk_ids: Sequence[uuid.UUID],
        cited_judgments: list,
        verification_status: str,
        metadata: dict | None = None,
    ) -> DraftSectionSource:
        source = DraftSectionSource(
            draft_id=draft_id,
            lawyer_id=lawyer_id,
            section=section,
            source_chunk_ids=list(source_chunk_ids),
            cited_judgments=cited_judgments,
            verification_status=verification_status,
            metadata_=metadata or {},
        )
        self.session.add(source)
        await self.session.flush()
        return source
