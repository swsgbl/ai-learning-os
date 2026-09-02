"""M5-01 Search provider abstraction：多搜索源可插拔（backlog M5-01）。

- SearchProvider 协议：name/kind/search(query, limit)——新搜索源实现协议即可插入；
- LocalCorpusProvider：检索 M1 语料 chunks（本地、无外网依赖，默认可用）；
- CloudWebProvider：通用 web 搜索——endpoint/api_key 未配置时不可用，
  不可用原因进入执行记录的 skipped（弃用原因可记录，不虚报可用）；
- build_search_registry：按 settings 装配注册表，路由层据此执行多源查询。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings
from app.repositories.chunks import ChunkRepository


class ProviderUnavailable(Exception):
    """搜索源当前不可用（未配置/网络不可达等），原因进 skipped 记录。"""


class SearchProvider(Protocol):
    name: str
    kind: str

    async def search(self, query: str, limit: int) -> list[dict]: ...


@dataclass
class ProviderEntry:
    """注册表条目：enabled=False 时 unavailable_reason 必有值（不虚报可用）。"""

    name: str
    kind: str
    enabled: bool
    unavailable_reason: str | None
    provider: SearchProvider | None


class LocalCorpusProvider:
    """本地语料检索：chunks 表 contains 匹配，无需外网。

    结果标注 authority="platform"——种子语料是平台自持、经导入审核的内容
    （M5-04 分层排序依据；不虚报为 official）。
    """

    name = "local-corpus"
    kind = "local-corpus"

    def __init__(self, chunks: ChunkRepository) -> None:
        self._chunks = chunks

    async def search(self, query: str, limit: int, owner: str | None = None) -> list[dict]:
        """M9-05 语料边界：owner（auth on）= 自有 + public；None = 本地模式不过滤。"""
        rows = await self._chunks.search_text(query, limit, owner=owner)
        return [
            {
                "title": f"{row['resource_id']}#chunk-{row['chunk_index']}",
                "url": f"/api/v1/resources/{row['resource_id']}/chunks/{row['chunk_index']}",
                "snippet": row["text"][:200],
                "source": self.name,
                "provider": self.name,
                "authority": "platform",
            }
            for row in rows
        ]


class CloudWebProvider:
    """通用 web 搜索：依赖 settings.search_cloud_endpoint/api_key，未配置即不可用。"""

    name = "cloud-web"
    kind = "web"

    def __init__(self, settings: Settings) -> None:
        self._endpoint = settings.search_cloud_endpoint
        self._api_key = settings.search_cloud_api_key

    async def search(self, query: str, limit: int) -> list[dict]:
        if not self._endpoint or not self._api_key:
            raise ProviderUnavailable("cloud-web 未配置 SEARCH_CLOUD_ENDPOINT/SEARCH_CLOUD_API_KEY")
        raise ProviderUnavailable(f"cloud-web 网络请求失败: {self._endpoint} 不可达")

    def is_configured(self) -> bool:
        return bool(self._endpoint and self._api_key)


def build_search_registry(chunks: ChunkRepository, settings: Settings) -> list[ProviderEntry]:
    """装配注册表：本地语料恒可用；cloud-web 按 endpoint/key 配置决定可用性。"""
    cloud = CloudWebProvider(settings)
    return [
        ProviderEntry(
            name="local-corpus",
            kind="local-corpus",
            enabled=True,
            unavailable_reason=None,
            provider=LocalCorpusProvider(chunks),
        ),
        ProviderEntry(
            name="cloud-web",
            kind="web",
            enabled=cloud.is_configured(),
            unavailable_reason=None if cloud.is_configured() else "SEARCH_CLOUD_ENDPOINT/SEARCH_CLOUD_API_KEY 未配置",
            provider=cloud,
        ),
    ]
