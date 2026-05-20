"""
Manual OCR Celery task.

OCR is queued only when the user presses the OCR button. Completing OCR does not
trigger extraction; extraction has its own user-triggered endpoint.

After successful OCR, a TypedVersion row is auto-created so the user can
immediately open the Tiptap editor and edit the extracted text.
"""
import logging
import uuid

from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status

logger = logging.getLogger(__name__)


def _document_type_value(document_type: object) -> str:
    return str(getattr(document_type, "value", document_type) or "").lower()


def _should_run_fir_second_pass(
    *,
    document_type: object,
    fir_auto_detected: bool,
    first_processor: str,
    fir_processor: str,
) -> bool:
    return (
        fir_auto_detected
        and _document_type_value(document_type) != "fir"
        and bool(fir_processor)
        and first_processor != fir_processor
    )


@celery_app.task(
    bind=True,
    name="ocr.run_ocr_on_document",
    max_retries=0,
)
def run_ocr_on_document(self, document_id: str) -> dict:
    import asyncio

    return asyncio.run(_ocr_task_async(self.request.id, document_id))


async def _ocr_task_async(job_id: str, document_id_str: str) -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.registry  # noqa: F401 - load SQLAlchemy relationship targets
    from app.core.config import settings
    from app.features.documents.fir_reconstruction import is_fir_text
    from app.features.documents.fir_template_service import FirTemplateService
    from app.features.documents.models import DocumentType, OcrStatus
    from app.features.documents.ocr_service import (
        OcrConfigurationError,
        OcrNoTextError,
        OcrProviderError,
        OcrStorageError,
        run_document_ocr,
    )
    from app.features.documents.repository import DocumentRepository
    from app.features.extraction.repository import ExtractionRepository

    document_id = uuid.UUID(document_id_str)
    logger.info("Starting OCR task job_id=%s document_id=%s", job_id, document_id)
    await set_job_status(job_id, JobStatus.processing)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as session:
            repo = DocumentRepository(session)
            doc = await repo.get_by_id(document_id)

            if not doc:
                logger.warning(
                    "OCR task could not find document job_id=%s document_id=%s",
                    job_id,
                    document_id,
                )
                await set_job_status(
                    job_id, JobStatus.failed, error="Document not found"
                )
                return {"status": "failed", "error": "Document not found"}

            logger.info(
                "OCR task loaded document job_id=%s document_id=%s bucket=%s "
                "key=%s mime_type=%s upload_status=%s ocr_status=%s",
                job_id,
                document_id,
                doc.r2_bucket,
                doc.r2_key,
                doc.mime_type,
                doc.upload_status.value,
                doc.ocr_status.value,
            )

            await repo.set_ocr_status(doc, OcrStatus.processing, job_id=job_id)
            await session.commit()

            fir_auto_detected = False
            second_pass_used = False
            template_confidence: float | None = None

            # ── Run OCR ────────────────────────────────────────────────────────
            try:
                ocr_result = await run_document_ocr(
                    r2_bucket=doc.r2_bucket,
                    r2_key=doc.r2_key,
                    mime_type=doc.mime_type,
                    document_type=doc.document_type.value,
                )
                fir_auto_detected = is_fir_text(ocr_result.text)
                first_processor = str(ocr_result.metadata.get("processor") or "")
                should_retry_with_fir_processor = _should_run_fir_second_pass(
                    document_type=doc.document_type,
                    fir_auto_detected=fir_auto_detected,
                    first_processor=first_processor,
                    fir_processor=settings.GOOGLE_DOCAI_FIR_PROCESSOR_ID,
                )
                if should_retry_with_fir_processor:
                    logger.info(
                        "FIR auto-detected after generic OCR; running FIR Form "
                        "Parser second pass job_id=%s document_id=%s "
                        "first_processor=%s fir_processor=%s",
                        job_id,
                        document_id,
                        first_processor,
                        settings.GOOGLE_DOCAI_FIR_PROCESSOR_ID,
                    )
                    ocr_result = await run_document_ocr(
                        r2_bucket=doc.r2_bucket,
                        r2_key=doc.r2_key,
                        mime_type=doc.mime_type,
                        document_type=DocumentType.fir.value,
                    )
                    second_pass_used = True
                    fir_auto_detected = True

            except OcrConfigurationError as exc:
                # Server-side config problem — admin needs to fix .env / credentials.
                logger.error(
                    "OCR configuration error job_id=%s document_id=%s: %s",
                    job_id,
                    document_id,
                    exc,
                )
                return await _fail_ocr(
                    session,
                    repo,
                    doc,
                    job_id,
                    document_id,
                    error_message=str(exc),
                    error_code=exc.code,
                    details=exc.details,
                )

            except OcrNoTextError as exc:
                # Document processed successfully but contained no readable text
                # (blank page, pure image, etc.). User should use Skip OCR.
                logger.warning(
                    "OCR no text extracted job_id=%s document_id=%s: %s",
                    job_id,
                    document_id,
                    exc,
                )
                return await _fail_ocr(
                    session,
                    repo,
                    doc,
                    job_id,
                    document_id,
                    error_message=str(exc),
                    error_code=exc.code,
                    details=exc.details,
                )

            except OcrStorageError as exc:
                # File is missing from R2 or R2 is unreachable.
                logger.error(
                    "OCR storage error job_id=%s document_id=%s: %s",
                    job_id,
                    document_id,
                    exc,
                )
                return await _fail_ocr(
                    session,
                    repo,
                    doc,
                    job_id,
                    document_id,
                    error_message=str(exc),
                    error_code=exc.code,
                    details=exc.details,
                )

            except OcrProviderError as exc:
                # Google Document AI returned an error after all retries.
                logger.warning(
                    "OCR provider error (retries exhausted) job_id=%s document_id=%s: %s",
                    job_id,
                    document_id,
                    exc,
                )
                return await _fail_ocr(
                    session,
                    repo,
                    doc,
                    job_id,
                    document_id,
                    error_message=str(exc),
                    error_code=exc.code,
                    details=exc.details,
                )

            except Exception as exc:
                logger.exception(
                    "Unexpected OCR failure job_id=%s document_id=%s",
                    job_id,
                    document_id,
                )
                return await _fail_ocr(
                    session,
                    repo,
                    doc,
                    job_id,
                    document_id,
                    error_message=f"Unexpected error: {exc}",
                    error_code="OCR_UNEXPECTED_ERROR",
                    details={},
                )

            # ── OCR succeeded — persist text ───────────────────────────────────
            # ocr_result.text  = plain normalized text  → stored as ocr_raw_text
            #                    (typing agent reads this as its input)
            # ocr_result.html  = structured Tiptap HTML → stored in TypedVersion
            #                    (editor loads this immediately after OCR)
            if fir_auto_detected and _document_type_value(doc.document_type) != "fir":
                logger.info(
                    "Auto-updating document_type to FIR job_id=%s document_id=%s",
                    job_id,
                    document_id,
                )
                doc.document_type = DocumentType.fir

            if isinstance(ocr_result.artifact, dict):
                ocr_result.artifact["fir_auto_detected"] = fir_auto_detected
                ocr_result.artifact["second_pass_used"] = second_pass_used
                ocr_result.artifact["processor_role"] = ocr_result.metadata.get(
                    "processor_role"
                )

            await repo.set_ocr_status(
                doc,
                OcrStatus.completed,
                raw_text=ocr_result.text,
                language=ocr_result.language,
                page_count=ocr_result.page_count,
                provider=ocr_result.provider,
                artifact=ocr_result.artifact,
                job_id=job_id,
            )
            await session.commit()

            artifact_summary = ocr_result.artifact.get("summary", {})
            logger.info(
                "OCR completed job_id=%s document_id=%s text_length=%s "
                "html_length=%s provider=%s pages=%s language=%s tables=%s "
                "form_fields=%s tokens=%s avg_confidence=%s fir_auto_detected=%s "
                "second_pass_used=%s processor=%s processor_role=%s",
                job_id,
                document_id,
                len(ocr_result.text),
                len(ocr_result.html),
                ocr_result.provider,
                ocr_result.page_count,
                ocr_result.language,
                artifact_summary.get("table_count"),
                artifact_summary.get("form_field_count"),
                artifact_summary.get("token_count"),
                artifact_summary.get("average_confidence"),
                fir_auto_detected,
                second_pass_used,
                ocr_result.metadata.get("processor"),
                ocr_result.metadata.get("processor_role"),
            )

            # ── Auto-create TypedVersion so user can edit in Tiptap ───────────
            # This is a separate commit: TypedVersion failure must not roll back
            # the OCR status that was already committed above.
            try:
                extraction_repo = ExtractionRepository(session)
                is_fir_document = _document_type_value(doc.document_type) == "fir"
                if fir_auto_detected or is_fir_document:
                    template_result = FirTemplateService().build_typed_version(
                        doc,
                        audit={
                            "fir_auto_detected": str(fir_auto_detected).lower(),
                            "second_pass_used": str(second_pass_used).lower(),
                            "processor": ocr_result.metadata.get("processor"),
                            "processor_role": ocr_result.metadata.get("processor_role"),
                        },
                    )
                    typed_content = template_result.html
                    agent_notes = template_result.notes
                    template_confidence = template_result.schema.confidence_score
                else:
                    typed_content = ocr_result.html
                    agent_notes = ""

                await extraction_repo.create_or_update_typed_version(
                    document_id=document_id,
                    typed_content=ocr_result.html,  # ← HTML, not plain text
                    agent_notes="",  # OCR is not AI-generated
                )
                if fir_auto_detected or is_fir_document:
                    await extraction_repo.create_or_update_typed_version(
                        document_id=document_id,
                        typed_content=typed_content,
                        agent_notes=agent_notes,
                    )
                await session.commit()
                logger.info(
                    "TypedVersion auto-created job_id=%s document_id=%s",
                    job_id,
                    document_id,
                )
            except Exception as tv_exc:
                await session.rollback()
                # Non-critical: OCR text is safely stored on Document.ocr_raw_text.
                # The TypedVersion will be created lazily when the user opens the editor.
                logger.warning(
                    "TypedVersion auto-creation failed (non-critical) "
                    "job_id=%s document_id=%s: %s",
                    job_id,
                    document_id,
                    tv_exc,
                )

            result = {
                "document_id": str(document_id),
                "provider": ocr_result.provider,
                "language": ocr_result.language,
                "page_count": ocr_result.page_count,
                "text_length": len(ocr_result.text),
                "artifact_summary": artifact_summary,
                "processor": ocr_result.metadata.get("processor"),
                "processor_role": ocr_result.metadata.get("processor_role"),
                "fir_auto_detected": fir_auto_detected,
                "second_pass_used": second_pass_used,
                "template_confidence": template_confidence,
            }
            await set_job_status(job_id, JobStatus.completed, result=result)
            return {"status": "completed", **result}

    finally:
        await engine.dispose()


async def _fail_ocr(
    session,
    repo,
    doc,
    job_id: str,
    document_id: uuid.UUID,
    *,
    error_message: str,
    error_code: str,
    details: dict,
) -> dict:
    """Set document OCR status to failed and update the job store."""
    from app.features.documents.models import OcrStatus

    await repo.set_ocr_status(
        doc,
        OcrStatus.failed,
        error=error_message,
        job_id=job_id,
    )
    await session.commit()

    await set_job_status(
        job_id,
        JobStatus.failed,
        error=error_message,
        result={
            "document_id": str(document_id),
            "error_code": error_code,
            "details": details,
        },
    )
    return {
        "status": "failed",
        "document_id": str(document_id),
        "error": error_message,
        "error_code": error_code,
    }
