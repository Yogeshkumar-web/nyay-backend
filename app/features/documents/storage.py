from __future__ import annotations

import asyncio

import boto3
from botocore.config import Config

from app.core.config import settings


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


async def read_r2_bytes(bucket: str, key: str) -> bytes:
    def _read() -> bytes:
        response = get_r2_client().get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    return await asyncio.to_thread(_read)
