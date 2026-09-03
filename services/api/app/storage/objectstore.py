from __future__ import annotations

from typing import Protocol


class ObjectStore(Protocol):
    """内容寻址对象存储最小接口；S3/MinIO 与内存替身同签名。"""

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


class MemoryObjectStore:
    """测试与无 S3 配置时的内存实现（进程生命周期内持久）。"""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self._objects[key] = data

    def get(self, key: str) -> bytes:
        return self._objects[key]

    def exists(self, key: str) -> bool:
        return key in self._objects

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)


class MinioObjectStore:
    """S3 兼容实现（MinIO / AWS S3），凭据来自 settings。"""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        client=None,
    ) -> None:
        # M8-00: client 可注入（单测用假 client 验证 bucket 初始化）
        if client is not None:
            self._client = client
        else:
            import boto3  # 延迟导入：无 S3 配置的环境不强制依赖

            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        self._bucket = bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """幂等：bucket 不存在则创建。MinIO 全新卷不含 bucket，缺此步首笔上传即失败。"""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except self._client.exceptions.ClientError:  # 与 exists() 同款异常面
            self._client.create_bucket(Bucket=self._bucket)

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else None
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, **(extra or {})
        )

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client.exceptions.ClientError:
            return False

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)


def make_object_store(settings) -> ObjectStore:
    """按 settings 选择后端：S3 凭据齐全用 MinIO，否则内存替身。"""
    if settings.s3_endpoint and settings.s3_bucket and settings.s3_access_key and settings.s3_secret_key:
        return MinioObjectStore(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )
    return MemoryObjectStore()
