import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CitedJudgment(BaseModel):
    case_name: str = Field(..., min_length=1)
    citation: str | None = None
    year: int | None = None
    court: str | None = None


class AnticipatoryBailExtraction(BaseModel):
    facts: str
    grounds: list[str]
    sections_invoked: list[str]
    cited_judgments: list[CitedJudgment]
    prayer: str


class RagDocumentCreate(BaseModel):
    lawyer_id: uuid.UUID
    case_id: uuid.UUID | None = None
    source_document_id: uuid.UUID | None = None
    draft_type: str = "anticipatory_bail"
    file_hash: str = Field(..., min_length=16, max_length=128)
    original_filename: str | None = None
    source_kind: str = "kb_draft"
    metadata: dict = Field(default_factory=dict)


class RagDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lawyer_id: uuid.UUID
    case_id: uuid.UUID | None
    source_document_id: uuid.UUID | None
    draft_type: str
    file_hash: str
    original_filename: str | None
    source_kind: str
    processing_status: str
    processing_error: str | None
    metadata_: dict
    created_at: datetime
    updated_at: datetime


class RagChunkCreate(BaseModel):
    document_id: uuid.UUID
    lawyer_id: uuid.UUID
    case_id: uuid.UUID | None = None
    draft_type: str = "anticipatory_bail"
    section: str
    chunk_text: str = Field(..., min_length=1)
    summary: str | None = None
    keywords: list[str] | None = None
    embedding: list[float] | None = Field(default=None, min_length=768, max_length=768)
    confidence_score: float | None = None
    token_count: int | None = None
    metadata: dict = Field(default_factory=dict)


class RagChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    lawyer_id: uuid.UUID
    case_id: uuid.UUID | None
    draft_type: str
    section: str
    chunk_text: str
    summary: str | None
    keywords: list[str] | None
    confidence_score: float | None
    token_count: int | None
    metadata_: dict
    created_at: datetime


class RetrievalResult(BaseModel):
    chunks: list[RagChunkResponse]
    confidence_score: float

    def should_generate(self, threshold: float = 0.65) -> bool:
        return self.confidence_score >= threshold and bool(self.chunks)
