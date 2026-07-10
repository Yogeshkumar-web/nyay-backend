from __future__ import annotations

import uuid

import pytest

import app.db.registry  # noqa: F401
from app.features.drafts.models import Draft, DraftStatus, DraftType
from app.features.drafts.repository import DraftRepository
from app.features.drafts.schemas import UpdateDraftRequest


class FakeSession:
    def __init__(self):
        self.flush_count = 0

    async def flush(self):
        self.flush_count += 1


def _draft(status: DraftStatus = DraftStatus.ready) -> Draft:
    return Draft(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        draft_type=DraftType.anticipatory_bail,
        title="Anticipatory Bail",
        content="<p>Generated draft</p>",
        status=status,
        generated_by_ai=True,
        version=1,
        revision=1,
    )


@pytest.mark.asyncio
async def test_generic_update_cannot_finalize_draft():
    repo = DraftRepository(FakeSession())  # type: ignore[arg-type]
    draft = _draft()

    with pytest.raises(ValueError, match="lawyer review endpoint"):
        await repo.update(draft, UpdateDraftRequest(status=DraftStatus.final))


@pytest.mark.asyncio
async def test_review_marks_draft_final_with_review_metadata():
    session = FakeSession()
    repo = DraftRepository(session)  # type: ignore[arg-type]
    draft = _draft()
    reviewer_id = uuid.uuid4()

    reviewed = await repo.mark_reviewed_final(
        draft,
        reviewed_by=reviewer_id,
        content="<p>Reviewed final draft</p>",
    )

    assert reviewed.status == DraftStatus.final
    assert reviewed.content == "<p>Reviewed final draft</p>"
    assert reviewed.reviewed_by == reviewer_id
    assert reviewed.reviewed_at is not None
    assert reviewed.final_accepted_at == reviewed.reviewed_at
    assert reviewed.revision == 2
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_export_metadata_requires_final_draft():
    repo = DraftRepository(FakeSession())  # type: ignore[arg-type]
    draft = _draft(status=DraftStatus.editing)

    with pytest.raises(ValueError, match="review is required"):
        await repo.mark_exported(draft, exported_by=uuid.uuid4())


@pytest.mark.asyncio
async def test_mark_exported_stamps_final_draft():
    session = FakeSession()
    repo = DraftRepository(session)  # type: ignore[arg-type]
    draft = _draft(status=DraftStatus.final)
    exporter_id = uuid.uuid4()

    exported = await repo.mark_exported(draft, exported_by=exporter_id)

    assert exported.exported_by == exporter_id
    assert exported.exported_at is not None
    assert session.flush_count == 1
