"""M10-12 smoke_search.sh 契约测试：脚本可用性锁定，不访问网络。

- SEARCH_CLOUD_ENDPOINT 未设置：明确 FAIL（exit 1），绝不虚构「通过」；
- 探针失败（stub python exit 1）：脚本 exit 1——fail-closed 传播；
- 探针成功（stub python exit 0）：脚本打印 ALL SEARCH SMOKE CHECKS PASSED；
- 文本契约：默认查询词、SEARCH_SMOKE_QUERY 覆盖、SEARCH_CLOUD_API_KEY 可选、
  probe 使用真实 CloudWebProvider、脚本源码无 Authorization/密钥形态回显。

真实端点冒烟留给运维显式执行（bash infra/smoke_search.sh）——本套件不发起
任何网络请求（探针 python 以 true/false 替身代替，endpoint 检查在 env 层失败）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "smoke_search.sh"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 不可用（脚本契约测试需要 bash）")

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")


def _bash_tool_path(tool: str) -> str:
    """取 bash 视角的工具绝对路径（command -v 对 shell 内建只返回名字，
    脚本的 [ -x ] 又不做 PATH 查找——type -P 只搜 PATH 恒返回绝对路径）。"""
    result = subprocess.run(
        [BASH, "-c", f"type -P {tool}"], capture_output=True, text=True, timeout=30, check=False
    )
    path = result.stdout.strip()
    assert path, f"bash 找不到 {tool}"
    return path


def _run_script(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in ("SEARCH_CLOUD_ENDPOINT", "SEARCH_CLOUD_API_KEY", "SEARCH_SMOKE_QUERY", "PYTHON"):
        env.pop(key, None)
    env.update(env_overrides)
    return subprocess.run(
        [BASH, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
        check=False,
    )


def test_script_fails_when_endpoint_missing() -> None:
    """SEARCH_CLOUD_ENDPOINT 未设置：exit 1 + FAIL 消息（不虚构通过）。"""
    result = _run_script({})
    assert result.returncode == 1
    assert "FAIL" in result.stderr
    assert "SEARCH_CLOUD_ENDPOINT" in result.stderr
    assert "PASSED" not in result.stdout + result.stderr


def test_script_propagates_probe_failure() -> None:
    """探针失败（stub python exit 1）：脚本 exit 1，不打印 PASSED。"""
    result = _run_script(
        {"SEARCH_CLOUD_ENDPOINT": "https://stub.invalid", "PYTHON": _bash_tool_path("false")}
    )
    assert result.returncode == 1
    assert "PASSED" not in result.stdout + result.stderr


def test_script_passes_when_probe_succeeds() -> None:
    """探针成功（stub python exit 0）：bash 流程走通并打印 PASSED。"""
    result = _run_script(
        {"SEARCH_CLOUD_ENDPOINT": "https://stub.invalid", "PYTHON": _bash_tool_path("true")}
    )
    assert result.returncode == 0, result.stderr
    assert "ALL SEARCH SMOKE CHECKS PASSED" in result.stdout


def test_script_text_contract() -> None:
    """脚本源码契约：必填 endpoint、可选 key、默认查询词、真实 provider、无敏感回显。"""
    text = SCRIPT.read_text(encoding="utf-8")
    # endpoint 必填（未设置明确失败）
    assert "SEARCH_CLOUD_ENDPOINT" in text
    # key 可选语义显式声明
    assert "SEARCH_CLOUD_API_KEY" in text
    assert "可选" in text
    # 查询词默认值与覆盖入口
    assert "SEARCH_SMOKE_QUERY" in text
    assert "AI Learning OS GitHub" in text
    # PASS 门槛：至少 1 条合法结果
    assert "至少 1 条" in text
    # probe 使用真实 provider（不是 mock/echo）
    assert "from app.search.providers import CloudWebProvider" in text
    # 脱敏：不回显鉴权头与密钥形态（key 只经环境变量注入）
    assert "Authorization" not in text
    assert "Bearer" not in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
