#!/usr/bin/env bash
# M14-01 compose 本地语音引擎可达性检查：host.docker.internal → WSL 127.0.0.1 服务。
#
# 目的（supervisor correction）：实证「容器内 API → 宿主 WSL 引擎」的完整网络路径
#   容器 → host.docker.internal（宿主网关）→ WSL2 localhost 转发 → WSL 内 127.0.0.1 服务，
# 不下载模型、不启动任何真实引擎——用 stdlib http.server 在 WSL 127.0.0.1 上起探针
# （绝不绑 0.0.0.0），再用 docker 容器访问 host.docker.internal 验证，随后清理。
#
# 在哪运行：**推荐 Windows 侧**（PowerShell / Git Bash——Docker Desktop 的 daemon
#   在 Windows named pipe 上，WSL 内的 docker.exe 访问不到）；探针服务恒在 WSL 内
#   由本脚本经 wsl.exe 编排，无需手动两步。仓库在 D:\ 时：
#   PowerShell: bash "D:\AI Learning OS\<repo-dir>\tools\voice\compose_voice_reachability.sh"
#   Git Bash:   bash tools/voice/compose_voice_reachability.sh
#   （WSL 内直接运行亦可：探针本地起，docker 用 Docker Desktop 的 WSL 集成——
#    若 daemon 不可达会明确 FAIL 并指引回 Windows 侧运行）
#
# 可选环境变量：
#   VOICE_PROBE_PORT     探针端口（默认 18010；不用 8010/8011 以免与真实引擎冲突）
#   DOCKER_PROBE_IMAGE   容器探针镜像（默认 aios/api:local——本机必含且带 python；
#                        任意含 python 的镜像均可，如 python:3.12-alpine）
set -euo pipefail

PORT="${VOICE_PROBE_PORT:-18010}"
IMAGE="${DOCKER_PROBE_IMAGE:-aios/api:local}"
WSL_LOOPBACK="127.0.0.1"

say() { printf '[voice-reachability] %s\n' "$*"; }
fail() { printf '[voice-reachability] FAIL: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker CLI 不可用（Windows 侧需 Docker Desktop；WSL 侧需其 WSL 集成）"
docker info >/dev/null 2>&1 || fail "docker daemon 不可达（Docker Desktop 未启动？——注意 WSL 内的 docker.exe 访问不到 Windows named pipe，请在 Windows 侧 PowerShell/Git Bash 运行本脚本）"

IN_WSL=0
if grep -qi microsoft /proc/version 2>/dev/null; then
  IN_WSL=1
fi

PROBE_PID=""
WSL_STARTED=0
cleanup() {
  if [ -n "$PROBE_PID" ]; then
    kill "$PROBE_PID" >/dev/null 2>&1 || true
    wait "$PROBE_PID" 2>/dev/null || true
  fi
  if [ "$WSL_STARTED" = "1" ]; then
    # setsid 探针无会话句柄——按「命令+探针端口」精确清理（勿把 VOICE_PROBE_PORT
    # 设为引擎端口：清理会按该端口匹配 http.server 进程）
    wsl.exe -e bash -c "pkill -f 'http.server $PORT' 2>/dev/null || true" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# ---- WSL 侧探针服务：stdlib http.server，只绑 127.0.0.1 ----
if [ "$IN_WSL" = "1" ]; then
  say "WSL 内运行：本地起探针（http.server 仅绑 $WSL_LOOPBACK:$PORT）"
  python3 -m http.server "$PORT" --bind "$WSL_LOOPBACK" >/dev/null 2>&1 &
  PROBE_PID=$!
else
  command -v wsl.exe >/dev/null 2>&1 || command -v wsl >/dev/null 2>&1 || fail "非 WSL 环境且找不到 wsl.exe（探针服务需要 WSL）"
  WSL_CMD="$(command -v wsl.exe || command -v wsl)"
  say "Windows 侧运行：经 WSL 起探针（http.server 仅绑 WSL $WSL_LOOPBACK:$PORT）"
  # setsid+nohup 必需：wsl.exe 会话退出会连带终止同会话后台进程（实测 WSL 2.7.13），
  # setsid 脱离会话后探针才能存活供宿主/容器探测。
  "$WSL_CMD" -e bash -c "setsid nohup python3 -m http.server $PORT --bind $WSL_LOOPBACK >/dev/null 2>&1 < /dev/null & sleep 0.5; ss -tln | grep -q ':$PORT ' || exit 1" >/dev/null 2>&1 || fail "WSL 探针启动失败（wsl.exe 不可用或 WSL 内无 python3）"
  WSL_STARTED=1
fi
sleep 1

# Windows 侧先确认宿主 localhost 能到 WSL 探针（WSL2 localhostForwarding），
# 再由容器经 host.docker.internal 验证完整路径。
if [ "$IN_WSL" != "1" ]; then
  if ! curl -sSf -o /dev/null --max-time 5 "http://$WSL_LOOPBACK:$PORT/"; then
    fail "宿主 localhost:$PORT 到 WSL 探针不可达（WSL2 localhostForwarding 未生效或被关闭）"
  fi
  say "宿主 localhost → WSL 探针就绪（localhostForwarding 生效）"
fi

# ---- 容器侧：经 host.docker.internal 访问（与 compose api 服务同路径）----
# --add-host host-gateway 与 compose api 服务的 extra_hosts 一致；镜像内用 python
# stdlib 发请求（与 api healthcheck 同款），不依赖 curl/wget。
say "容器探针：docker run $IMAGE → http://host.docker.internal:$PORT/"
if ! docker run --rm \
  --add-host host.docker.internal:host-gateway \
  "$IMAGE" \
  python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://host.docker.internal:${PORT}/', timeout=5).status == 200 else 1)"; then
  fail "容器无法经 host.docker.internal:$PORT 到达 WSL $WSL_LOOPBACK:$PORT 服务——检查：① Docker Desktop 是否运行 ② WSL2 localhostForwarding 是否生效 ③ 引擎端口与 AIOS_*_LOCAL_ENDPOINT 是否一致"
fi

say "PASS：容器 → host.docker.internal:$PORT → WSL $WSL_LOOPBACK:$PORT 网络路径可用"
say "结论：compose 形态配 AIOS_ASR_LOCAL_ENDPOINT=http://host.docker.internal:8010/v1（TTS 同理 :8011）即可让容器内 API 调用宿主 WSL 引擎；引擎自身仍只绑 WSL 127.0.0.1"
