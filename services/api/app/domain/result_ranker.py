"""M5-04 Result rank：搜索结果去重 + 权威度分层排序，排序理由可见。

- SOURCE_TIERS：官方(official) > OER(oer) > 平台(platform) > 社区(community)，
  数值越小越权威；
- 分层依据不虚报：结果显式带 authority 标注（official/oer/platform/community）
  时按标注分层；未标注的结果一律按 community 兜底并在 rank_reason 明示
  "未标注权威层级"——不猜测 URL 隐含权威度（ADR 44）；
- 去重键 = 规范化 URL（小写 scheme/host、去 fragment、去末尾斜杠、保留 query）；
  URL 为空/缺失的条目不参与去重（每条独立保留）；
- 同 URL 重复：保留层级更高者（rank_reason 说明保留原因）；同层保留先出现者；
- 同层内保持 provider 返回顺序（稳定排序，确定性）。
"""
from __future__ import annotations

from urllib.parse import urlsplit

SOURCE_TIERS: dict[str, int] = {"official": 1, "oer": 2, "platform": 3, "community": 4}

DEFAULT_TIER = "community"


def classify_source(result: dict) -> tuple[str, int, str]:
    """返回 (层级名, 层级序号, 排序理由)。显式标注优先，未标注 community 兜底。"""
    declared = result.get("authority") or result.get("source")
    if declared in SOURCE_TIERS:
        tier_num = SOURCE_TIERS[declared]
        return declared, tier_num, f"来源标注 {declared}({tier_num})"
    return (
        DEFAULT_TIER,
        SOURCE_TIERS[DEFAULT_TIER],
        f"未标注权威层级，按 {DEFAULT_TIER}({SOURCE_TIERS[DEFAULT_TIER]}) 兜底排序",
    )


def normalize_url(url: str) -> str:
    """规范化 URL 作为去重键：小写 scheme/host、去 fragment、去末尾斜杠、保留 query。

    端口解析失败（畸形端口）时退回原始 netloc 小写——去重键仍确定可用。
    """
    split = urlsplit(url.strip())
    scheme = split.scheme.lower()
    host = (split.hostname or "").lower()
    try:
        port = f":{split.port}" if split.port else ""
    except ValueError:
        port = ""
    path = split.path.rstrip("/") if split.path not in ("", "/") else split.path
    query = f"?{split.query}" if split.query else ""
    return f"{scheme}://{host}{port}{path}{query}"


def rank_and_dedup(results: list[dict]) -> list[dict]:
    """去重 + 分层排序（稳定）：层级升序，同层保原序；每条附 rank_reason。

    同 URL 重复时保留层级更高者，其 rank_reason 追加保留原因；被淘汰条目
    不出现在返回列表中——去重决策保留在胜出条目的理由里（可审计）。
    """
    annotated: list[dict] = []
    for order, result in enumerate(results):
        _tier_name, tier_num, reason = classify_source(result)
        annotated.append(
            {
                **result,
                "rank_reason": reason,
                "_tier": tier_num,
                "_order": order,
            }
        )

    chosen: dict[str, dict] = {}
    for item in annotated:
        url = item.get("url") or ""
        key = f"__item_{item['_order']}" if not url.strip() else normalize_url(url)
        current = chosen.get(key)
        if current is None:
            chosen[key] = item
            continue
        if item["_tier"] < current["_tier"]:
            chosen[key] = item
            item["rank_reason"] += "；URL 与同查询低层级结果重复，保留本条（层级更高）"
        else:
            current["rank_reason"] += "；URL 与同查询其他结果重复，保留本条（先出现或层级更高）"

    ranked = sorted(chosen.values(), key=lambda item: (item["_tier"], item["_order"]))
    return [{k: v for k, v in item.items() if not k.startswith("_")} for item in ranked]
