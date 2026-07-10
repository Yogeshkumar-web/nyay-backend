from __future__ import annotations

import io
import uuid
import zipfile
from types import SimpleNamespace

import pytest

from app.features.rag import ingestion as ingestion_module
from app.features.rag.chunking import (
    chunk_anticipatory_bail_text,
    extract_anticipatory_bail_schema,
)
from app.features.rag.embedding import HashEmbeddingProvider
from app.features.rag.ingestion import RagIngestionService
from app.features.rag.models import RagProcessingStatus, RagSection
from app.features.documents.sarvam_vision import _extract_zip_output
from app.features.documents.sarvam_vision import _prepare_sarvam_input


def test_chunking_preserves_legal_sections():
    text = """
    Facts
    The applicant has been falsely implicated in FIR No. 10/2026.

    Grounds
    The alleged offences are triable by magistrate and custodial interrogation is not required.

    Prayer
    It is prayed that anticipatory bail may kindly be granted.
    """

    chunks = chunk_anticipatory_bail_text(text)

    assert [chunk.section for chunk in chunks] == [
        RagSection.facts,
        RagSection.grounds,
        RagSection.prayer,
    ]
    assert chunks[0].metadata["chunker"] == "anticipatory_bail_section_v1"


def test_extraction_finds_sections_and_citations():
    extraction = extract_anticipatory_bail_schema(
        """
        Facts
        FIR invokes Section 420 IPC and section 120B IPC.

        Grounds
        The applicant relies on Siddharam Satlingappa Mhetre vs. State of Maharashtra, (2011) 1 SCC 694.
        """
    )

    assert any("420" in section for section in extraction.sections_invoked)
    assert extraction.cited_judgments[0].year == 2011


@pytest.mark.asyncio
async def test_hash_embedding_provider_returns_768_dimensions():
    embeddings = await HashEmbeddingProvider().embed_texts(["anticipatory bail facts"])

    assert len(embeddings) == 1
    assert len(embeddings[0]) == 768


@pytest.mark.asyncio
async def test_ingestion_dedupes_by_lawyer_file_hash(monkeypatch):
    lawyer_id = uuid.uuid4()
    existing = SimpleNamespace(id=uuid.uuid4(), processing_status="completed")
    repository = _FakeRagRepository(existing_document=existing)

    result = await RagIngestionService(
        repository,
        HashEmbeddingProvider(),
    ).ingest_file(
        lawyer_id=lawyer_id,
        file_bytes=b"same file",
        mime_type="application/pdf",
        original_filename="draft.pdf",
    )

    assert result.duplicate is True
    assert result.document is existing
    assert repository.created_documents == []


@pytest.mark.asyncio
async def test_ingestion_persists_chunks_with_lawyer_boundary(monkeypatch):
    lawyer_id = uuid.uuid4()
    repository = _FakeRagRepository()

    async def fake_process_document_bytes(file_bytes, mime_type, *, ocr_service=None):
        return SimpleNamespace(
            source_text="""
            Facts
            Applicant has cooperated with investigation.

            Grounds
            Custodial interrogation is not required.
            """,
            source_artifact={"pages": [{"page_number": 1, "text": "ok"}]},
            classification_details={"route": "digital_extract"},
            is_scanned=False,
            page_count=1,
        )

    monkeypatch.setattr(
        ingestion_module,
        "process_document_bytes",
        fake_process_document_bytes,
    )

    result = await RagIngestionService(
        repository,
        HashEmbeddingProvider(),
    ).ingest_file(
        lawyer_id=lawyer_id,
        file_bytes=b"%PDF fake",
        mime_type="application/pdf",
        original_filename="draft.pdf",
    )

    assert result.duplicate is False
    assert result.chunk_count == 2
    assert all(chunk.lawyer_id == lawyer_id for chunk in repository.created_chunks)
    assert all(len(chunk.embedding or []) == 768 for chunk in repository.created_chunks)
    assert repository.statuses[-1] == RagProcessingStatus.completed


def test_sarvam_zip_output_extracts_markdown_and_json():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("output.md", "# Page 1\n\nExtracted legal text")
        archive.writestr("output.json", '{"pages": [{"text": "Extracted legal text"}]}')

    extracted = _extract_zip_output(buffer.getvalue())

    assert "Extracted legal text" in extracted.text
    assert extracted.json_payload == {"pages": [{"text": "Extracted legal text"}]}


def test_sarvam_image_input_is_wrapped_as_flat_zip():
    filename, payload = _prepare_sarvam_input(b"\x89PNG\r\n\x1a\nfake", "image/png")

    assert filename == "document.zip"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["document.png"]
        assert archive.read("document.png").startswith(b"\x89PNG")


class _FakeRagRepository:
    def __init__(self, existing_document=None):
        self.existing_document = existing_document
        self.created_documents = []
        self.created_chunks = []
        self.statuses = []

    async def get_document_by_hash(self, *, lawyer_id, file_hash):
        return self.existing_document

    async def create_document(self, data):
        document = SimpleNamespace(
            id=uuid.uuid4(),
            lawyer_id=data.lawyer_id,
            case_id=data.case_id,
            metadata_=data.metadata,
            processing_status="pending",
            processing_error=None,
        )
        self.created_documents.append(document)
        return document

    async def set_document_status(self, document, status, *, error=None):
        document.processing_status = status.value
        document.processing_error = error
        self.statuses.append(status)
        return document

    async def create_chunk(self, data):
        chunk = SimpleNamespace(**data.model_dump())
        self.created_chunks.append(chunk)
        return chunk
