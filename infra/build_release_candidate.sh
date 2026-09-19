#!/usr/bin/env bash
# M10-17 本地 Release Candidate 包构建器（local build + local verify only）。
#
# 用法：
#   bash infra/build_release_candidate.sh --tag vX.Y.Z --output-dir artifacts/rc-vX.Y.Z \
#        [--web-build-arg http://127.0.0.1:8000]
#
# 做什么：
#   1. 校验 tag 严格 vX.Y.Z 且与 VERSION 文件逐字一致；
#   2. 校验 git worktree 干净，记录完整 commit SHA；
#   3. 校验输出目录在 gitignore 的 artifacts/ 或 temp/ 内（symlink 组件/
#      .. 越界/非空已存在目录/artifacts-temp 本身一律拒绝）；
#   4. 构建 api/web 镜像（钉本地 tag）+ minio 镜像（按 compose 锚定 tag）；
#   5. 以 AIOS_IMAGE_TAG=<tag> + AIOS_WEB_IMAGE_TAG=<tag>（同 tag 显式双变量，
#      M14-09 起 web 不再跟随 AIOS_IMAGE_TAG）+ --no-build 启动既有 compose local profile
#      （隔离项目名 aios-rc-<tag 中的点替换为连字符，如 v0.1.0 ->
#      aios-rc-v0-1-0），跑 infra/smoke_docker.sh；结束/失败都安全清理
#      compose 项目（down 不带 -v，绝不删除任何卷）；
#   6. docker save 两个镜像为独立归档（仅写入上面选定的目录）；
#   7. 调 python manifest 助手原子写 release-manifest.json + SHA256SUMS，
#      再独立 verify（不加载镜像）。
#
# 边界（fail-closed）：本地 RC，不是 production readiness 声明；不推镜像仓库、
# 不打 git tag、不发 GitHub Release、不碰任何生产 DB/服务/主机；不读取/输出
# 任何 key/token/password。冒烟脚本可用 AIOS_RELEASE_SMOKE_SCRIPT 覆盖（仅
# 测试替身用，默认 infra/smoke_docker.sh）。
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_ROOT="$(pwd -P)"
TAG=""
OUT_DIR=""
WEB_BUILD_ARG="${AIOS_PUBLIC_API_BASE_URL:-http://127.0.0.1:8000}"

say() { printf '[rc-build] %s\n' "$*"; }
fail() { printf '[rc-build] FAIL: %s\n' "$*" >&2; exit 1; }

usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '2,30p'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:-}"; shift 2 ;;
    --output-dir) OUT_DIR="${2:-}"; shift 2 ;;
    --web-build-arg) WEB_BUILD_ARG="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数: $1（用法见 --help）" ;;
  esac
done

[ -n "$TAG" ] || fail "必须提供 --tag vX.Y.Z"
[ -n "$OUT_DIR" ] || fail "必须提供 --output-dir <artifacts/ 或 temp/ 内的新目录>"

# --- 1. tag 形态与 VERSION 一致性 ----------------------------------------------
[[ "$TAG" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
  || fail "tag 必须严格形如 vX.Y.Z（如 v0.1.0），收到: $TAG"
BARE="${TAG#v}"
VERSION_TEXT="$(tr -d '[:space:]' < VERSION)"
[ -n "$VERSION_TEXT" ] || fail "VERSION 文件为空"
[ "$VERSION_TEXT" = "$BARE" ] \
  || fail "tag $TAG 与 VERSION 不一致：VERSION=$VERSION_TEXT，tag 裸版本=$BARE"
say "tag=$TAG 与 VERSION=$VERSION_TEXT 一致"

# --- 2. git 干净度与 commit SHA ------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail "不在 git 仓库内"
DIRTY="$(git status --porcelain)"
[ -z "$DIRTY" ] || { printf '[rc-build] FAIL: git worktree 不干净（先提交或暂存全部改动）:\n%s\n' "$DIRTY" >&2; exit 1; }
GIT_COMMIT="$(git rev-parse HEAD)"
say "git commit=$GIT_COMMIT（worktree 干净）"

# --- 3. 输出目录护栏（artifacts/temp 内、无 symlink 组件、非空拒绝）-----------
case "$OUT_DIR" in
  /*) OUT_ABS="$OUT_DIR" ;;
  *)  OUT_ABS="$REPO_ROOT/$OUT_DIR" ;;
esac
reject_path() {
  local partial="$1" part
  case "$partial" in
    "$REPO_ROOT/artifacts"/*|"$REPO_ROOT/temp"/*) ;;
    *) fail "输出目录必须位于仓库 artifacts/ 或 temp/ 内: $OUT_DIR" ;;
  esac
}
reject_path "$OUT_ABS"
BASE_NAME="$(basename "$OUT_ABS")"
[ "$BASE_NAME" != "artifacts" ] && [ "$BASE_NAME" != "temp" ] \
  || fail "输出目录不能是 artifacts/ 或 temp/ 本身"
partial=""
case "$OUT_ABS" in
  /*) partial="/" ;;
  *)  partial="." ;;
esac
old_ifs="$IFS"
IFS='/'
set -- $OUT_ABS
for part in "$@"; do
  IFS="$old_ifs"
  [ -z "$part" ] && continue
  [ "$part" != "." ] || continue
  [ "$part" != ".." ] || fail "输出目录路径含 ..，拒绝"
  if [ "$partial" = "/" ]; then partial="/$part"; else partial="$partial/$part"; fi
  [ ! -L "$partial" ] || fail "输出目录路径组件是符号链接，拒绝: $partial"
  IFS='/'
done
IFS="$old_ifs"
if [ -e "$OUT_ABS" ] && [ ! -d "$OUT_ABS" ]; then
  fail "输出路径已存在且不是目录: $OUT_ABS"
fi
if [ -d "$OUT_ABS" ] && [ -n "$(ls -A "$OUT_ABS")" ]; then
  fail "输出目录已存在且非空（拒绝混写）: $OUT_ABS"
fi
say "输出目录护栏通过: $OUT_ABS"

# --- 4. 同源构建两个镜像（钉本地 tag，不推任何仓库）----------------------------
say "building aios/api:$TAG（context=仓库根）"
docker build -f services/api/Dockerfile -t "aios/api:$TAG" "$REPO_ROOT"
say "building aios/web:$TAG（context=仓库根，NEXT_PUBLIC_API_BASE_URL=$WEB_BUILD_ARG）"
docker build -f apps/web/Dockerfile \
  --build-arg "NEXT_PUBLIC_API_BASE_URL=$WEB_BUILD_ARG" \
  -t "aios/web:$TAG" "$REPO_ROOT"

# M14-62（RC run 35423807031）：compose local profile 的 minio 服务钉本地
# 自建镜像（M14-13 起 MinIO 官方无镜像可拉），干净 Docker 主机上 --no-build
# 冒烟的镜像前置条件必须由本脚本满足——从 compose 的 minio 服务块就地提取
# image tag 与 build.context（compose 锚定：升版只改 compose tag 与
# Dockerfile ARG，本脚本零硬编码 pin 副本），--no-build 引用的即此镜像。
compose_minio_field() {
  # $1 = 字段行缩进（image 为 4 空格；build.context 为 6 空格）、$2 = 字段名
  awk -v prefix="$1" -v key="$2" '
    # Windows worktree 下 compose 可能以 CRLF 检出；GNU awk（Linux/WSL）保留
    # 记录尾 \r 使等值/锚定匹配失配（MSYS 文本模式剥 \r 故本地曾假绿）——
    # 每条记录先剥 CR 再匹配，LF/CRLF 两种检出形态行为一致（M14-62 R2）
    { sub(/\r$/, "") }
    $0 == "  minio:" { in_minio = 1; next }
    in_minio && /^  [A-Za-z0-9_-]+:$/ { in_minio = 0 }
    in_minio && index($0, prefix key ":") == 1 {
      sub("^" prefix key ":[ \t]*", "")
      gsub(/^["\047]|["\047]$/, "")
      print
      exit
    }
  ' infra/docker-compose.yml
}
MINIO_IMAGE="$(compose_minio_field "    " image)"
[ -n "$MINIO_IMAGE" ] || fail "compose 未给 minio 服务声明 image pin（无法锚定本地构建）"
case "$MINIO_IMAGE" in
  aios/minio:*) ;;
  *) fail "compose minio image pin 形态异常（预期 aios/minio:<tag>）: $MINIO_IMAGE" ;;
esac
MINIO_CONTEXT="$(compose_minio_field "      " context)"
[ -n "$MINIO_CONTEXT" ] || fail "compose 未给 minio 服务声明 build.context（无法定位构建上下文）"
case "$MINIO_CONTEXT" in
  ./*) ;;
  *) fail "compose minio build.context 必须形如 ./minio（相对 infra/）: $MINIO_CONTEXT" ;;
esac
MINIO_CONTEXT_DIR="$REPO_ROOT/infra/${MINIO_CONTEXT#./}"
[ -d "$MINIO_CONTEXT_DIR" ] || fail "minio 构建上下文不存在: $MINIO_CONTEXT_DIR"
[ -f "$MINIO_CONTEXT_DIR/Dockerfile" ] \
  || fail "minio 构建上下文缺 Dockerfile: $MINIO_CONTEXT_DIR/Dockerfile"
say "building $MINIO_IMAGE（context=infra/${MINIO_CONTEXT#./}，compose 锚定）"
docker build -t "$MINIO_IMAGE" "$MINIO_CONTEXT_DIR"

API_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "aios/api:$TAG")"
WEB_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "aios/web:$TAG")"
say "image ids: api=$API_IMAGE_ID web=$WEB_IMAGE_ID"

# --- 5. compose 冒烟（--no-build + API/Web 双 tag 显式；隔离项目名；安全清理）---
# COMPOSE_PROJECT_NAME 让本脚本与 smoke_docker.sh 共用同一个隔离项目
# （不占用/不拆除运维自己的 ai-learning-os 项目）；down 永不带 -v——绝不删除卷。
# compose project name 只允许小写字母数字/下划线/连字符（不允许点），而 tag
# 形如 v0.1.0 含点——用 bash 参数展开就地把点替换成连字符推导合法项目名
# （v0.1.0 -> aios-rc-v0-1-0）；镜像 tag 与归档名保持原样不变（M11-02 B-1
# 回归：run 33938835814 曾因 aios-rc-v0.1.0 被 compose 拒绝 invalid project
# name 而失败）。
export COMPOSE_PROJECT_NAME="aios-rc-${TAG//./-}"
COMPOSE=(docker compose -f infra/docker-compose.yml --profile local)
SMOKE_SCRIPT="${AIOS_RELEASE_SMOKE_SCRIPT:-infra/smoke_docker.sh}"
COMPOSE_STARTED=0
OUT_CREATED=0
cleanup() {
  local code=$?
  if [ "$COMPOSE_STARTED" = "1" ]; then
    say "清理 compose 项目 $COMPOSE_PROJECT_NAME（down --remove-orphans，不删卷）"
    docker compose -f infra/docker-compose.yml --profile local \
      down --remove-orphans >/dev/null 2>&1 || true
  fi
  if [ "$code" -ne 0 ] && [ "$OUT_CREATED" = "1" ] && [ -d "$OUT_ABS" ]; then
    if [ ! -f "$OUT_ABS/release-manifest.json" ]; then
      say "失败清理：移除未完成的包目录（无 manifest，不构成有效 RC）"
      rm -rf "$OUT_ABS"
    fi
  fi
}
trap cleanup EXIT

say "启动 compose local profile（AIOS_IMAGE_TAG=$TAG AIOS_WEB_IMAGE_TAG=$TAG --no-build）"
COMPOSE_STARTED=1
AIOS_IMAGE_TAG="$TAG" AIOS_WEB_IMAGE_TAG="$TAG" "${COMPOSE[@]}" up -d --no-build

say "运行冒烟: $SMOKE_SCRIPT"
bash "$SMOKE_SCRIPT"

say "冒烟通过，先行清理 compose 项目（保留卷）"
"${COMPOSE[@]}" down --remove-orphans
COMPOSE_STARTED=0

# --- 6. docker save 独立归档（仅写入选定目录）----------------------------------
mkdir -p "$OUT_ABS"
OUT_CREATED=1
API_ARCHIVE="aios-api-$TAG.tar"
WEB_ARCHIVE="aios-web-$TAG.tar"
say "saving $API_ARCHIVE"
docker save -o "$OUT_ABS/$API_ARCHIVE" "aios/api:$TAG"
say "saving $WEB_ARCHIVE"
docker save -o "$OUT_ABS/$WEB_ARCHIVE" "aios/web:$TAG"

# --- 7. manifest + SHA256SUMS 原子落盘，随后独立 verify ------------------------
PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || fail "找不到 python3/python（manifest 助手需要）"
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
(
  cd services/api
  "$PY" -m app.ops.cli release-candidate manifest \
    --output-dir "$OUT_ABS" \
    --tag "$TAG" \
    --version-file "$REPO_ROOT/VERSION" \
    --git-commit "$GIT_COMMIT" \
    --compose-file "$REPO_ROOT/infra/docker-compose.yml" \
    --api-image-id "$API_IMAGE_ID" \
    --web-image-id "$WEB_IMAGE_ID" \
    --api-archive "$API_ARCHIVE" \
    --web-archive "$WEB_ARCHIVE" \
    --build-context "." \
    --web-build-arg "$WEB_BUILD_ARG" \
    --smoke-script "$SMOKE_SCRIPT" \
    --generated-at "$GENERATED_AT"
)
(
  cd services/api
  "$PY" -m app.ops.cli release-candidate verify \
    --package-dir "$OUT_ABS" \
    --version-file "$REPO_ROOT/VERSION"
)

say "Release Candidate 包完成: $OUT_ABS"
say "边界：本地 Release Candidate（local build + local verify only），不是"
say "production readiness 声明；未推镜像仓库、未创建 git 标签、未发布 Release。"
