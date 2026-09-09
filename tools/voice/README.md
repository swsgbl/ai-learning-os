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
endpoint 配好但引擎不可达 = **fail visibly**：providers 视图仍如实报
`local-*`（configured ≠ healthy，不静默换替身），请求以 502 固定脱敏文案
显式失败（有单测锁定，见 test_voice_local_providers.py）。
`VOICE_MODE=hybrid` 语义：ASR 本地（同上），TTS 仍按云端语义。

## Compose 容器接入（M14-01 修正轮）

compose 已透传本地引擎槽位（`infra/docker-compose.yml` api 服务）：
`AIOS_ASR_LOCAL_ENDPOINT` / `AIOS_ASR_LOCAL_MODEL` / `AIOS_ASR_LOCAL_API_KEY`
与 `AIOS_TTS_LOCAL_*` 同名插值（key 只经部署 secret 注入）。容器内地址用
`host.docker.internal`（api 服务已配 `extra_hosts: host.docker.internal:
host-gateway`，Docker Desktop 自带、Linux 引擎显式映射）：

```powershell
# 启动 compose 栈（引擎先在 WSL 起好）
$env:AIOS_VOICE_MODE="local"
$env:AIOS_ASR_LOCAL_ENDPOINT="http://host.docker.internal:8010/v1"
$env:AIOS_TTS_LOCAL_ENDPOINT="http://host.docker.internal:8011/v1"
docker compose -f infra/docker-compose.yml --profile local up -d
```

**网络路径可达性已实证**（2026-09-10，本机：WSL 2.7.13 NAT 模式 + Docker
Desktop，引擎 29.7.2）：容器 → `host.docker.internal:18010` → Windows 宿主
→ WSL2 localhost 转发 → WSL 内**只绑 127.0.0.1** 的 stdlib 探针服务 = HTTP
200。两个实测要点已固化进 `compose_voice_reachability.sh`：

1. WSL 内绑 `127.0.0.1` 的服务在本机 WSL 版本下同样被 localhostForwarding
   转发（无需绑 0.0.0.0——引擎保持 loopback-only）；
2. 经 `wsl -e` 起的后台进程必须 `setsid nohup`，否则 wsl.exe 会话退出会连带
   杀掉进程（首版脚本即栽在此）。

复跑验证（不下载模型、不启动引擎，stdlib 探针 + 本地已有镜像）：

```powershell
bash "tools/voice/compose_voice_reachability.sh"   # PowerShell 或 Git Bash
# 或 WSL 内：bash tools/voice/compose_voice_reachability.sh（docker 走 Docker
# Desktop WSL 集成；daemon 不可达时脚本会明确 FAIL 并指引回 Windows 侧）
```

## 部署步骤（Windows + WSL2）

前置（M14-01 修正轮后**不再假设 apt Python/sox**）：WSL2（GPU 对 WSL 可见，
`nvidia-smi` 可验证）+ WSL 内 `git`、`curl` 与 **uv**（`~/.local/bin/uv`；
没有就装：`curl -LsSf https://astral.sh/uv/install.sh | sh`）。Python 3.11/3.10
由 uv 隔离下载到 venv（无需 apt/sudo；uv 探测已含 `~/.local/bin`——非登录
bash 的 PATH 不含它，脚本自行处理）。**sox 不需要**：官方 `load_wav` 显式
`torchaudio.load(..., backend='soundfile')`（wheel 自带 libsndfile），bootstrap
对克隆仓库自带的 asset wav 做真实加载探针实证。磁盘预留：模型 + venv 约
10-15GB（最小运行时依赖比官方全量 requirements 显著更小）。网络受限时
clone/pip/uv 需代理（ModelScope 模型下载国内直连可用）。WSL2 默认开启
localhost 转发——Windows 侧直接访问 `127.0.0.1:8010/8011` 即可（冒烟脚本即
按此从 Windows 运行；若转发被关，冒烟可改在 WSL 内执行同一脚本）。

```powershell
# 0) WSL 内前置（一次性，按需）：uv 缺失才装；无其他 apt 依赖
wsl -e bash -c "command -v ~/.local/bin/uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh"

# 1) 起 ASR（funasr-server，CPU，127.0.0.1:8010；首次启动下载 SenseVoiceSmall）
#    仓库路径含空格——必须整体加引号（见下方「PowerShell 调 WSL 的引号规则」）
wsl -e bash -c "cd '/mnt/d/AI Learning OS/ai-learning-os-worktrees/m14-01-local-voice-deployment' && bash tools/voice/bootstrap_funasr_wsl.sh"

# 2) 另开一个终端起 TTS（CosyVoice + bridge，GPU，127.0.0.1:8011；
#    首次运行克隆官方仓库、装 cu128 torch + 最小运行时依赖、下载 Fun-CosyVoice3-0.5B-2512）
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
│   ├── venv/                         # Python 3.10 独立 venv（uv 管理；cu128 torch + 最小运行时依赖）
│   ├── requirements.full.txt         # 仅 COSYVOICE_FULL_REQUIREMENTS=1 时生成的完整清单
│   └── Fun-CosyVoice3-0.5B/          # Fun-CosyVoice3-0.5B-2512 模型目录
└── smoke/asr_sample_zh.wav           # 冒烟用官方中文样例（自动下载，可 ASR_SMOKE_AUDIO 覆盖）
```

仓库根 `.gitignore` 的 `artifacts/` 规则覆盖以上全部路径；脚本不写任何
secret（bridge 可选 key 只经 `COSYVOICE_BRIDGE_API_KEY` 环境变量注入进程，
不落盘、不回显）。

## 最小运行时依赖（M14-01 修正轮）

CosyVoice **只装推理路径真实需要的包**：`tools/voice/cosyvoice-runtime-
requirements.txt`（18 个包 + torch/torchaudio：推理闭包 16 + bridge 服务面
fastapi/uvicorn——复审修正补入，bridge 顶层 import fastapi 且 main() 调
uvicorn.run）由固定 commit `074ca6d` 的
导入闭包静态推导（起点 `cosyvoice.cli.cosyvoice.AutoModel`，覆盖 cli/utils/
llm/flow/hifigan/tokenizer/transformer 模块；Matcha-TTS 经子模块 sys.path）。
安装后 bootstrap **真实执行** `import cosyvoice.cli.cosyvoice` 验证（不下载
模型），缺失即点名失败。明确排除（源码证据见清单头注释）：deepspeed/
tensorrt/vllm（仅函数内可选导入）、gradio（webui 专用）、librosa/lightning/
matplotlib 等训练链包。若最小清单在实际部署中不足：`COSYVOICE_FULL_
REQUIREMENTS=1` 回退官方完整 requirements（剔除 torch pin），并把缺失包报回
仓库修正清单。

## 幂等与固定版本

- venv/克隆/模型已存在即复用或断点续传；`pip install` 满足即 no-op；uv venv
  已存在则直接复用（解释器版本不重装）。
- `funasr==1.4.15`（PyPI，已验证含 `funasr-server` CLI 与
  `/v1/audio/transcriptions`）；CosyVoice 官方仓库固定 commit
  `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`（2026-05-25 main HEAD，含
  Fun-CosyVoice3 支持）；两者可分别用 `FUNASR_VERSION` / `COSYVOICE_COMMIT`
  覆盖。torch 版本不 pin（CPU/cu128 轮子随官方索引更新），脚本结尾打印
  实际安装版本供留档。
- `checkout -f` 会丢弃克隆目录内的本地改动——工具目录本就不应手改。

## API 测试证据（可复现命令）

`services/api` 全量测试的准确命令/解释器/commit 由 `run_api_tests.ps1` 固化
（解释器解析顺序：`-Python` 参数 → `AIOS_TEST_PYTHON` → 工作树 `.venv` →
相对同级主检出 `..\..\ai-learning-os\.venv`（标准 worktree 布局探测，无盘符
假设——复审修正，不硬编码机器特定绝对路径）；记录数字所用解释器形态
Python 3.11.15 / pytest 9.1.1）：

```powershell
powershell -ExecutionPolicy Bypass -File tools\voice\run_api_tests.ps1
# 输出 git head + 解释器路径/版本后执行：python -m pytest services/api/tests -q
```

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
- 最小运行时依赖清单为**静态推导 + 安装时 import 验证**口径：`AutoModel`
  完整加载（模型文件 + YAML 实例化）在真实部署轮验证；不足时用
  `COSYVOICE_FULL_REQUIREMENTS=1` 回退并回报缺失包。
- compose 可达性实证是**网络路径**口径（stdlib 探针 + 本地镜像），容器内 API
  调真实引擎的端到端验证随部署轮进行。
- 流式 ASR / 流式 TTS 未实现、未宣称（funasr-server 的 WebSocket 流式与
  CosyVoice bi-streaming 均为后续单独立项）。
- bridge 为单 worker、单模型串行推理（本地单用户口径）；并发容量未测。
- 本地引擎不提供 SLA/开机自启（前台进程，由运维手动起停）。
