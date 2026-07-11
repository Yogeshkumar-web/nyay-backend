import uuid
from typing import Sequence

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.rag.models import (
    DraftSectionSource,
    RagChunk,
    RagDocument,
    RagObservabilityEvent,
    RagProcessingStatus,
    RagQueryLog,
    RagVerifiedCitation,
)
from app.features.drafts.models import Draft
from app.features.rag.schemas import (
    RagChunkCreate,
    RagDocumentCreate,
    RagObservabilityEventCreate,
)

GLOBAL_BASE_SCOPE = "global_base"
LAWYER_PRIVATE_SCOPE = "lawyer_private"


class RagRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_document_by_hash(
        self,
        *,
        lawyer_id: uuid.UUID | None,
        file_hash: str,
        corpus_scope: str = LAWYER_PRIVATE_SCOPE,
    ) -> RagDocument | None:
        stmt = select(RagDocument).where(
            RagDocument.file_hash == file_hash,
            RagDocument.corpus_scope == corpus_scope,
        )
        if corpus_scope == GLOBAL_BASE_SCOPE:
            stmt = stmt.where(RagDocument.lawyer_id.is_(None))
        else:
            stmt = stmt.where(RagDocument.lawyer_id == lawyer_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_document(self, data: RagDocumentCreate) -> RagDocument:
        document = RagDocument(
            lawyer_id=data.lawyer_id,
            case_id=data.case_id,
            source_document_id=data.source_document_id,
            draft_type=data.draft_type,
            corpus_scope=data.corpus_scope,
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
            corpus_scope=data.corpus_scope,
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
        lawyer_id: uuid.UUID | None,
        draft_type: str = "anticipatory_bail",
        case_id: uuid.UUID | None = None,
        section: str | None = None,
        limit: int = 20,
    ) -> list[RagChunk]:
        stmt = select(RagChunk).where(
            _accessible_chunk_scope(lawyer_id),
            RagChunk.draft_type == draft_type,
        )
        if case_id is not None:
            stmt = stmt.where(RagChunk.case_id == case_id)
        if section is not None:
            stmt = stmt.where(RagChunk.section == section)
        result = await self.session.execute(stmt.limit(limit))
        return list(result.scalars().all())

    async def search_chunks_by_vector(
        self,
        *,
        lawyer_id: uuid.UUID,
        embedding: list[float],
        draft_type: str = "anticipatory_bail",
        case_id: uuid.UUID | None = None,
        section: str | None = None,
        limit: int = 20,
    ) -> list[tuple[RagChunk, float]]:
        embedding_literal = _pgvector_literal(embedding)
        conditions = [
            "(corpus_scope = 'global_base' OR (corpus_scope = 'lawyer_private' AND lawyer_id = :lawyer_id))",
            "draft_type = :draft_type",
            "embedding IS NOT NULL",
        ]
        params: dict[str, object] = {
            "lawyer_id": lawyer_id,
            "draft_type": draft_type,
            "embedding": embedding_literal,
            "limit": limit,
        }
        if case_id is not None:
            conditions.append("case_id = :case_id")
            params["case_id"] = case_id
        if section is not None:
            conditions.append("section = :section")
            params["section"] = section

        query = text(
            f"""
            SELECT id, embedding <=> CAST(:embedding AS vector) AS distance
            FROM rag_chunks
            WHERE {" AND ".join(conditions)}
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """
        )
        rows = (await self.session.execute(query, params)).all()
        if not rows:
            return []

        distances = {row.id: float(row.distance) for row in rows}
        chunks_by_id = await self._chunks_by_ids(list(distances))
        return [
            (chunks_by_id[chunk_id], distances[chunk_id])
            for chunk_id in distances
            if chunk_id in chunks_by_id
        ]

    async def search_chunks_by_keywords(
        self,
        *,
        lawyer_id: uuid.UUID,
        keywords: Sequence[str],
        draft_type: str = "anticipatory_bail",
        case_id: uuid.UUID | None = None,
        section: str | None = None,
        limit: int = 20,
    ) -> list[RagChunk]:
        cleaned = [keyword.strip() for keyword in keywords if keyword.strip()]
        if not cleaned:
            return []

        stmt = select(RagChunk).where(
            _accessible_chunk_scope(lawyer_id),
            RagChunk.draft_type == draft_type,
            or_(*(RagChunk.chunk_text.ilike(f"%{keyword}%") for keyword in cleaned)),
        )
        if case_id is not None:
            stmt = stmt.where(RagChunk.case_id == case_id)
        if section is not None:
            stmt = stmt.where(RagChunk.section == section)

        result = await self.session.execute(stmt.limit(limit))
        return list(result.scalars().all())

    async def _chunks_by_ids(self, chunk_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, RagChunk]:
        result = await self.session.execute(
            select(RagChunk).where(RagChunk.id.in_(chunk_ids))
        )
        return {chunk.id: chunk for chunk in result.scalars().all()}

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

    async def create_observability_event(
        self,
        data: RagObservabilityEventCreate,
    ) -> RagObservabilityEvent:
        event = RagObservabilityEvent(
            lawyer_id=data.lawyer_id,
            case_id=data.case_id,
            draft_id=data.draft_id,
            query_log_id=data.query_log_id,
            event_type=data.event_type,
            section=data.section,
            severity=data.severity,
            metrics=data.metrics,
            metadata_=data.metadata,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_observability_events(
        self,
        *,
        lawyer_id: uuid.UUID,
        case_id: uuid.UUID | None = None,
        draft_id: uuid.UUID | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[RagObservabilityEvent]:
        stmt = select(RagObservabilityEvent).where(
            RagObservabilityEvent.lawyer_id == lawyer_id
        )
        if case_id is not None:
            stmt = stmt.where(RagObservabilityEvent.case_id == case_id)
        if draft_id is not None:
            stmt = stmt.where(RagObservabilityEvent.draft_id == draft_id)
        if event_type is not None:
            stmt = stmt.where(RagObservabilityEvent.event_type == event_type)
        result = await self.session.execute(
            stmt.order_by(RagObservabilityEvent.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_verified_citation(
        self,
        *,
        lawyer_id: uuid.UUID | None,
        normalized_key: str,
        corpus_scope: str | None = None,
    ) -> RagVerifiedCitation | None:
        stmt = select(RagVerifiedCitation).where(
            RagVerifiedCitation.normalized_key == normalized_key,
            RagVerifiedCitation.is_active.is_(True),
        )
        if corpus_scope == GLOBAL_BASE_SCOPE:
            stmt = stmt.where(
                RagVerifiedCitation.corpus_scope == GLOBAL_BASE_SCOPE,
                RagVerifiedCitation.lawyer_id.is_(None),
            )
        elif corpus_scope == LAWYER_PRIVATE_SCOPE:
            stmt = stmt.where(
                RagVerifiedCitation.corpus_scope == LAWYER_PRIVATE_SCOPE,
                RagVerifiedCitation.lawyer_id == lawyer_id,
            )
        elif lawyer_id is not None:
            stmt = stmt.where(_accessible_citation_scope(lawyer_id))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_verified_citation(
        self,
        *,
        lawyer_id: uuid.UUID,
        normalized_keys: Sequence[str],
    ) -> RagVerifiedCitation | None:
        keys = [key for key in normalized_keys if key]
        if not keys:
            return None
        result = await self.session.execute(
            select(RagVerifiedCitation).where(
                _accessible_citation_scope(lawyer_id),
                RagVerifiedCitation.normalized_key.in_(keys),
                RagVerifiedCitation.is_active.is_(True),
            )
        )
        return result.scalars().first()

    async def upsert_verified_citation(
        self,
        *,
        lawyer_id: uuid.UUID,
        normalized_key: str,
        case_name: str,
        citation: str | None = None,
        year: int | None = None,
        court: str | None = None,
        source_chunk_id: uuid.UUID | None = None,
        metadata: dict | None = None,
        corpus_scope: str = LAWYER_PRIVATE_SCOPE,
    ) -> RagVerifiedCitation:
        existing = await self.get_verified_citation(
            lawyer_id=lawyer_id,
            normalized_key=normalized_key,
            corpus_scope=corpus_scope,
        )
        if existing:
            existing.case_name = case_name
            existing.citation = citation
            existing.year = year
            existing.court = court
            existing.source_chunk_id = source_chunk_id
            existing.is_active = True
            existing.corpus_scope = corpus_scope
            existing.metadata_ = metadata or {}
            await self.session.flush()
            return existing

        verified = RagVerifiedCitation(
            lawyer_id=lawyer_id,
            normalized_key=normalized_key,
            corpus_scope=corpus_scope,
            case_name=case_name,
            citation=citation,
            year=year,
            court=court,
            source_chunk_id=source_chunk_id,
            is_active=True,
            metadata_=metadata or {},
        )
        self.session.add(verified)
        await self.session.flush()
        return verified

    async def list_verified_citations(
        self,
        *,
        lawyer_id: uuid.UUID,
        limit: int = 100,
    ) -> list[RagVerifiedCitation]:
        result = await self.session.execute(
            select(RagVerifiedCitation)
            .where(
                _accessible_citation_scope(lawyer_id),
                RagVerifiedCitation.is_active.is_(True),
            )
            .order_by(RagVerifiedCitation.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

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

    async def get_draft_for_lawyer(
        self,
        *,
        draft_id: uuid.UUID,
        lawyer_id: uuid.UUID,
    ) -> Draft | None:
        result = await self.session.execute(
            select(Draft).where(
                Draft.id == draft_id,
                Draft.created_by == lawyer_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_draft_section_sources(
        self,
        *,
        draft_id: uuid.UUID,
        lawyer_id: uuid.UUID,
    ) -> list[DraftSectionSource]:
        result = await self.session.execute(
            select(DraftSectionSource)
            .where(
                DraftSectionSource.draft_id == draft_id,
                DraftSectionSource.lawyer_id == lawyer_id,
            )
            .order_by(DraftSectionSource.created_at.asc())
        )
        return list(result.scalars().all())


def _pgvector_literal(embedding: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in embedding) + "]"


def _accessible_chunk_scope(lawyer_id: uuid.UUID):
    return or_(
        RagChunk.corpus_scope == GLOBAL_BASE_SCOPE,
        (
            (RagChunk.corpus_scope == LAWYER_PRIVATE_SCOPE)
            & (RagChunk.lawyer_id == lawyer_id)
        ),
    )


def _accessible_citation_scope(lawyer_id: uuid.UUID):
    return or_(
        RagVerifiedCitation.corpus_scope == GLOBAL_BASE_SCOPE,
        (
            (RagVerifiedCitation.corpus_scope == LAWYER_PRIVATE_SCOPE)
            & (RagVerifiedCitation.lawyer_id == lawyer_id)
        ),
    )
