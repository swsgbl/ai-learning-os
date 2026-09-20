"""M10-12 smoke_search.sh 契约测试：脚本可用性锁定，不访问网络。

- SEARCH_CLOUD_ENDPOINT 未设置：明确 FAIL（exit 1），绝不虚构「通过」；
- 探针失败（stub python exit 1）：脚本 exit 1——fail-closed 传播；
- 探针成功（stub python exit 0）：脚本打印 ALL SEARCH SMOKE CHECKS PASSED；
- M14-66 回环代理绕过：env-dump 桩捕获脚本导出的 NO_PROXY/no_proxy——
  空起点补齐回环条目、既有条目保留、幂等不重复、双变量（大小写）同步；
- 文本契约：默认查询词、SEARCH_SMOKE_QUERY 覆盖、SEARCH_CLOUD_API_KEY 可选、
  probe 使用真实 CloudWebProvider、脚本源码无 Authorization/密钥形态回显、
  代理绕过只动 NO_PROXY/no_proxy（不触碰 HTTP_PROXY 代理变量本体）。

真实端点冒烟留给运维显式执行（bash infra/smoke_search.sh）——本套件不发起
任何网络请求（探针 python 以 true/false 替身代替，endpoint 检查在 env 层失败）。
脚本以 cwd=REPO_ROOT + 相对 POSIX 路径调用：Windows 绝对路径在 WSL bash 下
不可解析，相对路径让 WSL/Git Bash/Linux 一致工作。环境组装同样必须在
bash -c 内部完成：WSL bash 不继承 Windows 环境变量（Python 侧 env= 传参
到不了 WSL），unset/export 只有发生在 bash 会话内才对三种 bash 一致成立。
"""
from __future__ import annotations

import base64
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._subprocess_utf8 import run_bash

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "smoke_search.sh"
#: 调用脚本用的相对 POSIX 路径（配合 cwd=REPO_ROOT）——Windows 绝对路径
#: （D:\… 反斜杠形态）在 WSL bash 下不可解析（需 /mnt/<drive>/…），
#: 相对 POSIX 路径对 WSL/Git Bash/Linux 三种 bash 一致可用；脚本自身会
#: cd "$(dirname "$0")/.." 回仓库根，相对调用不影响其内部定位。
SCRIPT_RELATIVE = "infra/smoke_search.sh"

BASH = shutil.which("bash")

#: 脚本感知的全部输入环境键——调用前在 bash 内 unset，保证宿主残留
#: （含经 WSLENV 之类透传的）不影响各用例的起点环境。M14-66: 代理绕过
#: 修正读取并改写 NO_PROXY/no_proxy，这两个键也纳入确定性基线（env-dump
#: 桩用例依赖空起点）。
SMOKE_ENV_KEYS = (
    "SEARCH_CLOUD_ENDPOINT",
    "SEARCH_CLOUD_API_KEY",
    "SEARCH_SMOKE_QUERY",
    "PYTHON",
    "NO_PROXY",
    "no_proxy",
)

#: 环境变量名白名单形态（键来自测试自身，注入 bash -c 前校验防拼接）
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 不可用（脚本契约测试需要 bash）")

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")


def _bash_tool_path(tool: str) -> str:
    """取 bash 视角的工具绝对路径（command -v 对 shell 内建只返回名字，
    脚本的 [ -x ] 又不做 PATH 查找——type -P 只搜 PATH 恒返回绝对路径）。"""
    # 统一 UTF-8 文本模式：脚本/桩输出含中文，locale 编码（cp936）下 reader
    # 线程会抛 UnicodeDecodeError 把输出炸成 None（见 _subprocess_utf8 模块说明）。
    result = run_bash([BASH, "-c", f"type -P {tool}"], timeout=30)
    path = result.stdout.strip()
    assert path, f"bash 找不到 {tool}"
    return path


def _run_script(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """以 bash -c 在 bash 会话内组装环境后 exec 脚本。

    WSL bash 不继承 Windows 环境变量（无 WSLENV 透传时 SEARCH_CLOUD_ENDPOINT
    等在 WSL 内恒为空）——Python 侧 env= 只作用于 Windows 进程，改 env 字典
    到不了 WSL 里的脚本。故改为一条安全 shell 命令：先 unset 全部输入键清掉
    宿主/透传残留，再以 shlex.quote 注入本用例覆盖值（键先过白名单校验，
    防拼接），最后 exec 相对 POSIX 路径——subprocess 的 cwd=REPO_ROOT 会被
    WSL 自动转换为 /mnt/<drive>/…，环境组装与脚本执行全在 bash 内完成，
    对 WSL/Git Bash/Linux 一致成立。"""
    for key in env_overrides:
        assert _ENV_KEY_RE.fullmatch(key), f"非法环境变量名: {key}"
    command = "; ".join(
        [f"unset {' '.join(SMOKE_ENV_KEYS)}"]
        + [f"export {key}={shlex.quote(value)}" for key, value in env_overrides.items()]
        + [f"exec {shlex.quote(SCRIPT_RELATIVE)}"]
    )
    return run_bash([BASH, "-c", command], timeout=60, cwd=REPO_ROOT)


def _run_script_with_env_stub(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """以 env-dump 桩 python 运行脚本：桩打印 NO_PROXY/no_proxy 导出值后 exit 0。

    代理绕过修正发生在探针 exec 之前——桩在探针位置读到的是修正后的导出值，
    据此锁定脚本对两个变量的实际改写（空起点补齐/保留追加/幂等）。桩文件用
    bash 的 mktemp 创建，但路径由 Python 捕获后以字面量回传后续 bash 调用；
    桩内容经 base64 写入，避免外层 bash -c 在 WSL 互操作层丢失参数展开字符，
    同时不引入网络访问。
    """
    for key in env_overrides:
        assert _ENV_KEY_RE.fullmatch(key), f"非法环境变量名: {key}"
    created = run_bash([BASH, "-c", "mktemp"], timeout=30)
    assert created.returncode == 0, created.stderr
    stub = created.stdout.strip()
    assert stub and "\n" not in stub, created.stdout

    stub_source = (
        '#!/usr/bin/env bash\n'
        'printf "NO_PROXY=[%s] no_proxy=[%s]\\n" "$NO_PROXY" "$no_proxy"\n'
    )
    stub_payload = base64.b64encode(stub_source.encode("utf-8")).decode("ascii")
    stub_setup = (
        f"printf %s {stub_payload} | base64 -d"
        f" > {shlex.quote(stub)} && chmod +x {shlex.quote(stub)}"
    )
    prepared = run_bash([BASH, "-c", stub_setup], timeout=30)
    assert prepared.returncode == 0, prepared.stderr

    try:
        command = "; ".join(
            [f"unset {' '.join(SMOKE_ENV_KEYS)}"]
            + [f"export {key}={shlex.quote(value)}" for key, value in env_overrides.items()]
            + [f"export PYTHON={shlex.quote(stub)}", shlex.quote(SCRIPT_RELATIVE)]
        )
        return run_bash([BASH, "-c", command], timeout=60, cwd=REPO_ROOT)
    finally:
        cleaned = run_bash([BASH, "-c", f"rm -f {shlex.quote(stub)}"], timeout=30)
        assert cleaned.returncode == 0, cleaned.stderr


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


# ------------------------------------------------- M14-66 回环代理绕过（env-dump 桩）

def test_loopback_no_proxy_injected_from_empty() -> None:
    """空起点：脚本为 NO_PROXY/no_proxy 双变量补齐回环条目（WSL 继承代理下
    发往 127.0.0.1 的请求此前会被交给系统代理而必然失败）。"""
    result = _run_script_with_env_stub({"SEARCH_CLOUD_ENDPOINT": "https://stub.invalid"})
    assert result.returncode == 0, result.stderr
    assert "NO_PROXY=[127.0.0.1,localhost] no_proxy=[127.0.0.1,localhost]" in result.stdout


def test_loopback_no_proxy_preserves_existing_entries() -> None:
    """既有条目保留：预置 corp 代理绕过条目时仅追加回环（不删改——非回环
    endpoint 的代理行为不变）。"""
    result = _run_script_with_env_stub({
        "SEARCH_CLOUD_ENDPOINT": "https://stub.invalid",
        "NO_PROXY": "corp.example,10.0.0.0/8",
    })
    assert result.returncode == 0, result.stderr
    assert "NO_PROXY=[corp.example,10.0.0.0/8,127.0.0.1,localhost]" in result.stdout
    assert "no_proxy=[127.0.0.1,localhost]" in result.stdout


def test_loopback_no_proxy_idempotent_and_cross_case() -> None:
    """幂等 + 双变量同步：仅小写预置 localhost 时，小写不重复（幂等追加
    127.0.0.1），大写从零补齐（不同客户端读取大小写不一，缺一即失效）。"""
    result = _run_script_with_env_stub({
        "SEARCH_CLOUD_ENDPOINT": "https://stub.invalid",
        "no_proxy": "localhost",
    })
    assert result.returncode == 0, result.stderr
    assert "NO_PROXY=[127.0.0.1,localhost]" in result.stdout
    assert "no_proxy=[localhost,127.0.0.1]" in result.stdout


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
    # M14-66 回环代理绕过：双变量（大小写）与回环条目显式出现在源码中
    assert "NO_PROXY" in text
    assert "no_proxy" in text
    assert "127.0.0.1" in text
    assert "localhost" in text
    # 代理变量本体不被触碰（只追加绕过条目，不改写代理行为）
    assert "HTTP_PROXY" not in text
    assert "http_proxy" not in text
