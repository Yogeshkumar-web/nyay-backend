import uuid
from datetime import datetime
from typing import Any, Optional, Dict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)


# ─────────────────────────────
# Helpers
# ─────────────────────────────
def _dedupe_list(values: list[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(values))


# ─────────────────────────────
# Context Response
# ─────────────────────────────
class CaseContextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID

    context_json: Dict[str, Any]

    # 🔥 updated structure
    pushed_documents: Dict[str, Any]

    @computed_field
    @property
    def pushed_document_ids(self) -> list[str]:
        return [str(document_id) for document_id in self.pushed_documents.keys()]

    token_estimate: Optional[int]

    version: int
    last_updated_at: datetime


# ─────────────────────────────
# Push Request
# ─────────────────────────────
class PushToContextRequest(BaseModel):
    document_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=20)

    @field_validator("document_ids")
    @classmethod
    def validate_ids(cls, v: list[uuid.UUID]):
        if not v:
            raise ValueError("document_ids cannot be empty")

        # dedupe
        v = _dedupe_list(v)

        if len(v) > 20:
            raise ValueError("Too many documents")

        return v


# ─────────────────────────────
# Summary Response
# ─────────────────────────────
class CaseSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID

    summary_text: str

    context_snapshot_hash: str

    generated_at: datetime

    is_stale: bool = False

    token_count: Optional[int]
