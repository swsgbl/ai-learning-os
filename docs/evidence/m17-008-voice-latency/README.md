# M17-008 语音链路延迟度量（单链路，Phase 8 自动化部分）

日期：2026-10-22 · 采样：`services/api/scripts/measure_voice_latency.py`
（--base https://ndtool.cn/aios/api/v1 --samples 5，qa 账号，经生产 frp 链路）

## 指标（voice_latency.json，客户端观测往返——含网络）

| 环节 | p50 | p95 | 说明 |
|---|---|---|---|
| TTS（/voice/synthesize） | 3171 ms | 3897 ms | 含 frp 隧路网络 + cosyvoice 合成 |
| ASR（/voice/transcribe） | 1087 ms | 1309 ms | 与既有实测（0.9s 量级）一致 |
| ASR 识别相似度 | 0.737 | — | 与合成原文编辑距离比（句含数字，数字识别误差合理） |

## 诚实边界（不虚报覆盖）

- 本度量只覆盖**单链路**（本地引擎经 API 全链、单设备稳定网络）；
- **弱网 / NAT 穿透 / 设备切换 / 打断重连**不在本脚本范围——属运维
  显式分层验收（升级方案 Prompt 8 边界），LEARNING_EVIDENCE_STATUS
  该项保持 not_executed；
- N=5 小样本——p50/p95 是采样估计，不是 SLO 承诺；
- TTS p50 3.2s 含跨域网络（frp 隧路）——局域网/本机部署会显著更低
  （既有实测 0.9s 为 ASR 本机口径）。
