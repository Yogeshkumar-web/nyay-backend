from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from app.features.documents.document_processing_service import (
    OcrProvider,
    process_document_bytes,
)
from app.features.rag.chunking import (
    chunk_anticipatory_bail_text,
    extract_anticipatory_bail_schema,
)
from app.features.rag.embedding import EmbeddingProvider
from app.features.rag.models import RagDocument, RagProcessingStatus
from app.features.rag.repository import RagRepository
from app.features.rag.schemas import RagChunkCreate, RagDocumentCreate

GLOBAL_BASE_SCOPE = "global_base"
LAWYER_PRIVATE_SCOPE = "lawyer_private"


@dataclass(frozen=True)
class RagIngestionResult:
    document: RagDocument
    chunk_count: int
    duplicate: bool


class RagIngestionService:
    def __init__(
        self,
        repository: RagRepository,
        embedding_provider: EmbeddingProvider,
        *,
        ocr_provider: OcrProvider | None = None,
    ):
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.ocr_provider = ocr_provider

    async def ingest_file(
        self,
        *,
        lawyer_id: uuid.UUID,
        file_bytes: bytes,
        mime_type: str,
        original_filename: str | None = None,
        case_id: uuid.UUID | None = None,
        source_document_id: uuid.UUID | None = None,
        source_kind: str = "kb_draft",
        draft_type: str = "anticipatory_bail",
        corpus_scope: str = LAWYER_PRIVATE_SCOPE,
    ) -> RagIngestionResult:
        file_hash = calculate_file_hash(file_bytes)
        existing = await self.repository.get_document_by_hash(
            lawyer_id=lawyer_id,
            file_hash=file_hash,
            corpus_scope=corpus_scope,
        )
        if existing:
            return RagIngestionResult(existing, chunk_count=0, duplicate=True)

        document = await self.repository.create_document(
            RagDocumentCreate(
                lawyer_id=lawyer_id,
                case_id=case_id,
                source_document_id=source_document_id,
                draft_type=draft_type,
                corpus_scope=corpus_scope,
                file_hash=file_hash,
                original_filename=original_filename,
                source_kind=source_kind,
                metadata={"mime_type": mime_type},
            )
        )
        await self.repository.set_document_status(
            document,
            RagProcessingStatus.processing,
        )

        try:
            processed = await process_document_bytes(
                file_bytes,
                mime_type,
                ocr_service=self.ocr_provider,
            )
            extraction = extract_anticipatory_bail_schema(processed.source_text)
            chunks = chunk_anticipatory_bail_text(processed.source_text)
            embeddings = await self.embedding_provider.embed_texts(
                [chunk.text for chunk in chunks]
            )

            for chunk, embedding in zip(chunks, embeddings, strict=True):
                await self.repository.create_chunk(
                    RagChunkCreate(
                        document_id=document.id,
                        lawyer_id=lawyer_id,
                        case_id=case_id,
                        draft_type=draft_type,
                        corpus_scope=corpus_scope,
                        section=chunk.section.value,
                        chunk_text=chunk.text,
                        embedding=embedding,
                        token_count=_rough_token_count(chunk.text),
                        metadata={
                            **chunk.metadata,
                            "source_artifact": processed.source_artifact,
                            "classification": processed.classification_details,
                            "sections_invoked": extraction.sections_invoked,
                            "cited_judgments": [
                                item.model_dump() for item in extraction.cited_judgments
                            ],
                        },
                    )
                )
            document.metadata_ = {
                **document.metadata_,
                "extraction": extraction.model_dump(),
                "is_scanned": processed.is_scanned,
                "page_count": processed.page_count,
            }
            await self.repository.set_document_status(
                document,
                RagProcessingStatus.completed,
            )
            return RagIngestionResult(document, chunk_count=len(chunks), duplicate=False)
        except Exception as exc:
            await self.repository.set_document_status(
                document,
                RagProcessingStatus.failed,
                error=str(exc),
            )
            raise

    async def ingest_text(
        self,
        *,
        text: str,
        original_filename: str | None,
        lawyer_id: uuid.UUID | None = None,
        case_id: uuid.UUID | None = None,
        source_kind: str = "kb_draft",
        draft_type: str = "anticipatory_bail",
        corpus_scope: str = LAWYER_PRIVATE_SCOPE,
    ) -> RagIngestionResult:
        if corpus_scope == LAWYER_PRIVATE_SCOPE and lawyer_id is None:
            raise ValueError("lawyer_id is required for private KB ingestion.")
        if corpus_scope == GLOBAL_BASE_SCOPE:
            lawyer_id = None
            case_id = None

        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("Cannot ingest empty text.")

        file_bytes = normalized_text.encode("utf-8")
        file_hash = calculate_file_hash(file_bytes)
        existing = await self.repository.get_document_by_hash(
            lawyer_id=lawyer_id,
            file_hash=file_hash,
            corpus_scope=corpus_scope,
        )
        if existing:
            return RagIngestionResult(existing, chunk_count=0, duplicate=True)

        document = await self.repository.create_document(
            RagDocumentCreate(
                lawyer_id=lawyer_id,
                case_id=case_id,
                source_document_id=None,
                draft_type=draft_type,
                corpus_scope=corpus_scope,
                file_hash=file_hash,
                original_filename=original_filename,
                source_kind=source_kind,
                metadata={"mime_type": "text/markdown"},
            )
        )
        await self.repository.set_document_status(
            document,
            RagProcessingStatus.processing,
        )

        try:
            extraction = extract_anticipatory_bail_schema(normalized_text)
            chunks = chunk_anticipatory_bail_text(normalized_text)
            embeddings = await self.embedding_provider.embed_texts(
                [chunk.text for chunk in chunks]
            )

            for chunk, embedding in zip(chunks, embeddings, strict=True):
                await self.repository.create_chunk(
                    RagChunkCreate(
                        document_id=document.id,
                        lawyer_id=lawyer_id,
                        case_id=case_id,
                        draft_type=draft_type,
                        corpus_scope=corpus_scope,
                        section=chunk.section.value,
                        chunk_text=chunk.text,
                        embedding=embedding,
                        token_count=_rough_token_count(chunk.text),
                        metadata={
                            **chunk.metadata,
                            "source_artifact": {"provider": "text_import"},
                            "sections_invoked": extraction.sections_invoked,
                            "cited_judgments": [
                                item.model_dump() for item in extraction.cited_judgments
                            ],
                        },
                    )
                )
            document.metadata_ = {
                **document.metadata_,
                "extraction": extraction.model_dump(),
                "is_scanned": False,
                "page_count": None,
            }
            await self.repository.set_document_status(
                document,
                RagProcessingStatus.completed,
            )
            return RagIngestionResult(document, chunk_count=len(chunks), duplicate=False)
        except Exception as exc:
            await self.repository.set_document_status(
                document,
                RagProcessingStatus.failed,
                error=str(exc),
            )
            raise


def calculate_file_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _rough_token_count(text: str) -> int:
    return max(1, len(text.split()))
