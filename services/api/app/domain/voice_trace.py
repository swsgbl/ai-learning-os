"""M4-09 Latency tracing：语音链路各阶段耗时观测（05 文档验收「各阶段耗时可观测」）。

- 阶段集 = VAD / ASR / 意图 / FSM / LLM / TTS / 首音频（backlog M4-09 原文）；
- 纯服务端可自动埋点的是 asr（transcribe）、tts（synthesize）、intent（parse）、
  fsm（命令应用）；vad / llm / first_audio 发生在客户端音频流与上游模型，
  由客户端经 POST /voice/trace 上报——服务端不虚报测不到的环节；
- 观测只记录不判定：trace 数据不影响任何业务状态（与 ADR 36/39 同款边界），
  客户端上报值也不被信任用于业务决策，仅供可观测性；
- summarize_spans 纯函数：同输入恒同输出（与 ADR 27/29/30/31/34/38/39 同款幂等语义）。
"""
from __future__ import annotations

import math

TRACE_STAGES: tuple[str, ...] = (
    "vad",
    "asr",
    "intent",
    "fsm",
    "llm",
    "tts",
    "first_audio",
)

# 客户端上报耗时上限（10 分钟：防滥用，正常语音环节不会超过）
MAX_DURATION_MS = 600_000


def summarize_spans(spans: list[dict]) -> dict[str, dict]:
    """按阶段聚合耗时样本：count/avg/p50/p95/max。

    纯函数：排序后取分位（最近邻索引法），同输入恒同输出。
    空阶段缺省 count=0、其余字段 0.0——不虚报无数据的阶段。
    """
    by_stage: dict[str, list[int]] = {stage: [] for stage in TRACE_STAGES}
    for span in spans:
        stage = span["stage"]
        if stage in by_stage:
            by_stage[stage].append(int(span["duration_ms"]))

    summary: dict[str, dict] = {}
    for stage in TRACE_STAGES:
        samples = sorted(by_stage[stage])
        count = len(samples)
        if count == 0:
            summary[stage] = {"count": 0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
            continue
        summary[stage] = {
            "count": count,
            "avg_ms": sum(samples) / count,
            "p50_ms": float(_percentile(samples, 0.50)),
            "p95_ms": float(_percentile(samples, 0.95)),
            "max_ms": float(samples[-1]),
        }
    return summary


def _percentile(sorted_samples: list[int], ratio: float) -> int:
    """最近邻分位（nearest-rank）：索引 = ceil(p * n) - 1，越界收敛到 [0, n-1]。"""
    n = len(sorted_samples)
    index = math.ceil(ratio * n) - 1
    return sorted_samples[max(0, min(n - 1, index))]
