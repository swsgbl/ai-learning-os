#!/bin/sh
# M10-05 外部 coturn fail-closed entrypoint。
# 职责：1) 校验必需变量/取值（compose :? 挡缺失，这里再挡占位、弱值、冲突、假 TLS）；
# 2) 运行时在容器内生成 /run/aios-turnserver.conf——secret 只经部署 env 注入，
#    绝不落仓库/镜像（仓库内只有不含 secret 的展示示例 turnserver.conf.example）；
# 3) exec turnserver。COTURN_CONFIG_ONLY=true 时仅打印配置（secret 脱敏）退出，
#    供自动化测试与部署前排障使用。
set -eu

fail() {
  echo "[aios-coturn] FAIL-CLOSED: $1" >&2
  exit 1
}

info() {
  echo "[aios-coturn] $1" >&2
}

is_true() {
  case "$1" in
    1 | true | TRUE | yes) return 0 ;;
    *) return 1 ;;
  esac
}

is_false() {
  case "$1" in
    0 | false | FALSE | no | "") return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------- 1) 必需变量
SECRET="${COTURN_STATIC_AUTH_SECRET:-}"
EXTERNAL_IP="${COTURN_EXTERNAL_IP:-}"
# 防 CRLF 污染：Windows autocrlf 环境复制的 .env 可能让值尾部带 \r（secret 与
# LiveKit 侧静默不一致的隐性陷阱），读入即剥离
SECRET=$(printf '%s' "$SECRET" | tr -d '\r')
EXTERNAL_IP=$(printf '%s' "$EXTERNAL_IP" | tr -d '\r')
[ -n "$SECRET" ] || fail "COTURN_STATIC_AUTH_SECRET 未设置（compose :? 已挡，此处兜底）"
[ -n "$EXTERNAL_IP" ] || fail "COTURN_EXTERNAL_IP 未设置——必须显式给出对外可达 IP，不允许静默猜测"

# ---------------------------------------------------------------- 2) secret 占位/弱值拒绝
case "$SECRET" in
  *"<"* | *">"* | *"{"* | *"}"*)
    fail "COTURN_STATIC_AUTH_SECRET 仍是模板占位符——用 openssl rand -hex 32 生成部署 secret 注入" ;;
esac
case "$SECRET" in
  changeme | change-me | placeholder | example | dummy | secret | password | test | your*here)
    fail "COTURN_STATIC_AUTH_SECRET 是常见占位词——拒绝启动" ;;
esac
case "$SECRET" in
  *aios-local-dev*)
    fail "COTURN_STATIC_AUTH_SECRET 是仓库开发占位值（不可用于 coturn）" ;;
esac
SECRET_LEN=${#SECRET}
[ "$SECRET_LEN" -ge 32 ] || fail "COTURN_STATIC_AUTH_SECRET 长度 $SECRET_LEN < 32——拒绝弱 secret（openssl rand -hex 32）"

# ---------------------------------------------------------------- 3) external-ip 占位/loopback 拒绝
case "$EXTERNAL_IP" in
  *"<"* | *">"* | *"{"* | *"}"* | *example* | *your*)
    fail "COTURN_EXTERNAL_IP 仍是模板占位符——必须填真实公网/局域网 IP" ;;
esac
case "$EXTERNAL_IP" in
  127.* | ::1 | localhost)
    if ! is_true "${COTURN_ALLOW_LOOPBACK_EXTERNAL:-false}"; then
      fail "COTURN_EXTERNAL_IP 是 loopback——只允许本机监听冒烟（需显式 COTURN_ALLOW_LOOPBACK_EXTERNAL=true），生产必须真实可达 IP"
    fi
    info "警告：loopback external-ip 仅用于本机监听冒烟，绝不能用于生产部署" ;;
esac

# ---------------------------------------------------------------- 4) 端口与 relay 段校验
RELAY_START="${COTURN_RELAY_PORT_START:-50000}"
RELAY_END="${COTURN_RELAY_PORT_END:-50099}"
LISTEN_PORT="${COTURN_LISTEN_PORT:-3478}"
TLS_PORT="${COTURN_TLS_PORT:-5349}"
REALM="${COTURN_REALM:-ai-learning-os}"

case "$RELAY_START$RELAY_END$LISTEN_PORT$TLS_PORT" in
  *[!0-9]*)
    fail "端口必须为纯数字：RELAY=$RELAY_START-$RELAY_END LISTEN=$LISTEN_PORT TLS=$TLS_PORT" ;;
esac
[ "$RELAY_START" -ge 1024 ] || fail "COTURN_RELAY_PORT_START=$RELAY_START < 1024（拒绝特权端口）"
[ "$RELAY_START" -le "$RELAY_END" ] || fail "COTURN_RELAY_PORT_START($RELAY_START) > COTURN_RELAY_PORT_END($RELAY_END)"
RELAY_COUNT=$((RELAY_END - RELAY_START + 1))
[ "$RELAY_COUNT" -le 2000 ] || fail "relay 段 $RELAY_COUNT 个端口 > 2000——compose 逐端口映射开销过大，请缩小范围或改用 network_mode: host"

# 同机部署与主栈 LiveKit 媒体面 UDP 7882-7892 的硬冲突检查（listening 走 UDP+TCP 也一并查）
if ! is_true "${COTURN_ALLOW_LIVEKIT_PORT_OVERLAP:-false}"; then
  if [ "$RELAY_START" -le 7892 ] && [ "$RELAY_END" -ge 7882 ]; then
    fail "relay 段 $RELAY_START-$RELAY_END 与主栈 LiveKit UDP 7882-7892 冲突（跨机部署可显式 COTURN_ALLOW_LIVEKIT_PORT_OVERLAP=true）"
  fi
  if [ "$LISTEN_PORT" -ge 7882 ] && [ "$LISTEN_PORT" -le 7892 ]; then
    fail "COTURN_LISTEN_PORT=$LISTEN_PORT 落在主栈 LiveKit UDP 7882-7892 内——同机冲突"
  fi
fi

# ---------------------------------------------------------------- 5) TLS：启用即要求真实证书，不做假 TLS
CERT_FILE="${COTURN_CERT_FILE:-/etc/coturn/tls/cert.pem}"
PKEY_FILE="${COTURN_PKEY_FILE:-/etc/coturn/tls/key.pem}"
TLS_ENABLED_RAW="${COTURN_TLS_ENABLED:-false}"
if is_true "$TLS_ENABLED_RAW"; then
  [ -f "$CERT_FILE" ] || fail "COTURN_TLS_ENABLED=true 但证书不存在: $CERT_FILE——先挂载证书目录（compose 里取消注释 tls 卷），不做假 TLS 声明"
  [ -f "$PKEY_FILE" ] || fail "COTURN_TLS_ENABLED=true 但私钥不存在: $PKEY_FILE"
  TLS_CONF="tls-listening-port=$TLS_PORT
cert=$CERT_FILE
pkey=$PKEY_FILE"
  info "TLS 已启用：tls-listening-port=$TLS_PORT cert=$CERT_FILE"
elif is_false "$TLS_ENABLED_RAW"; then
  TLS_CONF=""
else
  fail "COTURN_TLS_ENABLED 只接受 true/false（当前: $TLS_ENABLED_RAW）"
fi

# ---------------------------------------------------------------- 6) 运行时生成配置（secret 不落仓库）
# 容器内默认写 /tmp（官方镜像以非 root 运行，/run 不可写）；COTURN_CONF_PATH 供宿主机
# COTURN_CONFIG_ONLY 排障/测试指定输出路径
CONF="${COTURN_CONF_PATH:-/tmp/aios-turnserver.conf}"
cat >"$CONF" <<EOF
# M10-05 由 aios-coturn-entrypoint 在容器内运行时生成。
# secret 只经部署 env 注入；展示示例（不含 secret）见 infra/coturn/turnserver.conf.example。
listening-port=$LISTEN_PORT
external-ip=$EXTERNAL_IP
min-port=$RELAY_START
max-port=$RELAY_END
realm=$REALM
fingerprint
lt-cred-mech
static-auth-secret=$SECRET
no-multicast-peers
$TLS_CONF
EOF
chmod 600 "$CONF"
# 加固说明（M10-05，经 coturn/coturn:latest 容器实测校准）：
# - 不写 no-tlsv1/no-tlsv1_1/no-loopback-peers——当前 coturn 已移除这些选项，
#   写入只会得到 Bad configuration format 告警；新版本默认等价防护
#   （旧 TLS 默认禁用、loopback relay 目标默认拒绝）；
# - 不写 no-cli（已废弃）——未设置 cli-password 时 CLI 默认关闭；
# - no-multicast-peers 仍为有效选项，显式保留。
if is_true "${COTURN_VERBOSE:-false}"; then
  printf '%s\n' 'verbose' >>"$CONF"
fi

# ---------------------------------------------------------------- 7) CONFIG_ONLY / exec
if is_true "${COTURN_CONFIG_ONLY:-false}"; then
  sed -e "s/^static-auth-secret=.*/static-auth-secret=<redacted len=$SECRET_LEN>/" "$CONF"
  info "COTURN_CONFIG_ONLY=true：仅打印配置（secret 已脱敏），未启动 turnserver"
  exit 0
fi

info "启动 turnserver：listen=$LISTEN_PORT/udp+tcp relay=$RELAY_START-$RELAY_END/udp+tcp external-ip=$EXTERNAL_IP tls=$TLS_ENABLED_RAW realm=$REALM"
exec turnserver -c "$CONF" --log-file=stdout
