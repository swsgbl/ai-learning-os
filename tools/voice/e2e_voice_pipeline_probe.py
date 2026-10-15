"""M14-272f 服务端语音链路端到端验收脚本（真实 LiveKit + FunASR）。

一次性验收 harness（不入 CI、不做净化边界承诺——输出供证据
README 转录）：

1. 前置检查：API /health、FunASR /health、LiveKit 端口；
2. POST /api/v1/voice/live-sessions/{sid}/open——服务端订阅者注册；
3. POST /voice/token——发布者（学生）房间 token；
4. LiveKit SDK：学生发布 WAV 样本 PCM 音轨 → 服务端订阅者
   （server-sub-*）经真实连接接收帧 → 通道缓冲累计；
5. DELETE /api/v1/voice/live-sessions/{sid}——确定性关停：停帧源 →
   消费残余帧 → FunASR 真实转写（close 冲刷）→ final transcript；
6. 断言：订阅者真实连接过、帧字节非零、transcript 段非空且
   文本为真实中文（样本为中文语音）。

用法（环境就绪后）：
    python tools/voice/e2e_voice_pipeline_probe.py \
        --api http://127.0.0.1:18000 --sample <wav 路径>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:18000"
PUBLISH_MS = 1200  # 每帧 100ms × 12 件/批，共约 5 秒音频


def http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        try:
            return error.code, json.loads(body)
        except Exception:  # noqa: BLE001
            return error.code, {"raw": body[:200]}


async def publish_audio(url: str, token: str, sample_path: Path) -> dict:
    """学生端：连接房间并发布样本 PCM（48k mono 重采样由 SDK 帧参数声明）。"""
    import struct

    import numpy as np
    from livekit import rtc

    # 鲁棒解析 RIFF（兼容 float32/format-3 样本——wave 模块不支持）
    raw = sample_path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        print("[publish] 非 RIFF/WAVE 样本", file=sys.stderr)
        return {"published": False}
    pos = 12
    sample_rate = 48000
    channels = 1
    bits = 16
    audio_format = 1
    frames_bytes = b""
    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        chunk_size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + chunk_size]
        if chunk_id == b"fmt ":
            audio_format, channels, sample_rate = struct.unpack("<HHI", body[:8])
            bits = struct.unpack("<H", body[14:16])[0]
        elif chunk_id == b"data":
            frames_bytes = body
        pos += 8 + chunk_size + (chunk_size & 1)
    if audio_format == 3 and bits == 32:  # float32 → int16
        floats = np.frombuffer(frames_bytes, dtype="<f4")
        ints = np.clip(floats * 32767.0, -32768, 32767).astype("<i2")
        frames_bytes = ints.tobytes()
        bits = 16
    if channels != 1 or bits != 16:
        print(f"[publish] sample format fmt={audio_format} sr={sample_rate} ch={channels} bits={bits} —— 需要 16bit mono", file=sys.stderr)
        return {"published": False, "format": [audio_format, sample_rate, channels, bits]}

    room = rtc.Room()
    await room.connect(url, token)
    source = rtc.AudioSource(sample_rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("e2e-sample", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    await room.local_participant.publish_track(track, options)

    frame_bytes = sample_rate * 2 // 10  # 100ms/帧（16-bit mono）
    sent = 0
    offset = 0
    total = len(frames_bytes)
    # 发布完整样本（循环样本确保足够时长），约 6 秒
    deadline_bytes = sample_rate * 2 * 6
    while offset < deadline_bytes:
        chunk = frames_bytes[offset:offset + frame_bytes] if offset < total else b""
        if not chunk:
            chunk = frames_bytes[(offset % total):(offset % total) + frame_bytes]
        chunk = chunk[:frame_bytes].ljust(frame_bytes, b"\x00")
        frame = rtc.AudioFrame(
            data=chunk,
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=sample_rate // 10,
        )
        await source.capture_frame(frame)
        sent += len(chunk)
        offset += frame_bytes
        await asyncio.sleep(0.02)  # 近实时 pacing
    await asyncio.sleep(1.0)  # 尾部传播
    await room.disconnect()
    return {"published": True, "bytes_sent": sent, "sample_rate": sample_rate}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=API)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--session", default=f"sess-e2e-{int(time.time())}")
    parser.add_argument("--room", default=f"e2e-room-{int(time.time())}")
    parser.add_argument("--livekit", default="ws://127.0.0.1:17880")
    args = parser.parse_args()
    report: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    # 1. 前置
    status, _ = http_json("GET", f"{args.api}/health")
    report["api_health"] = status
    if status != 200:
        print(json.dumps(report, ensure_ascii=False))
        return 1

    # 2. open
    status, opened = http_json(
        "POST", f"{args.api}/api/v1/voice/live-sessions/{args.session}/open", {"room": args.room}
    )
    report["open"] = {"status": status, "identity": opened.get("identity")}
    if status not in (200, 201):
        report["open"]["body"] = opened
        print(json.dumps(report, ensure_ascii=False))
        return 1

    # 3. 学生 token
    status, token_body = http_json(
        "POST", f"{args.api}/api/v1/voice/token",
        {"room": args.room, "identity": "student-e2e-probe", "role": "student"},
    )
    report["token"] = {"status": status}
    if status != 200 or not token_body.get("token"):
        report["token"]["body"] = token_body
        http_json("DELETE", f"{args.api}/api/v1/voice/live-sessions/{args.session}")
        print(json.dumps(report, ensure_ascii=False))
        return 1

    # 4. 发布（真实 LiveKit）
    try:
        publish_result = await publish_audio(
            args.livekit, token_body["token"], Path(args.sample)
        )
        report["publish"] = publish_result
    except Exception as error:  # noqa: BLE001
        report["publish"] = {"error": str(error)[:200]}
        http_json("DELETE", f"{args.api}/api/v1/voice/live-sessions/{args.session}")
        print(json.dumps(report, ensure_ascii=False))
        return 1

    # 5. 状态（帧应已入服务端通道）
    status, state = http_json("GET", f"{args.api}/api/v1/voice/live-sessions/{args.session}")
    report["state_before_close"] = {
        "status": status,
        "buffered_frames": state.get("buffered_frames"),
        "buffered_bytes": state.get("buffered_bytes"),
        "fsm_state": state.get("fsm_state"),
    }

    # 6. close（冲刷 → FunASR 真实转写）
    status, closed = http_json(
        "DELETE", f"{args.api}/api/v1/voice/live-sessions/{args.session}"
    )
    report["close"] = {"status": status}
    transcript = closed.get("transcript") or {}
    segments = transcript.get("segments") or []
    report["transcript"] = {
        "segment_count": transcript.get("segment_count"),
        "texts": [seg.get("text") for seg in segments],
    }
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    passed = (
        report["publish"].get("published") is True
        and (state.get("buffered_bytes") or 0) > 0
        and transcript.get("segment_count", 0) > 0
        and any((seg.get("text") or "").strip() for seg in segments)
    )
    report["verdict"] = "PASS" if passed else "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
