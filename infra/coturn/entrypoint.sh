#!/bin/sh
# M10-05 外部 coturn fail-closed entrypoint。
# 职责：1) 校验必需变量/取值（compose :? 挡缺失，这里再挡占位、弱值、无效布尔、
#    端口越界/自冲突/LiveKit 同机冲突、配置注入字符、假 TLS）；
# 2) 运行时在容器内生成 /tmp/aios-turnserver.conf——secret 只经部署 env 注入，
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

# 布尔开关严格小写 true/false——maybe/1/yes/on 等无效值一律拒绝启动，
# 绝不静默当 false（fail-closed，Codex 返工缺陷 5）
require_bool() { # $1=值 $2=变量名
  case "$1" in
    true | false) return 0 ;;
    *) fail "$2 只接受 true/false（当前: '$1'）——无效布尔值拒绝启动，不静默当 false" ;;
  esac
}

# 写入配置文件的值共用的注入防线：非可打印字符（换行/CR/控制字符）与空白
# 都可能向生成的 turnserver 配置注入额外配置行——一律拒绝（Codex 返工缺陷 4）
reject_config_metachars() { # $1=值 $2=变量名
  case "$1" in
    *[![:print:]]*)
      fail "$2 含非可打印字符（换行/CR/控制字符）——拒绝写入配置（防配置注入）" ;;
    *[[:space:]]*)
      fail "$2 含空白字符——拒绝写入配置（防配置注入）" ;;
  esac
}

# 严格 IPv4 校验：四段 0-255、仅数字与点（本模板生产路径为 IPv4；
# IPv6 不支持，见 docs/COTURN_DEPLOYMENT.md 边界声明）
is_valid_ipv4() { # $1=值
  case "$1" in
    "" | *.*.*.*.* | *[!0-9.]*) return 1 ;;
  esac
  oldIFS=$IFS
  IFS=.
  set -- $1
  IFS=$oldIFS
  [ $# -eq 4 ] || return 1
  for octet in "$@"; do
    [ "${#octet}" -le 3 ] || return 1
    [ "$octet" -le 255 ] || return 1
  done
  return 0
}

# 容器内文件路径防线：绝对路径（/ 开头）+ 字符白名单（无空白/换行/元字符）
require_abs_path() { # $1=值 $2=变量名
  reject_config_metachars "$1" "$2"
  case "$1" in
    /*) ;;
    *) fail "$2 必须是容器内绝对路径（以 / 开头，当前: '$1'）" ;;
  esac
  case "$1" in
    *[!A-Za-z0-9._/-]*)
      fail "$2 含空白/换行/不安全字符（仅允许字母数字与 . _ - /，当前: '$1'）——拒绝写入配置" ;;
  esac
}

# 防 CRLF 污染：Windows autocrlf 环境复制的 .env 可能让值混入 \r（secret 与
# LiveKit 侧静默不一致的隐性陷阱），读入即剥离
strip_cr() {
  printf '%s' "$1" | tr -d '\r'
}

# ---------------------------------------------------------------- 1) 必需变量
SECRET=$(strip_cr "${COTURN_STATIC_AUTH_SECRET:-}")
EXTERNAL_IP=$(strip_cr "${COTURN_EXTERNAL_IP:-}")
[ -n "$SECRET" ] || fail "COTURN_STATIC_AUTH_SECRET 未设置（compose :? 已挡，此处兜底）"
[ -n "$EXTERNAL_IP" ] || fail "COTURN_EXTERNAL_IP 未设置——必须显式给出对外可达 IP，不允许静默猜测"
reject_config_metachars "$SECRET" COTURN_STATIC_AUTH_SECRET

# ---------------------------------------------------------------- 2) 布尔开关严格化
require_bool "${COTURN_TLS_ENABLED:-false}" COTURN_TLS_ENABLED
require_bool "${COTURN_VERBOSE:-false}" COTURN_VERBOSE
require_bool "${COTURN_CONFIG_ONLY:-false}" COTURN_CONFIG_ONLY
require_bool "${COTURN_ALLOW_LOOPBACK_EXTERNAL:-false}" COTURN_ALLOW_LOOPBACK_EXTERNAL
require_bool "${COTURN_ALLOW_LIVEKIT_PORT_OVERLAP:-false}" COTURN_ALLOW_LIVEKIT_PORT_OVERLAP

# ---------------------------------------------------------------- 3) secret 占位/弱值拒绝
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

# ---------------------------------------------------------------- 4) external-ip 占位/格式/loopback
case "$EXTERNAL_IP" in
  *"<"* | *">"* | *"{"* | *"}"* | *example* | *your*)
    fail "COTURN_EXTERNAL_IP 仍是模板占位符——必须填真实公网/局域网 IP" ;;
esac
# 严格 IPv4 / PUBLIC/PRIVATE IPv4（1:1 NAT）——任意字符串放行会同时打开配置注入面
PUBLIC_IP="${EXTERNAL_IP%%/*}"
PRIVATE_IP="${EXTERNAL_IP#*/}"
if [ "$PUBLIC_IP" = "$PRIVATE_IP" ]; then
  is_valid_ipv4 "$EXTERNAL_IP" || \
    fail "COTURN_EXTERNAL_IP 必须是合法 IPv4（当前: '$EXTERNAL_IP'）——本模板仅支持 IPv4 与 PUBLIC/PRIVATE IPv4 形式，不支持 IPv6/主机名"
else
  is_valid_ipv4 "$PUBLIC_IP" || fail "COTURN_EXTERNAL_IP 的 PUBLIC 部分（'$PUBLIC_IP'）不是合法 IPv4"
  is_valid_ipv4 "$PRIVATE_IP" || fail "COTURN_EXTERNAL_IP 的 PRIVATE 部分（'$PRIVATE_IP'）不是合法 IPv4"
fi
case "$EXTERNAL_IP" in
  127.*)
    if [ "${COTURN_ALLOW_LOOPBACK_EXTERNAL:-false}" != "true" ]; then
      fail "COTURN_EXTERNAL_IP 是 loopback——只允许本机监听冒烟（需显式 COTURN_ALLOW_LOOPBACK_EXTERNAL=true），生产必须真实可达 IP"
    fi
    info "警告：loopback external-ip 仅用于本机监听冒烟，绝不能用于生产部署" ;;
esac

# ---------------------------------------------------------------- 5) 端口与 relay 段校验
RELAY_START=$(strip_cr "${COTURN_RELAY_PORT_START:-50000}")
RELAY_END=$(strip_cr "${COTURN_RELAY_PORT_END:-50099}")
LISTEN_PORT=$(strip_cr "${COTURN_LISTEN_PORT:-3478}")
TLS_PORT=$(strip_cr "${COTURN_TLS_PORT:-5349}")
REALM=$(strip_cr "${COTURN_REALM:-ai-learning-os}")
CERT_FILE=$(strip_cr "${COTURN_CERT_FILE:-/etc/coturn/tls/cert.pem}")
PKEY_FILE=$(strip_cr "${COTURN_PKEY_FILE:-/etc/coturn/tls/key.pem}")

case "$RELAY_START$RELAY_END$LISTEN_PORT$TLS_PORT" in
  *[!0-9]*)
    fail "端口必须为纯数字：RELAY=$RELAY_START-$RELAY_END LISTEN=$LISTEN_PORT TLS=$TLS_PORT" ;;
esac
# 全部端口（listening/TLS/relay 起止）都必须落在 1024-65535：拒绝特权端口，
# 也拒绝 65536 这类超出 TCP/UDP 合法上限的值（Codex 返工缺陷 2）
check_port_range() { # $1=值 $2=变量名
  [ "$1" -ge 1024 ] || fail "$2=$1 < 1024（拒绝特权端口；listening/TLS/relay 全部要求 1024-65535）"
  [ "$1" -le 65535 ] || fail "$2=$1 > 65535（超出 TCP/UDP 合法端口上限；listening/TLS/relay 全部要求 1024-65535）"
}
check_port_range "$RELAY_START" COTURN_RELAY_PORT_START
check_port_range "$RELAY_END" COTURN_RELAY_PORT_END
check_port_range "$LISTEN_PORT" COTURN_LISTEN_PORT
check_port_range "$TLS_PORT" COTURN_TLS_PORT
[ "$RELAY_START" -le "$RELAY_END" ] || fail "COTURN_RELAY_PORT_START($RELAY_START) > COTURN_RELAY_PORT_END($RELAY_END)"
RELAY_COUNT=$((RELAY_END - RELAY_START + 1))
[ "$RELAY_COUNT" -le 2000 ] || fail "relay 段 $RELAY_COUNT 个端口 > 2000——compose 逐端口映射开销过大，请缩小范围或改用 network_mode: host"

# 自身映射冲突无条件拒绝（无豁免开关）：compose 对 listening UDP+TCP、TLS TCP、
# relay UDP+TCP 全部无条件映射——任何两个角色占同一宿主端口都直接冲突。TLS 未启用
# 时同样拒绝（TLS 端口映射一直在，Codex 返工缺陷 3）
[ "$LISTEN_PORT" -ne "$TLS_PORT" ] || \
  fail "COTURN_LISTEN_PORT 与 COTURN_TLS_PORT 同为 $LISTEN_PORT——compose 无条件映射 TLS TCP 端口，端口自冲突拒绝（无豁免）"
if [ "$LISTEN_PORT" -ge "$RELAY_START" ] && [ "$LISTEN_PORT" -le "$RELAY_END" ]; then
  fail "COTURN_LISTEN_PORT=$LISTEN_PORT 落在 relay 段 $RELAY_START-$RELAY_END 内——端口自冲突拒绝（无豁免）"
fi
if [ "$TLS_PORT" -ge "$RELAY_START" ] && [ "$TLS_PORT" -le "$RELAY_END" ]; then
  fail "COTURN_TLS_PORT=$TLS_PORT 落在 relay 段 $RELAY_START-$RELAY_END 内——compose 无条件映射 TLS TCP 端口，端口自冲突拒绝（无豁免）"
fi

# 同机部署与主栈 LiveKit 宿主端口的冲突检查（Codex 返工缺陷 1）：LiveKit 占用
# TCP 7880(signal)/7881(rtc-tcp) + UDP 7882-7892(媒体)，即连续段 7880-7892。
# coturn 各角色 UDP+TCP 都做宿主映射，故按协议无关的端口区间判定；
# 跨机部署（coturn 与 LiveKit 不同宿主）可显式豁免
if [ "${COTURN_ALLOW_LIVEKIT_PORT_OVERLAP:-false}" != "true" ]; then
  if [ "$RELAY_START" -le 7892 ] && [ "$RELAY_END" -ge 7880 ]; then
    fail "relay 段 $RELAY_START-$RELAY_END 与主栈 LiveKit 端口 7880-7892（TCP 7880 signal / TCP 7881 rtc / UDP 7882-7892 媒体）冲突（跨机部署可显式 COTURN_ALLOW_LIVEKIT_PORT_OVERLAP=true）"
  fi
  if [ "$LISTEN_PORT" -ge 7880 ] && [ "$LISTEN_PORT" -le 7892 ]; then
    fail "COTURN_LISTEN_PORT=$LISTEN_PORT 落在主栈 LiveKit 端口 7880-7892（TCP 7880 signal / TCP 7881 rtc / UDP 7882-7892 媒体）内——同机冲突"
  fi
  if [ "$TLS_PORT" -ge 7880 ] && [ "$TLS_PORT" -le 7892 ]; then
    fail "COTURN_TLS_PORT=$TLS_PORT 落在主栈 LiveKit 端口 7880-7892（TCP 7880 signal / TCP 7881 rtc / UDP 7882-7892 媒体）内——同机冲突"
  fi
fi

# ---------------------------------------------------------------- 6) realm / 证书路径注入防线
# realm 只允许字母/数字/点/下划线/连字符——含换行/空白的值会注入额外配置行
case "$REALM" in
  "" | *[!A-Za-z0-9._-]*)
    fail "COTURN_REALM 只允许字母/数字/./_/-(当前: '$REALM')——含换行/空白的值拒绝写入配置" ;;
esac
require_abs_path "$CERT_FILE" COTURN_CERT_FILE
require_abs_path "$PKEY_FILE" COTURN_PKEY_FILE

# ---------------------------------------------------------------- 7) TLS：启用即要求真实证书，不做假 TLS
if [ "${COTURN_TLS_ENABLED:-false}" = "true" ]; then
  [ -f "$CERT_FILE" ] || fail "COTURN_TLS_ENABLED=true 但证书不存在: $CERT_FILE——先挂载证书目录（compose 里取消注释 tls 卷），不做假 TLS 声明"
  [ -f "$PKEY_FILE" ] || fail "COTURN_TLS_ENABLED=true 但私钥不存在: $PKEY_FILE"
  TLS_CONF="tls-listening-port=$TLS_PORT
cert=$CERT_FILE
pkey=$PKEY_FILE"
  info "TLS 已启用：tls-listening-port=$TLS_PORT cert=$CERT_FILE"
else
  TLS_CONF=""
fi

# ---------------------------------------------------------------- 8) 运行时生成配置（secret 不落仓库）
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
# 加固说明（M10-05，经 pin digest 的官方 coturn/coturn 镜像容器实测校准）：
# - 不写 no-tlsv1/no-tlsv1_1/no-loopback-peers——当前 coturn 已移除这些选项，
#   写入只会得到 Bad configuration format 告警；新版本默认等价防护
#   （旧 TLS 默认禁用、loopback relay 目标默认拒绝）；
# - 不写 no-cli（已废弃）——未设置 cli-password 时 CLI 默认关闭；
# - no-multicast-peers 仍为有效选项，显式保留。
if [ "${COTURN_VERBOSE:-false}" = "true" ]; then
  printf '%s\n' 'verbose' >>"$CONF"
fi

# ---------------------------------------------------------------- 9) CONFIG_ONLY / exec
if [ "${COTURN_CONFIG_ONLY:-false}" = "true" ]; then
  sed -e "s/^static-auth-secret=.*/static-auth-secret=<redacted len=$SECRET_LEN>/" "$CONF"
  info "COTURN_CONFIG_ONLY=true：仅打印配置（secret 已脱敏），未启动 turnserver"
  exit 0
fi

info "启动 turnserver：listen=$LISTEN_PORT/udp+tcp relay=$RELAY_START-$RELAY_END/udp+tcp external-ip=$EXTERNAL_IP tls=${COTURN_TLS_ENABLED:-false} realm=$REALM"
exec turnserver -c "$CONF" --log-file=stdout
