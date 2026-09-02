#!/usr/bin/env bash
# M8-00/M9-01 Docker 生产本地版冒烟（假定 compose 栈已 up）
# 用法：bash infra/smoke_docker.sh
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f infra/docker-compose.yml --profile local"
API=http://127.0.0.1:8000
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

# --- 2. 豁免端点 + Web ---
say "auth probe"
status=$(curl -s -m 15 "$API/api/v1/auth/status")
enabled=$(printf '%s' "$status" | python3 -c 'import json,sys; print(json.load(sys.stdin)["auth_enabled"])')
say "auth_enabled = $enabled"

for path in /health /api/v1/version /api/v1/auth/status; do
  code=$(http_code "$API$path")
  [ "$code" = "200" ] || fail "API $path -> $code"
  say "GET $path -> 200"
done

expected_version=$(tr -d '[:space:]' < VERSION)
got_version=$(curl -s -m 15 "$API/api/v1/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
[ "$got_version" = "$expected_version" ] || fail "/api/v1/version 返回 $got_version，期望 $expected_version"
say "version = $got_version"

code=$(http_code "$WEB/")
[ "$code" = "200" ] || fail "Web 首页 -> $code"
say "GET Web / -> 200"

code=$(http_code "$WEB/login")
[ "$code" = "200" ] || fail "Web /login -> $code"
say "GET Web /login -> 200"

# --- 3. /papers 认证两态断言 ---
case "$enabled" in
  True)
    code=$(http_code "$API/api/v1/papers")
    [ "$code" = "401" ] || fail "认证开启时无 token /papers 应 401，实得 $code"
    say "GET /papers without token -> 401 (gate enforced)"
    AUTH=""  # 真实 token 在 register/login 后补上（见第 5 节）
    ;;
  False)
    code=$(http_code "$API/api/v1/papers")
    [ "$code" = "200" ] || fail "认证关闭时 /papers 应直通 200，实得 $code"
    say "GET /papers without token -> 200 (auth disabled, honest status)"
    AUTH=""
    ;;
  *)
    fail "auth/status 响应异常: $status"
    ;;
esac

# --- 4. 认证开启时 register/login/me 闭环 ---
if [ "$enabled" = "True" ]; then
  smoke_user="smoke_$$_$RANDOM"
  auth_body="{\"username\":\"$smoke_user\",\"password\":\"smoke-password-123\"}"
  rcode=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST -H 'Content-Type: application/json' -d "$auth_body" "$API/api/v1/auth/register")
  [ "$rcode" = "201" ] || fail "register -> $rcode"
  token=$(curl -s -m 15 -X POST -H 'Content-Type: application/json' -d "$auth_body" "$API/api/v1/auth/login" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))')
  [ -n "$token" ] || fail "login 未返回 token"
  mcode=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -H "Authorization: Bearer $token" "$API/api/v1/auth/me")
  [ "$mcode" = "200" ] || fail "me with token -> $mcode"
  say "register/login/me -> 201/200/200"
fi

# --- 5. 上传 -> 重启 -> 读回（开启时带 token）---
payload=$(mktemp /tmp/smoke-payload-XXXXXX.json)
trap 'rm -f "$payload"' EXIT
if command -v cygpath >/dev/null 2>&1; then
  payload_curl=$(cygpath -m "$payload")
else
  payload_curl="$payload"
fi
printf '{"question":"does the object survive an api restart?"}' > "$payload"

if [ "$enabled" = "True" ]; then
  # 换上真实 token
  AUTH="Authorization: Bearer $token"
fi

resp=$(curl -s -m 30 -H "$AUTH" -F "file=@$payload_curl;type=application/json" "$API/api/v1/resources/upload")
rid=$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')
[ -n "$rid" ] || fail "上传失败: $resp"
say "uploaded resource_id=$rid"

say "restarting api ..."
$COMPOSE restart api >/dev/null
wait_healthy api 90

pcode=$(curl -s -o /dev/null -w '%{http_code}' -m 30 -X POST -H "$AUTH" "$API/api/v1/resources/$rid/parse")
[ "$pcode" = "200" ] || fail "重启后 parse -> $pcode（对象丢失 = 存储回退内存实现）"
say "POST /resources/$rid/parse after restart -> 200"

say "ALL SMOKE CHECKS PASSED"
