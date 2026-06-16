"""
Manual extraction Celery task.

Extraction is queued only when the user presses the extraction button. OCR
completion does not automatically trigger this task.
"""
import asyncio
import uuid

from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status


@celery_app.task(
    bind=True,
    name="extraction.run_extraction",
    max_retries=0,
)
def run_extraction(self, document_id: str) -> dict:
    return asyncio.run(_extraction_task_async(self.request.id, document_id))


async def _extraction_task_async(job_id: str, document_id_str: str) -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.features.documents.repository import DocumentRepository
    from app.features.extraction.repository import ExtractionRepository
    from app.features.extraction.service import _extract_fields_via_ai, _fields_to_html

    document_id = uuid.UUID(document_id_str)

    await set_job_status(job_id, JobStatus.processing)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as session:
            doc_repo = DocumentRepository(session)
            ext_repo = ExtractionRepository(session)

            doc = await doc_repo.get_by_id(document_id)
            if not doc:
                await set_job_status(
                    job_id, JobStatus.failed, error="Document not found"
                )
                return {"status": "failed", "error": "Document not found"}

            if not doc.ocr_raw_text:
                await set_job_status(
                    job_id, JobStatus.failed, error="No OCR text available"
                )
                return {"status": "failed", "error": "No OCR text available"}

            extraction = await ext_repo.create_or_get(
                document_id=document_id,
                case_id=doc.case_id,
            )

            try:
                await ext_repo.mark_processing(extraction)
                await session.commit()

                (
                    extracted_fields,
                    raw_response,
                    confidence,
                ) = await _extract_fields_via_ai(
                    doc.ocr_raw_text,
                    doc.document_type,
                )

                try:
                    formatted_content = _fields_to_html(
                        extracted_fields, doc.document_type.value
                    )
                except Exception:
                    formatted_content = None

                await ext_repo.set_extraction_complete(
                    extraction,
                    extracted_fields=extracted_fields,
                    raw_ai_response=raw_response,
                    confidence_score=confidence,
                    formatted_content=formatted_content,
                )
                await session.commit()

                result = {"document_id": str(document_id)}
                await set_job_status(job_id, JobStatus.completed, result=result)
                return {"status": "completed", **result}

            except Exception as exc:
                await ext_repo.set_extraction_failed(extraction)
                await session.commit()
                await set_job_status(job_id, JobStatus.failed, error=str(exc))
                return {
                    "status": "failed",
                    "document_id": str(document_id),
                    "error": str(exc),
                }
    finally:
        await engine.dispose()
