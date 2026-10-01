#!/usr/bin/env bash
# M14-70 本地语音链路冒烟 wrapper：定位项目 venv 解释器后原样 exec
# tools/voice/smoke_local_voice.py（M14-01 探针——判分/输出/环境契约全部由
# 探针自身负责；本脚本零探针复制、零 env 修改、零 secret 感知）。
# Python 选择链（M14-210）：显式 PYTHON → .venv/Scripts/python.exe（Windows）
# → .venv/bin/python（POSIX）→ 容器系统 python/python3；全部落空 = 明确
# FAIL，显式 PYTHON 不可用即 FAIL（不静默换用其它解释器）。
# exec 替换进程：探针退出码原样透传，不吞不改。
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { printf '[smoke-voice-local] FAIL: %s\n' "$*" >&2; exit 1; }

# M14-210 Python 选择链：显式 PYTHON → 仓库 venv（Windows → POSIX）→ 容器
# 系统 python/python3（生产镜像 /app 无仓库 venv，冒烟脚本随镜像打包后由
# 系统解释器执行探针）。显式 PYTHON 不可用即 FAIL——不静默换用其它解释器。
select_python() {
  if [ -n "${PYTHON:-}" ]; then
    command -v "$PYTHON" >/dev/null 2>&1 || fail "PYTHON 指定的解释器不可用: $PYTHON"
    return 0
  fi
  local _candidate
  for _candidate in .venv/Scripts/python.exe .venv/bin/python python python3; do
    if [ -x "$_candidate" ] || command -v "$_candidate" >/dev/null 2>&1; then
      PYTHON="$_candidate"
      return 0
    fi
  done
  fail "找不到可用 python（选择链：PYTHON= → .venv/Scripts/python.exe → .venv/bin/python → python → python3）"
}
select_python

exec "$PYTHON" tools/voice/smoke_local_voice.py
