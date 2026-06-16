import asyncio
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.features.documents.models import Document, UploadStatus


def storage_object_exists(bucket: str, key: str) -> bool:
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


async def main() -> None:
    repaired = 0
    failed = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.upload_status != UploadStatus.uploaded)
        )
        docs = list(result.scalars().all())

        for doc in docs:
            if storage_object_exists(doc.r2_bucket, doc.r2_key):
                doc.upload_status = UploadStatus.uploaded
                repaired += 1
            else:
                doc.upload_status = UploadStatus.failed
                failed += 1
            doc.updated_at = datetime.now(timezone.utc)

        await session.commit()

    await engine.dispose()
    print(f"Checked {len(docs)} documents. Repaired={repaired}. Missing={failed}.")


if __name__ == "__main__":
    asyncio.run(main())
