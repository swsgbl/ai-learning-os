# 本地真实语音引擎部署（M14-01 第一切片）

本目录是本地真实语音引擎的可复现 bootstrap 与冒烟工具。M14-01 切片只交付
adapter + 脚本 + 文档；**M14-02 轮（2026-09-10）已在本机完成真实部署与端到端
冒烟（双引擎 /health 200 + ASR/TTS 冒烟全过），但 `production_ready=false`
不变**——单机验证口径与剩余边界见下方「边界」节（docs/PROJECT_STATUS.md 同步）。

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
Desktop 29.7.2）：容器 → `host.docker.internal` → Windows 宿主 → WSL2
localhost 转发 → WSL 内**只绑 127.0.0.1** 的服务 = HTTP 200。先以 stdlib
探针（`:18010`）实证路径，后升级到**真实引擎端到端**：API 容器经
`host.docker.internal` 直探 8010/8011 `/health` 双 200，隔离 compose 项目
（`aios-m14-02-voice-e2e`，`AIOS_WEB_PORT=13000`）以真实鉴权跑通
providers / synthesize / transcribe 全链（见下方「边界」首条）。两个实测
要点已固化进 `compose_voice_reachability.sh`：

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
#    在仓库根（任意检出/worktree 均可）执行；wsl --cd . 让 WSL 直接落在当前
#    检出根，不再手写机器特定的 /mnt/<盘>/... 绝对路径
wsl --cd . bash -c "bash tools/voice/bootstrap_funasr_wsl.sh"

# 2) 另开一个终端起 TTS（CosyVoice + bridge，GPU，127.0.0.1:8011；
#    首次运行克隆官方仓库、装 cu128 torch + 最小运行时依赖、下载 Fun-CosyVoice3-0.5B-2512）
wsl --cd . bash -c "bash tools/voice/bootstrap_cosyvoice_wsl.sh"

#    （等价写法：wsl -e bash -c "cd '<仓库的 /mnt 路径>' && bash tools/voice/…"——
#     仓库路径含空格时必须整体加引号，见下方「PowerShell 调 WSL 的引号规则」）

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
- 两个 bootstrap 前台运行（`exec` 起服务）；停止用 `Ctrl+C` 或关闭终端
  ——或改用下方 M14-04 的受控生命周期管理（后台 setsid + manifest 精确回收）。

## 工件布局（全部 gitignored，绝不入库）

```text
artifacts/voice/                      # 根：VOICE_ARTIFACTS_DIR 可整体改址
├── funasr/
│   ├── venv/                         # Python 3.11 独立 venv（CPU torch + funasr）
│   ├── modelscope-cache/             # SenseVoiceSmall 模型缓存（MODELSCOPE_CACHE）
│   └── service/                      # M14-04 受控生命周期：manifest.json / launcher.sh /
│                                     #   service.log / spawn.pid / workdir.txt / control.lock
├── cosyvoice/
│   ├── CosyVoice/                    # 官方仓库克隆（固定 commit 074ca6d，含子模块）
│   ├── venv/                         # Python 3.10 独立 venv（uv 管理；cu128 torch + 最小运行时依赖）
│   ├── requirements.full.txt         # 仅 COSYVOICE_FULL_REQUIREMENTS=1 时生成的完整清单
│   ├── bootstrap_cosyvoice_wsl.snapshot.sh  # 运行期自保护快照（每次启动原子替换）
│   ├── Fun-CosyVoice3-0.5B/          # Fun-CosyVoice3-0.5B-2512 模型目录
│   └── service/                      # M14-04 受控生命周期（同 funasr/service/ 布局）
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

**清单装完后的终局 CUDA 闭包恢复 + pip check 门禁 + 运行期一致性探针
（M14-16/M14-17 生产实证修复）**：清单分支（最小/官方完整回退）按 PyPI
解析时，官方 pin 链（`lightning==2.2.4` 等）会把已装 torch 降级（生产实证
降到 2.3.1）且**连带降级其 CUDA 闭包**，而 cu128 轮 METADATA 不声明 torch
约束——混合 ABI 状态下 `pip check` 报「No broken requirements found」，
导入才在 torchaudio `_extension` 崩（`OSError: ... undefined symbol:
aoti_torch_abi_version`）。M14-16 曾以 `--no-deps` 只回写三件套主轮，
2026-09-13 生产复验证伪：主轮虽已 cu128，闭包仍是被连带降级的 12.1 系列
（`pip check` 实证：nvidia-cudnn-cu12 8.9.2.26 需 ==9.19.0.56、
nvidia-nccl-cu12 2.20.5 需 ==2.28.9、triton 2.3.1 需 ==3.6.0），
`import torch` 失败缺 `libcudnn.so.9`。M14-17 终局改为**完整依赖解析**
（同一 cu128 index，无 `--no-deps`）：三件套精确 pin + `torch 2.11.0+cu128`
METADATA（Linux 段，2026-09-13 自生产 venv 真实 wheel metadata 导出，非
记忆推导）声明的闭包成员——`cuda-toolkit[cublas,cudart,cufft,cufile,cupti,
curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==12.8.1`（12.8 系列 nvidia
runtime 的 meta-extras）、`cuda-bindings>=12.9.4,<13`、
`nvidia-cudnn-cu12==9.19.0.56`、`nvidia-nccl-cu12==2.28.9`、
`nvidia-cusparselt-cu12==0.7.1`、`nvidia-nvshmem-cu12==3.4.5`、
`triton==3.6.0`。精确 pin 使 PyPI 清单分支无从再降级任一轮；成员 pin 不满足
即强制解析安装（**即便三件套 pin 已满足，被降级的闭包仍会被修复**——可
自愈 2026-09-13 的存量破损态），全部满足即 no-op（不 force-reinstall）。
闭包成员同 index 可解析（生产 venv 初始安装实证：cuda-toolkit 12.8.1 /
cuda-bindings 12.9.7 / nvidia-nvjitlink 12.8.93 等均来自该 index 同一
解析）。其后 `pip check` **附加门禁**（fail-closed，文案指向「CUDA closure
未恢复」）+ 运行期一致性探针（**CUDA closure 契约表**逐项锁定 18 个闭包
成员：METADATA `==` pin 项精确相等、cuda-toolkit extras 的 11 个 nvidia
runtime 按通配前缀匹配、cuda-bindings 范围校验 + torch/torchaudio 基础
版本一致 + 双 `+cu128` 同源 + torchcodec 可导入，任一不满足即 FAIL 点名），
其后才是 CosyVoice import/WAV 探针——顺序由契约测试锁定
（`test_bootstrap_torch_reconciliation_order_contract` /
`test_bootstrap_cuda_closure_contract`）。注意 **`pip check` 只是附加门禁，
不能替代真实 import/运行探针**：它看不见混合 ABI（M14-16 实证报
「No broken requirements」），也看不见 extras 门控的 12.8 系列 nvidia
runtime 错配（2026-09-13 生产 venv 实证：cuda-toolkit 12.8.1 extras 要求
nvidia-cublas-cu12==12.8.4.1.* 而实为 12.1.3.1，`pip check` 只报三个
`==` pin）。

**M14-18 openai-whisper triton 元数据冲突修复（2026-09-13 生产实证）**：
M14-17 的终局 CUDA 闭包恢复本身已成功（service.log 实证 torch
`2.11.0+cu128` / triton `3.6.0` 及全部闭包成员就位），但最小运行时清单的
`openai-whisper==20231117` METADATA 声明 `triton<3,>=2.0.0`（无环境标记）
——与闭包的 `triton==3.6.0` 冲突，`pip check` 附加门禁被「openai-whisper
20231117 has requirement triton<3,>=2.0.0, but you have triton 3.6.0」卡死
FAIL；且清单分支装 20231117 时该约束会触发 pip 回溯把 torch 一路降级
（实证 2.14.0→…→2.3.1）再连带降级 CUDA 闭包。修法 = 最小清单升级
`openai-whisper==20250625`（METADATA 声明 `triton>=2`，x86_64/linux 环境
标记、无上界，与 cu128 闭包共存；运行时依赖集合与 20231117 完全一致——
more-itertools/numba/numpy/tiktoken/torch/tqdm，torch 无版本约束，不替换
torch/CUDA 包；supervisor 只读 dry-run 确认 + 2026-09-13 PyPI sdist
PKG-INFO 直读复核）。**为什么升级而不是绕过门禁**：冲突根因在第三方依赖
元数据而非门禁本身——`--no-deps` 装清单、强制降级 triton（破坏 torch
metadata 闭包，M14-17 已证 `import torch` 失败）或改写已装 dist-info 都
只是把元数据冲突藏进运行期；升级 pin 让门禁真实通过。CosyVoice 固定
commit `074ca6d` 实际用到的两个 whisper API（`whisper.log_mel_spectrogram
(audio, n_mels=128)`（cli/frontend.py:98、dataset/processor.py:196）与
`whisper.tokenizer.Tokenizer(encoding=…, num_languages=…, language=…,
task=…)`（tokenizer/tokenizer.py:7,236））在两版间**源码级不变**——
20231117（生产 venv 已装源码）与 20250625（PyPI sdist）逐字 diff：tokenizer.py
无差异、log_mel_spectrogram 仅 docstring 更新且明确支持 n_mels=128；生产
venv 只读探针（import + 签名）实证通过。官方 requirements.txt 在固定
commit 仍 pin 20231117——本清单**有意偏离**官方 pin（沿用官方 pin 的策略
让位于 CUDA 闭包一致性）。注意 20250625 在 PyPI 仅 sdist（无 wheel），pip
从源码构建（纯 Python 包）。契约测试：`test_openai_whisper_pin_bump_
contract`（pin + 升级依据锚点）、`test_openai_whisper_triton_no_bypass_
contract`（全生效行禁 `--no-deps`/`--force-reinstall`/`--ignore-installed`
/triton 降级 pin/dist-info 改写 + pip check 门禁保持 `if !` fail-closed
形态）、`test_whisper_api_compat_contract_when_installed`（whisper 可导入
环境的两 API 签名回归，canonical venv 无 whisper 时显式 skip）。

## 幂等与固定版本

- venv/克隆/模型已存在即复用或断点续传；`pip install` 满足即 no-op；uv venv
  已存在则直接复用（解释器版本不重装）。
- **CosyVoice bootstrap 运行期自保护（M14-02 实证修复）**：bash 按字节偏移
  增量解析脚本——模型下载可运行数小时，期间仓库内并行会话的 git 操作改写
  `bootstrap_cosyvoice_wsl.sh` 会让 bash 在旧偏移上解析新内容，产生与真实
  语法无关的伪错误（生产实证「line 169: syntax error near unexpected token
  `)`」，改写前后两版本各自 `bash -n` 均通过）。bootstrap 启动即把自身原子
  快照进 `artifacts/voice/cosyvoice/bootstrap_cosyvoice_wsl.snapshot.sh` 并
  exec 快照副本——运行期改写仓库内脚本不再影响已启动的 bootstrap。
- **模型就位判定以载荷文件为准**（cosyvoice3.yaml / flow.pt / llm.pt /
  hift.pt 齐备），不以「目录非空」为准（ModelScope 断点残留 `._____temp/`
  会让空壳目录非空）；`MODEL_DIR` 载荷不完整时自动回落 ModelScope 缓存
  （`hub/models/<org>/<name>/snapshots/<id>/` 新版或 `models/<org>/<name>/`
  旧版布局，根可用 `MODELSCOPE_CACHE` 改址），命中即采用、不重新下载；
  `COSYVOICE_SKIP_DOWNLOAD=1` 下仍无可用模型则显式 FAIL（不静默启动空
  bridge）。模型已就位、只重启 bridge 时用：
  `COSYVOICE_SKIP_DOWNLOAD=1 wsl -e bash -c "cd '<仓库>' && bash tools/voice/bootstrap_cosyvoice_wsl.sh"`。
- `funasr==1.4.15`（PyPI，已验证含 `funasr-server` CLI 与
  `/v1/audio/transcriptions`）；CosyVoice 官方仓库固定 commit
  `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`（2026-05-25 main HEAD，含
  Fun-CosyVoice3 支持）；两者可分别用 `FUNASR_VERSION` / `COSYVOICE_COMMIT`
  覆盖。FunASR 侧 torch 不 pin（CPU 轮子随官方索引更新）；CosyVoice 侧初始
  安装不 pin、但清单装完后终局闭包恢复固定三件套 + CUDA 闭包成员 pin
  （M14-16/M14-17，见上节）——最终 torch/torchaudio/torchcodec 恒为已验证
  cu128 组合且 CUDA 闭包与 torch metadata 一致；最小清单 `openai-whisper`
  固定 `20250625`（M14-18：triton>=2 元数据与 cu128 闭包共存，有意偏离
  官方 20231117 pin，见上节）。脚本结尾打印实际安装版本（含
  openai-whisper）供留档。
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

## 生命周期管理（M14-04：status / start / stop / restart）

`tools/voice/voice_service_control.py` 是两个引擎统一的受控生命周期入口
（单文件、纯标准库；Windows 侧自动经 `wsl.exe --cd <仓库> bash -c` 编排，
WSL/Linux 内直接跑同一套命令）。复杂度留给系统，简单留给用户：

```powershell
# Windows 侧（仓库根；PowerShell / Git Bash 均可；WSL 内用 python3 同名命令）
python tools/voice/voice_service_control.py status                 # 只读体检
python tools/voice/voice_service_control.py start --engine funasr   # 幂等启动
python tools/voice/voice_service_control.py stop  --engine cosyvoice
python tools/voice/voice_service_control.py restart                 # stop → start
```

- `--engine funasr|cosyvoice|all`（默认 all）；`--port` 覆盖单引擎端口
  （默认取 `FUNASR_PORT`/`COSYVOICE_PORT` 环境变量，再默认 8010/8011）；
- 退出码：0 成功/幂等无操作；1 操作失败；2 参数错误；3 安全拒绝。
  `status` 恒只读（不写不杀），退出码不反映健康度。

**manifest 即事实源**：start 生成 `artifacts/voice/<engine>/service/launcher.sh`
——`echo $$ > spawn.pid` 后 `exec bash bootstrap_*.sh`；bash 的 exec 链
（launcher → bootstrap（cosyvoice 含快照 re-exec）→ 服务进程）保持同一 PID，
故 spawn.pid 恒等于最终服务进程 PID。PID、端口、启动时间、cmdline 匹配标记、
WSL 工作区（launcher 内 `pwd -P` 落档）原子写入同目录 `manifest.json`；
进程输出追加到 `service.log`（全部 gitignored）。

**start 幂等（manifest/PID/端口三重事实校验）**：manifest PID 活且 cmdline
仍含启动标记 → 不重复启动（健康 200 = already running；端口未起/503 =
bootstrap 阶段，可达数分钟）；stale manifest（PID 已死 / 被无关进程复用 /
损坏）→ 清理并明确报告后放行；端口已有不受管监听 → 拒绝启动并报告属主 PID
（不 spawn 注定绑不上端口的第二实例）；并发 start/stop 经 O_EXCL 锁文件
串行化（崩溃残留的锁 10 分钟后安全回收，回收动作明确报告）。

**stop 安全边界（拒绝误杀）**——只停同时满足以下三点的进程，否则显式拒绝：

1. manifest 归属（无 manifest 的进程一概不碰，包括当前占用 8010/8011 的
   手工前台实例）；
2. `/proc/<pid>/cmdline` 仍含启动标记（PID 被无关进程复用 → 判 stale 清理
   manifest，绝不发信号）；
3. `/proc/<pid>/cwd` 与 manifest 记录的工作区一致（cmdline 匹配但 cwd 指向
   别的检出 → 疑似另一 worktree 的实例，拒绝并保留 manifest 供人工核实；
   cwd 不可读时跳过此项——PID + cmdline 双事实已是防御纵深）。

终止序列：`kill -TERM` → 宽限轮询（15s 内 /proc 消失即优雅退出）→
`kill -KILL` 兜底。**绝无 pkill / killall / fuser / 按端口杀进程**——kill
目标恒为 manifest 核验过的单个 PID（契约测试锁定，见
test_voice_service_control.py）。

**restart 复用缓存**：stop → start 序列中模型不重新下载——bootstrap 的载荷
文件判定（M14-02：cosyvoice3.yaml/flow.pt/llm.pt/hift.pt 齐备即跳过）与
wetext 离线缓存（M14-03）均命中 gitignored artifacts 内既有缓存；不改写
用户家目录 ModelScope/HuggingFace 缓存。stop 拒绝时 restart 中止，不启动
第二实例。

**三条 fail-closed（修正轮 2，2026-09-11）**：

1. `/health` 探测恒经零代理 opener（`ProxyHandler({})`）直连 127.0.0.1——
   继承的 `HTTP_PROXY`/`http_proxy`/系统代理不得劫持回环探测（本机实证：
   代理工具代笔回环端口返回 502/RST 会污染健康判定）；进程代理环境变量
   保持原样（不 set/unset）。
2. **端口不符拒绝**：manifest 记录端口 ≠ 当前请求端口（`--port`/环境变量）
   → status 如实报告双端口并保留 manifest；start/stop/restart 一律拒绝
   （不 spawn、不发信号、manifest 不清理——PID 已死也不并入 stale 清理）。
   操作该实例请用 manifest 记录的端口重跑。
3. **`--service-dir` 仓库内约束**：写路径命令（start/stop/restart——lock/
   launcher/spawn 均写入该目录）要求目录位于仓库内，否则在任何写动作之前
   拒绝（退出码 2）——launcher 以仓库根 cwd 的 repo-relative 路径寻址，
   仓库外目录在 WSL/bash 侧不可达。`status` 只读不受限。测试/诊断需显式设
   `VOICE_SERVICE_ALLOW_OUTSIDE_SERVICE_DIR=1`（仅测试用，生产勿设）。

**失败恢复**：start 后立即退出 / pidfile 未落 → 打印 service.log 尾部并
exit 1（manifest 不写）；terminate 后进程仍存活（TERM+KILL 均无效）→
manifest 保留以便重试；`status` 显示 `managed-starting` 且长期不变 →
看 service.log（bootstrap 阶段含 pip/模型下载，首启可很久）。接管 M14-02
手工前台实例：先在原终端 `Ctrl+C` 停掉，再 `start` 即纳入受管。

**测试与证据**：生命周期分支（幂等/stale/拒绝误杀/端口竞争/锁/路径解析）
全部由注入 FakeOps 的单测覆盖，fake health 端点（stdlib http.server，
仅 127.0.0.1 空闲端口）验证真实 HTTP 探测——测试不启动真实引擎、不占用
8010/8011（见 `services/api/tests/test_voice_service_control.py`）。

## 边界（实际部署状态，2026-09-10 M14-02 轮更新）

- **本机已完成真实部署与冒烟**（2026-09-10，WSL2 + RTX 5070 Ti）：模型
  Fun-CosyVoice3-0.5B-2512（约 8.5GB）与 SenseVoiceSmall 均经 bootstrap 下载
  落位 gitignored artifacts；`COSYVOICE_SKIP_DOWNLOAD=1` 复用重启后
  `127.0.0.1:8010` 与 `127.0.0.1:8011` /health 均 200，
  `smoke_local_voice.py` 全过（ASR 真实转写中文样例文本 + TTS 返回
  RIFF/WAV 字节；冒烟轮 178604 B / 2.6s，主管复跑 241964 B / 11.4s 冷启，
  证据 `.verify/m14-02-real-voice-deployment/evidence/`）。
  **但 `production_ready=false` 不变**：以上仅为单机验证——未接生产后端/
  生产 DB、未做并发/长稳/断电恢复验证、无开机自启与 SLA（前台进程手动
  起停）、首次 TTS 推理延迟（冷启 ~11-16s）未优化、WSL systemd 会话层
  重启会连带杀掉两个引擎（2026-09-10 12:00 实证，需运维重拉）。
- **compose 容器内 API 调真实引擎的端到端已验证**（2026-09-10 收口轮）：
  API 容器经 `host.docker.internal` 直探引擎 `/health` 双 200
  （`8010 …sensevoice` / `8011 …Fun-CosyVoice3-0.5B-2512`，证据
  `evidence/container_to_host_engine_health_20260910.log`）；隔离 compose
  项目 `aios-m14-02-voice-e2e`（`AIOS_WEB_PORT=13000`）以真实鉴权令牌
  验证：providers HTTP 200 且 local-funasr / local-cosyvoice 双 provider
  `fallback=false`；synthesize HTTP 200（provider=local-cosyvoice，
  134444 B RIFF/WAV）；transcribe HTTP 200（provider=local-funasr，文本
  正确，139ms）。
- **wetext 前端惰性 FST 缓存是明确的剩余边界**：重启复用 8.5GB CosyVoice
  主模型载荷，但 wetext 文本正则化资源（MB 级 FST：full_to_half / en/tn/*
  等）在**首次加载时一次性下载**到 `~/.cache/modelscope`（非 CosyVoice
  主模型，bootstrap 不预置）。已下载后同机复用；离线冷机首次起 bridge
  需外网（或预置该缓存）——缓存预置/离线打包未实现，属后续运维项。
- 最小运行时依赖清单已按**真实 AutoModel 加载实证**修正（M14-02）：静态
  导入闭包漏掉模型 YAML `!new:/!name:` 动态实例化路径上的 11 个包
  （conformer/diffusers/lightning/hydra-core/matplotlib/rich/gdown/wget/
  librosa/pyarrow/pyworld，官方 pin 沿用）——已补入清单并由契约测试锁定。
- bridge 加载失败现打印完整 traceback（M14-02 修复：旧版提示「细节见上方栈」
  但从未输出）。
- 流式 ASR / 流式 TTS 未实现、未宣称（funasr-server 的 WebSocket 流式与
  CosyVoice bi-streaming 均为后续单独立项）。
- bridge 为单 worker、单模型串行推理（本地单用户口径）；并发容量未测。
- 本地引擎不提供 SLA/开机自启（前台进程，由运维手动起停；M14-04 起受控
  生命周期入口可用，见上方「生命周期管理」节，但开机自启仍不在范围）。
- **M14-04（2026-09-10）**：受控生命周期管理已交付（status / start / stop /
  restart + manifest 三重事实校验 + 拒绝误杀边界），`production_ready=false`
  **不变**——仍无开机自启/断电自愈，WSL systemd 会话层重启后仍需运维经
  restart 重拉；当前本机 8010/8011 两个实例为 M14-02 轮手工前台启动，处于
  unmanaged 状态，本工具对其只读探测（status 如实显示 unmanaged-running），
  stop/restart 会拒绝触碰——接管需先在原终端手动停一次再 start。
