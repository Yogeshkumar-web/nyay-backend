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
    from app.features.documents.document_processing_service import process_document_bytes
    from app.features.documents.digital_extractor import (
        IMAGE_MIME_TYPES,
        PDF_MIME,
        build_single_image_page,
        split_pdf_pages,
    )
    from app.features.documents.models import (
        OcrStatus,
        ProcessingRoute,
        ProcessingRunStatus,
    )
    from app.features.documents.page_stitching import stitch_ocr_pages
    from app.features.documents.repository import DocumentRepository
    from app.features.documents.storage import read_r2_bytes
    from app.features.documents.structured_extraction import SarvamStructuredExtractor

    document_id = uuid.UUID(document_id_str)
    await set_job_status(job_id, JobStatus.processing)
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            repo = DocumentRepository(session)
            doc = await repo.get_by_id(document_id)
            processing_run = await repo.get_processing_run_by_job_id(job_id)
            if not doc:
                await set_job_status(
                    job_id, JobStatus.failed, error="Document not found"
                )
                return {"status": "failed", "error": "Document not found"}

            try:
                file_bytes = await read_r2_bytes(doc.r2_bucket, doc.r2_key)
                page_splits = []
                if processing_run is not None and doc.mime_type in {
                    PDF_MIME,
                    *IMAGE_MIME_TYPES,
                }:
                    await repo.update_processing_run(
                        processing_run,
                        status=ProcessingRunStatus.splitting_pages,
                    )
                    await session.commit()
                    page_splits = (
                        split_pdf_pages(file_bytes)
                        if doc.mime_type == PDF_MIME
                        else [build_single_image_page(file_bytes, doc.mime_type)]
                    )
                    page_rows: list[dict] = []
                    for page in page_splits:
                        page_rows.append(
                            {
                                "page_number": page.page_number,
                                "source_filename": page.filename,
                                "mime_type": page.mime_type,
                                "checksum_sha256": page.checksum,
                                "size_bytes": page.size_bytes,
                            }
                        )
                    await repo.replace_run_pages(doc, processing_run, page_rows)
                    await repo.update_processing_run(
                        processing_run,
                        status=ProcessingRunStatus.ocr_running,
                        metrics={"split_page_count": len(page_rows)},
                    )
                    await repo.mark_run_pages_ocr_running(processing_run)
                    await session.commit()
                from app.features.documents.sarvam_vision import SarvamVisionOcrProvider

                ocr_provider = SarvamVisionOcrProvider()
                if page_splits:
                    ocr_result = await ocr_provider.process_pages(page_splits)
                    if processing_run is not None:
                        await repo.update_run_page_ocr_results(
                            processing_run,
                            ocr_result.artifact["pages"],
                        )
                        await repo.update_processing_run(
                            processing_run,
                            status=ProcessingRunStatus.stitching_pages,
                        )
                        await session.commit()
                    stitched = stitch_ocr_pages(ocr_result.artifact["pages"])
                    ocr_artifact = {
                        **ocr_result.artifact,
                        "stitching": stitched.artifact(),
                    }
                    result_route = ProcessingRoute.scanned_ocr
                    result_source_text = stitched.text
                    result_source_artifact = ocr_artifact
                    result_classification_details = {
                        "classifier": "sarvam_page_batch_v1",
                        "page_count": ocr_result.page_count,
                        "ocr_batch_count": ocr_result.artifact.get("batch_count", 0),
                        "max_batch_pages": ocr_result.artifact.get("batch_size", 10),
                        "stitching_strategy": "page_marker_stitching_v1",
                    }
                    result_is_scanned = True
                    result_ocr_text = stitched.text
                    result_ocr_language = ocr_result.language
                    result_ocr_artifact = ocr_artifact
                    result_page_count = ocr_result.page_count
                    if processing_run is not None:
                        await repo.update_processing_run(
                            processing_run,
                            status=ProcessingRunStatus.structured_extraction,
                        )
                        await session.commit()
                    structured = await SarvamStructuredExtractor().extract(
                        stitched.text,
                        document_type_hint=doc.document_type.value,
                        page_artifact=ocr_artifact,
                    )
                    result_source_artifact = {
                        **result_source_artifact,
                        "structured_extraction": structured,
                    }
                    result_ocr_artifact = {
                        **result_ocr_artifact,
                        "structured_extraction": structured,
                    }
                else:
                    result = await process_document_bytes(
                        file_bytes,
                        doc.mime_type,
                        ocr_service=ocr_provider,
                    )
                    result_route = result.route
                    result_source_text = result.source_text
                    result_source_artifact = result.source_artifact
                    result_classification_details = result.classification_details
                    result_is_scanned = result.is_scanned
                    result_ocr_text = result.ocr_text
                    result_ocr_language = result.ocr_language
                    result_ocr_artifact = result.ocr_artifact
                    result_page_count = result.page_count
                await repo.complete_processing(
                    doc,
                    route=result_route,
                    source_text=result_source_text,
                    source_artifact=result_source_artifact,
                    classification_details=result_classification_details,
                    is_scanned=result_is_scanned,
                )

                if result_route == ProcessingRoute.digital_extract:
                    doc.ocr_status = OcrStatus.not_required
                    doc.ocr_raw_text = None
                    doc.ocr_language = None
                    doc.ocr_artifact = None
                    doc.ocr_provider = None
                    doc.ocr_error = None
                    doc.ocr_completed_at = None
                    doc.page_count = result_page_count
                else:
                    provider = (
                        result_ocr_artifact or {}
                    ).get("provider") or "ocr_provider"
                    await repo.set_ocr_status(
                        doc,
                        OcrStatus.completed,
                        raw_text=result_ocr_text,
                        language=result_ocr_language,
                        page_count=result_page_count,
                        provider=provider,
                        artifact=result_ocr_artifact,
                        job_id=job_id,
                    )
                await repo.update_processing_run(
                    processing_run,
                    status=ProcessingRunStatus.ready_for_review,
                    metrics={
                        "page_count": result_page_count,
                        "source_chars": len(result_source_text or ""),
                        "ocr_chars": len(result_ocr_text or ""),
                    },
                    metadata={
                        "processing_route": result_route.value,
                        "provider": (
                            result_ocr_artifact or result_source_artifact or {}
                        ).get("provider"),
                    },
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
                await repo.update_processing_run(
                    processing_run,
                    status=ProcessingRunStatus.failed,
                    error=str(exc),
                )
                await session.commit()
                await set_job_status(job_id, JobStatus.failed, error=str(exc))
                return {"status": "failed", "error": str(exc)}

        response = {
            "document_id": document_id_str,
            "processing_route": result_route.value,
        }
        await set_job_status(job_id, JobStatus.completed, result=response)
        return {"status": "completed", **response}
    finally:
        await engine.dispose()
