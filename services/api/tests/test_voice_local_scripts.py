r"""M14-01 tools/voice 脚本契约测试：语法/结构锁定 + bridge 行为（不触网、不装 SDK）。

- bash -n（host-aware）：Windows 上选中的 bash 可能是 WSL 启动器
  （C:\Windows\System32\bash.EXE——PowerShell 默认 PATH 下 shutil.which("bash")
  常解析到它），其读不了 Windows 盘符路径——复审修正：探测到 WSL bash 时经
  wsl.exe wslpath -u 把脚本路径转成 /mnt/… 再检查（wsl.exe 不可用则确定性
  手工转换）；Git Bash / MSYS / Cygwin / POSIX 保持 Windows 路径/原样直用；
- py_compile：bridge 与 smoke 脚本可编译；
- bridge 单元：_samples_to_wav_bytes 纯标准库序列化（RIFF/WAVE、采样率回读、
  越界钳制、空样本），create_app 的 OpenAI 兼容面（/health 加载中 503、
  /v1/audio/speech 的 404/400/503/401 与固定脱敏 JSON——不泄漏路径/key，
  全部在引擎未加载的确定性状态下断言，TestClient 不进入 lifespan）；
- 文本契约：脚本源码含关键锚点（固定 commit、cu128、端口、loopback、
  gitignored artifacts、官方模型 ID），且无密钥形态回显。

真实引擎冒烟（tools/voice/smoke_local_voice.py 对已部署服务）是运维显式动作，
本套件不发起任何网络请求。
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_VOICE = REPO_ROOT / "tools" / "voice"
BOOTSTRAP_FUNASR = TOOLS_VOICE / "bootstrap_funasr_wsl.sh"
BOOTSTRAP_COSYVOICE = TOOLS_VOICE / "bootstrap_cosyvoice_wsl.sh"
BRIDGE = TOOLS_VOICE / "cosyvoice_openai_bridge.py"
SMOKE = TOOLS_VOICE / "smoke_local_voice.py"
REACHABILITY = TOOLS_VOICE / "compose_voice_reachability.sh"
RUNTIME_REQUIREMENTS = TOOLS_VOICE / "cosyvoice-runtime-requirements.txt"
EVIDENCE_PS1 = TOOLS_VOICE / "run_api_tests.ps1"

BASH = shutil.which("bash")

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")


# ---------- host-aware bash 语法检查（复审修正：Windows/WSL 路径转换） ----------


def _is_wsl_kernel_release(uname_release: str) -> bool:
    """WSL 内核 uname -r 含 "microsoft"（WSL1 …-Microsoft / WSL2 …-microsoft-standard-）；
    Git Bash / MSYS / Cygwin / Linux 均不含（实测 Git Bash 3.6.10-710e5275.x86_64、
    WSL2 6.18.33.2-microsoft-standard-WSL2）。"""
    return "microsoft" in (uname_release or "").casefold()


@lru_cache(maxsize=8)
def _bash_flavor_is_wsl(bash_path: str, probe=subprocess.run) -> bool:
    """探测该 bash 是否 WSL 启动器（经 uname -r 判别；探测失败按非 WSL 保守处理）。"""
    try:
        result = probe(
            [bash_path, "-c", "uname -r"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, ValueError):
        return False
    return _is_wsl_kernel_release(result.stdout or "")


def _manual_windows_to_wsl_path(windows_path: str) -> str:
    """确定性手工转换（wsl.exe 不可用时的回退）：盘符→/mnt/<小写>、反斜杠→正斜杠。"""
    drive, _, rest = windows_path.partition(":")
    return f"/mnt/{drive.lower()}{rest.replace(chr(92), '/')}"


def _windows_to_wsl_path(windows_path: str, *, runner=subprocess.run) -> str:
    """Windows 路径 → WSL 路径。优先 wsl.exe wslpath -u（尊重真实挂载根配置）；
    wsl.exe 缺失/失败/输出非 / 开头时退回手工转换。"""
    try:
        result = runner(
            ["wsl.exe", "wslpath", "-u", windows_path],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, ValueError):
        result = None
    if result is not None and result.returncode == 0:
        converted = (getattr(result, "stdout", "") or "").strip()
        if converted.startswith("/"):
            return converted
    return _manual_windows_to_wsl_path(windows_path)


def _bash_syntax_command(script: Path) -> list[str] | None:
    """host-aware bash -n 命令：POSIX 或 Windows 上选中 Git Bash/MSYS → 直用原路径
    （现状行为）；Windows 上选中 WSL 启动器 → 先转换路径（否则 WSL bash 报
    「No such file or directory」——PowerShell 宿主实测缺陷）。"""
    if BASH is None:
        return None
    script_arg = str(script)
    if os.name == "nt" and _bash_flavor_is_wsl(BASH):
        script_arg = _windows_to_wsl_path(str(script))
    return [BASH, "-n", script_arg]


def _load_bridge_module():
    spec = importlib.util.spec_from_file_location("cosyvoice_openai_bridge", BRIDGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bridge():
    return _load_bridge_module()


# ---------- 语法检查 ----------


@pytest.mark.skipif(BASH is None, reason="bash 不可用（bootstrap 脚本语法检查需要 bash）")
@pytest.mark.parametrize(
    "script",
    [BOOTSTRAP_FUNASR, BOOTSTRAP_COSYVOICE, REACHABILITY],
    ids=["funasr", "cosyvoice", "reachability"],
)
def test_bash_scripts_pass_bash_n(script: Path) -> None:
    command = _bash_syntax_command(script)
    assert command is not None
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr


# ---------- host-aware 转换分支回归（不依赖宿主装了 WSL/Git Bash） ----------


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_wsl_kernel_release_detection_by_uname() -> None:
    """判别函数纯逻辑：WSL1/WSL2 内核串判 True；Git Bash/MSYS/Linux 判 False。"""
    assert _is_wsl_kernel_release("6.18.33.2-microsoft-standard-WSL2")
    assert _is_wsl_kernel_release("4.4.0-19041-Microsoft")
    assert not _is_wsl_kernel_release("3.6.10-710e5275.x86_64")  # Git Bash（实测）
    assert not _is_wsl_kernel_release("6.1.0-26-amd64")  # 常规 Linux
    assert not _is_wsl_kernel_release("")


def test_bash_flavor_probe_injected() -> None:
    """uname 探测分支（注入 runner，不 spawn 真进程、不依赖 WSL 安装）。"""

    def wsl_probe(cmd, **kwargs):
        return _FakeCompleted(0, "6.18.33.2-microsoft-standard-WSL2\n")

    def msys_probe(cmd, **kwargs):
        return _FakeCompleted(0, "3.6.10-710e5275.x86_64\n")

    def dead_probe(cmd, **kwargs):
        raise FileNotFoundError("no such bash")

    assert _bash_flavor_is_wsl("C:\\Windows\\System32\\bash.exe", probe=wsl_probe) is True
    assert _bash_flavor_is_wsl("C:\\Program Files\\Git\\usr\\bin\\bash.exe", probe=msys_probe) is False
    # 探测进程失败 → 保守按非 WSL（保持现状直用路径）
    assert _bash_flavor_is_wsl("X:\\bash.exe", probe=dead_probe) is False


def test_windows_to_wsl_path_prefers_wslpath_output() -> None:
    """wsl.exe wslpath -u 可用且输出 / 开头 → 采用其结果（尊重真实挂载根）。"""
    seen: list[list[str]] = []

    def runner(cmd, **kwargs):
        seen.append(list(cmd))
        return _FakeCompleted(0, "/mnt/d/AI Learning OS/x/script.sh\r\n")

    converted = _windows_to_wsl_path("D:\\AI Learning OS\\x\\script.sh", runner=runner)
    assert converted == "/mnt/d/AI Learning OS/x/script.sh"
    assert seen == [["wsl.exe", "wslpath", "-u", "D:\\AI Learning OS\\x\\script.sh"]]


def test_windows_to_wsl_path_falls_back_to_manual_conversion() -> None:
    """wsl.exe 缺失/非零退出/输出异常 → 确定性手工转换（盘符小写+正斜杠）。"""

    def no_wsl(cmd, **kwargs):
        raise FileNotFoundError("wsl.exe not installed")

    def failing(cmd, **kwargs):
        return _FakeCompleted(1, "")

    def garbage(cmd, **kwargs):
        return _FakeCompleted(0, "not-a-path")

    target = "D:\\AI Learning OS\\x\\a b.sh"
    expected = "/mnt/d/AI Learning OS/x/a b.sh"
    assert _windows_to_wsl_path(target, runner=no_wsl) == expected
    assert _windows_to_wsl_path("C:\\repo\\s.sh", runner=failing) == "/mnt/c/repo/s.sh"
    assert _windows_to_wsl_path(target, runner=garbage) == expected


@pytest.mark.parametrize("script", [BRIDGE, SMOKE], ids=["bridge", "smoke"])
def test_python_scripts_compile(script: Path) -> None:
    import py_compile

    py_compile.compile(str(script), doraise=True)


# ---------- bridge 单元：WAV 序列化（纯标准库） ----------


def test_samples_to_wav_bytes_produces_riff_wav(bridge) -> None:
    wav = bridge._samples_to_wav_bytes([0.0, 0.25, -0.25, 0.5, -0.5], sample_rate=24000)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav), "rb") as reader:
        assert reader.getframerate() == 24000
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getnframes() == 5


def test_samples_to_wav_bytes_clamps_out_of_range(bridge) -> None:
    """越界样本钳制到 ±1.0（防 int16 回绕），无样本时仍输出合法 WAV 头。"""
    wav = bridge._samples_to_wav_bytes([1.5, -2.0], sample_rate=8000)
    with wave.open(io.BytesIO(wav), "rb") as reader:
        frames = reader.readframes(2)
    assert frames == (32767).to_bytes(2, "little", signed=True) + (-32767).to_bytes(2, "little", signed=True)
    empty = bridge._samples_to_wav_bytes([], sample_rate=8000)
    with wave.open(io.BytesIO(empty), "rb") as reader:
        assert reader.getnframes() == 0


# ---------- bridge 行为：引擎未加载的确定性状态 ----------


def _client(bridge, *, api_key: str | None = None) -> TestClient:
    app = bridge.create_app(
        Path("/nonexistent-repo"), Path("/nonexistent-model"),
        model_name="Fun-CosyVoice3-0.5B-2512",
        prompt_text="prompt", prompt_wav=Path("/nonexistent.wav"),
        api_key=api_key,
    )
    # 不进入 with（不触发 lifespan）——引擎恒处 loading 态，行为确定性
    return TestClient(app)


def test_bridge_health_returns_503_while_loading(bridge) -> None:
    response = _client(bridge).get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "cosyvoice model is loading"}


def test_bridge_speech_rejects_unknown_model_and_format(bridge) -> None:
    client = _client(bridge)
    unknown = client.post("/v1/audio/speech", json={"model": "other-model", "input": "文本"})
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "unknown model"}
    bad_format = client.post(
        "/v1/audio/speech",
        json={"model": "Fun-CosyVoice3-0.5B-2512", "input": "文本", "response_format": "mp3"},
    )
    assert bad_format.status_code == 400
    assert bad_format.json() == {"detail": "only response_format=wav is supported"}


def test_bridge_speech_returns_503_sanitized_while_loading(bridge) -> None:
    """模型未就绪（未下载/依赖缺失/加载中同路径）：503 固定 JSON，无路径/key 泄漏。"""
    response = _client(bridge).post(
        "/v1/audio/speech",
        json={"model": "Fun-CosyVoice3-0.5B-2512", "input": "文本"},
    )
    assert response.status_code == 503
    body = response.text
    assert response.json() == {"detail": "cosyvoice model is loading"}
    for marker in ("/nonexistent", "C:", "prompt", "api", "key"):
        assert marker not in body


def test_bridge_speech_requires_bearer_when_key_configured(bridge) -> None:
    client = _client(bridge, api_key="bridge-secret-key")
    rejected = client.post(
        "/v1/audio/speech",
        json={"model": "Fun-CosyVoice3-0.5B-2512", "input": "文本"},
    )
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "invalid api key"}
    assert "bridge-secret-key" not in rejected.text
    # 鉴权先于模型校验：带 key 后回到 503 loading（无 key 时同请求也是 503）
    accepted_path = client.post(
        "/v1/audio/speech",
        json={"model": "Fun-CosyVoice3-0.5B-2512", "input": "文本"},
        headers={"Authorization": "Bearer bridge-secret-key"},
    )
    assert accepted_path.status_code == 503


def test_bridge_speech_rejects_blank_input(bridge) -> None:
    client = _client(bridge)
    response = client.post("/v1/audio/speech", json={"model": "Fun-CosyVoice3-0.5B-2512", "input": ""})
    assert response.status_code == 422  # pydantic 长度校验（不含敏感信息）


# ---------- 脚本文本契约 ----------


def test_bootstrap_funasr_text_contract() -> None:
    text = BOOTSTRAP_FUNASR.read_text(encoding="utf-8")
    for anchor in (
        "set -euo pipefail",
        "funasr-server",
        'HOST="127.0.0.1"',
        '--host "$HOST"',
        "--model sensevoice",
        "--device cpu",
        "8010",
        "download.pytorch.org/whl/cpu",
        "MODELSCOPE_CACHE",
        "artifacts/voice",
        "funasr==",
        "python-multipart",
        "1.4.15",
        # 修正轮：uv 隔离管理 Python（不假设 apt），含 ~/.local/bin 探测
        "uv venv --python 3.11",
        "--seed",
        ".local/bin/uv",
    ):
        assert anchor in text, anchor
    for pattern in SECRET_PATTERNS:
        assert pattern not in text


def test_bootstrap_cosyvoice_text_contract() -> None:
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    for anchor in (
        "set -euo pipefail",
        "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc",  # 固定 commit（可验证来源）
        "FunAudioLLM/CosyVoice.git",
        "download.pytorch.org/whl/cu128",
        "Fun-CosyVoice3-0.5B-2512",
        "cosyvoice_openai_bridge.py",
        "127.0.0.1",
        "8011",
        # 修正轮：uv 隔离管理 Python 3.10（不假设 apt）；最小运行时依赖 + 完整回退；
        # sox 疑问经 soundfile 后端真实加载探针解决
        "uv venv --python 3.10",
        "--seed",
        ".local/bin/uv",
        "cosyvoice-runtime-requirements.txt",
        "COSYVOICE_FULL_REQUIREMENTS",
        "requirements.full.txt",
        "submodule update --init --recursive",
        "snapshot_download",
        "import cosyvoice.cli.cosyvoice",
        'backend="soundfile"',
    ):
        assert anchor in text, anchor
    # 修正轮要点：不再假设 apt python3.10/sox
    assert "apt-get install python3.10" not in text
    assert "apt-get install sox" not in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text


def test_smoke_script_text_contract() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    for anchor in (
        "LocalFunAsrAsrProvider",
        "LocalCosyVoiceTtsProvider",
        "transcribe",  # 走主 API 同款 provider.transcribe
        "synthesize",
        "RIFF",
        "ALL LOCAL VOICE SMOKE CHECKS PASSED",
        "ASR_SMOKE_AUDIO",
        "ASR_SMOKE_EXPECTED_TEXT",
        "VOICE_SMOKE_TEXT",
        "127.0.0.1:8010",
        "127.0.0.1:8011",
        "asr_sample_zh.wav",
        "artifacts",
    ):
        assert anchor in text, anchor
    for pattern in SECRET_PATTERNS:
        assert pattern not in text


def test_bridge_script_text_contract() -> None:
    text = BRIDGE.read_text(encoding="utf-8")
    for anchor in (
        "/health",
        "/v1/audio/speech",
        "response_format",
        "wav",
        "127.0.0.1",
        "inference_zero_shot",
        "_samples_to_wav_bytes",
    ):
        assert anchor in text, anchor
    for pattern in SECRET_PATTERNS:
        assert pattern not in text


def test_scripts_do_not_import_repo_app_modules() -> None:
    """bridge 是独立进程（CosyVoice venv 内运行）：不 import 仓库 app 代码。"""
    bridge_text = BRIDGE.read_text(encoding="utf-8")
    assert "from app." not in bridge_text
    assert "import app" not in bridge_text
    # smoke 脚本则复用主 API adapter（同一代码路径，这是设计而非事故）
    smoke_text = SMOKE.read_text(encoding="utf-8")
    assert "from app.voice.providers import" in smoke_text


def test_bootstrap_python_missing_hints_are_actionable() -> None:
    """契约：解释器缺失时的失败提示指向 uv 安装（不假设 apt/python3.10 已装）。"""
    for script in (BOOTSTRAP_FUNASR, BOOTSTRAP_COSYVOICE):
        text = script.read_text(encoding="utf-8")
        assert "astral.sh/uv/install.sh" in text, script.name
        assert "uv venv --python" in text, script.name


def test_runtime_requirements_contract() -> None:
    """最小运行时依赖清单：导入闭包必需包 + bridge 服务面（fastapi/uvicorn，复审
    修正——bridge 顶层 import fastapi 且 main() 调 uvicorn.run，必须随清单安装）
    齐全；训练/WebUI/TensorRT 系一律排除。"""
    text = RUNTIME_REQUIREMENTS.read_text(encoding="utf-8")
    for required in (
        "HyperPyYAML", "omegaconf", "transformers", "tiktoken", "openai-whisper",
        "onnxruntime", "einops", "x-transformers", "inflect", "regex", "modelscope",
        "soundfile", "wetext", "numpy", "scipy", "tqdm",
        # bridge 服务面（复审修正）：顶层 import fastapi + uvicorn.run
        "fastapi==", "uvicorn==",
    ):
        assert required in text, required
    active = [line for line in text.splitlines()
              if line.strip() and not line.strip().startswith("#")]
    # pydantic 保持 fastapi 间接依赖（不直接 pin）——防止无理由显式 pin 回归
    assert not any(line.startswith("pydantic") for line in active)
    for excluded in (
        "deepspeed", "tensorrt", "vllm", "gradio", "librosa", "lightning",
        "pyworld", "matplotlib", "tensorboard", "grpcio", "gdown", "diffusers",
    ):
        # 排除项允许出现在注释（排除依据），不允许出现在生效行首
        active = [line for line in text.splitlines()
                  if line.strip() and not line.strip().startswith("#")]
        assert not any(line.startswith(excluded) for line in active), excluded


def test_reachability_script_contract() -> None:
    """可达性检查脚本：host.docker.internal 探测 + 恒绑 127.0.0.1 + setsid 存活修复
    + 精确清理（复审修正：唯一 PID 文件 + /proc cmdline 核验，绝不 pkill 广撒网）。"""
    text = REACHABILITY.read_text(encoding="utf-8")
    for anchor in (
        "set -euo pipefail",
        "host.docker.internal",
        "host-gateway",
        "--bind $WSL_LOOPBACK",
        'WSL_LOOPBACK="127.0.0.1"',
        "setsid nohup",  # 实测修复：wsl.exe 会话退出会杀同会话后台进程
        "aios/api:local",
        # 精确清理语义：唯一 PID 文件 + /proc/<pid>/cmdline 核验后才 kill + 删文件
        "/tmp/aios-voice-probe-",
        "/proc/$p/cmdline",
        r"echo \$\$ >",  # 探针 sh 先落 PID 再 exec python3（exec 保 PID；\$\$ 供 WSL bash 透传）
        "rm -f",
    ):
        assert anchor in text, anchor
    # 复审修正：生效代码绝不按端口 pkill（可能误杀同参数无关进程；注释提及不算）
    active_lines = [line for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")]
    assert not any("pkill" in line for line in active_lines)
    # 生效的 --bind 行绝不绑 0.0.0.0（探针只走 loopback；注释里提及不算）
    bind_lines = [line for line in text.splitlines() if "--bind" in line and not line.strip().startswith("#")]
    assert bind_lines, "探针脚本应有 --bind 行"
    assert all("0.0.0.0" not in line for line in bind_lines)
    for pattern in SECRET_PATTERNS:
        assert pattern not in text


def test_evidence_script_contract() -> None:
    """测试证据脚本：可复现命令 + 解释器解析顺序留档（复审修正：无机器特定绝对路径——
    canonical venv 回退改为相对同级主检出探测）。"""
    data = EVIDENCE_PS1.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 需要 UTF-8 BOM（中文注释）"
    text = data.decode("utf-8-sig")
    for anchor in (
        "pytest services/api/tests -q",
        "AIOS_TEST_PYTHON",
        "rev-parse HEAD",
        "requirements.txt",
        "requirements-dev.txt",
        # 相对同级主检出回退（标准 worktree 布局，无盘符假设）
        r"..\..\ai-learning-os\.venv\Scripts\python.exe",
    ):
        assert anchor in text, anchor
    # 复审修正：不硬编码任何盘符/机器特定绝对路径
    assert "D:\\" not in text
    assert "C:\\" not in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
