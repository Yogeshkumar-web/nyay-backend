import uuid
from datetime import datetime
from typing import Any, Optional, Dict

from pydantic import BaseModel, ConfigDict, computed_field


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
