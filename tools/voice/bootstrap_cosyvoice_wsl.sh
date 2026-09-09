#!/usr/bin/env bash
# M14-01 本地 TTS 引擎 bootstrap（在 WSL 内运行）：CosyVoice 官方仓库 + OpenAI 兼容 bridge。
#
# 修正轮（supervisor corrections）：
# - 不再假设 apt python3.10/sox：Python 经 uv 隔离管理（uv venv --python 3.10
#   --seed，缺 3.10 时由 uv 下载托管解释器，无需 apt/sudo；uv 探测含 ~/.local/bin
#   ——非登录 bash 的 PATH 不含它）；无 uv 时回退系统 python3.10（再缺失则给出
#   uv 安装指引 fail-closed）。
# - 依赖最小化：只装 bridge 推理路径真实需要的包（tools/voice/cosyvoice-
#   runtime-requirements.txt，来源为固定 commit 的导入闭包静态推导）；安装后
#   真实执行 `import cosyvoice.cli.cosyvoice` 验证（不下载模型），缺失即点名
#   失败；COSYVOICE_FULL_REQUIREMENTS=1 可回退官方完整 requirements。
# - sox 前置检查移除：官方 load_wav 显式 torchaudio backend='soundfile'
#   （wheel 自带 libsndfile），bootstrap 用真实加载探针实证（对克隆仓库自带的
#   asset/zero_shot_prompt.wav 执行 torchaudio.load），失败才点名补救。
# - torch 决策不变：cu128 轮子（RTX 5070 Ti/Blackwell），官方 cu121 pin 被剔除；
#   官方仓库固定 commit 074ca6d（含子模块 third_party/Matcha-TTS）不变。
# - torchcodec==0.11.1+cu128 随 cu128 torch 同装：torchaudio 2.11 的后端探测
#   需要 torchcodec，干净环境缺它则 torchaudio 导入/加载探针失败（2026-09-10
#   本机 venv 实证 torch 2.11.0+cu128 / torchaudio 2.11.0+cu128 /
#   torchcodec 0.11.1+cu128 import 与 WAV 读写均通过）。+cu128 本地版本轮
#   只存在于 pytorch cu128 index（PyPI 无），且其 METADATA 不 pin torch——
#   同命令安装不替换、不重解 torch 依赖。
#
# 用途：Python 3.10 独立 venv 内克隆官方仓库、装 cu128 torch + 最小运行时依赖、
#   下载 Fun-CosyVoice3-0.5B-2512 到 gitignored artifacts，然后在 127.0.0.1:8011
#   启动 tools/voice/cosyvoice_openai_bridge.py（/v1/audio/speech 恒 WAV；key
#   可选经 COSYVOICE_BRIDGE_API_KEY 注入，脚本不回显不落盘）。
#
# 幂等：克隆/子模块/模型已存在则跳过或断点续传（git checkout 固定 commit 会丢弃
#   克隆目录内本地改动——工具目录不应手改）；pip 安装满足即 no-op。本脚本对仓库
#   只读（新增文件全部落在 artifacts/），不写任何 secret。
#
# Windows 侧入口（PowerShell，仓库在 D:\ 时）：
#   wsl -e bash -c "cd '/mnt/d/AI Learning OS/<repo-dir>' && bash tools/voice/bootstrap_cosyvoice_wsl.sh"
#   注意：非登录 bash 的 PATH 不含 ~/.local/bin（uv 所在）——本脚本自行探测，
#   无需 bash -lc。
#
# 可选环境变量：
#   VOICE_ARTIFACTS_DIR        artifacts 根目录（默认 <repo>/artifacts/voice，已 gitignore）
#   COSYVOICE_UV               uv 可执行文件路径（默认自动探测 command -v / ~/.local/bin/uv）
#   COSYVOICE_PYTHON           直接指定 python3.10 解释器（跳过 uv；不推荐，破坏隔离性）
#   COSYVOICE_FULL_REQUIREMENTS=1  回退官方完整 requirements（剔除 torch pin）
#   COSYVOICE_COMMIT           官方仓库固定 commit（默认下方默认值）
#   COSYVOICE_SKIP_DOWNLOAD=1  跳过模型下载（模型已就位时）
#   COSYVOICE_PORT             监听端口（默认 8011；恒绑 127.0.0.1）
#   COSYVOICE_BRIDGE_API_KEY   bridge 可选鉴权 key（透传给 bridge 进程，不回显）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARTIFACTS="${VOICE_ARTIFACTS_DIR:-$REPO_ROOT/artifacts/voice}"
COSYVOICE_DIR="$ARTIFACTS/cosyvoice/CosyVoice"
VENV_DIR="$ARTIFACTS/cosyvoice/venv"
MODEL_DIR="$ARTIFACTS/cosyvoice/Fun-CosyVoice3-0.5B"
MODEL_ID="FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
#: 官方 main HEAD（2026-05-25 验证；含 Fun-CosyVoice3 支持与 AutoModel 入口）
COSYVOICE_COMMIT_DEFAULT="074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
HOST="127.0.0.1"
PORT="${COSYVOICE_PORT:-8011}"

say() { printf '[bootstrap-cosyvoice] %s\n' "$*"; }
fail() { printf '[bootstrap-cosyvoice] FAIL: %s\n' "$*" >&2; exit 1; }

# ---- 官方仓库克隆（固定 commit + 子模块，幂等；先克隆——依赖探针要用仓库内 asset）----
if [ ! -d "$COSYVOICE_DIR/.git" ]; then
  say "克隆官方 CosyVoice（--recursive 含 third_party/Matcha-TTS；网络受限时需代理）"
  mkdir -p "$(dirname "$COSYVOICE_DIR")"
  git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "$COSYVOICE_DIR"
fi
COMMIT="${COSYVOICE_COMMIT:-$COSYVOICE_COMMIT_DEFAULT}"
say "固定 commit $COMMIT（本地改动会被丢弃——工具目录不应手改）"
git -C "$COSYVOICE_DIR" fetch --all --tags >/dev/null 2>&1 || say "git fetch 失败（离线时若 commit 已在本地可继续）"
git -C "$COSYVOICE_DIR" checkout -f "$COMMIT"
git -C "$COSYVOICE_DIR" submodule update --init --recursive
ASSET_WAV="$COSYVOICE_DIR/asset/zero_shot_prompt.wav"
[ -f "$ASSET_WAV" ] || fail "官方样例 wav 缺失（克隆/子模块不完整：asset/zero_shot_prompt.wav）"

# ---- venv：uv 隔离管理 Python 3.10（无 apt 假设；--seed 装入 pip）----
if [ ! -x "$VENV_DIR/bin/python" ]; then
  if [ -n "${COSYVOICE_PYTHON:-}" ]; then
    say "使用显式指定解释器创建 venv（COSYVOICE_PYTHON）"
    "$COSYVOICE_PYTHON" -m venv "$VENV_DIR" || fail "venv 创建失败（COSYVOICE_PYTHON 指定的解释器不可用）"
  else
    UV_BIN=""
    if [ -n "${COSYVOICE_UV:-}" ]; then
      UV_BIN="$COSYVOICE_UV"
    elif command -v uv >/dev/null 2>&1; then
      UV_BIN="$(command -v uv)"
    elif [ -x "$HOME/.local/bin/uv" ]; then
      UV_BIN="$HOME/.local/bin/uv"  # 非登录 bash 的 PATH 不含 ~/.local/bin
    fi
    if [ -n "$UV_BIN" ]; then
      say "uv 创建隔离 venv（Python 3.10；本机缺失时由 uv 下载托管解释器，无需 apt/sudo）"
      "$UV_BIN" venv --python 3.10 --seed "$VENV_DIR" || fail "uv venv 失败（网络受限时 uv 下载解释器需代理）"
    elif command -v python3.10 >/dev/null 2>&1; then
      say "未找到 uv，回退系统 python3.10（推荐安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh）"
      python3.10 -m venv "$VENV_DIR" || fail "venv 创建失败（需 python3.10-venv 包）"
    else
      fail "无 uv 且无系统 python3.10（安装 uv 后重试：curl -LsSf https://astral.sh/uv/install.sh | sh，新终端或 bash -lc 下执行）"
    fi
  fi
fi
say "venv Python: $("$VENV_DIR/bin/python" --version 2>&1)"

# ---- 依赖：先 cu128 torch（含 torchcodec），再最小运行时清单（或官方完整清单回退）----
# torchcodec 必须与 torch/torchaudio 同一条 cu128 index 命令安装：torchaudio 2.11
# 后端探测需要它；+cu128 本地版本轮只在 pytorch cu128 index（PyPI 解析拿不到）；
# torchcodec METADATA 不约束 torch 版本，不会替换/重解 torch 依赖。
say "安装 cu128 torch/torchaudio/torchcodec==0.11.1+cu128（RTX 5070 Ti/Blackwell；网络受限时需代理）"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install torch torchaudio torchcodec==0.11.1+cu128 --index-url https://download.pytorch.org/whl/cu128
if [ "${COSYVOICE_FULL_REQUIREMENTS:-0}" = "1" ]; then
  say "COSYVOICE_FULL_REQUIREMENTS=1：回退官方完整 requirements（剔除 torch/torchaudio 的 cu121 pin）"
  grep -vE '^(torch|torchaudio)==' "$COSYVOICE_DIR/requirements.txt" > "$ARTIFACTS/cosyvoice/requirements.full.txt"
  "$VENV_DIR/bin/pip" install -r "$ARTIFACTS/cosyvoice/requirements.full.txt"
else
  say "安装最小运行时依赖（tools/voice/cosyvoice-runtime-requirements.txt，导入闭包推导）"
  "$VENV_DIR/bin/pip" install -r "$REPO_ROOT/tools/voice/cosyvoice-runtime-requirements.txt"
fi

# ---- 安装即验证：真实 import 官方入口（不下载模型；缺失点名失败）----
say "验证导入闭包：import cosyvoice.cli.cosyvoice（bridge 唯一入口）"
if ! "$VENV_DIR/bin/python" - "$COSYVOICE_DIR" <<'PYEOF'
import sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "third_party" / "Matcha-TTS"))
try:
    import cosyvoice.cli.cosyvoice  # noqa: F401 —— 验证导入闭包完整
except ModuleNotFoundError as cause:
    print(f"[bootstrap-cosyvoice] FAIL: 依赖缺失: {cause.name}", file=sys.stderr)
    sys.exit(1)
print("[bootstrap-cosyvoice] import cosyvoice.cli.cosyvoice OK")
PYEOF
then
  fail "导入验证失败——最小依赖清单不足；可设 COSYVOICE_FULL_REQUIREMENTS=1 回退官方完整 requirements 后重试，并把缺失包报回仓库修正 cosyvoice-runtime-requirements.txt"
fi

# ---- sox 疑问的实证解法：load_wav 走 soundfile 后端，用真实加载探针实证 ----
# 官方 file_utils.load_wav 显式 torchaudio.load(..., backend='soundfile')——
# 不依赖 sox/libsox；soundfile wheel 自带 libsndfile。探针失败才给出补救指引。
if ! "$VENV_DIR/bin/python" - "$ASSET_WAV" <<'PYEOF'
import sys

import torchaudio

try:
    torchaudio.load(sys.argv[1], backend="soundfile")
except Exception:  # noqa: BLE001 —— 任何后端失败都 fail-closed
    print("[bootstrap-cosyvoice] FAIL: torchaudio soundfile 后端加载 wav 失败"
          "（soundfile wheel 自带 libsndfile，正常无需系统库；可重装 soundfile 或"
          "在 WSL 内 apt 安装 libsndfile1 后重试）", file=sys.stderr)
    sys.exit(1)
print("[bootstrap-cosyvoice] torchaudio.load(backend='soundfile') OK（无需 sox）")
PYEOF
then
  fail "音频后端探针失败（见上）"
fi

# ---- 模型下载（幂等：目录已有文件即跳过；可 COSYVOICE_SKIP_DOWNLOAD=1 显式跳过）----
if [ "${COSYVOICE_SKIP_DOWNLOAD:-0}" = "1" ]; then
  say "跳过模型下载（COSYVOICE_SKIP_DOWNLOAD=1）"
elif [ -n "$(ls -A "$MODEL_DIR" 2>/dev/null || true)" ]; then
  say "模型目录已存在，跳过下载: artifacts/voice/cosyvoice/Fun-CosyVoice3-0.5B"
else
  say "经 ModelScope 下载 $MODEL_ID（数 GB，gitignored artifacts；可断点续传）"
  "$VENV_DIR/bin/python" - "$MODEL_ID" "$MODEL_DIR" <<'PYEOF'
import sys

model_id, local_dir = sys.argv[1], sys.argv[2]
from modelscope import snapshot_download

snapshot_download(model_id, local_dir=local_dir)
PYEOF
fi

say "依赖就绪，已安装版本（记录用）："
"$VENV_DIR/bin/pip" freeze | grep -E '^(torch|torchaudio|torchcodec|modelscope|transformers)=' || true
say "启动 OpenAI 兼容 bridge: http://$HOST:$PORT/v1/audio/speech（wav；key 可选）"
say "模型在 bridge 启动后后台加载——/health 返回 200 才 ready（期间 503）"

# bridge 恒绑 loopback；COSYVOICE_BRIDGE_API_KEY（若有）只透传给 bridge 进程，不回显
exec "$VENV_DIR/bin/python" "$REPO_ROOT/tools/voice/cosyvoice_openai_bridge.py" \
  --repo-dir "$COSYVOICE_DIR" \
  --model-dir "$MODEL_DIR" \
  --host "$HOST" --port "$PORT"
