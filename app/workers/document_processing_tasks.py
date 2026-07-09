"""Classify uploaded documents, extract text, and OCR scanned pages."""

import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="documents.process_document",
    max_retries=0,
)
def process_document(self, document_id: str) -> dict:
    return asyncio.run(_process_document_async(self.request.id, document_id))


async def _process_document_async(job_id: str, document_id_str: str) -> dict:
    import app.db.registry  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.features.documents.document_processing_service import (
        process_document_bytes,
    )
    from app.features.documents.models import OcrStatus, ProcessingRoute
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
            if not doc:
                await set_job_status(
                    job_id, JobStatus.failed, error="Document not found"
                )
                return {"status": "failed", "error": "Document not found"}

            try:
                file_bytes = await read_r2_bytes(doc.r2_bucket, doc.r2_key)
                result = await process_document_bytes(file_bytes, doc.mime_type)
                await repo.complete_processing(
                    doc,
                    route=result.route,
                    source_text=result.source_text,
                    source_artifact=result.source_artifact,
                    classification_details=result.classification_details,
                    is_scanned=result.is_scanned,
                )

                if result.route == ProcessingRoute.digital_extract:
                    doc.ocr_status = OcrStatus.not_required
                    doc.ocr_raw_text = None
                    doc.ocr_language = None
                    doc.ocr_artifact = None
                    doc.ocr_provider = None
                    doc.ocr_error = None
                    doc.ocr_completed_at = None
                    doc.page_count = result.page_count
                else:
                    await repo.set_ocr_status(
                        doc,
                        OcrStatus.completed,
                        raw_text=result.ocr_text,
                        language=result.ocr_language,
                        page_count=result.page_count,
                        provider="local_ocr",
                        artifact=result.ocr_artifact,
                        job_id=job_id,
                    )
                await session.commit()
            except Exception as exc:
                logger.exception(
                    "Document processing failed | job_id=%s | document_id=%s",
                    job_id,
                    document_id,
                )
                await repo.fail_processing(doc, error=str(exc))
                await repo.set_ocr_status(doc, OcrStatus.failed, error=str(exc))
                await session.commit()
                await set_job_status(job_id, JobStatus.failed, error=str(exc))
                return {"status": "failed", "error": str(exc)}

        response = {
            "document_id": document_id_str,
            "processing_route": result.route.value,
        }
        await set_job_status(job_id, JobStatus.completed, result=response)
        return {"status": "completed", **response}
    finally:
        await engine.dispose()
