#!/usr/bin/env bash
# M10-01 LLM 真实端点冒烟：验证部署的 LLM 槽位（endpoint/key/model）真实可用。
# 需要：LLM_ENDPOINT / LLM_API_KEY / LLM_MODEL 环境变量（key 不入日志）。
# 无 key 时明确失败——绝不虚构「通过」。
# M14-71 LLM_NUM_CTX（可选正整数，非敏感）：请求级上下文窗口提示——仅在
# 设置时随 chat completions payload 顶层 options.num_ctx 出示。属 provider
# 特定的请求 Hint，兼容性不保证（OpenAI 规范外字段；Ollama /v1 实证不可靠
# 消费，不据此声称生效）。M14-71 本地冒烟固定走模型别名 aios-qwen3.5-9b-4096
# （模型层 num_ctx=4096，infra/provision_ollama_model.ps1 幂等供给），本变量
# 保持未设置（payload 与既有形态一致，不带 options）。
# M14-100 超时与预算（docs/evidence/m14-100-local-llm-timeout-budget/）：
# - LLM_TIMEOUT_SECONDS（可选正数，非敏感）：gateway 请求超时覆写；未设置 =
#   gateway 默认 30s。M14-98 实证：饱和 16GB GPU 上 thinking 模型
#   max_tokens=2048 生成 >30s，两跑 llm 冒烟 httpx.ReadTimeout——本地冒烟
#   由运维按机器负载显式调大（本切片不实测默认值，不擅自改全局默认）。
# - LLM_SMOKE_MAX_TOKENS（可选正整数，非敏感）：简单探针的有界输出预算覆写，
#   默认 256。2048 已实证超时（M14-98）；过小预算有 thinking-only 空 content
#   风险（M14-71 教训，冒烟仍要求非空 content）——256 是未实测折中，真实
#   重跑切片可按实测调整。rubric judge 探针保持 gateway 生产默认 1024
#   （冒烟不改变生产 chat 语义）。
# - loopback 端点由 gateway 强制绕过环境代理（trust_env=False）——冒烟
#   shell 不再依赖调用方 NO_PROXY 手工正确性（M14-98 attempt1 的
#   httpcore http_proxy 帧教训）。
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { printf '[smoke-llm] FAIL: %s\n' "$*" >&2; exit 1; }
say() { printf '[smoke-llm] %s\n' "$*"; }

[ -n "${LLM_ENDPOINT:-}" ] || fail "LLM_ENDPOINT 未设置（真实端点冒烟需要部署 key——这不是可跳过的检查）"
[ -n "${LLM_API_KEY:-}" ] || fail "LLM_API_KEY 未设置"
[ -n "${LLM_MODEL:-}" ] || fail "LLM_MODEL 未设置"
if [ -n "${LLM_NUM_CTX:-}" ]; then
  case "${LLM_NUM_CTX}" in
    *[!0-9]*|'') fail "LLM_NUM_CTX 必须是正整数（当前值不是纯数字形态）: ${LLM_NUM_CTX}" ;;
  esac
  [ "${LLM_NUM_CTX}" -gt 0 ] || fail "LLM_NUM_CTX 必须是 >=1 的整数: ${LLM_NUM_CTX}"
  say "请求级上下文窗口提示: options.num_ctx=${LLM_NUM_CTX}"
fi
# M14-100: bash 侧只拦「非数字形态」；>0 的值语义由 gateway 构造校验兜底
#（探针 heredoc 捕获 ValueError 干净 FAIL，不裸 traceback）。
if [ -n "${LLM_TIMEOUT_SECONDS:-}" ]; then
  [[ "${LLM_TIMEOUT_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "LLM_TIMEOUT_SECONDS 必须是正数（当前值不是纯数字形态）: ${LLM_TIMEOUT_SECONDS}"
  say "gateway 请求超时覆写: ${LLM_TIMEOUT_SECONDS}s"
fi
if [ -n "${LLM_SMOKE_MAX_TOKENS:-}" ]; then
  case "${LLM_SMOKE_MAX_TOKENS}" in
    *[!0-9]*|'') fail "LLM_SMOKE_MAX_TOKENS 必须是正整数（当前值不是纯数字形态）: ${LLM_SMOKE_MAX_TOKENS}" ;;
  esac
  [ "${LLM_SMOKE_MAX_TOKENS}" -gt 0 ] || fail "LLM_SMOKE_MAX_TOKENS 必须是 >=1 的整数: ${LLM_SMOKE_MAX_TOKENS}"
  say "冒烟探针输出预算覆写: max_tokens=${LLM_SMOKE_MAX_TOKENS}"
fi

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PYTHON" ] || PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || fail "找不到项目 venv python（用 PYTHON= 指定）"

say "probing $LLM_ENDPOINT ($LLM_MODEL) ..."
LLM_ENDPOINT="$LLM_ENDPOINT" LLM_API_KEY="$LLM_API_KEY" LLM_MODEL="$LLM_MODEL" LLM_NUM_CTX="${LLM_NUM_CTX:-}" LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-}" LLM_SMOKE_MAX_TOKENS="${LLM_SMOKE_MAX_TOKENS:-}" \
"$PYTHON" - <<'PROBE_EOF'
import os
import sys

sys.path.insert(0, "services/api")

from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable

_num_ctx_raw = os.environ.get("LLM_NUM_CTX", "").strip()
_timeout_raw = os.environ.get("LLM_TIMEOUT_SECONDS", "").strip()
_budget_raw = os.environ.get("LLM_SMOKE_MAX_TOKENS", "").strip()
try:
    gateway = LlmGateway(
        endpoint=os.environ["LLM_ENDPOINT"],
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL"],
        num_ctx=int(_num_ctx_raw) if _num_ctx_raw else None,
        timeout_seconds=float(_timeout_raw) if _timeout_raw else None,
    )
except ValueError as cause:
    print(f"[smoke-llm] FAIL: LLM gateway 配置非法: {cause}", file=sys.stderr)
    sys.exit(1)
try:
    out = gateway.chat(
        (ChatMessage(role="user", content='只输出 JSON：{"ok": true}'),),
        max_tokens=int(_budget_raw) if _budget_raw else 256,
    )
except LlmUnavailable as cause:
    print(f"[smoke-llm] FAIL: LLM 端点不可用: {cause}", file=sys.stderr)
    sys.exit(1)
# M14-71: 空 content（thinking 模型把预算耗在推理、/v1 只回空正文）不算
# 通过——绝不把 0 字节响应记作 pass
if not out.strip():
    print(
        "[smoke-llm] FAIL: 模型返回空 content（thinking-only/0 字节不算通过）",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"[smoke-llm] model responded ({len(out)} chars)")
PROBE_EOF

# rubric judge 全链路探针：真实模型按结构 schema 判一道简答题
LLM_ENDPOINT="$LLM_ENDPOINT" LLM_API_KEY="$LLM_API_KEY" LLM_MODEL="$LLM_MODEL" LLM_NUM_CTX="${LLM_NUM_CTX:-}" LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-}" \
"$PYTHON" - <<'PROBE_EOF'
import os
import sys

sys.path.insert(0, "services/api")

from app.llm.gateway import LlmGateway
from app.llm.rubric_judge import LlmRubricJudge

_num_ctx_raw = os.environ.get("LLM_NUM_CTX", "").strip()
_timeout_raw = os.environ.get("LLM_TIMEOUT_SECONDS", "").strip()
try:
    judge = LlmRubricJudge(
        LlmGateway(
            endpoint=os.environ["LLM_ENDPOINT"],
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
            num_ctx=int(_num_ctx_raw) if _num_ctx_raw else None,
            timeout_seconds=float(_timeout_raw) if _timeout_raw else None,
        )
    )
except ValueError as cause:
    print(f"[smoke-llm] FAIL: LLM gateway 配置非法: {cause}", file=sys.stderr)
    sys.exit(1)
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
