#!/usr/bin/env python
"""M14-01 本地真实语音引擎冒烟：探测 FunASR ASR 与 CosyVoice bridge TTS（真实 HTTP）。

用途（仅运维/开发显式执行，本仓库测试套件不运行本脚本）：确认本地部署的
local-funasr / local-cosyvoice 引擎真实可用——走主 API 同款 adapter
（app.voice.providers 的 LocalFunAsrAsrProvider / LocalCosyVoiceTtsProvider），
不做任何 mock，绝不虚构「通过」。

输入（全部经环境变量，均可选覆盖）：
- ASR_LOCAL_ENDPOINT   默认 http://127.0.0.1:8010/v1（bootstrap_funasr_wsl.sh）
- TTS_LOCAL_ENDPOINT   默认 http://127.0.0.1:8011/v1（bootstrap_cosyvoice_wsl.sh）
- ASR_LOCAL_MODEL / TTS_LOCAL_MODEL / ASR_LOCAL_API_KEY / TTS_LOCAL_API_KEY
  （与主 API 同名设置；key 可选——本地服务默认无鉴权，注入后不回显）
- ASR_SMOKE_AUDIO      真实短语音 WAV 路径；缺省时下载官方样例到
                       gitignored artifacts（FunAudioLLM/CosyVoice 仓库 asset，
                       中文短句），已存在则复用（离线时请显式指定路径）
- ASR_SMOKE_EXPECTED_TEXT  设置时要求其 casefold 出现在 casefold 转写中
- VOICE_SMOKE_TEXT     TTS 合成文本（默认一句中文）

输出：每步 latency_ms / bytes / RIFF/WAV / 文本结果与 PASS/FAIL；任一 FAIL
则 exit 1。输出不包含任何 key；endpoint 为本机 loopback，可打印。
loopback 健康探测与 provider 调用均绕过系统/环境代理（trust_env=False，
M14-106——Windows 注册表/环境系统代理不得劫持 127.0.0.1 探测造成假失败）。

用法（Windows/PowerShell，先启动两个 bootstrap 脚本的 WSL 服务）：
  python tools/voice/smoke_local_voice.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.voice.providers import (
    LocalCosyVoiceTtsProvider,
    LocalFunAsrAsrProvider,
    ProviderUnavailable,
)

TAG = "[smoke-local-voice]"
#: 官方中文样例（FunAudioLLM/CosyVoice 仓库 asset；与 TTS zero-shot prompt 同源）
DEFAULT_SAMPLE_URL = "https://raw.githubusercontent.com/FunAudioLLM/CosyVoice/main/asset/zero_shot_prompt.wav"
DEFAULT_SMOKE_TEXT = "AI Learning OS 本地语音链路冒烟检查。"


def _say(message: str) -> None:
    print(f"{TAG} {message}", flush=True)


def _fail(message: str) -> None:
    print(f"{TAG} FAIL: {message}", file=sys.stderr, flush=True)


def _health_url(endpoint: str) -> str:
    """{endpoint}/health——endpoint 含 /v1 前缀时挂在服务根上。"""
    base = endpoint.rstrip("/")
    base = base.removesuffix("/v1")
    return f"{base}/health"


def _prepare_sample_audio() -> Path | None:
    """ASR 样例：ASR_SMOKE_AUDIO 显式路径优先；否则下载官方样例到 gitignored artifacts。"""
    explicit = os.environ.get("ASR_SMOKE_AUDIO", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            _fail(f"ASR_SMOKE_AUDIO 指向的文件不存在: {explicit}")
            return None
        return path
    target = REPO_ROOT / "artifacts" / "voice" / "smoke" / "asr_sample_zh.wav"
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    _say(f"下载官方中文样例到 gitignored artifacts（{DEFAULT_SAMPLE_URL.rsplit('/', 1)[-1]}）")
    try:
        with urllib.request.urlopen(DEFAULT_SAMPLE_URL, timeout=60) as response:
            data = response.read()
    except OSError as cause:
        _fail(f"样例下载失败（离线时请用 ASR_SMOKE_AUDIO= 指定本地 WAV）: {type(cause).__name__}")
        return None
    if not data:
        _fail("样例下载内容为空")
        return None
    target.write_bytes(data)
    return target


def _probe_health(endpoint: str, label: str) -> bool:
    import httpx

    try:
        # M14-106：loopback 健康探测绕过系统/环境代理——Windows 注册表代理会连
        # 127.0.0.1 一起劫持，造成引擎在线却探测不可达的假失败；与本地 provider
        # 的 trust_env=False 同口径，10s 超时契约不变。
        response = httpx.get(_health_url(endpoint), timeout=10.0, trust_env=False)
    except httpx.HTTPError:
        _say(f"{label} health: 不可达（服务未启动？）")
        return False
    _say(f"{label} health: HTTP {response.status_code}")
    return response.status_code == 200


def probe_asr(audio: bytes) -> bool:
    endpoint = os.environ.get("ASR_LOCAL_ENDPOINT", "http://127.0.0.1:8010/v1").strip()
    model = os.environ.get("ASR_LOCAL_MODEL", "sensevoice").strip() or "sensevoice"
    api_key = os.environ.get("ASR_LOCAL_API_KEY", "").strip()
    if not _probe_health(endpoint, "asr"):
        return False
    provider = LocalFunAsrAsrProvider(endpoint, api_key, model)
    try:
        result = asyncio.run(provider.transcribe(audio))
    except ProviderUnavailable as cause:
        # cause 文案为 provider 固定脱敏消息（不含 endpoint/key）
        _fail(f"本地 ASR 不可用: {cause}")
        return False
    _say(f"asr provider={result.provider} latency_ms={result.latency_ms} "
         f"confidence={result.confidence:.3f} transcript_bytes={len(result.text.encode('utf-8'))}")
    _say(f"asr 文本结果: {result.text!r}")
    if not result.text.strip():
        _fail("ASR 转写为空（PASS 需要非空转写文本）")
        return False
    expected = os.environ.get("ASR_SMOKE_EXPECTED_TEXT", "").strip()
    if expected and expected.casefold() not in result.text.casefold():
        _fail("ASR 转写未包含期望文本（ASR_SMOKE_EXPECTED_TEXT 的 casefold 包含比对失败）")
        return False
    return True


def probe_tts() -> bool:
    endpoint = os.environ.get("TTS_LOCAL_ENDPOINT", "http://127.0.0.1:8011/v1").strip()
    model = os.environ.get("TTS_LOCAL_MODEL", "Fun-CosyVoice3-0.5B-2512").strip() or "Fun-CosyVoice3-0.5B-2512"
    api_key = os.environ.get("TTS_LOCAL_API_KEY", "").strip()
    text = os.environ.get("VOICE_SMOKE_TEXT", "").strip() or DEFAULT_SMOKE_TEXT
    if not _probe_health(endpoint, "tts"):
        return False
    provider = LocalCosyVoiceTtsProvider(endpoint, api_key, model)
    try:
        result = asyncio.run(provider.synthesize(text))
    except ProviderUnavailable as cause:
        _fail(f"本地 TTS 不可用: {cause}")
        return False
    is_riff = result.audio[:4] == b"RIFF" and result.audio[8:12] == b"WAVE"
    _say(f"tts provider={result.provider} latency_ms={result.latency_ms} "
         f"audio_bytes={len(result.audio)} riff_wav={'yes' if is_riff else 'NO'}")
    if not result.audio:
        _fail("TTS 响应音频为空")
        return False
    if not is_riff:
        _fail("TTS 响应不带 RIFF/WAV 头（请求了 response_format=wav）")
        return False
    return True


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # pragma: no cover —— 非 Windows 控制台
            pass
    ok_asr = ok_tts = False
    sample = _prepare_sample_audio()
    if sample is None:
        _fail("ASR 样例不可用（ASR 探针无法执行）")
    else:
        audio = sample.read_bytes()
        if not audio:
            _fail("ASR 样例文件为空")
        else:
            _say(f"asr 样例: {sample.name} ({len(audio)} bytes)")
            ok_asr = probe_asr(audio)
    ok_tts = probe_tts()
    _say(f"asr={'PASS' if ok_asr else 'FAIL'} tts={'PASS' if ok_tts else 'FAIL'}")
    if not (ok_asr and ok_tts):
        _fail("本地语音引擎冒烟未全部通过（见上方 FAIL 明细）")
        return 1
    print(f"{TAG} ALL LOCAL VOICE SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
