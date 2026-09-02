#!/usr/bin/env bash
# M8-00 Docker 生产本地版冒烟（假定 compose 栈已 up）：
#   1. postgres/redis/minio/api/web/livekit 全部 healthy
#   2. API /health、/api/v1/version（== 仓库根 VERSION）、/api/v1/papers、Web 首页 全 200
#   3. 上传对象 -> 重启 api -> 解析读回（MemoryObjectStore 回退会在此暴露为 500）
# 用法：bash infra/smoke_docker.sh
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f infra/docker-compose.yml --profile local"
API=http://127.0.0.1:8000
# 与 compose 的 ${AIOS_WEB_PORT:-3000} 插值一致（宿主 3000 被占时 AIOS_WEB_PORT=3100）
WEB="http://127.0.0.1:${AIOS_WEB_PORT:-3000}"

say() { printf '[smoke] %s\n' "$*"; }
fail() { printf '[smoke] FAIL: %s\n' "$*" >&2; exit 1; }

wait_healthy() {
  local svc="$1" tries="${2:-60}" cid
  cid=$($COMPOSE ps -q "$svc")
  [ -n "$cid" ] || fail "$svc 容器不存在（栈未启动？）"
  for _ in $(seq 1 "$tries"); do
    if [ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" = "healthy" ]; then
      say "$svc healthy"
      return 0
    fi
    sleep 2
  done
  fail "$svc 在 $((tries * 2))s 内未达 healthy"
}

http_code() { curl -s -o /dev/null -w '%{http_code}' -m 15 "$1"; }

# --- 1. 全服务 healthy ---
for svc in postgres redis minio api web livekit; do
  wait_healthy "$svc"
done

# --- 2. 关键端点 ---
for path in /health /api/v1/papers; do
  code=$(http_code "$API$path")
  [ "$code" = "200" ] || fail "API $path -> $code"
  say "GET $path -> 200"
done

expected_version=$(tr -d '[:space:]' < VERSION)
got_version=$(curl -s -m 15 "$API/api/v1/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
[ "$got_version" = "$expected_version" ] || fail "/api/v1/version 返回 $got_version，期望 $expected_version（VERSION 未随镜像打包？）"
say "GET /api/v1/version -> $got_version"

code=$(http_code "$WEB/")
[ "$code" = "200" ] || fail "Web 首页 -> $code"
say "GET Web / -> 200"

# --- 3. 上传 -> 重启 api -> 读回 ---
payload=$(mktemp /tmp/smoke-payload-XXXXXX.json)
trap 'rm -f "$payload"' EXIT
# Git Bash 的 Windows 原生 curl 读不了 MSYS /tmp 路径；CI (Linux) 无 cygpath
if command -v cygpath >/dev/null 2>&1; then
  payload_curl=$(cygpath -m "$payload")
else
  payload_curl="$payload"
fi
printf '{"question":"does the object survive an api restart?"}' > "$payload"

resp=$(curl -s -m 30 -F "file=@$payload_curl;type=application/json" "$API/api/v1/resources/upload")
rid=$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')
[ -n "$rid" ] || fail "上传失败: $resp"
say "uploaded resource_id=$rid"

say "restarting api ..."
$COMPOSE restart api >/dev/null
wait_healthy api 90

pcode=$(curl -s -o /dev/null -w '%{http_code}' -m 30 -X POST "$API/api/v1/resources/$rid/parse")
# parse 需要从对象存储读回原文：内存回退在重启后丢对象 -> 500；MinIO 持久 -> 200
[ "$pcode" = "200" ] || fail "重启后 parse -> $pcode（对象丢失 = 存储回退内存实现）"
say "POST /resources/$rid/parse after restart -> 200"

say "ALL SMOKE CHECKS PASSED"
