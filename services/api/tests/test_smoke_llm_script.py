"""M14-71 / M14-100 infra/smoke_llm.sh 脚本契约测试（离线、确定性：只读脚本文本）。

契约锚点：
- LLM_NUM_CTX 是可选请求级窗口提示：非纯数字/非正整数形态必须显式失败；
- 未设置时默认空（probe 以 None 构造 gateway——payload 不带 options）；
- 两个 probe（简单补全 + rubric judge）都只在设置时透传 num_ctx；
- 注释措辞必须声明兼容性不保证（Ollama /v1 实证不可靠消费 options.num_ctx），
  且 M14-71 本地路径固定走模型别名 aios-qwen3.5-9b-4096（LLM_NUM_CTX 不设）。
- M14-100 LLM_TIMEOUT_SECONDS / LLM_SMOKE_MAX_TOKENS：形态校验 fail-closed、
  两 probe 透传 timeout、简单探针有界预算默认 256（M14-98 实证 2048 超 30s
  超时；rubric judge 保持 gateway 生产默认 1024——冒烟不改变生产语义）。
"""
from __future__ import annotations

from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "smoke_llm.sh"


def _text() -> str:
    assert _SCRIPT.is_file(), f"缺少冒烟脚本: {_SCRIPT}"
    return _SCRIPT.read_text(encoding="utf-8")


def test_script_validates_llm_num_ctx_when_set() -> None:
    text = _text()
    assert "LLM_NUM_CTX" in text
    # 非纯数字与 <=0 形态都必须 fail（fail-closed，不静默忽略）
    assert "*[!0-9]*" in text
    assert '"${LLM_NUM_CTX}" -gt 0' in text


def test_script_default_keeps_num_ctx_unset_for_probes() -> None:
    text = _text()
    # 两处 probe 均以 `${LLM_NUM_CTX:-}` 空默认透传（未设置 => None）
    assert text.count('LLM_NUM_CTX="${LLM_NUM_CTX:-}"') == 2
    assert 'int(_num_ctx_raw) if _num_ctx_raw else None' in text


def test_script_doc_states_compat_not_guaranteed_and_fixed_alias_path() -> None:
    text = _text()
    # 措辞不得声称 Ollama /v1 可靠消费 options.num_ctx
    assert "兼容性不保证" in text
    assert "不可靠" in text
    # M14-71 本地路径固定走模型层别名，LLM_NUM_CTX 保持未设置
    assert "aios-qwen3.5-9b-4096" in text
    assert "provision_ollama_model.ps1" in text


def test_script_rejects_empty_thinking_only_response() -> None:
    """M14-71: 空 content（thinking-only/0 字节）不算通过。"""
    text = _text()
    assert "不算通过" in text
    assert "out.strip()" in text


def test_script_probe_token_budgets() -> None:
    """M14-100: 简单探针有界预算默认 256（LLM_SMOKE_MAX_TOKENS 可覆写）——
    2048 已实证在饱和 GPU 上生成 >30s（M14-98 两跑超时）；32 级小预算有
    thinking-only 空 content 风险（M14-71 教训）。rubric judge 探针不显式
    传 max_tokens（走 gateway 生产默认 1024——冒烟不改变生产语义）。"""
    text = _text()
    assert "max_tokens=int(_budget_raw) if _budget_raw else 256" in text
    # 探针调用不得再固定 2048（带逗号的调用形态；头部注释引用 M14-98 实证
    # 事实的措辞不在此限）
    assert "max_tokens=2048," not in text
    assert "max_tokens=32" not in text
    assert "LLM_SMOKE_MAX_TOKENS" in text


def test_script_validates_timeout_and_budget_env_shapes() -> None:
    """M14-100: LLM_TIMEOUT_SECONDS（正数形态）与 LLM_SMOKE_MAX_TOKENS（正
    整数形态）的 bash 侧形态校验必须 fail-closed；值语义（>0）由 gateway
    构造校验兜底（heredoc 捕获 ValueError 干净 FAIL）。"""
    text = _text()
    assert "^[0-9]+([.][0-9]+)?$" in text
    assert '"${LLM_SMOKE_MAX_TOKENS}" -gt 0' in text
    # 构造失败必须干净 FAIL（不裸 traceback）
    assert 'except ValueError as cause:' in text
    assert text.count("LLM gateway 配置非法") == 2


def test_script_probes_pass_timeout_through() -> None:
    """M14-100: 两个 probe 都以 `${LLM_TIMEOUT_SECONDS:-}` 空默认透传 timeout
    （未设置 => None = gateway 既有默认 30s，cloud 零漂移）。"""
    text = _text()
    assert text.count('LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-}"') == 2
    assert (
        text.count("timeout_seconds=float(_timeout_raw) if _timeout_raw else None") == 2
    )
