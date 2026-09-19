#!/usr/bin/env bash
# M10-13 云语音真实端点冒烟：验证部署的 cloud-openai ASR/TTS 槽位真实可用（真实
# provider、真实网络调用——仅运维显式执行；本仓库测试套件不运行本脚本）。
# 需要（全部必填，缺一明确 FAIL 不虚报）：ASR_CLOUD_ENDPOINT、ASR_CLOUD_API_KEY、
#   ASR_CLOUD_MODEL、TTS_CLOUD_ENDPOINT、TTS_CLOUD_API_KEY、TTS_CLOUD_MODEL（OpenAI
#   兼容 /audio/transcriptions 与 /audio/speech 端点）与 ASR_SMOKE_AUDIO（真实短
#   WAV 文件路径，内容应是含语音的音频——key 只经环境变量注入，不回显）。
# 可选覆盖（窄口径，仅这三个）：VOICE_SMOKE_TEXT（TTS 合成文本，默认
#   "AI Learning OS cloud voice smoke"）；ASR_SMOKE_EXPECTED_TEXT（设置时要求其
#   casefold 文本出现在 casefold 转写中——用于锁定真实可懂转写而非任意输出）；
#   TTS_CLOUD_VOICE（云端 TTS 音色，默认 tongtong——BigModel 官方 glm-tts 预置
#   音色；置空回落该默认，不回显）。
# PASS 门槛：ASR 转写非空（设置了期望文本则 casefold 包含）且 TTS 响应非空并带
#   RIFF/WAV 头（请求 response_format=wav）——绝不虚构「通过」。
# 输出脱敏：只打印 provider/转写长度/TTS 字节数等摘要，不打印 endpoint、key、
#   鉴权头、音频路径或转写正文。
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { printf '[smoke-voice-cloud] FAIL: %s\n' "$*" >&2; exit 1; }
say() { printf '[smoke-voice-cloud] %s\n' "$*"; }

[ -n "${ASR_CLOUD_ENDPOINT:-}" ] || fail "ASR_CLOUD_ENDPOINT 未设置（真实端点冒烟需要真实 OpenAI 兼容转写端点——这不是可跳过的检查）"
[ -n "${ASR_CLOUD_API_KEY:-}" ] || fail "ASR_CLOUD_API_KEY 未设置（真实端点冒烟需要部署 key——key 只经环境变量注入，不回显）"
[ -n "${ASR_CLOUD_MODEL:-}" ] || fail "ASR_CLOUD_MODEL 未设置（真实端点冒烟需要转写模型名）"
[ -n "${TTS_CLOUD_ENDPOINT:-}" ] || fail "TTS_CLOUD_ENDPOINT 未设置（真实端点冒烟需要真实 OpenAI 兼容合成端点——这不是可跳过的检查）"
[ -n "${TTS_CLOUD_API_KEY:-}" ] || fail "TTS_CLOUD_API_KEY 未设置（真实端点冒烟需要部署 key——key 只经环境变量注入，不回显）"
[ -n "${TTS_CLOUD_MODEL:-}" ] || fail "TTS_CLOUD_MODEL 未设置（真实端点冒烟需要合成模型名）"
[ -n "${ASR_SMOKE_AUDIO:-}" ] || fail "ASR_SMOKE_AUDIO 未设置（ASR 探针需要真实短语音 WAV 文件路径）"
[ -f "$ASR_SMOKE_AUDIO" ] || fail "ASR_SMOKE_AUDIO 指向的文件不存在（需要真实短语音 WAV）——路径不回显"

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PYTHON" ] || PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || fail "找不到项目 venv python（用 PYTHON= 指定）"

"$PYTHON" - <<'PROBE_EOF'
import asyncio
import os
import sys

sys.path.insert(0, "services/api")

from app.voice.providers import CloudOpenAiAsrProvider, CloudOpenAiTtsProvider, ProviderUnavailable

asr = CloudOpenAiAsrProvider(
    os.environ["ASR_CLOUD_ENDPOINT"].strip(),
    os.environ["ASR_CLOUD_API_KEY"].strip(),
    os.environ["ASR_CLOUD_MODEL"].strip(),
)
tts = CloudOpenAiTtsProvider(
    os.environ["TTS_CLOUD_ENDPOINT"].strip(),
    os.environ["TTS_CLOUD_API_KEY"].strip(),
    os.environ["TTS_CLOUD_MODEL"].strip(),
    voice=(os.environ.get("TTS_CLOUD_VOICE") or "").strip() or "tongtong",
)
# 可选覆盖的窄口径默认值（不引入第四个开关）
text = os.environ.get("VOICE_SMOKE_TEXT") or "AI Learning OS cloud voice smoke"
expected = (os.environ.get("ASR_SMOKE_EXPECTED_TEXT") or "").strip()


def fail(message: str) -> None:
    print(f"[smoke-voice-cloud] FAIL: {message}", file=sys.stderr)
    sys.exit(1)


with open(os.environ["ASR_SMOKE_AUDIO"], "rb") as handle:
    audio = handle.read()  # 只在本进程内使用，路径与内容都不回显
if not audio:
    fail("ASR_SMOKE_AUDIO 文件为空（需要真实短语音）")

try:
    transcription = asyncio.run(asr.transcribe(audio))
except ProviderUnavailable as cause:
    # cause 文案为 provider 固定脱敏消息（不含 endpoint/key）
    fail(f"cloud ASR 不可用: {cause}")
if not transcription.text.strip():
    fail("ASR 转写为空（PASS 需要非空转写文本）")
if expected and expected.casefold() not in transcription.text.casefold():
    fail("ASR 转写未包含期望文本（ASR_SMOKE_EXPECTED_TEXT 的 casefold 包含比对失败）——转写正文不回显")

try:
    synthesis = asyncio.run(tts.synthesize(text))
except ProviderUnavailable as cause:
    fail(f"cloud TTS 不可用: {cause}")
if not synthesis.audio:
    fail("TTS 响应音频为空")
if synthesis.audio[:4] != b"RIFF" or synthesis.audio[8:12] != b"WAVE":
    fail("TTS 响应不带 RIFF/WAV 头（请求了 response_format=wav）")

print(
    f"[smoke-voice-cloud] asr provider={transcription.provider} "
    f"transcript_len={len(transcription.text)} latency_ms={transcription.latency_ms}"
)
print(
    f"[smoke-voice-cloud] tts provider={synthesis.provider} "
    f"audio_bytes={len(synthesis.audio)} latency_ms={synthesis.latency_ms}"
)
if expected:
    print("[smoke-voice-cloud] expected text check: included (casefold)")
PROBE_EOF

say "ALL VOICE CLOUD SMOKE CHECKS PASSED"
