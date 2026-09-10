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
#   COSYVOICE_SKIP_WETEXT_WARMUP=1  跳过 wetext 离线缓存预热（M14-03；启动延迟
#                              敏感时用——bridge 启动时仍会按需预热/告警）
#   MODELSCOPE_CACHE           ModelScope 缓存根（默认 <artifacts>/cosyvoice/
#                              modelscope-cache；wetext 离线缓存所在，见 M14-03 段）
#   COSYVOICE_PORT             监听端口（默认 8011；恒绑 127.0.0.1）
#   COSYVOICE_BRIDGE_API_KEY   bridge 可选鉴权 key（透传给 bridge 进程，不回显）
#   COSYVOICE_BOOTSTRAP_SNAPSHOT / COSYVOICE_BOOTSTRAP_REPO_ROOT
#                              内部自保护变量（快照重执行机制），外部无需设置
set -euo pipefail

# 快照重执行（见下方自保护块）后 $0 指向 artifacts 内副本——仓库根只能经
# COSYVOICE_BOOTSTRAP_REPO_ROOT 透传，不能从 $0 再推导
if [ -n "${COSYVOICE_BOOTSTRAP_REPO_ROOT:-}" ]; then
  REPO_ROOT="$COSYVOICE_BOOTSTRAP_REPO_ROOT"
else
  REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi
ARTIFACTS="${VOICE_ARTIFACTS_DIR:-$REPO_ROOT/artifacts/voice}"
COSYVOICE_DIR="$ARTIFACTS/cosyvoice/CosyVoice"
VENV_DIR="$ARTIFACTS/cosyvoice/venv"
MODEL_DIR="$ARTIFACTS/cosyvoice/Fun-CosyVoice3-0.5B"
MODEL_ID="FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
#: 官方 main HEAD（2026-05-25 验证；含 Fun-CosyVoice3 支持与 AutoModel 入口）
COSYVOICE_COMMIT_DEFAULT="074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
HOST="127.0.0.1"
PORT="${COSYVOICE_PORT:-8011}"

# ---- 运行期自保护：快照重执行（M14-02 生产实证修复）----
# bash 按字节偏移增量解析脚本：本脚本的模型下载可运行数小时，期间仓库内任何
# 并行会话的 git 操作（commit/checkout 重写本文件）都会让 bash 在旧字节偏移
# 上解析新内容——产生与真实语法无关的伪语法错误（实证：运行中 66eb080→
# 6c2f7aa 改写后，下载完成的续读点落在探针 Python 代码上，报「line 169:
# syntax error near unexpected token ')'」，而两个版本各自 bash -n 均通过）。
# 启动即把自身原子快照进 gitignored artifacts 并 exec 快照副本：此后本文件
# 被改写不再影响本进程（快照仅在下一次启动时整体替换——临时文件 + mv 原子
# 改名，正在运行的旧快照 inode 不被触碰）。
if [ "${COSYVOICE_BOOTSTRAP_SNAPSHOT:-0}" != "1" ]; then
  mkdir -p "$ARTIFACTS/cosyvoice"
  SNAPSHOT="$ARTIFACTS/cosyvoice/bootstrap_cosyvoice_wsl.snapshot.sh"
  SNAPSHOT_TMP="$SNAPSHOT.tmp.$$"
  if ! cat -- "$0" > "$SNAPSHOT_TMP" 2>/dev/null; then
    printf '[bootstrap-cosyvoice] FAIL: 无法写入脚本快照(%s)\n' "$SNAPSHOT" >&2
    exit 1
  fi
  mv -f "$SNAPSHOT_TMP" "$SNAPSHOT"
  COSYVOICE_BOOTSTRAP_SNAPSHOT=1 COSYVOICE_BOOTSTRAP_REPO_ROOT="$REPO_ROOT" \
    exec bash "$SNAPSHOT" "$@"
fi

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

# ---- 模型就位检测与下载（幂等；可 COSYVOICE_SKIP_DOWNLOAD=1 显式跳过）----
# 就位判定以关键载荷文件为准（cosyvoice3.yaml/flow.pt/llm.pt/hift.pt），不以
# 「目录非空」为准：ModelScope 断点残留 ._____temp/ 会让空壳目录非空（本机
# 实证），误判已下载会让 bridge 启动后加载失败。MODEL_DIR 载荷不完整时回落
# ModelScope 缓存布局（hub/models/<org>/<name>/snapshots/<id>/ 新版或
# models/<org>/<name>/ 旧版，根可用 MODELSCOPE_CACHE 改址）——缓存与
# MODEL_DIR 不同位时自动采用缓存快照，不重新下载。
model_payload_ready() {
  [ -f "$1/cosyvoice3.yaml" ] && [ -f "$1/flow.pt" ] \
    && [ -f "$1/llm.pt" ] && [ -f "$1/hift.pt" ]
}

modelscope_cache_model_dir() {
  msc_root="${MODELSCOPE_CACHE:-$HOME/.cache/modelscope}"
  for cand in "$msc_root/hub/models/$MODEL_ID"/snapshots/*/ "$msc_root/models/$MODEL_ID"/; do
    if model_payload_ready "${cand%/}"; then
      printf '%s\n' "${cand%/}"
      return 0
    fi
  done
  return 1
}

if [ "${COSYVOICE_SKIP_DOWNLOAD:-0}" = "1" ]; then
  say "跳过模型下载（COSYVOICE_SKIP_DOWNLOAD=1）"
elif model_payload_ready "$MODEL_DIR"; then
  say "模型载荷已就位，跳过下载: artifacts/voice/cosyvoice/Fun-CosyVoice3-0.5B"
else
  say "经 ModelScope 下载 $MODEL_ID（数 GB，gitignored artifacts；可断点续传）"
  "$VENV_DIR/bin/python" - "$MODEL_ID" "$MODEL_DIR" <<'PYEOF'
import sys

model_id, local_dir = sys.argv[1], sys.argv[2]
from modelscope import snapshot_download

snapshot_download(model_id, local_dir=local_dir)
PYEOF
fi

# ---- 模型目录终检：载荷缺失时回落 ModelScope 缓存；仍缺失则 fail-closed ----
if ! model_payload_ready "$MODEL_DIR"; then
  if CACHE_DIR="$(modelscope_cache_model_dir)"; then
    say "MODEL_DIR 载荷不完整，回落 ModelScope 缓存模型目录: $CACHE_DIR"
    MODEL_DIR="$CACHE_DIR"
  elif [ "${COSYVOICE_SKIP_DOWNLOAD:-0}" = "1" ]; then
    fail "模型未就位：MODEL_DIR 载荷不完整且 ModelScope 缓存未命中（COSYVOICE_SKIP_DOWNLOAD=1 不下载——去掉该变量重跑补齐，或核对 VOICE_ARTIFACTS_DIR / MODELSCOPE_CACHE 指向）"
  else
    fail "下载后载荷校验失败（cosyvoice3.yaml/flow.pt/llm.pt/hift.pt 应齐备）——重跑可断点续传"
  fi
fi

# ---- wetext 离线缓存（M14-03 Round 2）：确定性 artifacts 内 ModelScope 缓存 + 预热 ----
# CosyVoice frontend 以无路径参数的 wetext.Normalizer() 构造文本正则化前端 →
# wetext 内部 snapshot_download("pengzhendong/wetext")（revision=master，可变）
# 默认落用户家目录 ~/.cache/modelscope——宿主"已预置"仍依赖网络、重启不可预测。
# 修法（上游支持层，源码实证）：modelscope 1.20 的 snapshot_download 调用时读
# MODELSCOPE_CACHE 环境变量 → 缓存确定性指向 gitignored artifacts；本步骤在
# bridge 启动前预热（payload 以 wetext==0.0.4 lang=auto/tn 实际打开的四个 FST
# 为准），齐备即跳过。预热只是缓存落位；真正的零网络复用由 bridge 完成——
# 其在引擎加载前对 pengzhendong/wetext 的 snapshot_download 窄绑定
# local_files_only=True（见 cosyvoice_openai_bridge.py，契约测试锁定），
# 复用路径零文件下载且零 ModelScope 元数据/API 请求。
# COSYVOICE_SKIP_WETEXT_WARMUP=1 显式跳过预热；预热失败只告警不阻断
# （bridge 会按 payload 状态决定预热重试/绑定/告警）。
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ARTIFACTS/cosyvoice/modelscope-cache}"
wetext_repo_dir="$MODELSCOPE_CACHE/hub/pengzhendong/wetext"
wetext_payload_ready() {
  [ -f "$wetext_repo_dir/en/tn/tagger.fst" ] \
    && [ -f "$wetext_repo_dir/en/tn/verbalizer.fst" ] \
    && [ -f "$wetext_repo_dir/zh/tn/tagger.fst" ] \
    && [ -f "$wetext_repo_dir/zh/tn/verbalizer.fst" ]
}
if [ "${COSYVOICE_SKIP_WETEXT_WARMUP:-0}" = "1" ]; then
  say "跳过 wetext 预热（COSYVOICE_SKIP_WETEXT_WARMUP=1）；MODELSCOPE_CACHE=$MODELSCOPE_CACHE"
elif wetext_payload_ready; then
  say "wetext 离线缓存就绪（payload 齐备，无需下载）: $wetext_repo_dir"
else
  say "预置 wetext 文本正则化 FST 到 artifacts 缓存（pengzhendong/wetext，约 52MB）"
  if ! "$VENV_DIR/bin/python" - <<'PYEOF'
from modelscope import snapshot_download

snapshot_download("pengzhendong/wetext")
PYEOF
  then
    say "WARN: wetext 预热失败（网络？）——不阻断；bridge 启动时会重试，仍失败将退化为无文本正则化（bridge 会显式告警）"
  elif wetext_payload_ready; then
    say "wetext 离线缓存已就位: $wetext_repo_dir"
  else
    say "WARN: wetext 预热后 payload 仍不齐（仓库布局变更？）——bridge 启动时会重试并告警"
  fi
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
