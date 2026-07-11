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
    lawyer_id: uuid.UUID | None
    case_id: uuid.UUID | None = None
    source_document_id: uuid.UUID | None = None
    draft_type: str = "anticipatory_bail"
    corpus_scope: str = Field(default="lawyer_private", pattern="^(global_base|lawyer_private)$")
    file_hash: str = Field(..., min_length=16, max_length=128)
    original_filename: str | None = None
    source_kind: str = "kb_draft"
    metadata: dict = Field(default_factory=dict)


class RagDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lawyer_id: uuid.UUID | None
    case_id: uuid.UUID | None
    source_document_id: uuid.UUID | None
    draft_type: str
    corpus_scope: str
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
    lawyer_id: uuid.UUID | None
    case_id: uuid.UUID | None = None
    draft_type: str = "anticipatory_bail"
    corpus_scope: str = Field(default="lawyer_private", pattern="^(global_base|lawyer_private)$")
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
    lawyer_id: uuid.UUID | None
    case_id: uuid.UUID | None
    draft_type: str
    corpus_scope: str
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


class RagRetrieveRequest(BaseModel):
    query_text: str = Field(..., min_length=3)
    case_id: uuid.UUID | None = None
    draft_type: str = "anticipatory_bail"
    section: str | None = None
    limit: int = Field(default=8, ge=1, le=20)
    min_confidence: float = Field(default=0.65, ge=0.0, le=1.0)


class RagRetrievedChunk(BaseModel):
    chunk: RagChunkResponse
    score: float
    match_reasons: list[str] = Field(default_factory=list)


class RagRetrievalResponse(BaseModel):
    chunks: list[RagRetrievedChunk]
    confidence_score: float
    should_generate: bool
    refusal_reason: str | None = None
    query_log_id: uuid.UUID | None = None
    filters: dict = Field(default_factory=dict)


class RagGenerateSectionRequest(BaseModel):
    case_facts: str = Field(..., min_length=20)
    sections_invoked: list[str] = Field(default_factory=list)
    target_section: str = Field(..., pattern="^(facts|grounds|prayer)$")
    case_id: uuid.UUID | None = None
    draft_id: uuid.UUID | None = None
    draft_type: str = "anticipatory_bail"
    additional_instructions: str | None = Field(default=None, max_length=2000)
    retrieval_limit: int = Field(default=8, ge=1, le=20)
    min_confidence: float = Field(default=0.65, ge=0.0, le=1.0)


class RagGeneratedSectionResponse(BaseModel):
    section: str
    content: str | None = None
    source_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    cited_judgments: list[CitedJudgment] = Field(default_factory=list)
    confidence_score: float
    status: str
    refusal_reason: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    query_log_id: uuid.UUID | None = None
    draft_section_source_id: uuid.UUID | None = None


class VerifiedCitationCreate(BaseModel):
    case_name: str = Field(..., min_length=1, max_length=500)
    citation: str | None = Field(default=None, max_length=255)
    year: int | None = Field(default=None, ge=1800, le=2100)
    court: str | None = Field(default=None, max_length=255)
    source_chunk_id: uuid.UUID | None = None
    corpus_scope: str = Field(default="lawyer_private", pattern="^(global_base|lawyer_private)$")
    metadata: dict = Field(default_factory=dict)


class VerifiedCitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lawyer_id: uuid.UUID | None
    normalized_key: str
    corpus_scope: str
    case_name: str
    citation: str | None
    year: int | None
    court: str | None
    source_chunk_id: uuid.UUID | None
    is_active: bool
    metadata_: dict
    created_at: datetime
    updated_at: datetime


class CitationVerificationItem(BaseModel):
    citation: CitedJudgment
    status: str
    matched_citation_id: uuid.UUID | None = None
    warning: str | None = None


class CitationVerificationRequest(BaseModel):
    text: str | None = None
    citations: list[CitedJudgment] = Field(default_factory=list)


class CitationVerificationResponse(BaseModel):
    verified: list[CitationVerificationItem] = Field(default_factory=list)
    unverified: list[CitationVerificationItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DraftAssemblySection(BaseModel):
    section: str = Field(..., pattern="^(facts|grounds|prayer)$")
    content: str = Field(..., min_length=1)
    source_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)


class DraftAssemblyRequest(BaseModel):
    case_metadata: dict = Field(default_factory=dict)
    sections: list[DraftAssemblySection] = Field(..., min_length=1)
    verified_citations: list[CitedJudgment] = Field(default_factory=list)
    unverified_citations: list[CitedJudgment] = Field(default_factory=list)


class DraftAssemblyResponse(BaseModel):
    html: str
    section_source_map: dict[str, list[uuid.UUID]]
    verified_citations: list[CitedJudgment] = Field(default_factory=list)
    excluded_citations: list[CitedJudgment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RagObservabilityEventCreate(BaseModel):
    lawyer_id: uuid.UUID
    case_id: uuid.UUID | None = None
    draft_id: uuid.UUID | None = None
    query_log_id: uuid.UUID | None = None
    event_type: str = Field(..., min_length=1, max_length=100)
    section: str | None = Field(default=None, max_length=100)
    severity: str = Field(default="info", pattern="^(info|warning|error)$")
    metrics: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class RagObservabilityEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lawyer_id: uuid.UUID
    case_id: uuid.UUID | None
    draft_id: uuid.UUID | None
    query_log_id: uuid.UUID | None
    event_type: str
    section: str | None
    severity: str
    metrics: dict
    metadata_: dict
    created_at: datetime


class RagDraftTraceResponse(BaseModel):
    draft_id: uuid.UUID
    events: list[RagObservabilityEventResponse] = Field(default_factory=list)
    section_sources: dict[str, list[uuid.UUID]] = Field(default_factory=dict)
    query_log_ids: list[uuid.UUID] = Field(default_factory=list)
