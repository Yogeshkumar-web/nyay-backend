import asyncio
import io
import logging
import uuid
from datetime import datetime, timezone, timedelta

import boto3
from botocore.config import Config
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.features.drafts.models import DraftExport, ExportFormat
from app.workers.celery_app import celery_app
from app.workers.job_store import JobStatus, set_job_status

from xhtml2pdf import pisa
from bs4 import BeautifulSoup
from docx import Document

logger = logging.getLogger(__name__)


def _get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


async def _process_export(export_id: str, job_id: str):
    await set_job_status(job_id, JobStatus.processing)

    async with AsyncSessionLocal() as db:
        # Fetch the export and draft
        stmt = (
            select(DraftExport)
            .options(selectinload(DraftExport.draft))
            .where(DraftExport.id == uuid.UUID(export_id))
        )
        result = await db.execute(stmt)
        export = result.scalar_one_or_none()

        if not export or not export.draft:
            await set_job_status(
                job_id, JobStatus.failed, error="Export or Draft not found"
            )
            return

        draft = export.draft
        html_content = draft.content or ""

        # Add basic HTML structure for xhtml2pdf if missing
        if not html_content.strip().startswith("<html"):
            html_content = f"<html><body>{html_content}</body></html>"

        file_bytes = b""

        try:
            if export.export_format == ExportFormat.pdf:
                # PDF Conversion using xhtml2pdf
                pdf_io = io.BytesIO()
                # Basic CSS for High Court
                css = """
                @page { size: A4; margin-left: 3cm; margin-right: 2cm; margin-top: 2cm; margin-bottom: 2cm; }
                body { font-family: "Times New Roman", serif; font-size: 14pt; line-height: 1.5; }
                p { text-align: justify; margin-bottom: 10px; }
                table { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 8pt 0 12pt; }
                th, td { border: 1px solid #555; padding: 5pt 6pt; vertical-align: top; line-height: 1.35; word-wrap: break-word; }
                th { font-weight: bold; background-color: #f5f5f5; }
                """
                styled_html = f"<html><head><style>{css}</style></head><body>{draft.content}</body></html>"
                pisa_status = pisa.CreatePDF(styled_html, dest=pdf_io)
                if pisa_status.err:
                    raise Exception("PDF Generation failed")
                file_bytes = pdf_io.getvalue()
                mime_type = "application/pdf"

            elif export.export_format == ExportFormat.docx:
                # DOCX Conversion using python-docx and bs4
                doc = Document()
                soup = BeautifulSoup(html_content, "html.parser")

                # Simple parser: just iterate over elements
                for element in (
                    soup.body.find_all(
                        ["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"],
                        recursive=False,
                    )
                    if soup.body
                    else soup.find_all(
                        ["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"]
                    )
                ):
                    text = element.get_text().strip()
                    if not text:
                        continue
                    if element.name.startswith("h"):
                        level = int(element.name[1])
                        doc.add_heading(text, level=level)
                    else:
                        doc.add_paragraph(text)

                docx_io = io.BytesIO()
                doc.save(docx_io)
                file_bytes = docx_io.getvalue()
                mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

            # Upload to R2
            r2 = _get_r2_client()
            r2.put_object(
                Bucket=export.r2_bucket,
                Key=export.r2_key,
                Body=file_bytes,
                ContentType=mime_type,
            )

            # Update export metadata
            export.file_size_bytes = len(file_bytes)
            export.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
            await db.commit()

            await set_job_status(
                job_id, JobStatus.completed, result={"export_id": str(export.id)}
            )

        except Exception as e:
            logger.exception("Export generation failed")
            await set_job_status(job_id, JobStatus.failed, error=str(e))


@celery_app.task(bind=True, name="exports.generate")
def generate_export_task(self, export_id: str):
    job_id = self.request.id
    asyncio.run(_process_export(export_id, job_id))
