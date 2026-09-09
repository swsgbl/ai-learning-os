#!/usr/bin/env bash
# M14-01 本地 ASR 引擎 bootstrap（在 WSL 内运行）：FunASR SenseVoiceSmall CPU 服务。
#
# 用途：创建 Python 3.11 独立 venv，安装 CPU 版 torch/torchaudio 与
#   funasr/fastapi/uvicorn/python-multipart，然后在 127.0.0.1:8010 启动
#   funasr-server（OpenAI 兼容 /v1/audio/transcriptions，无鉴权——仅本机可达）。
#   模型文件由 funasr AutoModel 首次启动时经 ModelScope 下载到
#   $MODELSCOPE_CACHE（gitignored artifacts 目录，绝不入库）。
#
# 幂等：venv 已存在则复用；pip 安装满足即 no-op；模型缓存已存在则断点续传。
#   本脚本只读仓库（新增文件全部落在 artifacts/），不写任何 secret。
#
# Windows 侧入口（PowerShell，仓库在 D:\ 时）：
#   wsl -e bash -c "cd '/mnt/d/AI Learning OS/<repo-dir>' && bash tools/voice/bootstrap_funasr_wsl.sh"
#   （路径含空格必须整体加引号；建议 wsl --cd 或先 cd 再调用，见 tools/voice/README.md）
#
# 可选环境变量：
#   VOICE_ARTIFACTS_DIR  artifacts 根目录（默认 <repo>/artifacts/voice，已 gitignore）
#   FUNASR_PYTHON        指定 python 解释器（默认自动探测 python3.11 → python3）
#   FUNASR_VERSION       funasr pip 版本（默认 1.4.15，已验证含 funasr-server CLI）
#   FUNASR_PORT          监听端口（默认 8010；恒绑 127.0.0.1，不提供改绑选项）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARTIFACTS="${VOICE_ARTIFACTS_DIR:-$REPO_ROOT/artifacts/voice}"
VENV_DIR="$ARTIFACTS/funasr/venv"
CACHE_DIR="$ARTIFACTS/funasr/modelscope-cache"
HOST="127.0.0.1"
PORT="${FUNASR_PORT:-8010}"

say() { printf '[bootstrap-funasr] %s\n' "$*"; }
fail() { printf '[bootstrap-funasr] FAIL: %s\n' "$*" >&2; exit 1; }

# ---- Python 3.11 探测（推荐 3.11；可用 FUNASR_PYTHON 显式指定）----
if [ -n "${FUNASR_PYTHON:-}" ]; then
  PY="$FUNASR_PYTHON"
elif command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
  say "未找到 python3.11，回退 $PY（推荐 3.11；可用 FUNASR_PYTHON= 指定）"
else
  fail "未找到 python3（WSL 内需先安装：sudo apt-get install python3.11 python3.11-venv）"
fi
PY_VERSION="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
say "使用 Python $PY_VERSION"

# ---- venv（幂等：已存在复用）----
if [ ! -x "$VENV_DIR/bin/python" ]; then
  say "创建独立 venv: artifacts/voice/funasr/venv"
  "$PY" -m venv "$VENV_DIR" || fail "venv 创建失败（Debian/Ubuntu 需 python3-venv 包：sudo apt-get install python3.11-venv）"
fi

# ---- 依赖（幂等：满足即 no-op）----
say "安装 CPU 版 torch/torchaudio + funasr 服务栈（首次约数百 MB，网络受限时需代理或 pip 镜像）"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
"$VENV_DIR/bin/pip" install "funasr==${FUNASR_VERSION:-1.4.15}" fastapi uvicorn python-multipart
[ -x "$VENV_DIR/bin/funasr-server" ] || fail "funasr-server CLI 不存在（funasr 安装异常）"

mkdir -p "$CACHE_DIR"

say "依赖就绪，已安装版本（记录用）："
"$VENV_DIR/bin/pip" freeze | grep -E '^(funasr|torch|torchaudio)=' || true
say "模型缓存目录（gitignored artifacts）: artifacts/voice/funasr/modelscope-cache"
say "首次启动会下载 SenseVoiceSmall 到上述缓存（约 1GB 量级，可断点续传）"
say "启动 funasr-server: http://$HOST:$PORT/v1/audio/transcriptions（无鉴权，仅本机）"

# MODELSCOPE_CACHE 把模型钉在 gitignored artifacts；恒绑 loopback（脚本不提供 0.0.0.0 选项）
export MODELSCOPE_CACHE="$CACHE_DIR"
exec "$VENV_DIR/bin/funasr-server" --host "$HOST" --port "$PORT" --model sensevoice --device cpu
