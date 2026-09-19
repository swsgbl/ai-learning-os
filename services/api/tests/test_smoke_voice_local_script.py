"""M14-70 smoke_voice_local.sh 契约测试：wrapper 文本/路径锁定，不访问网络。

- 行为契约（PYTHON=true/false 替身）：wrapper exec 探针 python，退出码原样
  透传（替身 exit 0 → 0；替身 exit 1 → 1），wrapper 自身不判分不打印 PASS；
- 文本契约：set -euo pipefail、cd 仓库根、.venv/Scripts/python.exe 先于
  .venv/bin/python 的选择顺序、exec tools/voice/smoke_local_voice.py、venv
  双缺失明确 FAIL、无 export（env 零修改）、无探针复制（不 import
  app.voice.providers、不自带 transcribe/synthesize 判分）、无 secret 形态。

真实本地语音冒烟留给运维显式执行（bash infra/smoke_voice_local.sh）——本
套件不发起任何网络请求（替身 python 不触网），也不依赖 .venv 存在（行为
用例全部经 PYTHON= 注入替身，绕开 venv 探测分支）。
脚本以 cwd=REPO_ROOT + 相对 POSIX 路径调用（Windows 绝对路径在 WSL bash 下
不可解析）；环境组装在 bash -c 内部完成（WSL bash 不继承 Windows 环境变量）。
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._subprocess_utf8 import run_bash

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "smoke_voice_local.sh"
#: 调用脚本用的相对 POSIX 路径（配合 cwd=REPO_ROOT）——Windows 绝对路径
#: （D:\… 反斜杠形态）在 WSL bash 下不可解析（需 /mnt/<drive>/…），相对
#: POSIX 路径对 WSL/Git Bash/Linux 三种 bash 一致可用；脚本自身会
#: cd "$(dirname "$0")/.." 回仓库根，相对调用不影响其内部定位。
SCRIPT_RELATIVE = "infra/smoke_voice_local.sh"

BASH = shutil.which("bash")

#: wrapper 感知的全部输入环境键——只有 PYTHON（可选执行器覆盖，测试注入
#: 替身用）；调用前在 bash 内 unset，清掉宿主/WSLENV 透传残留。
WRAPPER_ENV_KEYS = ("PYTHON",)

#: 环境变量名白名单形态（键来自测试自身，注入 bash -c 前校验防拼接）
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 不可用（脚本契约测试需要 bash）")

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")


def _bash_tool_path(tool: str) -> str:
    """取 bash 视角的工具绝对路径（type -P 只搜 PATH 恒返回绝对路径，
    能过脚本内 [ -x ] 检查；command -v 对 shell 内建只返回名字）。"""
    result = run_bash([BASH, "-c", f"type -P {tool}"], timeout=30)
    path = result.stdout.strip()
    assert path, f"bash 找不到 {tool}"
    return path


def _run_wrapper(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """以 bash -c 在 bash 会话内组装环境后 exec wrapper。

    WSL bash 不继承 Windows 环境变量——Python 侧 env= 只作用于 Windows 进程，
    到不了 WSL 里的脚本。故改为一条安全 shell 命令：先 unset 全部输入键，
    再以 shlex.quote 注入覆盖值（键先过白名单校验，防拼接），最后 exec
    相对 POSIX 路径；subprocess 的 cwd=REPO_ROOT 会被 WSL 自动转换为
    /mnt/<drive>/…，对 WSL/Git Bash/Linux 一致成立。"""
    for key in env_overrides:
        assert _ENV_KEY_RE.fullmatch(key), f"非法环境变量名: {key}"
    command = "; ".join(
        [f"unset {' '.join(WRAPPER_ENV_KEYS)}"]
        + [f"export {key}={shlex.quote(value)}" for key, value in env_overrides.items()]
        + [f"exec {shlex.quote(SCRIPT_RELATIVE)}"]
    )
    return run_bash([BASH, "-c", command], timeout=60, cwd=REPO_ROOT)


def test_wrapper_exits_zero_when_probe_succeeds() -> None:
    """探针成功（stub python exit 0）：wrapper exit 0，不自带判分输出。"""
    result = _run_wrapper({"PYTHON": _bash_tool_path("true")})
    assert result.returncode == 0, result.stderr
    assert "FAIL" not in result.stderr
    # wrapper 不复制探针判分：exec 后进程即替身，无自己的 PASS 文案
    assert "PASSED" not in result.stdout


def test_wrapper_propagates_probe_failure() -> None:
    """探针失败（stub python exit 1）：退出码原样透传（exec 替换进程）。"""
    result = _run_wrapper({"PYTHON": _bash_tool_path("false")})
    assert result.returncode == 1
    assert "PASSED" not in result.stdout + result.stderr


def test_wrapper_text_contract() -> None:
    """wrapper 源码契约：严管模式、仓库根定位、venv 选择顺序、exec 探针。"""
    text = SCRIPT.read_text(encoding="utf-8")
    # 严管模式 + 回仓库根（脚本可从任意 cwd 调用）
    assert "set -euo pipefail" in text
    assert 'cd "$(dirname "$0")/.."' in text
    # venv 选择顺序：Windows Scripts 解释器优先于 POSIX bin 解释器
    assert ".venv/Scripts/python.exe" in text
    assert ".venv/bin/python" in text
    assert text.index(".venv/Scripts/python.exe") < text.index(".venv/bin/python")
    # 双缺失明确 FAIL（不静默换替身、不虚报成功）
    assert "找不到项目 venv python" in text
    # 唯一出口 = exec 探针（零参数调用 M14-01 探针，退出码透传）
    assert 'exec "$PYTHON" tools/voice/smoke_local_voice.py' in text
    # env 零修改：不 export 任何变量（可选 PYTHON 覆盖只是读取）
    assert "export" not in text
    # 零探针复制：判分/网络细节留在 tools/voice/smoke_local_voice.py
    assert "from app.voice.providers" not in text
    assert "transcribe" not in text
    assert "synthesize" not in text
    # 脱敏：无鉴权头与密钥形态（wrapper 无 secret 感知）
    assert "Authorization" not in text
    assert "Bearer" not in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text, pattern
