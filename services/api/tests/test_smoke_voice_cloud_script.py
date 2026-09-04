"""M10-13 smoke_voice_cloud.sh 契约测试：脚本可用性锁定，不访问网络。

- 七个必填 env（ASR/TTS 各自 endpoint/key/model + ASR_SMOKE_AUDIO）任一缺失：
  明确 FAIL（exit 1），绝不虚构「通过」；
- ASR_SMOKE_AUDIO 指向不存在的文件：FAIL（真实短语音是硬前提）；
- 探针失败（stub python exit 1）：脚本 exit 1——fail-closed 传播；
- 探针成功（stub python exit 0）：脚本打印 ALL VOICE CLOUD SMOKE CHECKS PASSED；
- 文本契约：真实 CloudOpenAiAsrProvider/CloudOpenAiTtsProvider 导入、ASR/TTS 双
  探针、必填 env 名、RIFF/WAV 判据、非空转写与 casefold 期望文本、VOICE_SMOKE_TEXT
  覆盖、脚本源码无 Authorization/Bearer/密钥形态回显。

真实端点冒烟留给运维显式执行（bash infra/smoke_voice_cloud.sh）——本套件不发起
任何网络请求（探针 python 以 true/false 替身代替，全部 env 检查在 bash 层失败或
被替身短路），也不读取任何真实 secret（脚本感知的全部输入键先在 bash 内 unset，
再只注入本用例的占位值）。
脚本以 cwd=REPO_ROOT + 相对 POSIX 路径调用：Windows 绝对路径在 WSL bash 下
不可解析，相对路径让 WSL/Git Bash/Linux 一致工作。环境组装同样必须在
bash -c 内部完成：WSL bash 不继承 Windows 环境变量（Python 侧 env= 传参
到不了 WSL），unset/export 只有发生在 bash 会话内才对三种 bash 一致成立。
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "smoke_voice_cloud.sh"
#: 调用脚本用的相对 POSIX 路径（配合 cwd=REPO_ROOT）——Windows 绝对路径
#: （D:\… 反斜杠形态）在 WSL bash 下不可解析（需 /mnt/<drive>/…），
#: 相对 POSIX 路径对 WSL/Git Bash/Linux 三种 bash 一致可用；脚本自身会
#: cd "$(dirname "$0")/.." 回仓库根，相对调用不影响其内部定位。
SCRIPT_RELATIVE = "infra/smoke_voice_cloud.sh"

BASH = shutil.which("bash")

#: 脚本感知的全部输入环境键——调用前在 bash 内 unset，保证宿主残留
#: （含经 WSLENV 之类透传的）不影响各用例的起点环境，也不读取真实 secret。
SMOKE_ENV_KEYS = (
    "ASR_CLOUD_ENDPOINT",
    "ASR_CLOUD_API_KEY",
    "ASR_CLOUD_MODEL",
    "TTS_CLOUD_ENDPOINT",
    "TTS_CLOUD_API_KEY",
    "TTS_CLOUD_MODEL",
    "ASR_SMOKE_AUDIO",
    "VOICE_SMOKE_TEXT",
    "ASR_SMOKE_EXPECTED_TEXT",
    "PYTHON",
)

#: 七个必填键（脚本对每个都显式 FAIL）；VOICE_SMOKE_TEXT / ASR_SMOKE_EXPECTED_TEXT
#: 是窄口径可选覆盖，PYTHON 是测试注入替身用的执行器覆盖。
REQUIRED_ENV_KEYS = SMOKE_ENV_KEYS[:7]

#: 环境变量名白名单形态（键来自测试自身，注入 bash -c 前校验防拼接）
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 不可用（脚本契约测试需要 bash）")

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")

#: 全必填齐备时的占位 env（不触网：PYTHON 用 true/false 替身，端点/key 占位值
#: 从不被任何真实请求消费；ASR_SMOKE_AUDIO 只需过 bash -f 存在性检查，
#: 替身 python 不读取该文件）。
STUB_ENV = {
    "ASR_CLOUD_ENDPOINT": "https://stub.invalid/v1",
    "ASR_CLOUD_API_KEY": "stub-asr-key",
    "ASR_CLOUD_MODEL": "stub-asr-model",
    "TTS_CLOUD_ENDPOINT": "https://stub.invalid/v1",
    "TTS_CLOUD_API_KEY": "stub-tts-key",
    "TTS_CLOUD_MODEL": "stub-tts-model",
    "ASR_SMOKE_AUDIO": "README.md",
}


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
    """以 bash -c 在 bash 会话内组装环境后 exec 脚本。

    WSL bash 不继承 Windows 环境变量（无 WSLENV 透传时 ASR_CLOUD_ENDPOINT
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
    return subprocess.run(
        [BASH, "-c", command],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
        check=False,
    )


def test_script_fails_when_each_required_env_missing() -> None:
    """七个必填 env 任一缺失：exit 1 + FAIL 消息点名缺失键（不虚构通过）。"""
    for missing in REQUIRED_ENV_KEYS:
        overrides = {key: value for key, value in STUB_ENV.items() if key != missing}
        result = _run_script(overrides)
        assert result.returncode == 1, f"{missing}: {result.stderr}"
        assert "FAIL" in result.stderr, missing
        assert missing in result.stderr
        assert "PASSED" not in result.stdout + result.stderr


def test_script_fails_when_audio_file_missing() -> None:
    """ASR_SMOKE_AUDIO 指向不存在的文件：FAIL（真实短语音是硬前提）。"""
    result = _run_script({**STUB_ENV, "ASR_SMOKE_AUDIO": "no-such-audio.wav"})
    assert result.returncode == 1
    assert "FAIL" in result.stderr
    assert "ASR_SMOKE_AUDIO" in result.stderr
    assert "PASSED" not in result.stdout + result.stderr


def test_script_propagates_probe_failure() -> None:
    """探针失败（stub python exit 1）：脚本 exit 1，不打印 PASSED。"""
    result = _run_script({**STUB_ENV, "PYTHON": _bash_tool_path("false")})
    assert result.returncode == 1
    assert "PASSED" not in result.stdout + result.stderr


def test_script_passes_when_probe_succeeds() -> None:
    """探针成功（stub python exit 0）：bash 流程走通并打印 PASSED。"""
    result = _run_script({**STUB_ENV, "PYTHON": _bash_tool_path("true")})
    assert result.returncode == 0, result.stderr
    assert "ALL VOICE CLOUD SMOKE CHECKS PASSED" in result.stdout


def test_script_text_contract() -> None:
    """脚本源码契约：必填 env、真实 provider 双探针、RIFF 判据、脱敏输出。"""
    text = SCRIPT.read_text(encoding="utf-8")
    # 七个必填 env 名全部显式声明（缺一 FAIL 语义由行为测试锁定）
    for key in REQUIRED_ENV_KEYS:
        assert key in text, key
    # 可选覆盖窄口径：默认合成文本 + 期望文本 casefold 判据
    assert "VOICE_SMOKE_TEXT" in text
    assert "AI Learning OS cloud voice smoke" in text
    assert "ASR_SMOKE_EXPECTED_TEXT" in text
    assert "casefold" in text
    # PASS 门槛：非空转写 + RIFF/WAV 头（response_format=wav）
    assert "非空" in text
    assert "RIFF" in text
    # 探针使用真实 provider（不是 mock/echo），ASR/TTS 双探针
    assert "from app.voice.providers import CloudOpenAiAsrProvider, CloudOpenAiTtsProvider" in text
    assert "transcribe" in text
    assert "synthesize" in text
    # 脱敏：不回显鉴权头与密钥形态（key 只经环境变量注入）
    assert "Authorization" not in text
    assert "Bearer" not in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
