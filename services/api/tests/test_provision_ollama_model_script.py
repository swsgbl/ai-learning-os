"""M14-71 infra/provision_ollama_model.ps1 供给脚本契约测试（离线、确定性）。

契约锚点（只读脚本文本，不执行 PowerShell、不触碰本机 Ollama）：
- 固定别名 aios-qwen3.5-9b-4096 = FROM qwen3.5:9b + PARAMETER num_ctx 4096；
- 幂等：目标已存在且一致 => 跳过 create 成功；不一致 => fail-closed 不覆盖；
- fail-closed：可达性 / base 存在性（不隐式 pull）/ create 后独立 show 校验；
- qwen3:4b 明确拒绝（不可用，不修复不下载）。
"""
from __future__ import annotations

from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "infra" / "provision_ollama_model.ps1"
)


def _text() -> str:
    assert _SCRIPT.is_file(), f"缺少供给脚本: {_SCRIPT}"
    return _SCRIPT.read_text(encoding="utf-8-sig")


def test_script_pins_alias_base_and_num_ctx_contract() -> None:
    text = _text()
    assert '$Alias = "aios-qwen3.5-9b-4096"' in text
    assert '$BaseModel = "qwen3.5:9b"' in text
    assert '$NumCtx = 4096' in text
    # create 请求以 from=base + parameters.num_ctx 固化模型层上下文
    assert "from       = $BaseModel" in text
    assert "num_ctx = $NumCtx" in text


def test_script_is_idempotent_and_fails_closed_on_drift() -> None:
    text = _text()
    # 幂等：已存在且一致 => 跳过 create 直接 PASS
    assert "幂等跳过 create" in text
    assert "Test-TargetMatches" in text
    # 漂移 => 拒绝覆盖（人工 ollama rm 后重跑）
    assert "fail-closed 拒绝覆盖" in text
    assert "ollama rm" in text


def test_script_fails_closed_on_env_and_create_problems() -> None:
    text = _text()
    assert '$ErrorActionPreference = "Stop"' in text
    # Ollama 可达性检查
    assert "/api/version" in text
    # base 缺失 => 失败且不隐式拉取
    assert "不隐式拉取" in text
    assert "/api/show" in text
    # create 后独立复核（不信任 create 返回）
    assert "create 后校验失败" in text
    # 非 success 状态拒绝
    assert '"success"' in text


def test_script_rejects_qwen3_4b_base() -> None:
    text = _text()
    assert "qwen3:4b" in text
    assert "已知不可用" in text


def test_script_target_check_anchors_num_ctx_and_parent() -> None:
    text = _text()
    # 校验锚定 num_ctx 精确值与父模型回指 base（可复现性）
    assert "num_ctx\\s+$NumCtx" in text
    assert "parent_model" in text


def test_script_requires_mandatory_exact_parent_model() -> None:
    """parent_model 必须非空且恰等于 base——缺失/空白同样不匹配（fail-closed）。"""
    text = _text()
    # 拒绝可选父模型形态：`if ($parent -and ...)` 把缺失当可接受
    assert "if ($parent -and" not in text
    # 空白判定在前（缺失/空白 => return $false），再精确相等比较
    assert "[string]::IsNullOrWhiteSpace($parent)" in text
    assert "if ($parent -ne $BaseModel) { return $false }" in text
    # 空值比较用 $null 在左（robust PowerShell null comparison）
    assert "$null -ne $existing" in text
    assert "$existing -ne $null" not in text
