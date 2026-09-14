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
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._subprocess_utf8 import run_bash, run_utf8

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
def _bash_flavor_is_wsl(bash_path: str, probe=run_utf8) -> bool:
    """探测该 bash 是否 WSL 启动器（经 uname -r 判别；探测失败按非 WSL 保守处理）。

    默认 probe 走 run_utf8（UTF-8 + replace）：WSL 启动器在部分宿主下向
    stderr 打 GBK 本地化报错（byte 0xff），locale 文本模式的 reader 线程会抛
    UnicodeDecodeError（见 _subprocess_utf8 模块说明）。注入的 fake 形态为
    ``f(cmd, **kwargs)``，与本调用签名兼容。"""
    try:
        result = probe([bash_path, "-c", "uname -r"], timeout=30)
    except (OSError, ValueError):
        return False
    return _is_wsl_kernel_release(result.stdout or "")


def _manual_windows_to_wsl_path(windows_path: str) -> str:
    """确定性手工转换（wsl.exe 不可用时的回退）：盘符→/mnt/<小写>、反斜杠→正斜杠。"""
    drive, _, rest = windows_path.partition(":")
    return f"/mnt/{drive.lower()}{rest.replace(chr(92), '/')}"


def _windows_to_wsl_path(windows_path: str, *, runner=run_utf8) -> str:
    """Windows 路径 → WSL 路径。优先 wsl.exe wslpath -u（尊重真实挂载根配置）；
    wsl.exe 缺失/失败/输出非 / 开头时退回手工转换。

    默认 runner 走 run_utf8（UTF-8 + replace）：wsl.exe 不可用时输出 GBK
    本地化报错，locale 文本模式的 reader 线程会抛 UnicodeDecodeError
    （见 _subprocess_utf8 模块说明）。"""
    try:
        result = runner(["wsl.exe", "wslpath", "-u", windows_path], timeout=30)
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
    # 统一 UTF-8 文本模式：WSL 启动器/Git Bash 的 stderr 可能带 GBK 本地化
    # 报错字节，locale 文本模式的 reader 线程会抛 UnicodeDecodeError
    # （见 _subprocess_utf8 模块说明）。
    result = run_bash(command, timeout=60)
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
        # torchcodec：torchaudio 2.11 后端探测需要；+cu128 本地版本轮只在
        # pytorch cu128 index（必须与 torch 同命令从该 index 安装）
        "torchcodec==0.11.1+cu128",
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
        # M14-02 修复回归：加载失败必须显式打印完整栈（被捕获的异常不自动打印，
        # 旧文案「细节见上方栈」从未兑现——生产失败无法定位）
        "traceback.print_exc()",
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


def test_bootstrap_torchcodec_pinned_on_cu128_install_line() -> None:
    """torchcodec 确定性回归：初始 cu128 安装命令必须一次性带上
    torch/torchaudio/torchcodec==0.11.1+cu128——torchaudio 2.11 后端探测需要
    torchcodec，而 0.11.1+cu128 本地版本轮只存在于 download.pytorch.org/whl/
    cu128（PyPI 解析拿不到），也不能挪进 cosyvoice-runtime-requirements.txt
    （该文件按 PyPI 安装）。形态锁定为「不 pin torch/torchaudio 的初始安装行」
    全串（M14-17 起终局闭包恢复行以 torch==2.11.0+cu128 精确 pin 区分——
    --no-deps 前缀已随 M14-17 移除，不能再作区分依据）。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    initial = 'pip" install torch torchaudio torchcodec==0.11.1+cu128'
    install_lines = [line for line in text.splitlines() if initial in line]
    assert install_lines, "bootstrap 应有不 pin torch 的初始 cu128 安装命令行"
    assert len(install_lines) == 1, "初始 torch 安装命令应唯一（便于 cu128 同源解析）"
    line = install_lines[0]
    assert "--index-url https://download.pytorch.org/whl/cu128" in line
    assert "--no-deps" not in line


OFFLINE_FAST_PATH_GATES = (
    "gate_pip_check",
    "gate_consistency_probe",
    "gate_import_probe",
    "gate_wav_probe",
)

#: 五类联网安装命令（fast path 命中时一个都不允许执行）
NETWORK_INSTALL_MARKERS = (
    'pip" install --upgrade pip',
    'pip" install torch torchaudio torchcodec==0.11.1+cu128',
    'install -r "$REPO_ROOT/tools/voice/cosyvoice-runtime-requirements.txt"',
    'install -r "$ARTIFACTS/cosyvoice/requirements.full.txt"',
    'pip" install torch==2.11.0+cu128',
)


def test_bootstrap_torch_reconciliation_order_contract() -> None:
    """M14-17 生产实证回归（2026-09-13）：M14-16 的终局 --no-deps 三件套回写
    不充分——生产 venv 实证 torch/torchaudio/torchcodec 均已 cu128，但
    nvidia-cudnn-cu12 8.9.2.26（torch metadata 需 ==9.19.0.56）、
    nvidia-nccl-cu12 2.20.5（需 ==2.28.9）、triton 2.3.1（需 ==3.6.0），多数
    CUDA runtime 仍是 12.1 系列（清单分支装 torch 2.3.1 时连带降级的闭包），
    import torch 失败缺 libcudnn.so.9——pip check 恰报出这三个 == pin 冲突。
    根因：--no-deps 只回写三个主轮，不恢复 torch metadata 声明的依赖闭包。
    契约（顺序锁定，M14-25 门禁函数化后形态）：门禁函数定义（单一事实源，
    判定与门禁共享）→ M14-25 offline fast path 判定 → 初始 cu128 同命令
    安装 → 最小/完整清单分支 → 终局 CUDA 闭包恢复（三件套精确 pin + 闭包
    成员 pin/extras，**完整依赖解析、无 --no-deps**、同一 cu128 index，
    禁止 force-reinstall 全量重写）→ pip check 附加门禁（fail-closed 指向
    CUDA closure 未恢复）→ 运行期一致性探针（CUDA closure 契约表 +
    torch/torchaudio 基础版本一致 + 同为 +cu128 + torchcodec 可导入）→
    CosyVoice import 探针 → WAV 加载探针。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    lines = text.splitlines()

    def sole_line(marker: str) -> int:
        hits = [i for i, line in enumerate(lines) if marker in line]
        assert len(hits) == 1, f"契约锚点应恰好出现一次: {marker!r}（实际 {len(hits)} 次）"
        return hits[0]

    def gate_def(gate: str) -> int:
        hits = [i for i, line in enumerate(lines) if line.strip() == f"{gate}() {{"]
        assert len(hits) == 1, f"门禁函数 {gate} 应恰定义一次（单一事实源）"
        return hits[0]

    # 门禁函数定义顺序 = 门禁顺序（pip check → 一致性 → import → WAV）
    pip_check_def = gate_def("gate_pip_check")
    consistency_def = gate_def("gate_consistency_probe")
    import_def = gate_def("gate_import_probe")
    wav_def = gate_def("gate_wav_probe")
    assert pip_check_def < consistency_def < import_def < wav_def

    # M14-25 fast path 判定在门禁定义之后、依赖安装之前
    fast_path_judge = sole_line("FAST_PATH_HIT=1")
    assert wav_def < fast_path_judge

    initial_install = sole_line('pip" install torch torchaudio torchcodec==0.11.1+cu128')
    minimal_reqs = sole_line('install -r "$REPO_ROOT/tools/voice/cosyvoice-runtime-requirements.txt"')
    full_reqs = sole_line('install -r "$ARTIFACTS/cosyvoice/requirements.full.txt"')
    closure_restore = sole_line('pip" install torch==2.11.0+cu128')
    assert fast_path_judge < initial_install
    assert initial_install < minimal_reqs < closure_restore
    assert initial_install < full_reqs < closure_restore

    # 门禁调用（安装后 fail-closed 执行）顺序不变
    pip_check_gate = sole_line("if ! gate_pip_check; then")
    consistency_gate = sole_line("if ! gate_consistency_probe; then")
    import_gate = sole_line("if ! gate_import_probe; then")
    wav_gate = sole_line("if ! gate_wav_probe; then")
    assert closure_restore < pip_check_gate < consistency_gate < import_gate < wav_gate

    # 终局闭包恢复内容：三件套精确 pin + torch 2.11.0+cu128 METADATA（Linux
    # 段）声明的 CUDA 闭包成员 + 同一 cu128 index；完整依赖解析（绝无
    # --no-deps——只回写主轮会留下 12.1 系列闭包，import torch 缺
    # libcudnn.so.9）；禁止 force-reinstall/ignore-installed 全量重写
    # （pin 已满足时 pip no-op）
    restore_line = lines[closure_restore]
    for pin in (
        "torch==2.11.0+cu128", "torchaudio==2.11.0+cu128", "torchcodec==0.11.1+cu128",
        # torch 2.11.0+cu128 METADATA（Linux 段）直接声明的 == pin 项
        "nvidia-cudnn-cu12==9.19.0.56", "nvidia-nccl-cu12==2.28.9",
        "nvidia-cusparselt-cu12==0.7.1", "nvidia-nvshmem-cu12==3.4.5", "triton==3.6.0",
        # cuda-toolkit extras==12.8.1（12.8 系列 nvidia runtime）+ cuda-bindings 范围
        "cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==12.8.1",
        "cuda-bindings>=12.9.4,<13",
    ):
        assert pin in restore_line, pin
    assert "--no-deps" not in restore_line
    assert "--index-url https://download.pytorch.org/whl/cu128" in restore_line
    assert "--force-reinstall" not in restore_line
    assert "--ignore-installed" not in restore_line

    # pip check 附加门禁（闭包恢复与一致性探针之间）：fail-closed，失败文案
    # 指向 CUDA closure 未恢复；注释必须说明它只是附加门禁、不能替代真实
    # import/运行探针（两个实证盲区：混合 ABI 报 No broken requirements、
    # extras 门控的 12.8 系列 runtime 错配不报）
    gate_body = "\n".join(lines[closure_restore:consistency_gate])
    for anchor in (
        "CUDA closure 未恢复",
        "附加门禁",
        "不能替代",
    ):
        assert anchor in gate_body, anchor

    # 一致性探针内容（gate_consistency_probe 函数定义区间）：CUDA closure
    # 契约 + 基础版本一致 + 同 +cu128 + torchcodec 可导入
    probe_body = "\n".join(lines[consistency_def:import_def])
    for anchor in (
        "_base_version(torch_version) != _base_version(torchaudio_version)",
        '"+cu128" not in torch_version or "+cu128" not in torchaudio_version',
        "import torchcodec",
        "pip check",
    ):
        assert anchor in probe_body, anchor
    # 一致性门禁 fail-closed 文案在门禁调用段（守卫块内、探针调用之后）
    consistency_gate_region = "\n".join(lines[consistency_gate:import_gate])
    assert "运行期一致性探针失败" in consistency_gate_region


def test_bootstrap_offline_fast_path_contract() -> None:
    """M14-25 offline fast path 契约（生产实证 PID 18447 回归：模型/venv/
    wetext 缓存完整时，脚本仍因无条件 pip upgrade/install 与 CUDA 闭包恢复
    联网而中止——PyPI 不可达导致离线重启失败）：
    - 门禁函数单一事实源：pip check / CUDA closure 一致性 / import
      cosyvoice / torchaudio WAV 四道门禁各定义恰一次，fast path 判定与
      安装后 fail-closed 门禁调用同一组函数——不引入第二套契约逻辑；
    - fast path 判定块本身零网络：判定依据 = 本地解释器 + 本地 metadata +
      本地探针，无 pip install / --index-url / snapshot_download / git
      clone（不允许先访问网络探测可用性再决定 fast path）；
    - 判定失败点名缺失项（venv 缺失 / pip check / 一致性 / import / WAV
      五类原因），命中与未命中均有明确日志；
    - 五类联网安装命令全部位于 fast-path 跳过守卫块内（命中即零执行）；
    - 门禁 fail-closed 执行仍在依赖安装之后（契约失败绝不因 fast path
      改动而弱化或绕过安装路径）；
    - 模型段 / wetext 段 / bridge 启动在守卫块之外之后（fast path 命中时
      直达，且 COSYVOICE_SKIP_DOWNLOAD 缺模型 fail-closed 语义不动）。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    lines = text.splitlines()

    def sole_active(marker: str) -> int:
        hits = [i for i, line in enumerate(lines)
                if marker in line and line.strip() and not line.strip().startswith("#")]
        assert len(hits) == 1, f"契约锚点应恰出现一次: {marker!r}（实际 {len(hits)} 次）"
        return hits[0]

    # 门禁函数单一事实源 + 定义在判定块之前
    gate_defs = {gate: sole_active(f"{gate}() {{") for gate in OFFLINE_FAST_PATH_GATES}
    fast_path_judge = sole_active("FAST_PATH_HIT=1")
    assert max(gate_defs.values()) < fast_path_judge

    # 判定块：命中/未命中日志 + 五类缺失原因点名
    hit_say = sole_active('say "offline fast path 命中')
    miss_say = sole_active('say "offline fast path 未命中')
    guard = sole_active('if [ "$FAST_PATH_HIT" != "1" ]; then')
    assert fast_path_judge < hit_say < miss_say < guard
    for reason in (
        "venv 解释器缺失",
        "pip check 未通过",
        "一致性探针未通过",
        "import cosyvoice.cli.cosyvoice 探针未通过",
        "torchaudio WAV 加载探针未通过",
    ):
        assert reason in text, reason

    # 判定块零网络：判定区间（FAST_PATH_HIT 赋值到守卫行）生效行无网络命令
    for line in lines[fast_path_judge:guard]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for banned in ('pip" install', "--index-url", "snapshot_download", "git clone"):
            assert banned not in line, f"fast path 判定块不得含网络命令: {line.strip()}"
    # 判定区间内四道门禁全部被调用（判定 = 门禁本身，非弱化替身）
    judge_region = "\n".join(lines[fast_path_judge:guard])
    for gate in OFFLINE_FAST_PATH_GATES:
        assert gate in judge_region, gate

    # 守卫块边界：守卫行之后首个顶层 fi；五类联网安装命令全部在块内（缩进）
    guard_close = min(i for i, line in enumerate(lines) if line == "fi" and i > guard)
    for marker in NETWORK_INSTALL_MARKERS:
        pos = sole_active(marker)
        assert guard < pos < guard_close, f"联网安装命令必须在 fast-path 跳过守卫块内: {marker}"
        assert lines[pos].startswith("  "), f"守卫块内命令应有缩进: {marker}"

    # 门禁 fail-closed 调用也在守卫块内（安装之后；契约失败走安装路径再验证）
    for gate in OFFLINE_FAST_PATH_GATES:
        pos = sole_active(f"if ! {gate}; then")
        assert guard < pos < guard_close, gate

    # 模型段 / wetext 段 / bridge 启动在守卫块之外之后（fast path 命中直达）
    model_detect = sole_active("model_payload_ready()")
    wetext_export = sole_active('export MODELSCOPE_CACHE=')
    bridge_exec = sole_active(
        'exec "$VENV_DIR/bin/python" "$REPO_ROOT/tools/voice/cosyvoice_openai_bridge.py"',
    )
    assert guard_close < model_detect < wetext_export < bridge_exec

    # skip-download 缺模型 fail-closed 语义保持（既有门禁不被 fast path 弱化）
    assert "模型未就位" in text
    assert 'COSYVOICE_SKIP_DOWNLOAD:-0}" = "1"' in text


def _bash_friendly(path: Path) -> str:
    """路径 → 当前宿主 bash 可用形式：Git Bash/MSYS/POSIX 用正斜杠盘符；
    WSL 启动器转 /mnt/…（与 _bash_syntax_command 同一 host-aware 判别）。"""
    if os.name == "nt" and BASH is not None and _bash_flavor_is_wsl(BASH):
        return _windows_to_wsl_path(str(path))
    return path.as_posix()


#: R3 preflight 脚本（LF 源码；经选中的 BASH 以脚本文件形态执行——WSL 启动器
#: 对 `bash -c '<复合串>'` 做二次命令行解析会字面化内层引号，脚本文件与
#: bootstrap 本体同形态不受影响）：逐项验证 env 透传（WSL 只透传 WSLENV 声明
#: 变量）、PIP_CALL_LOG 目录、fake .git 位置、stub 可执行位与首行解释器
#: （CRLF 检测）、fake 仓库可被该 BASH 的 git 读取且 commit 与 env 一致。
PREFLIGHT_SCRIPT = """#!/bin/sh
# M14-25 fast path 行为实证 preflight（R3）
set -eu
test -n "${VOICE_ARTIFACTS_DIR:-}" \\
  || { echo "PREFLIGHT FAIL: VOICE_ARTIFACTS_DIR 未透传（WSL 需 WSLENV 声明）" >&2; exit 1; }
test -n "${PIP_CALL_LOG:-}" \\
  || { echo "PREFLIGHT FAIL: PIP_CALL_LOG 未透传（WSL 需 WSLENV 声明）" >&2; exit 1; }
test -n "${COSYVOICE_COMMIT:-}" \\
  || { echo "PREFLIGHT FAIL: COSYVOICE_COMMIT 未透传（WSL 需 WSLENV 声明）" >&2; exit 1; }
test "${COSYVOICE_SKIP_DOWNLOAD:-}" = "1" \\
  || { echo "PREFLIGHT FAIL: COSYVOICE_SKIP_DOWNLOAD != 1" >&2; exit 1; }
test -d "${PIP_CALL_LOG%/*}" \\
  || { echo "PREFLIGHT FAIL: PIP_CALL_LOG 目录不存在" >&2; exit 1; }
REPO="$VOICE_ARTIFACTS_DIR/cosyvoice/CosyVoice"
PY="$VOICE_ARTIFACTS_DIR/cosyvoice/venv/bin/python"
PIP="$VOICE_ARTIFACTS_DIR/cosyvoice/venv/bin/pip"
test -d "$REPO/.git" \\
  || { echo "PREFLIGHT FAIL: fake .git 错位（$REPO/.git 不存在——布局与 env 不一致）" >&2; exit 1; }
test -x "$PY" && test -x "$PIP" \\
  || { echo "PREFLIGHT FAIL: venv stub 不可执行" >&2; exit 1; }
test "$(head -n 1 "$PY")" = "#!/bin/sh" \\
  || { echo "PREFLIGHT FAIL: python stub 首行非 #!/bin/sh（CRLF bad interpreter?）" >&2; exit 1; }
test "$(head -n 1 "$PIP")" = "#!/bin/sh" \\
  || { echo "PREFLIGHT FAIL: pip stub 首行非 #!/bin/sh（CRLF bad interpreter?）" >&2; exit 1; }
test "$(git -C "$REPO" rev-parse HEAD)" = "$COSYVOICE_COMMIT" \\
  || { echo "PREFLIGHT FAIL: git rev-parse 与 COSYVOICE_COMMIT 不一致（dubious ownership?）" >&2; exit 1; }
echo PREFLIGHT_OK
"""


class _FakeBootstrapLayout:
    """临时 fake 布局（不触网、不用生产 venv/模型）：真实 bootstrap 脚本副本
    + fake git 仓库（固定 commit 可 checkout）+ fake venv（python/pip 桩，
    pip 桩逐次记录 argv 到日志）+ 模型四载荷 + wetext 四 FST。

    fake python 桩受 FAKE_PYTHON_FAIL 控制（miss 场景让全部探针失败）。"""

    def __init__(self, root: Path, *, python_fail: bool) -> None:
        """root 必须是短生命周期短路径根（Windows MAX_PATH 隔离，R2）：
        pytest tmp_path 的深层编号路径（…/test_bootstrap_offline_fast_pa0/…）
        + git objects 哈希文件名会超 260 字符——git 报「Filename too long」/
        「Error building trees」且 commit 失败曾被吞（独立复现实证）。调用方
        用 tempfile.TemporaryDirectory(prefix="m1425-…") 提供短根。

        R3：WSL 启动器（C:\\Windows\\System32\\bash.EXE）只透传 WSLENV 声明
        的环境变量——测试自有变量必须显式声明（见 self.env 的 WSLENV 构造），
        否则脚本内 VOICE_ARTIFACTS_DIR 等全部丢失、回落 REPO_ROOT 默认路径，
        触发真实 git clone（supervisor PowerShell 复现取证：clone 目标
        repo/artifacts/… 而 fake .git 在布局路径上，/proc environ 缺全部
        测试变量）。fake stub 必须以显式 LF 字节写入（Windows write_text 会
        把 \\n 翻成 CRLF——WSL /bin/sh 报「bad interpreter」致 fast path 误
        判 miss 进安装路径）。"""
        self.root = root
        repo_voice = root / "repo" / "tools" / "voice"
        repo_voice.mkdir(parents=True)
        self.script = repo_voice / "bootstrap_cosyvoice_wsl.sh"
        shutil.copyfile(BOOTSTRAP_COSYVOICE, self.script)
        (repo_voice / "cosyvoice_openai_bridge.py").write_text("# stub\n", encoding="utf-8")

        # VOICE_ARTIFACTS_DIR 语义 = artifacts 根，脚本自行拼接 cosyvoice/…
        # （COSYVOICE_DIR=$ARTIFACTS/cosyvoice/CosyVoice）。fake 数据必须落在
        # art_base/cosyvoice/ 下与脚本拼接结果逐层对齐——错一层即触发真实
        # git clone（联网，破坏测试边界；上一回合实证教训）。
        art_base = root / "artifacts" / "voice"
        art = art_base / "cosyvoice"
        cv = art / "CosyVoice"
        (cv / "asset").mkdir(parents=True)
        (cv / "asset" / "zero_shot_prompt.wav").write_bytes(b"RIFF-stub")

        # fake git 仓库：每条命令 fail-fast（非零退出即断言失败并带 stderr——
        # R2：曾吞掉 commit 失败，只在 rev-parse 才暴露）；core.longpaths=true
        # 为加固（主修是调用方给的短根——bootstrap 之后也要对该路径跑 git）
        def _git(*args: str) -> subprocess.CompletedProcess[str]:
            result = run_utf8(["git", *args], timeout=60, cwd=cv)
            assert result.returncode == 0, (
                f"git {' '.join(args)} 失败 (exit {result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
            return result

        _git("init", "-q")
        _git("config", "core.longpaths", "true")
        _git("-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", "stub")
        self.commit = _git("rev-parse", "HEAD").stdout.strip()
        assert self.commit, "git rev-parse HEAD 未返回 commit 哈希"

        venv_bin = art / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        # R3：显式 LF 字节写入 stub——write_text 的 locale newline 翻译会在
        # Windows 产出 CRLF，WSL /bin/sh 执行报「bad interpreter: /bin/sh^M」
        fail_guard = b'if [ -n "$FAKE_PYTHON_FAIL" ]; then exit 1; fi\n'
        (venv_bin / "python").write_bytes(b"#!/bin/sh\n" + fail_guard + b"exit 0\n")
        self.pip_log = root / "pip-calls.log"
        (venv_bin / "pip").write_bytes(
            b'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$PIP_CALL_LOG"\nexit 0\n'
        )
        for stub in (venv_bin / "python", venv_bin / "pip"):
            stub.chmod(0o755)

        model = art / "Fun-CosyVoice3-0.5B"
        model.mkdir(parents=True)
        for name in ("cosyvoice3.yaml", "flow.pt", "llm.pt", "hift.pt"):
            (model / name).write_bytes(b"stub")

        wetext = art / "modelscope-cache" / "hub" / "pengzhendong" / "wetext"
        for sub in ("en/tn", "zh/tn"):
            (wetext / sub).mkdir(parents=True)
            for name in ("tagger.fst", "verbalizer.fst"):
                (wetext / sub / name).write_bytes(b"stub")

        self.env = {
            **os.environ,
            "VOICE_ARTIFACTS_DIR": _bash_friendly(art_base),
            "COSYVOICE_COMMIT": self.commit,
            "COSYVOICE_SKIP_DOWNLOAD": "1",
            "PIP_CALL_LOG": _bash_friendly(self.pip_log),
        }
        if python_fail:
            self.env["FAKE_PYTHON_FAIL"] = "1"
        # R3：WSL 启动器只透传 WSLENV 声明的变量——显式声明全部测试自有变量
        # （保留既有 WSLENV 声明，去重追加；值保持 POSIX 形态，无需 /p flag）
        if os.name == "nt" and _bash_flavor_is_wsl(BASH):
            declared = [v for v in self.env.get("WSLENV", "").split(":") if v]
            for var in ("VOICE_ARTIFACTS_DIR", "PIP_CALL_LOG", "COSYVOICE_COMMIT",
                        "COSYVOICE_SKIP_DOWNLOAD", "FAKE_PYTHON_FAIL"):
                if var not in declared:
                    declared.append(var)
            self.env["WSLENV"] = ":".join(declared)
        # 防错位守卫（运行前）：fake .git 必须恰在脚本将检查的
        # $VOICE_ARTIFACTS_DIR/cosyvoice/CosyVoice/.git 路径上——否则脚本会
        # 进入真实 git clone 联网，测试零网络边界被破坏（R1 返工要求）。
        assert (cv / ".git").is_dir(), (
            f"fake git 仓库错位: {cv / '.git'} 不存在——布局与脚本拼接路径"
            "不一致，将触发真实 clone（禁止）"
        )

    def preflight(self) -> None:
        """R3 preflight：经同一选中的 BASH（含 WSL 启动器）在启动 bootstrap
        前验证测试前提成立——env 已透传（WSL 下丢变量即回落默认路径触发真实
        clone）、fake .git/venv stub 在脚本将读取的路径上可见且可执行、stub
        首行恰为 '#!/bin/sh'（CRLF 会致 /bin/sh bad interpreter）、fake 仓库
        可被该 BASH 的 git rev-parse 读取且 commit 与 env 一致。任一不成立
        立即失败并输出完整诊断。

        实现注：经**脚本文件**而非 `bash -c '<复合串>'` 执行——WSL 启动器
        对 -c 参数做二次命令行解析，内层引号被字面化（实证 stderr 出现
        字面 '""'），脚本文件与 bootstrap 本体同形态、不受该解析影响。"""
        probe = self.root / "preflight.sh"
        probe.write_bytes(PREFLIGHT_SCRIPT.encode("utf-8"))
        probe.chmod(0o755)
        result = run_utf8([BASH, _bash_friendly(probe)], timeout=60, env=self.env)
        assert result.returncode == 0 and "PREFLIGHT_OK" in result.stdout, (
            "fast path 行为实证 preflight 失败（env 未透传/布局错位/stub CRLF/"
            f"git 不可读）——BASH={BASH} exit={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def run(self, *, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        # 真实执行（非 bash -n 语法检查）：host-aware 路径转换同语法检查；
        # R3：先 preflight（同 BASH 验证 env 透传/布局/stub），再启动脚本
        self.preflight()
        return run_utf8([BASH, _bash_friendly(self.script)], timeout=timeout, env=self.env)

    def pip_calls(self) -> list[str]:
        if not self.pip_log.exists():
            return []
        return [line for line in
                self.pip_log.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.skipif(BASH is None, reason="bash 不可用（fast path 行为实证需要 bash）")
def test_bootstrap_offline_fast_path_hit_runs_zero_pip_install() -> None:
    """M14-25 行为实证（hit）：fake venv 四探针全过 + 模型/wetext payload
    齐备 → bootstrap 全程零 pip install/upgrade/index-url（fake pip 桩逐次
    记录 argv，只有只读的 check/freeze），命中日志明示 offline fast path，
    wetext 缓存就绪不触发下载，exit 0 直达 bridge exec。

    布局根用 tempfile.TemporaryDirectory 短根（Windows MAX_PATH 隔离，R2）：
    不嵌套 pytest 深层编号 tmp 路径——git objects 哈希文件名在其下会超
    260 字符（「Filename too long」实证）；临时目录自动清理、零网络。"""
    with tempfile.TemporaryDirectory(prefix="m1425-fp-hit-") as short_root:
        layout = _FakeBootstrapLayout(Path(short_root), python_fail=False)
        result = layout.run()
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "offline fast path 命中" in result.stdout
        assert "wetext 离线缓存就绪" in result.stdout
        # 安装段整体未执行（say 都不该出现）
        assert "安装 cu128 torch" not in result.stdout
        assert "终局 CUDA 闭包恢复" not in result.stdout
        # R3：硬断言零真实 clone（env 丢失误回落默认路径时，WSL 启动器下曾
        # 发生真实 github clone——禁止再次发生）
        combined = result.stdout + result.stderr
        assert "Cloning into" not in combined, "禁止真实 git clone（env/布局错位）"
        assert "FunAudioLLM/CosyVoice.git" not in combined
        pip_calls = layout.pip_calls()
        assert "check" in pip_calls, "fast path 判定仍必须执行 pip check 门禁"
        assert not any(
            "install" in call or "upgrade" in call or "--index-url" in call
            for call in pip_calls
        ), f"fast path 命中时不得出现任何安装/索引命令: {pip_calls}"


@pytest.mark.skipif(BASH is None, reason="bash 不可用（fast path 行为实证需要 bash）")
def test_bootstrap_offline_fast_path_miss_falls_back_to_install() -> None:
    """M14-25 行为实证（miss）：运行时契约探针失败（fake python 桩全失败）
    → 未命中日志点名一致性探针 → 进入既有安装路径（fake pip 记录到
    install/--index-url 调用）→ 安装后门禁 fail-closed（exit 1，一致性探针
    失败文案在 stderr）——契约失败绝不绕过安装、绝不静默继续。

    布局根同上：tempfile 短根（Windows MAX_PATH 隔离，R2）。"""
    with tempfile.TemporaryDirectory(prefix="m1425-fp-miss-") as short_root:
        layout = _FakeBootstrapLayout(Path(short_root), python_fail=True)
        result = layout.run()
        assert result.returncode == 1, "契约失败必须 fail-closed（exit 1），不得绕过"
        assert "offline fast path 未命中" in result.stdout
        assert "一致性探针未通过" in result.stdout
        assert "运行期一致性探针失败" in result.stderr
        # R3：硬断言零真实 clone（同 hit 场景）
        combined = result.stdout + result.stderr
        assert "Cloning into" not in combined, "禁止真实 git clone（env/布局错位）"
        assert "FunAudioLLM/CosyVoice.git" not in combined
        pip_calls = layout.pip_calls()
        assert any("install" in call for call in pip_calls), (
            f"契约失败必须走既有安装路径: {pip_calls}"
        )
        assert any("--index-url https://download.pytorch.org/whl/cu128" in call
                   for call in pip_calls), "安装路径含 cu128 index 恢复命令"


def test_bootstrap_cuda_closure_contract() -> None:
    """M14-17 CUDA closure 契约表锁定——来源为生产 venv 真实 wheel metadata
    （2026-09-13 自 torch-2.11.0+cu128.dist-info/METADATA 与 cuda-toolkit
    12.8.1 extras 导出，非记忆推导）。一致性探针内的 CUDA_CLOSURE 表必须
    逐项锁定：torch 2.11.0+cu128 Linux 段直接声明的 == pin 项（cudnn/nccl/
    cusparselt/nvshmem/triton/cuda-toolkit）+ cuda-toolkit 12.8.1 extras 的
    11 个 nvidia runtime（" *" 通配前缀匹配）+ cuda-bindings 范围
    （>=12.9.4,<13）；失败文案点名「CUDA closure 未恢复」并指引报回仓库。
    结构契约：生效代码（非注释行）绝无 --no-deps（M14-16 只回写主轮的根因
    形态）；cu128 index 恰用于两处（初始安装 + 终局闭包恢复），清单分支
    install -r 行恒按 PyPI 解析（不带 pytorch index——PyPI-only 依赖正确
    解析的前提）。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    active_lines = [line for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")]

    # 契约表逐项锁定（成员: 期望版本，与真实 METADATA 逐字一致）
    for member in (
        '"nvidia-cudnn-cu12": "9.19.0.56"',
        '"nvidia-nccl-cu12": "2.28.9"',
        '"nvidia-cusparselt-cu12": "0.7.1"',
        '"nvidia-nvshmem-cu12": "3.4.5"',
        '"triton": "3.6.0"',
        '"cuda-toolkit": "12.8.1"',
        '"nvidia-cublas-cu12": "12.8.4.1.*"',
        '"nvidia-cuda-runtime-cu12": "12.8.90.*"',
        '"nvidia-cuda-cupti-cu12": "12.8.90.*"',
        '"nvidia-cuda-nvrtc-cu12": "12.8.93.*"',
        '"nvidia-cufft-cu12": "11.3.3.83.*"',
        '"nvidia-cufile-cu12": "1.13.1.3.*"',
        '"nvidia-curand-cu12": "10.3.9.90.*"',
        '"nvidia-cusolver-cu12": "11.7.3.90.*"',
        '"nvidia-cusparse-cu12": "12.5.8.93.*"',
        '"nvidia-nvjitlink-cu12": "12.8.93.*"',
        '"nvidia-nvtx-cu12": "12.8.90.*"',
    ):
        assert member in text, member
    # cuda-bindings 范围契约（torch metadata 声明 >=12.9.4,<13）与通配匹配实现
    assert 'CUDA_BINDINGS_MIN = "12.9.4"' in text
    assert 'CUDA_BINDINGS_MAX = "13"' in text
    assert 'endswith(".*")' in text
    # 契约校验失败必须点名 CUDA closure 未恢复（缺失/版本不符两条路径）
    assert text.count("CUDA closure 未恢复") >= 3
    # 生效代码绝无 --no-deps（注释中的历史叙述不算）
    assert not any("--no-deps" in line for line in active_lines), (
        "生效代码不得出现 --no-deps（M14-16 实证：只回写主轮会留下 12.1 系列 CUDA 闭包）"
    )
    # index 语义：cu128 index 恰两处（初始安装 + 终局闭包恢复）；
    # 清单分支恒按 PyPI 解析（不带 pytorch index）
    cu128_lines = [line for line in active_lines
                   if "--index-url https://download.pytorch.org/whl/cu128" in line]
    assert len(cu128_lines) == 2, "cu128 index 应恰用于初始安装与终局闭包恢复"
    for req_line in (line for line in active_lines if "install -r " in line):
        assert "download.pytorch.org" not in req_line, (
            "清单分支 install -r 恒按 PyPI 解析（cu128 闭包由终局恢复统一负责）"
        )


def test_bootstrap_cosyvoice_self_snapshot_contract() -> None:
    """运行期自保护回归（2026-09-10 生产实证缺陷）：bash 按字节偏移增量解析
    脚本——bootstrap 的模型下载可运行数小时，期间本文件被并行会话的 git 操作
    （commit/checkout）改写后，bash 在旧字节偏移上解析新内容，产生与真实语法
    无关的伪错误（实证「line 169: syntax error near unexpected token ')'」，
    而改写前后两个版本各自 bash -n 均通过）。契约：启动即把自身原子快照进
    gitignored artifacts 并 exec 快照副本，此后本文件被改写不再影响运行中
    进程；快照运行经内部 env 守卫防循环，仓库根经 env 透传（快照内 $0 已
    不能推导 REPO_ROOT）。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    for anchor in (
        'COSYVOICE_BOOTSTRAP_SNAPSHOT:-0}" != "1"',  # 防循环守卫（快照运行不再快照）
        'cat -- "$0"',  # 快照内容 = 启动时本文件的字节
        "mv -f",  # 临时文件 + 原子改名（不触碰运行中旧快照的 inode）
        "bootstrap_cosyvoice_wsl.snapshot.sh",  # 快照落位 gitignored artifacts/cosyvoice/
        "COSYVOICE_BOOTSTRAP_REPO_ROOT",  # 仓库根经 env 透传给快照运行
        'exec bash "$SNAPSHOT"',  # 以快照副本接管进程（退出码/信号语义不变）
    ):
        assert anchor in text, anchor
    # 快照重执行必须发生在任何长耗时阶段之前（首个重活 = 官方仓库克隆）
    assert text.index('exec bash "$SNAPSHOT"') < text.index("git clone --recursive")


def test_bootstrap_cosyvoice_model_detection_contract() -> None:
    """模型目录检测契约：就位判定 = 关键载荷文件齐全，而非「目录非空」
    （ModelScope 断点残留 ._____temp/ 会让空壳目录非空——本机实证，误判已
    下载会让 bridge 启动后加载失败）。MODEL_DIR 载荷不完整时回落 ModelScope
    缓存布局（hub/models/<org>/<name>/snapshots/<id>/ 新版与 models/<org>/
    <name>/ 旧版，根可用 MODELSCOPE_CACHE 改址）——缓存与 MODEL_DIR 不同位
    时自动采用缓存，不重新下载；SKIP_DOWNLOAD 下仍无可用模型则 fail-closed
    给出指引，绝不静默启动空 bridge。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    for anchor in (
        "model_payload_ready()",
        "cosyvoice3.yaml", "flow.pt", "llm.pt", "hift.pt",  # 载荷谓词四要素
        "modelscope_cache_model_dir()",
        "hub/models/$MODEL_ID",  # ModelScope ≥1.x 缓存布局
        "models/$MODEL_ID",  # 旧版缓存布局
        "MODELSCOPE_CACHE",  # 缓存根可改址（与 FunASR bootstrap 同名变量）
        "回落 ModelScope 缓存",  # 命中回落时必须明示（运维可观测）
        "模型未就位",  # 载荷缺失 + 缓存未命中 → fail-closed 文案
    ):
        assert anchor in text, anchor
    # 旧的「目录非空即跳过下载」判定必须移除（会误判 ._____temp 空壳目录）
    assert 'ls -A "$MODEL_DIR"' not in text


def test_bridge_wetext_offline_cache_contract() -> None:
    """M14-03 Round 2 wetext 零网络复用（bridge 侧契约）：
    - 缓存位置确定性：MODELSCOPE_CACHE 经 setdefault 指向 model-dir 同级的
      artifacts 内 modelscope-cache（显式设置的环境变量优先，跨 worktree/
      Windows/WSL 可移植——不得出现机器特定绝对路径）；
    - payload 判定 = wetext==0.0.4 lang=auto/tn 实际打开的四个 FST；
    - payload 齐备即窄绑定：仅 model_id == pengzhendong/wetext 的
      snapshot_download 强制 local_files_only=True（其余 id 原样透传原始
      函数——不得是全局 monkeypatch）；绑定在引擎线程/cosyvoice 导入执行前
      发生（main() wiring 先于 uvicorn 服务启动）；
    - 预热是唯一容忍网络的步骤（冷机一次性），失败不绑定并显式告警；
    - COSYVOICE_SKIP_WETEXT_WARMUP=1 为显式跳过 hatch（跳过 = 不绑定）；
    - 加载后内省 text_frontend——降级必须可见；
    - 无 secret 泄漏。"""
    text = BRIDGE.read_text(encoding="utf-8")
    for anchor in (
        'WETEXT_MODEL_ID = "pengzhendong/wetext"',
        "en/tn/tagger.fst", "en/tn/verbalizer.fst",
        "zh/tn/tagger.fst", "zh/tn/verbalizer.fst",
        'os.environ.setdefault("MODELSCOPE_CACHE"',
        'model_dir.parent / "modelscope-cache"',
        "COSYVOICE_SKIP_WETEXT_WARMUP",
        "wetext offline cache READY",
        "wetext resource provisioning FAILED",  # fail-visible（不静默）
        "text frontend active",  # M14-03：加载后前端状态内省（可观测）
        "WARN: text frontend EMPTY",  # 降级必须显式告警（上游静默吞掉）
        "traceback.print_exc()",
        # Round 2：窄绑定 local-only
        "_bind_wetext_snapshot_local_only",
        'kwargs["local_files_only"] = True',
        "if model_id != WETEXT_MODEL_ID:",  # 窄作用域守卫
        "return _original_snapshot_download(model_id, *args, **kwargs)",  # 透传
        "wetext snapshot_download bound LOCAL-ONLY",
    ):
        assert anchor in text, anchor
    # 绑定调用必须由 wiring 函数发出，且 wiring 在服务启动（uvicorn）之前
    assert text.count("_bind_wetext_snapshot_local_only()") >= 2  # 定义调用 + 齐备路径调用
    assert text.index("_wire_wetext_offline_cache(model_dir)") < text.index("import uvicorn")
    # 不落盘第三方文件、不全局替换：绑定只出现在 wetext 常量守卫分支内
    assert "modelscope.snapshot_download = " in text  # 窄替换（仅包命名空间单属性）
    # 可移植性：不得硬编码机器特定绝对路径（盘符 / 家目录 / WSL 挂载点）
    for forbidden in ("/home/", "/mnt/", "D:\\", "C:\\"):
        assert forbidden not in text, forbidden
    for pattern in SECRET_PATTERNS:
        assert pattern not in text, pattern


def test_bootstrap_wetext_warmup_contract() -> None:
    """M14-03 wetext 离线缓存（bootstrap 侧契约）：
    - MODELSCOPE_CACHE 默认指向 gitignored artifacts 内
      ($ARTIFACTS/cosyvoice/modelscope-cache)，显式设置优先；
    - payload 判定同 bridge（四个 FST 齐备 = 就绪，纯本地复用不下载）；
    - 预热在 bridge exec 之前、在主模型缓存回落判定之后（不改变既有回落语义）；
    - COSYVOICE_SKIP_WETEXT_WARMUP=1 显式跳过；
    - 预热失败只告警不阻断（bridge 会重试；fail-closed 会把可用性绑在 ~52MB
      辅助资源上——设计取舍已在脚本注释与 README 说明）。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    for anchor in (
        'export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ARTIFACTS/cosyvoice/modelscope-cache}"',
        "wetext_payload_ready()",
        "en/tn/tagger.fst", "en/tn/verbalizer.fst",
        "zh/tn/tagger.fst", "zh/tn/verbalizer.fst",
        'snapshot_download("pengzhendong/wetext")',
        "COSYVOICE_SKIP_WETEXT_WARMUP",
        "WARN: wetext 预热失败",  # 告警不阻断
    ):
        assert anchor in text, anchor
    # 顺序：缓存导出 + 预热必须发生在 bridge exec 之前（锚定 exec 行本身，
    # 文件头部注释里也提及 bridge 文件名，不能用裸文件名判序）
    exec_line = 'exec "$VENV_DIR/bin/python" "$REPO_ROOT/tools/voice/cosyvoice_openai_bridge.py"'
    assert text.index('export MODELSCOPE_CACHE=') < text.index(exec_line)
    # 顺序：导出必须在主模型缓存回落判定之后（回落语义读用户级默认缓存，不受影响）
    assert text.index("modelscope_cache_model_dir()") < text.index('export MODELSCOPE_CACHE=')


def test_runtime_requirements_contract() -> None:
    """最小运行时依赖清单：导入闭包必需包 + bridge 服务面（fastapi/uvicorn，复审
    修正——bridge 顶层 import fastapi 且 main() 调 uvicorn.run，必须随清单安装）
    齐全；训练/WebUI/TensorRT 系一律排除。"""
    text = RUNTIME_REQUIREMENTS.read_text(encoding="utf-8")
    for required in (
        "HyperPyYAML", "omegaconf", "transformers", "tiktoken", "openai-whisper",
        "onnxruntime", "einops", "x-transformers", "inflect", "regex", "modelscope",
        "soundfile", "wetext", "numpy", "scipy", "tqdm",
        # M14-02 实测补入：模型 YAML !new:/!name: 动态实例化（pydoc.locate）
        # 链上的 Matcha-TTS 自身 PyPI 依赖与 dataset 模块导入——静态闭包不可见，
        # AutoModel 在 venv 内真实加载逐项实证缺失（官方 pin 沿用）
        "conformer==0.3.2", "diffusers==0.29.0", "lightning==2.2.4",
        "hydra-core==1.3.2", "matplotlib==3.7.5", "rich==13.7.1",
        "gdown==5.1.0", "wget", "librosa==0.10.2", "pyarrow==18.1.0",
        "pyworld==0.3.4",
        # bridge 服务面（复审修正）：顶层 import fastapi + uvicorn.run
        "fastapi==", "uvicorn==",
    ):
        assert required in text, required
    active = [line for line in text.splitlines()
              if line.strip() and not line.strip().startswith("#")]
    # pydantic 保持 fastapi 间接依赖（不直接 pin）——防止无理由显式 pin 回归
    assert not any(line.startswith("pydantic") for line in active)
    # cu128 轮子（torch/torchaudio/torchcodec）只存在于 pytorch cu128 index——
    # 本清单按 PyPI 安装拿不到 +cu128 本地版本轮，必须留在 bootstrap 侧安装；
    # M14-17：CUDA 闭包成员（nvidia-*/triton/cuda-toolkit/cuda-bindings）同样
    # 由 bootstrap 侧 cu128 index 终局闭包恢复统一解析——进入本清单会把闭包
    # 成员交回 PyPI 解析（可能装出错误版本，再被清单分支连带降级）
    assert not any(
        line.startswith((
            "torch==", "torchaudio==", "torchcodec",
            "nvidia-", "triton", "cuda-toolkit", "cuda-bindings",
        )) for line in active
    ), "cu128 主轮/闭包成员不得进入 PyPI 解析的最小清单"
    for excluded in (
        # M14-02 修正：librosa/lightning/pyworld/matplotlib/gdown/diffusers 曾列
        # 排除（静态闭包"零引用"），被真实 AutoModel 加载证伪——已移入上方必需
        # 清单（模型 YAML 动态实例化路径）；仍排除的仅剩真正训练/部署侧包
        "deepspeed", "tensorrt", "vllm", "gradio", "tensorboard", "grpcio",
    ):
        # 排除项允许出现在注释（排除依据），不允许出现在生效行首
        active = [line for line in text.splitlines()
                  if line.strip() and not line.strip().startswith("#")]
        assert not any(line.startswith(excluded) for line in active), excluded


def test_openai_whisper_pin_bump_contract() -> None:
    """M14-18 openai-whisper 20231117 → 20250625 pin 契约（生产实证修复）：
    20231117 的 METADATA 声明 `triton<3,>=2.0.0`（无环境标记）——与 torch
    2.11.0+cu128 闭包的 `triton==3.6.0` 直接冲突：2026-09-13 生产 bootstrap
    实证（service.log）M14-17 终局闭包恢复已成功（torch 2.11.0+cu128 /
    triton 3.6.0 就位），pip check 附加门禁仍被「openai-whisper 20231117
    has requirement triton<3,>=2.0.0, but you have triton 3.6.0」卡死 FAIL；
    且清单分支装 20231117 时该约束触发 pip 回溯把 torch 一路降级到 2.3.1
    （实证 2.14.0→2.3.1 回溯）。20250625 METADATA 声明 `triton>=2`
    （x86_64/linux 环境标记，无上界）——与 cu128 闭包共存、运行时依赖集合
    与 20231117 完全一致（supervisor 只读 dry-run 确认不替换 torch/CUDA 包；
    2026-09-13 sdist PKG-INFO 直读复核）。修法 = 升级 pin 而非绕过门禁；
    官方 requirements.txt 在固定 commit 仍 pin 20231117——本清单**有意偏离**
    官方 pin（沿用官方 pin 的策略在此处让位于 CUDA 闭包一致性）。"""
    text = RUNTIME_REQUIREMENTS.read_text(encoding="utf-8")
    active = [line.strip() for line in text.splitlines()
              if line.strip() and not line.strip().startswith("#")]
    pins = [line for line in active if line.startswith("openai-whisper==")]
    assert pins == ["openai-whisper==20250625"], (
        "openai-whisper 应唯一精确 pin 20250625（20231117 的 triton<3 元数据"
        "与 cu128 闭包的 triton==3.6.0 冲突，pip check 门禁实证失败）"
    )
    # 升级依据必须留档在清单注释（证据锚点：旧约束 / 新约束 / 门禁实证）
    for anchor in (
        "triton<3,>=2.0.0",  # 20231117 METADATA 旧约束（生产实证卡门禁）
        "triton>=2",  # 20250625 METADATA 新约束（与 cu128 闭包共存）
        "triton==3.6.0",  # M14-17 CUDA 闭包契约（whisper 不得迫使降级）
        "pip check",  # 门禁实证出处
        "20250625",  # 新 pin
    ):
        assert anchor in text, anchor


def test_openai_whisper_triton_no_bypass_contract() -> None:
    """M14-18 反绕过契约：升级 pin 是唯一允许的修复路径——绝不允许
    ① 忽略/弱化 pip check 门禁；② 清单分支 --no-deps（会让 whisper 的
    triton<3 约束（若回归）不再被解析，掩盖而非修复冲突）；③ 强制降级
    triton 到 <3（破坏 torch 2.11.0+cu128 metadata 闭包，M14-17 已证
    import torch 失败）；④ 改写已装 dist-info 元数据（伪造 pip check 通过）。
    生效行级断言（注释中的历史叙述不算）。"""
    for path in (RUNTIME_REQUIREMENTS, BOOTSTRAP_COSYVOICE):
        text = path.read_text(encoding="utf-8")
        active_lines = [line for line in text.splitlines()
                        if line.strip() and not line.strip().startswith("#")]
        # 反绕过 ②③：生效行绝不出现 triton 降级 pin / --no-deps /
        # --force-reinstall / --ignore-installed（M14-17 已禁 --no-deps，
        # 此处对全生效行扩展到其余 pip 绕过旗标）
        for line in active_lines:
            for banned in ("--no-deps", "--force-reinstall", "--ignore-installed"):
                assert banned not in line, f"{path.name} 生效行不得含 {banned}: {line.strip()}"
        assert not any(
            ("triton<" in line) or ("triton==" in line and "3.6.0" not in line)
            for line in active_lines
        ), (
            f"{path.name} 生效行不得 pin/降级 triton（CUDA 闭包成员由 bootstrap "
            "cu128 index 终局恢复统一解析，triton==3.6.0 契约见 M14-17）"
        )
        # 反绕过 ④：生效行绝不触碰已装 dist-info（改 METADATA 伪造 pip check）
        assert not any("dist-info" in line for line in active_lines), (
            f"{path.name} 生效行不得改写已装 dist-info 元数据"
        )
    # 反绕过 ①：pip check 门禁保持 fail-closed 形态——M14-25 门禁函数化后，
    # pip check 命令恰一处（gate_pip_check 函数定义内），门禁调用行
    # `if ! gate_pip_check; then` 恰一处且非零退出即 FAIL（不得短路绕过）
    bootstrap = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    pip_check_cmd_lines = [line for line in bootstrap.splitlines()
                           if "pip\" check" in line and not line.strip().startswith("#")]
    assert len(pip_check_cmd_lines) == 1, "pip check 命令应恰一处（gate_pip_check 函数内）"
    gate_call_lines = [line for line in bootstrap.splitlines()
                       if "if ! gate_pip_check; then" in line and not line.strip().startswith("#")]
    assert len(gate_call_lines) == 1, "pip check 门禁调用应恰一处"
    gate_line = gate_call_lines[0]
    assert gate_line.strip().startswith("if !"), "门禁必须 if ! 形态（非零退出即 FAIL）"
    assert "|| true" not in gate_line and "|| :" not in gate_line, "门禁不得被短路绕过"


def test_whisper_api_compat_contract_when_installed() -> None:
    """M14-18 CosyVoice 固定 commit 074ca6d 实际用到的两个 whisper API 的
    签名级回归（whisper 可导入的环境执行；canonical 测试 venv 无 whisper 时
    跳过——真实验证面 = CosyVoice artifacts venv / 任一装了 openai-whisper 的
    环境）。CosyVoice 调用点（固定 commit 源码）：
    - cosyvoice/cli/frontend.py:98 与 cosyvoice/dataset/processor.py:196：
      `whisper.log_mel_spectrogram(audio_tensor, n_mels=128)`（位置参数 +
      n_mels 关键字）；
    - cosyvoice/tokenizer/tokenizer.py:7,236：`from whisper.tokenizer
      import Tokenizer` + `Tokenizer(encoding=..., num_languages=...,
      language=..., task=...)`。
    2026-09-13 已以 20231117（生产 venv 已装源码）与 20250625（PyPI sdist）
    逐字 diff 实证：两 API 在两版间源码级不变（audio.py 仅 docstring 更新，
    tokenizer.py 无差异）。"""
    if importlib.util.find_spec("whisper") is None:
        pytest.skip("本环境未安装 openai-whisper（canonical venv 无 whisper；"
                    "签名回归在装有 openai-whisper 的环境（如 CosyVoice "
                    "artifacts venv）执行）")
    import inspect

    import whisper

    sig = inspect.signature(whisper.log_mel_spectrogram)
    assert "audio" in sig.parameters, "log_mel_spectrogram 必须接受位置参数 audio"
    assert "n_mels" in sig.parameters, "log_mel_spectrogram 必须接受 n_mels 关键字（CosyVoice 传 n_mels=128）"
    # n_mels 必须可按关键字传入（cosyvoice 调用形态 n_mels=128）
    assert sig.parameters["n_mels"].kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY,
    )

    from whisper import tokenizer as whisper_tokenizer

    tokenizer_params = set(inspect.signature(whisper_tokenizer.Tokenizer).parameters)
    for kwarg in ("encoding", "num_languages", "language", "task"):
        assert kwarg in tokenizer_params, (
            f"whisper.tokenizer.Tokenizer 必须接受 {kwarg} 构造 kwarg"
            "（CosyVoice tokenizer.py get_tokenizer 以该形态构造）"
        )


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


# ---------- M14-19：ModelScope 模型下载载荷契约（snapshot_download allow_patterns） ----------

PAYLOAD_MODULE = TOOLS_VOICE / "cosyvoice_model_payload.py"

#: ModelScope 仓库实测清单（2026-09-13 直读
#: GET /api/v1/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/repo/files?Recursive=true
#: ——19 文件 + 2 目录树）。测试以该快照为选择地面真值：上游布局变更时本
#: 常量即需同步更新（契约自证于实现时点的仓库形态）。
MODELSCOPE_REPO_INVENTORY: tuple[str, ...] = (
    ".gitattributes",
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/generation_config.json",
    "CosyVoice-BlankEN/merges.txt",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/tokenizer_config.json",
    "CosyVoice-BlankEN/vocab.json",
    "README.md",
    "asset/dingding.png",
    "campplus.onnx",
    "configuration.json",
    "cosyvoice3.yaml",
    "flow.decoder.estimator.fp32.onnx",
    "flow.pt",
    "hift.pt",
    "llm.pt",
    "llm.rl.pt",
    "speech_tokenizer_v3.batch.onnx",
    "speech_tokenizer_v3.onnx",
)


def _load_payload_module():
    spec = importlib.util.spec_from_file_location("cosyvoice_model_payload", PAYLOAD_MODULE)
    assert spec is not None and spec.loader is not None, (
        "缺少 tools/voice/cosyvoice_model_payload.py——M14-19 下载载荷契约模块"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def payload():
    return _load_payload_module()


def test_model_payload_required_files_allowed_and_selected(payload) -> None:
    """必需载荷全数放行：配置运行时（Fun-CosyVoice3-0.5B-2512）的每个必需
    文件都被 allow_patterns 覆盖，且在实测仓库清单上被过滤函数选中——
    过滤绝不能漏掉任何加载闭包必需文件（漏了 = bridge 启动加载失败）。"""
    import fnmatch

    patterns = payload.validate_runtime_payload(payload.MODEL_ID)
    required = payload.required_files_for(payload.MODEL_ID)
    assert required, "必需载荷集非空"
    for rel in required:
        assert any(fnmatch.fnmatch(rel, pattern) for pattern in patterns), rel
    selected = payload.filter_repo_files(list(MODELSCOPE_REPO_INVENTORY), list(patterns))
    for rel in required:
        assert rel in selected, rel


def test_model_payload_excludes_unreferenced_repo_assets(payload) -> None:
    """无关可选资产全数排除：llm.rl.pt（固定 commit 全仓零引用）/
    speech_tokenizer_v3.batch.onnx（仅 online_feature 训练单例路径引用）/
    flow.decoder.estimator.fp32.onnx（仅 load_trt=True 路径引用；bridge 默认
    False）/ asset/dingding.png / README.md / .gitattributes / configuration.json
    （运行时零引用）——按 2026-09-13 实测清单，19 文件恰选中 12 个必需件。"""
    patterns = payload.validate_runtime_payload(payload.MODEL_ID)
    selected = set(payload.filter_repo_files(list(MODELSCOPE_REPO_INVENTORY), list(patterns)))
    excluded = {
        "llm.rl.pt",
        "speech_tokenizer_v3.batch.onnx",
        "flow.decoder.estimator.fp32.onnx",
        "asset/dingding.png",
        "README.md",
        ".gitattributes",
        "configuration.json",
    }
    assert selected.isdisjoint(excluded)
    # 净效果锁定：恰为 12 个必需件（11 个 strict 必需 + generation_config.json
    # ——from_pretrained 标准布局件，242B 保持 BlankEN 目录完整）
    assert selected == set(payload.required_files_for(payload.MODEL_ID)) | {
        "CosyVoice-BlankEN/generation_config.json",
    }
    assert len(selected) == 12


def test_model_payload_blanken_selected_only_by_explicit_runtime(payload) -> None:
    """CosyVoice-BlankEN/model.safetensors：仅当运行时载荷显式选择时下载。
    配置运行时（CosyVoice3）显式选择它——固定 commit 074ca6d 源码实证：
    cosyvoice3.yaml 经 override qwen_pretrain_path=<model_dir>/CosyVoice-BlankEN
    即时构造 Qwen2Encoder（!new: 急切）→ Qwen2ForCausalLM.from_pretrained，
    2026-09-13 WSL 生产 venv 探针实证缺 model.safetensors 即 OSError
    「Error no file named ... model.safetensors ...」——属真正必需载荷。
    反面：不列 BlankEN 的运行时载荷（未来轻量运行时形态）经同一过滤函数
    自然排除之——排除是默认，选择必须显式。"""
    import fnmatch

    patterns = payload.validate_runtime_payload(payload.MODEL_ID)
    assert "CosyVoice-BlankEN/model.safetensors" in payload.required_files_for(payload.MODEL_ID)
    assert any(
        fnmatch.fnmatch("CosyVoice-BlankEN/model.safetensors", pattern)
        for pattern in patterns
    )
    minimal = payload.RuntimePayload(
        allow_patterns=(
            "cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt",
            "campplus.onnx", "speech_tokenizer_v3.onnx",
        ),
        required_files=(
            "cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt",
            "campplus.onnx", "speech_tokenizer_v3.onnx",
        ),
    )
    payload.validate_payload_contract(minimal)  # 自身契约合法（必需 ⊆ 白名单）
    selected = payload.filter_repo_files(
        list(MODELSCOPE_REPO_INVENTORY), list(minimal.allow_patterns),
    )
    assert "CosyVoice-BlankEN/model.safetensors" not in selected
    assert "CosyVoice-BlankEN/merges.txt" not in selected


def test_model_payload_filter_fails_closed(payload) -> None:
    """fail-closed 契约：未知 model_id / 空·畸形白名单 / 必需集为空 /
    必需文件未被白名单覆盖——一律 PayloadContractError（modelscope 1.20
    语义下空 allow_patterns = 不过滤 = 整仓下载，绝不能静默放行到调用）。"""
    # 未知运行时（防未定义载荷的整仓回退）
    with pytest.raises(payload.PayloadContractError):
        payload.runtime_payload_for("someone/other-model")
    with pytest.raises(payload.PayloadContractError):
        payload.validate_runtime_payload("someone/other-model")
    # 空 / 畸形白名单（含 str 形态——modelscope 接受 str 但契约拒绝歧义）
    for malformed in ([], (), None, "cosyvoice3.yaml", ["  "], ["llm.pt", 123], ["llm.pt", ""]):
        with pytest.raises(payload.PayloadContractError):
            payload.validate_allow_patterns(malformed)
    # 必需集为空：不是合法运行时载荷
    with pytest.raises(payload.PayloadContractError):
        payload.validate_payload_contract(
            payload.RuntimePayload(allow_patterns=("llm.pt",), required_files=()),
        )
    # 必需文件未被白名单覆盖（未来运行时 glob 笔误会漏下载必需件）
    with pytest.raises(payload.PayloadContractError):
        payload.validate_payload_contract(
            payload.RuntimePayload(allow_patterns=("*.yaml",), required_files=("llm.pt",)),
        )
    # 过滤函数自身同样 fail-closed（空/畸形白名单不得退化为全选）
    with pytest.raises(payload.PayloadContractError):
        payload.filter_repo_files(["llm.pt"], [])
    with pytest.raises(payload.PayloadContractError):
        payload.filter_repo_files(["llm.pt"], "llm.pt")


def test_model_payload_filter_mirrors_modelscope_fnmatch_semantics(payload) -> None:
    """过滤语义镜像 modelscope 1.20 _download_filter：对 repo 相对全路径
    fnmatch（树节点由 modelscope 侧先跳过；本镜像保序、纯函数）。"""
    files = [
        "llm.pt",
        "CosyVoice-BlankEN/llm.pt",
        "CosyVoice-BlankEN/model.safetensors",
        "sub/dir/flow.pt",
    ]
    # 精确路径 = 路径锚定（不误伤同名子路径文件）
    assert payload.filter_repo_files(files, ["llm.pt"]) == ["llm.pt"]
    assert payload.filter_repo_files(files, ["flow.pt"]) == []
    # 前缀目录通配可用（本契约未使用，语义须与 modelscope 一致以备未来运行时）
    assert payload.filter_repo_files(files, ["CosyVoice-BlankEN/*"]) == [
        "CosyVoice-BlankEN/llm.pt",
        "CosyVoice-BlankEN/model.safetensors",
    ]
    # 全路径通配 + 保序（fnmatch * 跨 / 匹配，与 modelscope 一致；不匹配 .safetensors）
    assert payload.filter_repo_files(files, ["*.pt"]) == [
        "llm.pt", "CosyVoice-BlankEN/llm.pt", "sub/dir/flow.pt",
    ]


def test_bootstrap_cosyvoice_download_filter_contract() -> None:
    """M14-19 bootstrap 下载过滤契约：模型下载段经 cosyvoice_model_payload
    契约模块 fail-closed 解析 allow_patterns 后传给 snapshot_download（白名单
    参数为 modelscope 1.20 受支持形态）；旧的无过滤整仓调用必须移除；下载后
    逐一存在性校验必需文件（上游布局变更即点名失败）；wetext 预热调用
    （独立小仓库、自有 payload 判定）保持原状不受影响。"""
    text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    for anchor in (
        "cosyvoice_model_payload.py",  # 契约模块（与测试同一真相源）
        "import cosyvoice_model_payload",  # 显式导入（非字符串拼接）
        "validate_runtime_payload",  # fail-closed 解析（未知/畸形/漏覆盖即拒）
        "required_files_for",  # 下载后必需件存在性校验
        "allow_patterns=list(allow_patterns)",  # 受支持参数形态
        "M14-19 载荷校验失败",  # 下载后校验失败点名（fail-closed 文案）
    ):
        assert anchor in text, anchor
    # 旧的无过滤调用（整仓下载根因形态）必须移除
    assert "snapshot_download(model_id, local_dir=local_dir)\n" not in text
    # 模型下载调用恰一行且带白名单参数（wetext 调用形如
    # snapshot_download("pengzhendong/wetext")，不匹配该断言面；头部注释里的
    # 历史形态叙述不算——只看生效行）
    model_download_lines = [
        line for line in text.splitlines()
        if "snapshot_download(model_id" in line and not line.lstrip().startswith("#")
    ]
    assert model_download_lines == [
        "snapshot_download(model_id, local_dir=local_dir, allow_patterns=list(allow_patterns))",
    ]
    # wetext 预热调用保持原状（独立小仓库，M14-03 契约不动）
    assert 'snapshot_download("pengzhendong/wetext")' in text
    # bash 侧四文件载荷判定保持不变（既有 fail-closed 面零弱化）
    for preserved in (
        "model_payload_ready()",
        "cosyvoice3.yaml", "flow.pt", "llm.pt", "hift.pt",
        "modelscope_cache_model_dir()",
    ):
        assert preserved in text, preserved


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
