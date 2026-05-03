"""
Typing Celery task — converts OCR text to HC-formatted typed version.
"""
import asyncio
import uuid

from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status


@celery_app.task(
    bind=True,
    name="typing.run_type_document",
    max_retries=3,
    default_retry_delay=30,
)
def run_type_document(self, document_id: str) -> dict:
    return asyncio.run(_typing_task_async(self.request.id, document_id))


async def _typing_task_async(job_id: str, document_id_str: str) -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.features.documents.repository import DocumentRepository
    from app.features.extraction.repository import ExtractionRepository
    from app.features.extraction.service import _type_document_via_claude

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

        try:
            typed_content, raw_response = await _type_document_via_claude(doc.ocr_raw_text)

            # Check if typed version already exists
            existing = await ext_repo.get_typed_version(document_id)
            if existing:
                existing.typed_content = typed_content
                existing.raw_ai_response = raw_response
                await session.flush()
            else:
                await ext_repo.create_typed_version(
                    document_id=document_id,
                    typed_content=typed_content,
                    raw_ai_response=raw_response,
                )

            await session.commit()

            await set_job_status(
                job_id,
                JobStatus.completed,
                result={"document_id": str(document_id)},
            )

        except Exception as exc:
            await set_job_status(job_id, JobStatus.failed, error=str(exc))
            raise

        finally:
            await engine.dispose()

    return {"status": "completed", "document_id": str(document_id)}