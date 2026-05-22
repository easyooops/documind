"""Storage backend abstraction — local filesystem and cloud providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles


class StorageBackend(ABC):
    """Abstract base class for file storage operations."""

    @abstractmethod
    async def save(self, data: bytes, path: str, content_type: str) -> str:
        ...

    @abstractmethod
    async def load(self, path: str) -> bytes:
        ...

    @abstractmethod
    async def get_download_url(self, path: str, expires_in: int = 3600) -> str:
        ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        ...


class LocalStorage(StorageBackend):
    """Local filesystem storage backend."""

    def __init__(self, base_path: str = "./data/outputs"):
        self.base = Path(base_path)
        self.base.mkdir(parents=True, exist_ok=True)

    async def save(self, data: bytes, path: str, content_type: str) -> str:
        full_path = self.base / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(full_path, "wb") as f:
            await f.write(data)
        return str(full_path)

    async def load(self, path: str) -> bytes:
        full_path = self.base / path
        async with aiofiles.open(full_path, "rb") as f:
            return await f.read()

    async def get_download_url(self, path: str, expires_in: int = 3600) -> str:
        return f"/api/v1/downloads/{path}"

    async def delete(self, path: str) -> None:
        full_path = self.base / path
        if full_path.exists():
            full_path.unlink()

    async def exists(self, path: str) -> bool:
        return (self.base / path).exists()


class S3Storage(StorageBackend):
    """AWS S3 storage backend."""

    def __init__(self, bucket: str, region: str):
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    async def save(self, data: bytes, path: str, content_type: str) -> str:
        self.client.put_object(
            Bucket=self.bucket, Key=path, Body=data, ContentType=content_type
        )
        return f"s3://{self.bucket}/{path}"

    async def load(self, path: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=path)
        return response["Body"].read()

    async def get_download_url(self, path: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": path},
            ExpiresIn=expires_in,
        )

    async def delete(self, path: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=path)

    async def exists(self, path: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False


def create_storage_backend() -> StorageBackend:
    """Factory function to create the configured storage backend."""
    from src.core.config import settings

    if settings.storage_type == "local":
        return LocalStorage(settings.storage_local_path)
    elif settings.storage_type == "s3":
        if not settings.aws_s3_bucket or not settings.aws_s3_region:
            raise ValueError("S3 storage requires AWS_S3_BUCKET and AWS_S3_REGION")
        return S3Storage(settings.aws_s3_bucket, settings.aws_s3_region)
    raise ValueError(f"Unsupported storage type: {settings.storage_type}")
