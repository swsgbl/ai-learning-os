#!/usr/bin/env bash
# M10-12 搜索真实端点冒烟：验证部署的 cloud-web 搜索槽位真实可用。
# 需要：SEARCH_CLOUD_ENDPOINT（SearXNG-compatible base URL，必填）；
#       SEARCH_CLOUD_API_KEY 可选（无鉴权 SearXNG 不需要；设置时经鉴权头出示）。
# 查询词默认 "AI Learning OS GitHub"，可用 SEARCH_SMOKE_QUERY 覆盖。
# 至少 1 条合法结果（http/https URL 可解析）才算 PASS——绝不虚构「通过」。
# 输出脱敏：只打印 provider/结果数等摘要，不打印 endpoint、key 或鉴权头。
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { printf '[smoke-search] FAIL: %s\n' "$*" >&2; exit 1; }
say() { printf '[smoke-search] %s\n' "$*"; }

[ -n "${SEARCH_CLOUD_ENDPOINT:-}" ] || fail "SEARCH_CLOUD_ENDPOINT 未设置（真实端点冒烟需要真实 SearXNG-compatible 端点——这不是可跳过的检查）"
# SEARCH_CLOUD_API_KEY 可选：留空即无鉴权调用；key 只经环境变量注入，不回显
QUERY="${SEARCH_SMOKE_QUERY:-AI Learning OS GitHub}"

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PYTHON" ] || PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || fail "找不到项目 venv python（用 PYTHON= 指定）"

SEARCH_CLOUD_ENDPOINT="$SEARCH_CLOUD_ENDPOINT" SEARCH_CLOUD_API_KEY="${SEARCH_CLOUD_API_KEY:-}" SEARCH_SMOKE_QUERY="$QUERY" \
"$PYTHON" - <<'PROBE_EOF'
import asyncio
import os
import sys

sys.path.insert(0, "services/api")

from app.search.providers import CloudWebProvider, ProviderUnavailable

endpoint = os.environ["SEARCH_CLOUD_ENDPOINT"].strip()
api_key = os.environ.get("SEARCH_CLOUD_API_KEY", "").strip() or None
query = os.environ["SEARCH_SMOKE_QUERY"]

provider = CloudWebProvider(endpoint=endpoint, api_key=api_key)
try:
    results = asyncio.run(provider.search(query, 5))
except ProviderUnavailable as cause:
    # cause 文案为 provider 固定脱敏消息（不含 endpoint/key）
    print(f"[smoke-search] FAIL: cloud-web 搜索不可用: {cause}", file=sys.stderr)
    sys.exit(1)
if not results:
    print("[smoke-search] FAIL: 0 条合法结果（需要至少 1 条 http/https 结果）", file=sys.stderr)
    sys.exit(1)
providers = sorted({str(r["provider"]) for r in results})
authorities = sorted({str(r.get("authority")) for r in results})
print(f"[smoke-search] provider={','.join(providers)} results={len(results)} query_len={len(query)}")
print(f"[smoke-search] result authorities: {', '.join(authorities)}")
PROBE_EOF

say "ALL SEARCH SMOKE CHECKS PASSED"
