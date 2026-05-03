"""
Extraction Celery task.

Flow:
  run_ocr_on_document completes
    → run_extraction.delay(document_id)
      → Claude API extraction
        → stores extracted_fields in extraction_results
"""
import asyncio
import uuid

from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status


@celery_app.task(
    bind=True,
    name="extraction.run_extraction",
    max_retries=3,
    default_retry_delay=30,
)
def run_extraction(self, document_id: str) -> dict:
    return asyncio.run(_extraction_task_async(self.request.id, document_id))


async def _extraction_task_async(job_id: str, document_id_str: str) -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.features.documents.repository import DocumentRepository
    from app.features.extraction.models import ExtractionStatus
    from app.features.extraction.repository import ExtractionRepository
    from app.features.extraction.service import _extract_fields_via_claude

    document_id = uuid.UUID(document_id_str)

    await set_job_status(job_id, JobStatus.processing)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        doc_repo = DocumentRepository(session)
        ext_repo = ExtractionRepository(session)

        doc = await doc_repo.get_by_id(document_id)
        if not doc:
            await set_job_status(job_id, JobStatus.failed, error="Document not found")
            await engine.dispose()
            return {"status": "failed"}

        if not doc.ocr_raw_text:
            await set_job_status(job_id, JobStatus.failed, error="No OCR text available")
            await engine.dispose()
            return {"status": "failed"}

        # Create or get existing extraction record
        extraction = await ext_repo.get_by_document(document_id)
        if not extraction:
            extraction = await ext_repo.create(
                document_id=document_id,
                case_id=doc.case_id,
            )

        # Mark as processing
        extraction.extraction_status = ExtractionStatus.processing
        await session.flush()
        await session.commit()

        try:
            extracted_fields, raw_response, confidence = await _extract_fields_via_claude(
                doc.ocr_raw_text,
                doc.document_type,
            )

            await ext_repo.set_extraction_complete(
                extraction,
                extracted_fields=extracted_fields,
                raw_ai_response=raw_response,
                confidence_score=confidence,
            )
            await session.commit()

            await set_job_status(
                job_id,
                JobStatus.completed,
                result={"document_id": str(document_id)},
            )

        except Exception as exc:
            await ext_repo.set_extraction_failed(extraction)
            await session.commit()
            await set_job_status(job_id, JobStatus.failed, error=str(exc))
            raise

        finally:
            await engine.dispose()

    return {"status": "completed", "document_id": str(document_id)}