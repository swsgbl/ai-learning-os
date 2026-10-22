#!/usr/bin/env python3
"""M17-008 语音链路延迟度量（升级方案 Phase 8 自动化部分）。

经生产/本地 API 的权威端点度量（服务端自动埋点链路）：
- TTS：POST /voice/synthesize N 次——客户端观测往返时延（含网络）；
- ASR：用 TTS 产物 WAV POST /voice/transcribe N 次——时延 + 识别文本
  与原文的相似度（编辑距离比，粗粒度识别质量信号）。

诚实边界（Phase 8 验收边界，不虚报覆盖）：
- 本脚本只覆盖「单链路」度量（本地引擎经 API 全链）；弱网/NAT/设备
  切换/打断重连属运维显式分层验收，不在本脚本范围；
- N 为小样本（缺省 5）——p50/p95 是采样估计，不是承诺 SLO。

用法：
  python scripts/measure_voice_latency.py \
      --base https://ndtool.cn/aios/api/v1 --username U --password P \
      --samples 5 [--out voice_latency.json]

exit 0 = 采样完成（指标本身不设 pass/fail 阈值——记录型工具）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path


def _login(base: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{base}/auth/login", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def _pct(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"samples": 0}
    return {
        "samples": len(values),
        "mean_ms": round(statistics.mean(values)),
        "p50_ms": round(_pct(values, 50)),
        "p95_ms": round(_pct(values, 95)),
        "min_ms": round(min(values)),
        "max_ms": round(max(values)),
    }


def _similarity(a: str, b: str) -> float:
    """编辑距离相似度（0-1，粗粒度识别质量信号）。"""
    if a == b:
        return 1.0
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    if not shorter:
        return 1.0
    prev = list(range(len(shorter) + 1))
    for i, ca in enumerate(longer, 1):
        cur = [i]
        for j, cb in enumerate(shorter, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / len(longer)


def measure_tts(base: str, token: str, samples: int) -> tuple[list[float], bytes, str]:
    """TTS N 次：返回 (时延秒列表, 最后一次 WAV, 最后一次文本)。"""
    timings: list[float] = []
    wav = b""
    text = ""
    for index in range(samples):
        text = f"语音链路度量第 {index + 1} 次：数据分析练习。"
        body = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            f"{base}/voice/synthesize",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=180) as resp:
            wav = resp.read()
        timings.append(time.perf_counter() - start)
    return timings, wav, text


def measure_asr(base: str, token: str, wav: bytes, samples: int, expected: str) -> tuple[list[float], list[float]]:
    """ASR N 次（同一段 WAV）：返回 (时延秒列表, 相似度列表——与合成原文比对）。"""
    timings: list[float] = []
    similarities: list[float] = []
    for _ in range(samples):
        boundary = "----aiosmeasure"
        parts = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="probe.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode() + wav + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{base}/voice/transcribe",
            data=parts,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {token}",
            },
        )
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=180) as resp:
            out = json.load(resp)
        timings.append(time.perf_counter() - start)
        text = out.get("text") or out.get("transcript") or ""
        similarities.append(_similarity(expected, text.strip()))
    return timings, similarities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="API base（如 https://ndtool.cn/aios/api/v1）")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--out", default=None, help="可选 JSON 输出路径")
    args = parser.parse_args()

    token = _login(args.base, args.username, args.password)
    print(f"采样中（{args.samples} 次/环节，经 {args.base}）…")
    tts_ms, wav, tts_text = measure_tts(args.base, token, args.samples)
    print(f"  TTS 完成（WAV {len(wav)} 字节）")
    asr_ms, sims = measure_asr(args.base, token, wav, args.samples, expected=tts_text)
    print(f"  ASR 完成（与合成原文相似度均值 {statistics.mean(sims):.2f}）")

    report = {
        "schema": "aios-voice-latency/1",
        "base": args.base,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope_note": "单链路度量（本地引擎经 API 全链）；弱网/NAT/设备切换不在范围（运维显式分层验收）",
        "tts_roundtrip": _stats([s * 1000 for s in tts_ms]),
        "asr_roundtrip": _stats([s * 1000 for s in asr_ms]),
        "asr_similarity": {
            "samples": len(sims),
            "mean": round(statistics.mean(sims), 3) if sims else 0,
            "min": round(min(sims), 3) if sims else 0,
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
        print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
