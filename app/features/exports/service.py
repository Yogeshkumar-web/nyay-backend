import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict

import boto3
from botocore.config import Config
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.features.drafts.models import Draft, DraftExport, DraftStatus, ExportFormat
from app.features.users.models import User
from app.workers.export_tasks import generate_export_task


class ExportService:
    def __init__(self, db: AsyncSession):
        self.db = db

        self.r2 = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )

    # ============================================================
    # TRIGGER EXPORT
    # ============================================================

    async def trigger_export(
        self,
        draft_id: uuid.UUID,
        export_format: str,
        current_user: User,
    ) -> Dict:
        # ---- Fetch draft
        stmt = select(Draft).where(Draft.id == draft_id)
        result = await self.db.execute(stmt)
        draft = result.scalar_one_or_none()

        if not draft:
            raise NotFoundError("Draft not found")

        # ---- Access control (CRITICAL FIX)
        if draft.created_by != current_user.id:
            raise ForbiddenError("Not authorized to export this draft")

        # ---- Draft readiness check
        if draft.status != DraftStatus.ready:
            raise ValidationError("Draft is not ready for export")

        # ---- Validate export format
        try:
            fmt = ExportFormat(export_format)
        except ValueError:
            raise ValidationError("Invalid export format")

        # ---- Idempotency (avoid duplicate spam)
        existing_stmt = select(DraftExport).where(
            DraftExport.draft_id == draft.id,
            DraftExport.export_format == fmt,
        )
        existing = (await self.db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            return {"job_id": None, "export_id": str(existing.id)}

        # ---- Create export record (NOT committed yet)
        export = DraftExport(
            draft_id=draft.id,
            exported_by=current_user.id,
            export_format=fmt,
            r2_key=f"exports/{draft.id}/{uuid.uuid4()}.{fmt.value}",
            r2_bucket=settings.R2_BUCKET_NAME,
        )

        self.db.add(export)
        await self.db.flush()  # keep transaction open

        # ---- Trigger async job
        task = generate_export_task.delay(str(export.id))

        # ---- Commit AFTER task scheduled
        await self.db.commit()

        return {
            "job_id": task.id,
            "export_id": str(export.id),
        }

    # ============================================================
    # GET EXPORT
    # ============================================================

    async def get_export(
        self,
        export_id: uuid.UUID,
        current_user: User,
    ) -> DraftExport:
        stmt = select(DraftExport).where(DraftExport.id == export_id)
        result = await self.db.execute(stmt)
        export = result.scalar_one_or_none()

        if not export:
            raise NotFoundError("Export not found")

        # ---- Access control
        if export.exported_by != current_user.id:
            raise ForbiddenError("Not authorized")

        return export

    # ============================================================
    # PRESIGNED URL
    # ============================================================

    async def get_presigned_url(
        self,
        export_id: uuid.UUID,
        current_user: User,
    ) -> Dict:
        export = await self.get_export(export_id, current_user)

        # ---- Ensure file is ready
        if not export.file_size_bytes:
            raise NotFoundError("Export file is not ready yet")

        # ---- Generate URL
        expires_in = 900  # 15 minutes

        try:
            url = self.r2.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": export.r2_bucket,
                    "Key": export.r2_key,
                },
                ExpiresIn=expires_in,
            )
        except Exception:
            raise ValidationError("Failed to generate download URL")

        return {
            "url": url,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        }
