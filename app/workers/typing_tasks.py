"""
Typing Celery task — invokes the LangGraph Typing Agent to convert OCR text
+ PDF page images into HC-formatted Tiptap HTML, then persists to TypedVersion.

Flow:
  1. Load Document from DB — validate OCR text present
  2. Generate R2 presigned GET URL (10-min expiry) for Node 1 (pdf fetch)
  3. Build default_state() and invoke typing_graph
  4. Save final_html → TypedVersion.typed_content
     Save processing_notes → TypedVersion.agent_notes
  5. Update job status

Fallback: if the agent returns empty HTML, raw OCR text is used as a <p> block
so the user is never left with an empty Tiptap editor.
"""
import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status

logger = logging.getLogger(__name__)

# R2 presigned URL TTL — long enough for the full agent pipeline (6 nodes, 2 retries)
_PRESIGN_EXPIRY_SECONDS = 600  # 10 minutes


def _document_type_value(document_type: object) -> str:
    return str(getattr(document_type, "value", document_type) or "").lower()


def _should_use_fir_template(document_type: object, ocr_text: str | None) -> bool:
    from app.features.documents.fir_reconstruction import is_fir_text

    return _document_type_value(document_type) == "fir" or is_fir_text(ocr_text or "")


# ─────────────────────────────────────────────────────────────────────────────
# Celery task entry point
# ─────────────────────────────────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="typing.run_type_document",
    max_retries=3,
    default_retry_delay=30,
)
def run_type_document(self, document_id: str) -> dict:
    return asyncio.run(_typing_task_async(self.request.id, document_id))


# ─────────────────────────────────────────────────────────────────────────────
# Async implementation
# ─────────────────────────────────────────────────────────────────────────────


async def _typing_task_async(job_id: str, document_id_str: str) -> dict:
    import app.db.registry  # noqa: F401 — load all ORM models for SQLAlchemy
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.agents.typing.graph import typing_graph
    from app.agents.typing.state import default_state
    from app.core.config import settings
    from app.features.documents.models import DocumentType
    from app.features.documents.repository import DocumentRepository
    from app.features.extraction.repository import ExtractionRepository

    document_id = uuid.UUID(document_id_str)
    logger.info("typing_task started | job_id=%s | document_id=%s", job_id, document_id)
    await set_job_status(job_id, JobStatus.processing)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as session:
            doc_repo = DocumentRepository(session)
            ext_repo = ExtractionRepository(session)

            # ── 1. Load + validate document ───────────────────────────────────
            doc = await doc_repo.get_by_id(document_id)
            if not doc:
                logger.warning("typing_task: document not found | job_id=%s", job_id)
                await set_job_status(
                    job_id, JobStatus.failed, error="Document not found"
                )
                return {"status": "failed", "error": "Document not found"}

            if not doc.ocr_raw_text:
                logger.warning("typing_task: no OCR text | job_id=%s", job_id)
                await set_job_status(
                    job_id, JobStatus.failed, error="No OCR text available"
                )
                return {"status": "failed", "error": "No OCR text available"}

            document_type_value = _document_type_value(doc.document_type)
            should_use_fir_template = _should_use_fir_template(
                doc.document_type,
                doc.ocr_raw_text,
            )
            fir_auto_detected = (
                document_type_value != DocumentType.fir.value
                and should_use_fir_template
            )

            if should_use_fir_template:
                from app.features.documents.fir_template_service import (
                    FirTemplateService,
                )

                if fir_auto_detected:
                    logger.info(
                        "typing_task auto-detected FIR; updating document_type "
                        "| job_id=%s | document_id=%s",
                        job_id,
                        document_id,
                    )
                    doc.document_type = DocumentType.fir

                artifact = (
                    doc.ocr_artifact if isinstance(doc.ocr_artifact, dict) else {}
                )
                result = FirTemplateService().build_typed_version(
                    doc,
                    audit={
                        "fir_auto_detected": str(fir_auto_detected).lower(),
                        "second_pass_used": str(
                            artifact.get("second_pass_used", False)
                        ).lower(),
                        "processor": artifact.get("processor"),
                        "processor_role": artifact.get("processor_role"),
                    },
                )
                await ext_repo.create_or_update_typed_version(
                    document_id=document_id,
                    typed_content=result.html,
                    agent_notes=result.notes,
                )
                await session.commit()

                logger.info(
                    "typing_task completed via FIR template | job_id=%s"
                    " | document_id=%s | html_len=%d | confidence=%.2f"
                    " | missing=%s",
                    job_id,
                    document_id,
                    len(result.html),
                    result.schema.confidence_score,
                    ",".join(result.schema.missing_required_fields) or "none",
                )

                await set_job_status(
                    job_id,
                    JobStatus.completed,
                    result={
                        "document_id": str(document_id),
                        "html_length": len(result.html),
                        "retry_count": 0,
                        "review_passed": True,
                        "pipeline": "fir_template",
                        "confidence": result.schema.confidence_score,
                        "missing_required_fields": result.schema.missing_required_fields,
                    },
                )

                return {
                    "status": "completed",
                    "document_id": str(document_id),
                    "html_length": len(result.html),
                    "pipeline": "fir_template",
                }

            # ── 2. R2 presigned URL for PDF fetch (Node 1) ────────────────────
            presigned_url = _generate_presigned_get_url(
                bucket=doc.r2_bucket,
                key=doc.r2_key,
                expires_in=_PRESIGN_EXPIRY_SECONDS,
            )
            if not presigned_url:
                logger.warning(
                    "typing_task: R2 presign failed, Node 1 will use text-only fallback"
                    " | job_id=%s",
                    job_id,
                )

            # ── 3. Build state + invoke graph ─────────────────────────────────
            ocr_text = doc.ocr_raw_text or ""

            # Legacy check: old OCR code stored HTML in ocr_raw_text.
            # The typing agent (fir_parser) expects raw OCR text — feeding it
            # stripped HTML produces garbled output.  For these legacy docs the
            # OCR HTML is already well-formatted, so we use it directly as the
            # final output and skip the agent entirely.
            is_legacy_html = "<" in ocr_text and ">" in ocr_text
            result_state: dict = {}  # populated only on the agent path

            if is_legacy_html:
                logger.warning(
                    "typing_task: ocr_raw_text is legacy HTML — using it"
                    " directly as final_html (re-run OCR to enable AI pipeline)"
                    " | doc=%s",
                    document_id,
                )
                from datetime import datetime, timezone as _tz

                _ts = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                final_html = ocr_text
                processing_notes = (
                    f"ts={_ts} | legacy:html_passthrough"
                    " | re-run OCR to enable full typing agent"
                )
                retry_count = 0
                review_passed = True
            else:
                # New path: plain text → full typing agent pipeline
                state = default_state(
                    ocr_raw_text=ocr_text,
                    document_type=doc.document_type.value,
                    document_id=str(document_id),
                    presigned_url=presigned_url,
                    total_pages=doc.page_count or 0,
                    ocr_artifact=doc.ocr_artifact,
                )

                try:
                    result_state = await typing_graph.ainvoke(state)
                except Exception as exc:
                    logger.exception(
                        "typing_graph raised | job_id=%s | document_id=%s",
                        job_id,
                        document_id,
                    )
                    await set_job_status(job_id, JobStatus.failed, error=str(exc))
                    return {"status": "failed", "error": str(exc)}

                final_html = result_state.get("final_html") or ""
                processing_notes = result_state.get("processing_notes") or ""
                retry_count = result_state.get("retry_count", 0)
                review_passed = result_state.get("review_passed", False)

            # ── 4. Fallback: empty HTML → raw OCR so editor is never blank ────
            # (legacy_html path already has final_html set; only needed for agent path)
            if not is_legacy_html and (
                not final_html.strip() or final_html == "<p></p>"
            ):
                logger.warning(
                    "typing_graph produced empty HTML, falling back to OCR text"
                    " | job_id=%s",
                    job_id,
                )
                final_html = f"<p>{doc.ocr_raw_text}</p>"
                processing_notes = (processing_notes + " | fallback:empty_html").lstrip(
                    " | "
                )

            # ── 5. Persist to TypedVersion ────────────────────────────────────
            logger.info(
                "typing_html_preview | doc=%s | html_len=%d | start=%s",
                document_id,
                len(final_html),
                repr(final_html[:300]),
            )
            await ext_repo.create_or_update_typed_version(
                document_id=document_id,
                typed_content=final_html,
                agent_notes=processing_notes,
            )
            await session.commit()

            # ── Cost / quality audit log — grep for "typing_agent_audit" in ops ──
            _rs = result_state if not is_legacy_html else {}
            logger.info(
                "typing_agent_audit | job_id=%s | document_id=%s | html_len=%d"
                " | retries=%d | review=%s | quality=%.2f | lang=%s | sections=%d"
                " | notes=%s",
                job_id,
                document_id,
                len(final_html),
                retry_count,
                "pass" if review_passed else "fail",
                _rs.get("ocr_quality_score", 0.0),
                _rs.get("ocr_language", "legacy"),
                len(_rs.get("formatted_sections", {})),
                processing_notes[:200],
            )

            logger.info(
                "typing_task completed | job_id=%s | document_id=%s"
                " | html_len=%d | retries=%d | review=%s",
                job_id,
                document_id,
                len(final_html),
                retry_count,
                review_passed,
            )

            await set_job_status(
                job_id,
                JobStatus.completed,
                result={
                    "document_id": str(document_id),
                    "html_length": len(final_html),
                    "retry_count": retry_count,
                    "review_passed": review_passed,
                },
            )

            return {
                "status": "completed",
                "document_id": str(document_id),
                "html_length": len(final_html),
            }

    finally:
        await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# R2 helper
# ─────────────────────────────────────────────────────────────────────────────


def _generate_presigned_get_url(
    bucket: str,
    key: str,
    expires_in: int = _PRESIGN_EXPIRY_SECONDS,
) -> str:
    """
    Generate an R2 presigned GET URL. Synchronous (boto3 is sync).
    Returns "" on any error — Node 1 treats an empty URL as fetch_error and
    falls back to text-only mode (no page images). The pipeline still completes.
    """
    import boto3

    from app.core.config import settings

    try:
        r2 = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        return r2.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception as exc:
        logger.warning(
            "_generate_presigned_get_url failed: %s | bucket=%s | key=%s",
            exc,
            bucket,
            key,
        )
        return ""
