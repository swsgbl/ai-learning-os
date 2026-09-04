"""M5-01 Search provider abstraction：多搜索源可插拔（backlog M5-01）。

- SearchProvider 协议：name/kind/search(query, limit, owner)——新搜索源实现协议即可插入；
- LocalCorpusProvider：检索 M1 语料 chunks（本地、无外网依赖，默认可用）；
- CloudWebProvider：SearXNG-compatible JSON API web 搜索（M10-12 真实实现）——
  GET {SEARCH_CLOUD_ENDPOINT}/search?q=<query>&format=json，key 可选（Bearer）；
  SEARCH_MODE=local/hybrid 或 PRIVACY_SEND_CONTEXT_TO_CLOUD=false 或 endpoint
  缺失/非法时不可用，不可用原因进入执行记录的 skipped（弃用原因可记录，不虚报可用）；
- build_search_registry：按 settings 装配注册表，路由层据此执行多源查询。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.repositories.chunks import ChunkRepository

# 结果 authority 白名单（与 result_ranker.SOURCE_TIERS 同集合）：上游显式给受控
# 标注时透传，未标注/未知标注一律 community 兜底——不猜测 URL 隐含权威度（ADR 44）。
CLOUD_AUTHORITY_WHITELIST = frozenset({"official", "oer", "platform", "community"})

# snippet 上限：与 LocalCorpusProvider 的 200 字符截断同口径
CLOUD_SNIPPET_MAX = 200


class ProviderUnavailable(Exception):
    """搜索源当前不可用（未配置/网络不可达等），原因进 skipped 记录。"""


class SearchProvider(Protocol):
    name: str
    kind: str

    async def search(self, query: str, limit: int, owner: str | None = None) -> list[dict]: ...


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
    """SearXNG-compatible JSON API web 搜索（M10-12 真实实现）。

    - GET {endpoint}/search，query 参数 q 与 format=json；endpoint 是 base URL；
    - SEARCH_CLOUD_API_KEY 可选：设置时以 Authorization: Bearer 出示，
      不设置支持无鉴权 SearXNG；
    - 异步 httpx、transport 可注入（单测不触网）、超时限制；
    - fail-closed：HTTP 非 2xx / 响应非合法 JSON / results 非列表 / 网络/超时
      一律 ProviderUnavailable（进 skipped，不虚报可用）；错误信息为固定脱敏
      文案——不含 endpoint、API key、Authorization 等敏感值（不回显请求 URL）；
    - 结果归一化：results[].title/url/content -> title/url/snippet，
      provider/source=cloud-web，authority 默认 community（上游显式给受控
      标注且在白名单内时透传）；缺失/非法 URL（非 http/https、无 host）的
      结果过滤掉；返回条数不超过 limit（客户端截断）。
    """

    name = "cloud-web"
    kind = "web"

    def __init__(
        self,
        *,
        endpoint: str | None,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.strip().rstrip("/") if endpoint else None
        self._api_key = (api_key or "").strip() or None
        self._timeout = timeout_seconds
        self._transport = transport  # 测试注入 httpx.MockTransport

    async def search(
        self, query: str, limit: int, owner: str | None = None
    ) -> list[dict]:
        """执行 web 查询；owner 参数对齐路由调用形态（云端源无本地归属过滤，忽略）。"""
        if not self._endpoint:
            # 正常不可达：registry 已在 endpoint 缺失/非法时禁用本源（防御性兜底）
            raise ProviderUnavailable("cloud-web 未配置 SEARCH_CLOUD_ENDPOINT")
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{self._endpoint}/search",
                    params={"q": query, "format": "json"},
                    headers=headers,
                )
        except httpx.HTTPError as cause:
            # str(cause) 含请求 URL（endpoint）——用固定文案，不回显敏感值
            raise ProviderUnavailable("cloud-web 搜索请求失败（网络错误或超时）") from cause
        if not httpx.codes.is_success(response.status_code):
            raise ProviderUnavailable(f"cloud-web 搜索端点返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as cause:
            raise ProviderUnavailable("cloud-web 搜索响应不是合法 JSON") from cause
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ProviderUnavailable("cloud-web 搜索响应 results 字段不是列表")
        return _normalize_cloud_results(results, limit)


def _is_valid_result_url(url: object) -> bool:
    """合法结果 URL：字符串、http/https、host 非空（其余一律过滤）。"""
    if not isinstance(url, str) or not url.strip():
        return False
    split = urlsplit(url.strip())
    return split.scheme in ("http", "https") and bool(split.netloc)


def _normalize_cloud_results(results: list, limit: int) -> list[dict]:
    """results[].title/url/content -> SearchResultItem 契约（snippet 取 content）。"""
    items: list[dict] = []
    for raw in results:
        if not isinstance(raw, dict):
            continue
        url = raw.get("url")
        if not _is_valid_result_url(url):
            continue
        authority = raw.get("authority")
        items.append(
            {
                "title": str(raw.get("title") or ""),
                "url": url.strip(),
                "snippet": str(raw.get("content") or "")[:CLOUD_SNIPPET_MAX],
                "source": CloudWebProvider.name,
                "provider": CloudWebProvider.name,
                "authority": authority if authority in CLOUD_AUTHORITY_WHITELIST else "community",
            }
        )
        if len(items) >= limit:
            break
    return items


def _endpoint_unavailable_reason(endpoint: str | None) -> str | None:
    """endpoint 配置检查：返回不可用原因（None = 合法）。原因不回显 endpoint 值。"""
    if not endpoint or not endpoint.strip():
        return "SEARCH_CLOUD_ENDPOINT 未配置"
    split = urlsplit(endpoint.strip())
    if split.scheme not in ("http", "https") or not split.netloc:
        return "SEARCH_CLOUD_ENDPOINT 非法（需要 http/https 的 base URL）"
    return None


def cloud_web_availability(settings: Settings) -> tuple[bool, str | None]:
    """cloud-web 启用判定（M10-12 隐私与路由修正）：

    1. PRIVACY_SEND_CONTEXT_TO_CLOUD=false：隐私总闸关闭，云检索不出站；
    2. SEARCH_MODE != cloud（local/hybrid，检索暂无混合形态）：本地路由不出站
       ——即使 endpoint/key 配齐也禁用；
    3. endpoint 缺失或非法（非 http/https base URL）禁用；
    key 可选（无鉴权 SearXNG 不需要），不参与启用判定。
    """
    if not settings.privacy_send_context_to_cloud:
        return False, "PRIVACY_SEND_CONTEXT_TO_CLOUD=false：隐私总闸关闭，cloud-web 不出站"
    if settings.search_mode != "cloud":
        return (
            False,
            f"SEARCH_MODE={settings.search_mode}：检索路由本地语料，cloud-web 不出站",
        )
    reason = _endpoint_unavailable_reason(settings.search_cloud_endpoint)
    if reason is not None:
        return False, reason
    return True, None


def build_search_registry(chunks: ChunkRepository, settings: Settings) -> list[ProviderEntry]:
    """装配注册表：本地语料恒可用；cloud-web 按隐私/路由/endpoint 判定可用性。"""
    cloud = CloudWebProvider(
        endpoint=settings.search_cloud_endpoint,
        api_key=settings.search_cloud_api_key,
    )
    enabled, reason = cloud_web_availability(settings)
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
            enabled=enabled,
            unavailable_reason=reason,
            provider=cloud,
        ),
    ]
