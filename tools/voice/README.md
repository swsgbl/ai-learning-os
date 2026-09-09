# 本地真实语音引擎部署（M14-01 第一切片）

本目录是本地真实语音引擎的可复现 bootstrap 与冒烟工具。**本切片只交付
adapter + 脚本 + 文档；真实模型部署与端到端验证是另立的验收任务，在此之前
`production_ready=false`**（见 docs/PROJECT_STATUS.md M14-01 边界）。

## 架构（定版）

主 API **不嵌任何模型 SDK**，只通过 OpenAI 兼容 HTTP 调本机服务（全部只绑
`127.0.0.1`，密钥可选、绝不入库）：

| 环节 | provider 名 | 本机服务 | 端口 | 说明 |
|------|-------------|----------|------|------|
| ASR | `local-funasr` | funasr-server（SenseVoiceSmall，CPU） | 8010 | `/v1/audio/transcriptions`，无鉴权；适合录音片段转写；流式（Paraformer-zh-streaming / sherpa-onnx）另立项 |
| TTS | `local-cosyvoice` | CosyVoice 官方仓库 + `cosyvoice_openai_bridge.py` | 8011 | `/v1/audio/speech` 恒返回 WAV；`/health` ready 后 200 |

首发显式**不做**三引擎同卡常驻：ASR 走 CPU、TTS 用 GPU（cu128 torch），
Qwen3-14B 等 LLM 另行安排（RTX 5070 Ti 16GB 放不下全部常驻）。

## 主 API 接入（.env）

```dotenv
VOICE_MODE=local
ASR_LOCAL_ENDPOINT=http://127.0.0.1:8010/v1
ASR_LOCAL_MODEL=sensevoice
# ASR_LOCAL_API_KEY=            # 可选；本地服务默认无鉴权
TTS_LOCAL_ENDPOINT=http://127.0.0.1:8011/v1
TTS_LOCAL_MODEL=Fun-CosyVoice3-0.5B-2512
# TTS_LOCAL_API_KEY=            # 可选；与 bridge 的 COSYVOICE_BRIDGE_API_KEY 一致
```

endpoint 配好 → `GET /api/v1/voice/providers` 显示 `local-funasr` /
`local-cosyvoice`（`fallback=false`），`POST /transcribe`、`POST /synthesize`
的 `X-Voice-Provider` 响应头同理；**未配置则降级 `fake`/`tone` 并透出
`fallback=true`（响应头 `X-Voice-Fallback: 1`），不虚报已接真实引擎**。
`VOICE_MODE=hybrid` 语义：ASR 本地（同上），TTS 仍按云端语义。

注意：compose 容器内的 API 访问不到宿主 WSL 的 `127.0.0.1`（容器 loopback
≠ 宿主）——本切片的本地引擎部署形态是**宿主直跑 API**（PowerShell/WSL 起
`uvicorn`），compose 形态接入属后续工作（未验证，不宣称支持）。

## 部署步骤（Windows + WSL2）

前置：WSL2（GPU 对 WSL 可见，`nvidia-smi` 可验证）；Ubuntu 内需 `python3.11`、
`python3.10`（含 `-venv`）、`git`、`sox`（CosyVoice/torchaudio 依赖）；磁盘
预留：模型 + venv 约 15-20GB（Fun-CosyVoice3-0.5B 数 GB + cu128 torch 数 GB）。
网络受限时 clone/pip 需代理（ModelScope 模型下载国内直连可用）。WSL2 默认开启
localhost 转发——Windows 侧直接访问 `127.0.0.1:8010/8011` 即可（冒烟脚本即
按此从 Windows 运行；若转发被关，冒烟可改在 WSL 内执行同一脚本）。

```powershell
# 0) WSL 内前置（一次性，按需）
wsl -u root -e bash -c "apt-get update && apt-get install -y python3.10 python3.10-venv python3.11 python3.11-venv sox libsox-dev git"

# 1) 起 ASR（funasr-server，CPU，127.0.0.1:8010；首次启动下载 SenseVoiceSmall）
#    仓库路径含空格——必须整体加引号（见下方「PowerShell 调 WSL 的引号规则」）
wsl -e bash -c "cd '/mnt/d/AI Learning OS/ai-learning-os-worktrees/m14-01-local-voice-deployment' && bash tools/voice/bootstrap_funasr_wsl.sh"

# 2) 另开一个终端起 TTS（CosyVoice + bridge，GPU，127.0.0.1:8011；
#    首次运行克隆官方仓库、装 cu128 torch、下载 Fun-CosyVoice3-0.5B-2512）
wsl -e bash -c "cd '/mnt/d/AI Learning OS/ai-learning-os-worktrees/m14-01-local-voice-deployment' && bash tools/voice/bootstrap_cosyvoice_wsl.sh"

# 3) 等待就绪（另开终端；/health 200 = ready，加载中 503）
wsl -e bash -c "curl -s http://127.0.0.1:8010/health && echo && curl -s http://127.0.0.1:8011/health && echo"

# 4) 冒烟（Windows 侧，仓库 venv 的 python；探测 ASR+TTS，输出 latency/bytes/RIFF/文本与 PASS/FAIL）
python tools/voice/smoke_local_voice.py
```

### PowerShell 调 WSL 的引号规则（实测要点）

- `wsl -e bash -c "<命令>"` 的 `<命令>` 是**一个整体参数**：Windows 侧用双引号
  包住，内部的 Linux 路径再用单引号包住（仓库路径含空格，缺单引号必错）。
- 也可用 `wsl --cd '<仓库的 Windows 路径>'` 让 WSL 直接落在仓库目录后再执行
  `bash tools/voice/bootstrap_funasr_wsl.sh`，避免手写 `/mnt/d/...`。
- 脚本内部一律 `set -euo pipefail` + 全量引号，bash 侧不怕空格。
- 两个 bootstrap 前台运行（`exec` 起服务）；停止用 `Ctrl+C` 或关闭终端。

## 工件布局（全部 gitignored，绝不入库）

```text
artifacts/voice/                      # 根：VOICE_ARTIFACTS_DIR 可整体改址
├── funasr/
│   ├── venv/                         # Python 3.11 独立 venv（CPU torch + funasr）
│   └── modelscope-cache/             # SenseVoiceSmall 模型缓存（MODELSCOPE_CACHE）
├── cosyvoice/
│   ├── CosyVoice/                    # 官方仓库克隆（固定 commit 074ca6d，含子模块）
│   ├── venv/                         # Python 3.10 独立 venv（cu128 torch + 官方 requirements）
│   ├── requirements.aios.txt         # 官方 requirements 剔除 torch pin 后的安装清单
│   └── Fun-CosyVoice3-0.5B/          # Fun-CosyVoice3-0.5B-2512 模型目录
└── smoke/asr_sample_zh.wav           # 冒烟用官方中文样例（自动下载，可 ASR_SMOKE_AUDIO 覆盖）
```

仓库根 `.gitignore` 的 `artifacts/` 规则覆盖以上全部路径；脚本不写任何
secret（bridge 可选 key 只经 `COSYVOICE_BRIDGE_API_KEY` 环境变量注入进程，
不落盘、不回显）。

## 幂等与固定版本

- venv/克隆/模型已存在即复用或断点续传；`pip install` 满足即 no-op。
- `funasr==1.4.15`（PyPI，已验证含 `funasr-server` CLI 与
  `/v1/audio/transcriptions`）；CosyVoice 官方仓库固定 commit
  `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`（2026-05-25 main HEAD，含
  Fun-CosyVoice3 支持）；两者可分别用 `FUNASR_VERSION` / `COSYVOICE_COMMIT`
  覆盖。torch 版本不 pin（CPU/cu128 轮子随官方索引更新），脚本结尾打印
  实际安装版本供留档。
- `checkout -f` 会丢弃克隆目录内的本地改动——工具目录本就不应手改。

## 冒烟脚本

`smoke_local_voice.py` 走主 API 同款 adapter（`LocalFunAsrAsrProvider` /
`LocalCosyVoiceTtsProvider`，非 mock），逐项检查：

- ASR：`/health` → 转写非空（设 `ASR_SMOKE_EXPECTED_TEXT` 时 casefold 包含）
  → 输出 latency/置信度/文本结果；
- TTS：`/health` → 响应非空且 RIFF/WAV（请求 `response_format=wav`）→
  输出 latency/bytes。

默认下载官方中文样例（FunAudioLLM/CosyVoice 仓库 `asset/zero_shot_prompt.wav`）
到 `artifacts/voice/smoke/`；离线时用 `ASR_SMOKE_AUDIO=<本地 wav 路径>` 指定。
任一 FAIL → exit 1，绝不虚构「通过」。可覆盖项：`ASR_LOCAL_ENDPOINT` /
`TTS_LOCAL_ENDPOINT` / `ASR_LOCAL_MODEL` / `TTS_LOCAL_MODEL` /
`ASR_LOCAL_API_KEY` / `TTS_LOCAL_API_KEY` / `VOICE_SMOKE_TEXT`。

## 边界（未验证事项，勿视为已完成）

- **真实模型未在本切片部署/启动**（本机尚未执行 bootstrap、未下载模型）；
  脚本正确性由语法检查 + 契约测试锁定（`services/api/tests/test_voice_local_scripts.py`），
  实际部署验证是另立的验收任务——完成前 `production_ready=false`。
- 流式 ASR / 流式 TTS 未实现、未宣称（funasr-server 的 WebSocket 流式与
  CosyVoice bi-streaming 均为后续单独立项）。
- bridge 为单 worker、单模型串行推理（本地单用户口径）；并发容量未测。
- 本地引擎不提供 SLA/开机自启（前台进程，由运维手动起停）。
