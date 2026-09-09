"""M14-01 tools/voice 脚本契约测试：语法/结构锁定 + bridge 行为（不触网、不装 SDK）。

- bash -n：两个 bootstrap 脚本语法合法（不执行——bootstrap 只在 WSL 显式运行）；
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
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_VOICE = REPO_ROOT / "tools" / "voice"
BOOTSTRAP_FUNASR = TOOLS_VOICE / "bootstrap_funasr_wsl.sh"
BOOTSTRAP_COSYVOICE = TOOLS_VOICE / "bootstrap_cosyvoice_wsl.sh"
BRIDGE = TOOLS_VOICE / "cosyvoice_openai_bridge.py"
SMOKE = TOOLS_VOICE / "smoke_local_voice.py"

BASH = shutil.which("bash")

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")


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
@pytest.mark.parametrize("script", [BOOTSTRAP_FUNASR, BOOTSTRAP_COSYVOICE], ids=["funasr", "cosyvoice"])
def test_bootstrap_scripts_pass_bash_n(script: Path) -> None:
    result = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr


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
        "python3.11",
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
        "requirements.aios.txt",
        "python3.10",
        "submodule update --init --recursive",
        "snapshot_download",
    ):
        assert anchor in text, anchor
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
    """契约：python 缺失时的失败提示给出可执行的安装命令（fail-closed 且可操作）。"""
    funasr_text = BOOTSTRAP_FUNASR.read_text(encoding="utf-8")
    assert "apt-get install python3.11" in funasr_text or "apt-get install python3" in funasr_text
    cosyvoice_text = BOOTSTRAP_COSYVOICE.read_text(encoding="utf-8")
    assert "apt-get install python3.10" in cosyvoice_text
    assert "apt-get install sox" in cosyvoice_text
