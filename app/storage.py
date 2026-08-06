"""Acesso ao object storage (MinIO em desenvolvimento, S3 em produção)."""
import asyncio
from typing import Any, BinaryIO

import boto3
from botocore.config import Config

from app import config

_client: Any = None


def client() -> Any:
    """Cliente boto3 criado uma única vez; instanciá-lo por requisição custa caro."""
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _client


def reset() -> None:
    global _client
    _client = None


async def upload(fileobj: BinaryIO, key: str, content_type: str) -> None:
    """Envia em streaming: o arquivo já está em disco pelo spool do Starlette e nunca é lido inteiro na memória."""
    await asyncio.to_thread(
        client().upload_fileobj, fileobj, config.S3_BUCKET, key, ExtraArgs={"ContentType": content_type}
    )


async def presigned_url(key: str, ttl_seconds: int) -> str:
    url = await asyncio.to_thread(
        client().generate_presigned_url,
        "get_object",
        Params={"Bucket": config.S3_BUCKET, "Key": key},
        ExpiresIn=ttl_seconds,
    )
    return url.replace(config.S3_ENDPOINT_URL, config.S3_PUBLIC_ENDPOINT_URL)
