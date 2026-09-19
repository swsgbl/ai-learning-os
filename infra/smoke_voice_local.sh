#!/usr/bin/env bash
# M14-70 本地语音链路冒烟 wrapper：定位项目 venv 解释器后原样 exec
# tools/voice/smoke_local_voice.py（M14-01 探针——判分/输出/环境契约全部由
# 探针自身负责；本脚本零探针复制、零 env 修改、零 secret 感知）。
# Python 选择顺序：.venv/Scripts/python.exe（Windows）→ .venv/bin/python
# （POSIX）；两者皆缺 = 明确 FAIL（可用 PYTHON= 显式指定）。
# exec 替换进程：探针退出码原样透传，不吞不改。
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { printf '[smoke-voice-local] FAIL: %s\n' "$*" >&2; exit 1; }

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PYTHON" ] || PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || fail "找不到项目 venv python（用 PYTHON= 指定）"

exec "$PYTHON" tools/voice/smoke_local_voice.py
