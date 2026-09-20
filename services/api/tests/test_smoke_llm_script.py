"""M14-71 infra/smoke_llm.sh 脚本契约测试（离线、确定性：只读脚本文本）。

契约锚点：
- LLM_NUM_CTX 是可选请求级窗口提示：非纯数字/非正整数形态必须显式失败；
- 未设置时默认空（probe 以 None 构造 gateway——payload 不带 options）；
- 两个 probe（简单补全 + rubric judge）都只在设置时透传 num_ctx；
- 注释措辞必须声明兼容性不保证（Ollama /v1 实证不可靠消费 options.num_ctx），
  且 M14-71 本地路径固定走模型别名 aios-qwen3.5-9b-4096（LLM_NUM_CTX 不设）。
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
    """简单探针 2048（thinking 预算独立于 content）；rubric judge 走 gateway 默认 1024。"""
    text = _text()
    assert "max_tokens=2048" in text
    assert "max_tokens=32" not in text
