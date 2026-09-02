#!/usr/bin/env bash
# M10-01 LLM 真实端点冒烟：验证部署的 LLM 槽位（endpoint/key/model）真实可用。
# 需要：LLM_ENDPOINT / LLM_API_KEY / LLM_MODEL 环境变量（key 不入日志）。
# 无 key 时明确失败——绝不虚构「通过」。
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { printf '[smoke-llm] FAIL: %s\n' "$*" >&2; exit 1; }
say() { printf '[smoke-llm] %s\n' "$*"; }

[ -n "${LLM_ENDPOINT:-}" ] || fail "LLM_ENDPOINT 未设置（真实端点冒烟需要部署 key——这不是可跳过的检查）"
[ -n "${LLM_API_KEY:-}" ] || fail "LLM_API_KEY 未设置"
[ -n "${LLM_MODEL:-}" ] || fail "LLM_MODEL 未设置"

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PYTHON" ] || PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || fail "找不到项目 venv python（用 PYTHON= 指定）"

say "probing $LLM_ENDPOINT ($LLM_MODEL) ..."
LLM_ENDPOINT="$LLM_ENDPOINT" LLM_API_KEY="$LLM_API_KEY" LLM_MODEL="$LLM_MODEL" \
"$PYTHON" - <<'PROBE_EOF'
import os
import sys

sys.path.insert(0, "services/api")

from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable

gateway = LlmGateway(
    endpoint=os.environ["LLM_ENDPOINT"],
    api_key=os.environ["LLM_API_KEY"],
    model=os.environ["LLM_MODEL"],
)
try:
    out = gateway.chat(
        (ChatMessage(role="user", content='只输出 JSON：{"ok": true}'),),
        max_tokens=32,
    )
except LlmUnavailable as cause:
    print(f"[smoke-llm] FAIL: LLM 端点不可用: {cause}", file=sys.stderr)
    sys.exit(1)
print(f"[smoke-llm] model responded ({len(out)} chars)")
PROBE_EOF

# rubric judge 全链路探针：真实模型按结构 schema 判一道简答题
LLM_ENDPOINT="$LLM_ENDPOINT" LLM_API_KEY="$LLM_API_KEY" LLM_MODEL="$LLM_MODEL" \
"$PYTHON" - <<'PROBE_EOF'
import os
import sys

sys.path.insert(0, "services/api")

from app.llm.gateway import LlmGateway
from app.llm.rubric_judge import LlmRubricJudge

judge = LlmRubricJudge(
    LlmGateway(
        endpoint=os.environ["LLM_ENDPOINT"],
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL"],
    )
)
judgement = judge.judge(
    "简述快速排序的核心思想",
    ("提到分治", "提到递归"),
    "快速排序用分治思想，选基准把数组分成两半，再递归处理两侧。",
)
if judgement is None:
    print("[smoke-llm] FAIL: judge 返回 None（进复核）——模型输出不符合结构 schema", file=sys.stderr)
    sys.exit(1)
assert judgement.judge_model == os.environ["LLM_MODEL"]
print(
    f"[smoke-llm] rubric judge ok: achieved={[c.achieved for c in judgement.criteria]} "
    f"confidence={judgement.confidence}"
)
PROBE_EOF

say "ALL LLM SMOKE CHECKS PASSED"
