import uuid
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict


class CaseContextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    context_json: dict[str, Any]
    pushed_document_ids: list[uuid.UUID]
    token_estimate: Optional[int]
    last_updated_at: datetime


class PushToContextRequest(BaseModel):
    document_ids: list[uuid.UUID]


class CaseSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    summary_text: str
    context_snapshot_hash: str
    generated_at: datetime
    is_stale: bool = False