#!/usr/bin/env bash
# M9-08 语音连通性验证（local/public 两套模式，真实客户端鉴权+连接）。
# 用法：AIOS_MODE=local|public bash infra/smoke_voice.sh
#   local：默认 compose 栈（127.0.0.1）。
#   public：需先按 DEVELOPMENT 局域网模式带强配置启动栈，并传
#           AIOS_PUBLIC_HOST=<LAN_IP>（脚本据此推导 ws_url 断言）。
# 每一步明确 PASS/FAIL；任何一步失败即整体 FAIL（绝不以 health 200 冒充语音可用）。
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${AIOS_MODE:-local}"
API=${AIOS_API_URL:-http://127.0.0.1:8000}
PY=.venv/Scripts/python.exe
[ -x "$PY" ] || PY=.venv/bin/python
fail() { printf '[voice] FAIL: %s\n' "$*" >&2; exit 1; }
say() { printf '[voice] %s\n' "$*"; }

say "mode=$MODE"

# --- 1. 注册/登录 ---
suffix="$$_$RANDOM"
body="{\"username\":\"voice_smoke_$suffix\",\"password\":\"voice-smoke-123\"}"
reg=$(curl -s -m 15 -X POST -H 'Content-Type: application/json' -d "$body" "$API/api/v1/auth/register")
lid=$(curl -s -m 15 -X POST -H 'Content-Type: application/json' -d "$body" "$API/api/v1/auth/login" \
  | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))')
[ -n "$lid" ] || fail "login 未返回 token: $reg"
say "login ok"

# --- 2. 签发语音 token（拿到浏览器可达 ws_url 与 access token）---
room="smoke-$suffix"
vresp=$(curl -s -m 15 -X POST -H "Authorization: Bearer $lid" -H 'Content-Type: application/json' \
  -d "{\"room\":\"$room\",\"identity\":\"smoke-$suffix\",\"role\":\"student\"}" \
  "$API/api/v1/voice/token")
ws_url=$(printf '%s' "$vresp" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("ws_url",""))')
access=$(printf '%s' "$vresp" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("token",""))')
[ -n "$ws_url" ] || fail "voice token 响应缺 ws_url: $vresp"
[ -n "$access" ] || fail "voice token 响应缺 token: $vresp"
say "voice token ok: ws_url=$ws_url"

# --- 3. 模式断言：public 模式 ws_url 必须是局域网可达地址（不含容器内部地址）---
if [ "$MODE" = "public" ]; then
  host="${AIOS_PUBLIC_HOST:-}"
  [ -n "$host" ] || fail "public 模式需传 AIOS_PUBLIC_HOST=<LAN_IP>"
  case "$ws_url" in
    "ws://$host:7880"|"wss://$host:7880") say "PASS: ws_url 指向 $host" ;;
    *) fail "ws_url=$ws_url 未指向公开地址 $host（容器内部地址泄漏？）" ;;
  esac
else
  case "$ws_url" in
    "ws://127.0.0.1:7880") say "PASS: ws_url = 127.0.0.1（本机模式）" ;;
    *) fail "本机模式 ws_url=$ws_url 应为 ws://127.0.0.1:7880" ;;
  esac
fi

# --- 4. 真实客户端连接（livekit.rtc：connect -> CONNECTED -> data publish）---
say "launching real client (livekit.rtc)..."
"$PY" infra/voice_connect_check.py --url "$ws_url" --token "$access" --room "$room" \
  && say "PASS: real client connected + data channel" \
  || fail "real client 连接失败（鉴权/网络/媒体协商任一失败）"

say "ALL VOICE CHECKS PASSED ($MODE)"
