"""
OCR Celery task.

Flow:
  confirm_upload (is_scanned=True)
    → run_ocr_on_document.delay(document_id)
      → Google Document AI
        → stores ocr_raw_text, ocr_language, page_count
          → (Sprint 4) run_extraction.delay(document_id)
"""
import json
import uuid
from datetime import datetime, timezone

from celery import shared_task

from app.workers.celery_app import celery_app
from app.workers.job_store import set_job_status, JobStatus


# ── Google Document AI ─────────────────────────────────────────────────────────

async def _run_ocr(r2_bucket: str, r2_key: str) -> dict:
    """
    Calls Google Document AI to OCR a document stored in R2.
    Returns: { text: str, language: str, page_count: int }
    """
    from google.cloud import documentai
    from app.core.config import settings

    # Download file bytes from R2
    import boto3
    from botocore.config import Config

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    response = s3.get_object(Bucket=r2_bucket, Key=r2_key)
    file_bytes = response["Body"].read()
    mime_type = response.get("ContentType", "application/pdf")

    # Detect language from key extension as fallback
    client = documentai.DocumentProcessorServiceClient()
    name = (
        f"projects/{settings.GOOGLE_PROJECT_ID}"
        f"/locations/{settings.GOOGLE_LOCATION}"
        f"/processors/{settings.GOOGLE_DOCAI_PROCESSOR_ID}"
    )

    raw_document = documentai.RawDocument(content=file_bytes, mime_type=mime_type)
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)

    result = client.process_document(request=request)
    document = result.document

    # Detect language
    language = "en"
    if document.pages:
        detected_langs = document.pages[0].detected_languages
        if detected_langs:
            lang_code = detected_langs[0].language_code
            if "hi" in lang_code:
                language = "hi+en" if len(detected_langs) > 1 else "hi"

    return {
        "text": document.text,
        "language": language,
        "page_count": len(document.pages),
    }


# ── Celery task ────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="ocr.run_ocr_on_document",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def run_ocr_on_document(self, document_id: str) -> dict:
    """
    Celery task: OCR a document.
    Uses asyncio.run() to call async DB + Google Doc AI code.
    """
    import asyncio
    return asyncio.run(_ocr_task_async(self.request.id, document_id))


async def _ocr_task_async(job_id: str, document_id_str: str) -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.core.config import settings
    from app.features.documents.repository import DocumentRepository
    from app.features.documents.models import OcrStatus

    document_id = uuid.UUID(document_id_str)

    # Update job status → processing
    await set_job_status(job_id, JobStatus.processing)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        repo = DocumentRepository(session)
        doc = await repo.get_by_id(document_id)

        if not doc:
            await set_job_status(job_id, JobStatus.failed, error="Document not found")
            await engine.dispose()
            return {"status": "failed", "error": "Document not found"}

        # Mark as processing
        await repo.set_ocr_status(doc, OcrStatus.processing)
        await session.commit()

        try:
            ocr_result = await _run_ocr(doc.r2_bucket, doc.r2_key)

            await repo.set_ocr_status(
                doc,
                OcrStatus.completed,
                raw_text=ocr_result["text"],
                language=ocr_result["language"],
                page_count=ocr_result["page_count"],
            )
            await session.commit()

            await set_job_status(
                job_id,
                JobStatus.completed,
                result={"document_id": str(document_id), "language": ocr_result["language"]},
            )

            # Sprint 4: trigger extraction
            from app.workers.extraction_tasks import run_extraction
            run_extraction.delay(str(document_id))

        except Exception as exc:
            await repo.set_ocr_status(doc, OcrStatus.failed)
            await session.commit()
            await set_job_status(job_id, JobStatus.failed, error=str(exc))
            raise

        finally:
            await engine.dispose()

    return {"status": "completed", "document_id": str(document_id)}