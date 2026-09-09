#!/usr/bin/env bash
# M14-01 本地 TTS 引擎 bootstrap（在 WSL 内运行）：CosyVoice 官方仓库 + OpenAI 兼容 bridge。
#
# 用途：Python 3.10 独立 venv（官方要求）内——克隆官方 CosyVoice（固定 commit，
#   含 third_party/Matcha-TTS 子模块），安装 cu128 torch/torchaudio（RTX 5070 Ti
#   Blackwell 需 cu128；requirements.txt 的 cu121 torch pin 被替换）与其余官方
#   requirements，下载 Fun-CosyVoice3-0.5B-2512 到 gitignored artifacts，然后在
#   127.0.0.1:8011 启动 tools/voice/cosyvoice_openai_bridge.py（/v1/audio/speech，
#   WAV；key 可选，经 COSYVOICE_BRIDGE_API_KEY 注入，脚本不回显不落盘）。
#
# 幂等：克隆/子模块/模型已存在则跳过或断点续传（git checkout 固定 commit 会丢弃
#   克隆目录内的本地改动——工具目录本就不应手改）；pip 满足即 no-op。
#   本脚本对仓库只读（新增文件全部落在 artifacts/），不写任何 secret。
#
# Windows 侧入口（PowerShell，仓库在 D:\ 时）：
#   wsl -e bash -c "cd '/mnt/d/AI Learning OS/<repo-dir>' && bash tools/voice/bootstrap_cosyvoice_wsl.sh"
#   （路径含空格必须整体加引号；详见 tools/voice/README.md）
#
# 可选环境变量：
#   VOICE_ARTIFACTS_DIR      artifacts 根目录（默认 <repo>/artifacts/voice，已 gitignore）
#   COSYVOICE_PYTHON         指定 python 解释器（默认自动探测 python3.10）
#   COSYVOICE_COMMIT         官方仓库固定 commit（默认下方 COSYVOICE_COMMIT 默认值）
#   COSYVOICE_SKIP_DOWNLOAD=1  跳过模型下载（模型已就位时）
#   COSYVOICE_PORT           监听端口（默认 8011；恒绑 127.0.0.1）
#   COSYVOICE_BRIDGE_API_KEY bridge 可选鉴权 key（透传给 bridge 进程，本脚本不回显）
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

# ---- Python 3.10 探测（官方要求；可用 COSYVOICE_PYTHON 显式指定）----
if [ -n "${COSYVOICE_PYTHON:-}" ]; then
  PY="$COSYVOICE_PYTHON"
elif command -v python3.10 >/dev/null 2>&1; then
  PY=python3.10
else
  fail "未找到 python3.10（CosyVoice 官方要求 3.10；WSL/Ubuntu: sudo apt-get install python3.10 python3.10-venv，或用 COSYVOICE_PYTHON= 指定）"
fi
say "使用 $($PY --version 2>&1)"

# ---- sox 依赖（torchaudio 加载 prompt wav 需要；缺即 fail-closed）----
if ! command -v sox >/dev/null 2>&1; then
  fail "缺少 sox（sudo apt-get install sox libsox-dev）"
fi

# ---- 官方仓库克隆（固定 commit + 子模块，幂等）----
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

# ---- venv + 依赖（幂等：满足即 no-op）----
if [ ! -x "$VENV_DIR/bin/python" ]; then
  say "创建独立 venv: artifacts/voice/cosyvoice/venv"
  "$PY" -m venv "$VENV_DIR" || fail "venv 创建失败（需 python3.10-venv 包）"
fi
say "安装 cu128 torch/torchaudio（RTX 5070 Ti/Blackwell；网络受限时需代理）"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
say "安装官方 requirements（torch/torchaudio 的 cu121 pin 已剔除，保留 cu128 版本）"
grep -vE '^(torch|torchaudio)==' "$COSYVOICE_DIR/requirements.txt" > "$ARTIFACTS/cosyvoice/requirements.aios.txt"
"$VENV_DIR/bin/pip" install -r "$ARTIFACTS/cosyvoice/requirements.aios.txt"

# ---- 模型下载（幂等：目录已有文件即跳过；可 COSYVOICE_SKIP_DOWNLOAD=1 显式跳过）----
if [ "${COSYVOICE_SKIP_DOWNLOAD:-0}" = "1" ]; then
  say "跳过模型下载（COSYVOICE_SKIP_DOWNLOAD=1）"
elif [ -n "$(ls -A "$MODEL_DIR" 2>/dev/null || true)" ]; then
  say "模型目录已存在，跳过下载: artifacts/voice/cosyvoice/Fun-CosyVoice3-0.5B"
else
  say "经 ModelScope 下载 $MODEL_ID（约数 GB，gitignored artifacts；可断点续传）"
  "$VENV_DIR/bin/python" - "$MODEL_ID" "$MODEL_DIR" <<'PYEOF'
import sys

model_id, local_dir = sys.argv[1], sys.argv[2]
from modelscope import snapshot_download

snapshot_download(model_id, local_dir=local_dir)
PYEOF
fi

say "依赖就绪，已安装版本（记录用）："
"$VENV_DIR/bin/pip" freeze | grep -E '^(torch|torchaudio|modelscope)=' || true
say "启动 OpenAI 兼容 bridge: http://$HOST:$PORT/v1/audio/speech（wav；key 可选）"
say "模型在 bridge 启动后后台加载——/health 返回 200 才 ready（期间 503）"

# bridge 恒绑 loopback；COSYVOICE_BRIDGE_API_KEY（若有）只透传给 bridge 进程，不回显
exec "$VENV_DIR/bin/python" "$REPO_ROOT/tools/voice/cosyvoice_openai_bridge.py" \
  --repo-dir "$COSYVOICE_DIR" \
  --model-dir "$MODEL_DIR" \
  --host "$HOST" --port "$PORT"
