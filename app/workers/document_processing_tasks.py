"""Run the provider-neutral document digitization pipeline."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="documents.process_document",
    autoretry_for=(),
    max_retries=0,
)
def process_document(self, document_id: str) -> dict:
    return asyncio.run(_process_document_async(self.request.id, document_id))


@celery_app.task(
    bind=True,
    name="documents.retry_document_page",
    autoretry_for=(),
    max_retries=0,
)
def retry_document_page(
    self,
    document_id: str,
    run_id: str,
    page_number: int,
) -> dict:
    return asyncio.run(
        _retry_document_page_async(
            self.request.id,
            document_id,
            run_id,
            page_number,
        )
    )


@celery_app.task(name="documents.reconcile_queued_runs")
def reconcile_queued_runs() -> dict:
    return asyncio.run(_reconcile_queued_runs_async())


async def _process_document_async(job_id: str, document_id_str: str) -> dict:
    import app.db.registry  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.features.documents.models import OcrStatus, ProcessingRunStatus
    from app.features.documents.processing_orchestrator import (
        DocumentProcessingOrchestrator,
    )
    from app.features.documents.providers.contracts import DocumentProviderError
    from app.features.documents.providers.factory import (
        get_document_ocr_provider,
        get_typing_provider,
        get_vision_provider,
    )
    from app.features.documents.repository import DocumentRepository
    from app.features.documents.storage import read_r2_bytes

    document_id = uuid.UUID(document_id_str)
    await set_job_status(job_id, JobStatus.processing)
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            repo = DocumentRepository(session)
            doc = await repo.get_by_id(document_id)
            run = await repo.get_processing_run_by_job_id(job_id)
            if doc is None or run is None:
                message = "Document processing run was not found."
                await set_job_status(job_id, JobStatus.failed, error=message)
                return {"status": "failed", "error": message}

            try:
                file_bytes = await read_r2_bytes(doc.r2_bucket, doc.r2_key)
                orchestrator = DocumentProcessingOrchestrator(
                    session,
                    vision_provider=get_vision_provider(),
                    typing_provider=get_typing_provider(),
                    document_ocr_provider=get_document_ocr_provider(),
                )
                result = await orchestrator.process(
                    doc=doc,
                    run=run,
                    file_bytes=file_bytes,
                )
            except Exception as exc:
                logger.exception(
                    "Document processing failed | job_id=%s | document_id=%s",
                    job_id,
                    document_id,
                )
                await session.rollback()
                doc = await repo.get_by_id(document_id)
                run = await repo.get_processing_run_by_job_id(job_id)
                if doc is None or run is None:
                    message = "Document processing failed and its run state was not found."
                    await set_job_status(job_id, JobStatus.failed, error=message)
                    return {"status": "failed", "error": message}
                if isinstance(exc, DocumentProviderError):
                    message = str(exc)
                    metadata = {
                        **(run.metadata_ or {}),
                        "error_category": exc.category.value,
                        "retryable": exc.retryable,
                    }
                else:
                    message = "Document processing failed. Retry the document."
                    metadata = {
                        **(run.metadata_ or {}),
                        "error_category": "internal",
                        "retryable": True,
                    }
                await repo.fail_processing(doc, error=message)
                await repo.set_ocr_status(doc, OcrStatus.failed, error=message)
                await repo.mark_unfinished_pages_failed(run, error=message)
                await repo.update_processing_run(
                    run,
                    status=ProcessingRunStatus.failed,
                    error=message,
                    metadata=metadata,
                )
                await session.commit()
                await set_job_status(job_id, JobStatus.failed, error=message)
                return {"status": "failed", "error": message}

        response = {
            "document_id": document_id_str,
            "processing_run_id": str(result.run_id),
            "processing_route": result.route.value,
            "page_count": result.page_count,
            "typed_revision_id": str(result.typed_revision_id),
        }
        await set_job_status(job_id, JobStatus.completed, result=response)
        return {"status": "completed", **response}
    finally:
        await engine.dispose()


async def _retry_document_page_async(
    job_id: str,
    document_id_str: str,
    run_id_str: str,
    page_number: int,
) -> dict:
    import app.db.registry  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.features.documents.models import (
        DocumentPageStatus,
        ProcessingRunStatus,
        ProcessingStatus,
    )
    from app.features.documents.processing_orchestrator import (
        DocumentProcessingOrchestrator,
    )
    from app.features.documents.providers.contracts import DocumentProviderError
    from app.features.documents.providers.factory import (
        get_typing_provider,
        get_vision_provider,
    )
    from app.features.documents.repository import DocumentRepository
    from app.features.documents.storage import read_r2_bytes

    document_id = uuid.UUID(document_id_str)
    run_id = uuid.UUID(run_id_str)
    await set_job_status(job_id, JobStatus.processing)
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            repo = DocumentRepository(session)
            doc = await repo.get_by_id(document_id)
            run = await repo.get_processing_run(run_id)
            page = await repo.get_run_page(run_id, page_number)
            if doc is None or run is None or page is None or run.document_id != doc.id:
                message = "Document page retry state was not found."
                await set_job_status(job_id, JobStatus.failed, error=message)
                return {"status": "failed", "error": message}
            try:
                file_bytes = await read_r2_bytes(doc.r2_bucket, doc.r2_key)
                orchestrator = DocumentProcessingOrchestrator(
                    session,
                    vision_provider=get_vision_provider(),
                    typing_provider=get_typing_provider(),
                )
                result = await orchestrator.retry_page(
                    doc=doc,
                    run=run,
                    page=page,
                    file_bytes=file_bytes,
                )
            except Exception as exc:
                logger.exception(
                    "Document page retry failed | run_id=%s | page=%s",
                    run_id,
                    page_number,
                )
                await session.rollback()
                doc = await repo.get_by_id(document_id)
                run = await repo.get_processing_run(run_id)
                page = await repo.get_run_page(run_id, page_number)
                if doc is None or run is None or page is None:
                    message = "Document page retry failed and its state was not found."
                    await set_job_status(job_id, JobStatus.failed, error=message)
                    return {"status": "failed", "error": message}
                message = (
                    str(exc)
                    if isinstance(exc, DocumentProviderError)
                    else "Document page retry failed. Retry the page again."
                )
                page.status = DocumentPageStatus.failed
                page.error = message
                await repo.update_processing_run(
                    run,
                    status=ProcessingRunStatus.failed,
                    error=message,
                )
                doc.processing_status = ProcessingStatus.failed
                doc.processing_error = message
                await session.commit()
                await set_job_status(job_id, JobStatus.failed, error=message)
                return {"status": "failed", "error": message}

        response = {
            "document_id": document_id_str,
            "processing_run_id": run_id_str,
            "page_number": page_number,
            "ready_for_review": result is not None,
        }
        await set_job_status(job_id, JobStatus.completed, result=response)
        return {"status": "completed", **response}
    finally:
        await engine.dispose()


async def _reconcile_queued_runs_async() -> dict:
    import app.db.registry  # noqa: F401
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.features.documents.models import (
        DocumentProcessingRun,
        ProcessingRunStatus,
    )

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=settings.DOCUMENT_PROCESSING_STALE_AFTER_SECONDS
    )
    requeued = 0
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(DocumentProcessingRun).where(
                    DocumentProcessingRun.status == ProcessingRunStatus.queued,
                    DocumentProcessingRun.started_at < cutoff,
                )
            )
            for run in result.scalars().all():
                process_document.apply_async(
                    args=[str(run.document_id)],
                    task_id=run.job_id,
                )
                requeued += 1
        return {"requeued": requeued}
    finally:
        await engine.dispose()
