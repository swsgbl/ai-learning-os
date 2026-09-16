# M14-13 CI R2: MinIO 自建镜像 —— 按官方不可变 pin 本地源码构建。
#
# 背景：MinIO 社区版自 2025-10 起停止分发官方 Docker 镜像（source-only 分发），
# `minio/minio:latest` 拉取失败使 Docker CI 在项目构建之前即挂；旧 tag 也不会
# 再收到安全补丁。改为从官方源码按不可变 commit 自建，构建输入全部显式 pin
# （supervisor 核验定版）：
#
#   MinIO 源码  https://codeload.github.com/minio/minio/tar.gz/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
#               （= tag RELEASE.2025-10-15T17-29-55Z，官方最后一个社区发布；
#               该 commit 的 go.mod 为 go 1.24.0 + toolchain go1.24.8。
#               codeload 的 commit URL 按完整 SHA 取该 commit 的树——不可变）
#   builder     golang:1.24.8-alpine3.22@sha256:3d78beb141d98f42337f1252ecf2a5f20374109929a4c3f6817f9e4179cc0ae5
#               （与 go.mod 的 toolchain go1.24.8 精确对齐）
#   runtime     alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce
#
# 供应链完整性模型（fail-closed）：
#   1. 源码唯一来源 = codeload 官方不可变 commit URL（HTTPS，完整 40 位 SHA
#      寻址，绝无 tag/branch 可移动 ref）；全文件零 apk add —— 下载用本
#      builder 阶段已 pin 的 golang 工具链现场编译的纯标准库 Go fetcher
#      （net/http 默认 transport，代理感知），解压用自带 BusyBox tar；
#   2. 依赖完整性由源码树内官方 go.sum 承担：GOFLAGS=-mod=readonly（模块图
#      只读，不许改写 go.mod/go.sum）、GOSUMDB 默认开启、GOPROXY 不覆盖；
#      指令面零 bypass；
#   3. 基镜像 digest pin（与 infra/coturn/ 同款纪律）+ GOTOOLCHAIN=local ——
#      只用 builder 内置 go1.24.8，零运行时 toolchain 下载。
#
# 本镜像只含 minio 二进制（无 mc）——compose 健康检查用 Alpine 自带 BusyBox
# wget 探 /minio/health/cluster（`mc ready` 消费的同一就绪信号）。
# rationale/pins/limitations/长期替代见 docs/evidence/m14-13-minio-selfbuild/。

FROM golang:1.24.8-alpine3.22@sha256:3d78beb141d98f42337f1252ecf2a5f20374109929a4c3f6817f9e4179cc0ae5 AS build

ARG MINIO_RELEASE=RELEASE.2025-10-15T17-29-55Z
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
ARG MINIO_SOURCE_URL=https://codeload.github.com/minio/minio/tar.gz/${MINIO_COMMIT}

ENV CGO_ENABLED=0 \
    GOFLAGS=-mod=readonly \
    GOTOOLCHAIN=local

WORKDIR /src

# 唯一源码获取：codeload 官方不可变 commit URL。下载器（M14-40 修正轮 2）
# = 用本 builder 阶段已 pin 的 golang 工具链现场编译的小型纯标准库 Go
# fetcher：net/http 默认 transport 语义（ProxyFromEnvironment——读
# HTTPS_PROXY/https_proxy，HTTPS 经 HTTP CONNECT 出代理且由代理侧解析目标
# 域名）。实测动因：三次修正镜像重建均失败于 build-20260916-164346/
# -164601/-164823 的 `wget: bad address 'codeload.github.com'`——BuildKit
# RUN 层 DNS 不稳定（普通 Docker 容器 DNS 可解）；BusyBox wget 无视
# HTTPS_PROXY（无 HTTP CONNECT 支持，代理在场也走不了）；Docker 内建代理
# http://http.docker.internal:3128 对 CONNECT codeload.github.com:443 探通
# （HTTP/1.0 200 OK）。fetcher 校验 HTTP 200 后写同一 minio-src.tar.gz，
# 解压仍用 BusyBox tar，临时源码包与 fetcher 一并删除——零 apk add、零
# git、零 curl、零 GOPROXY 改动、零新增上游源。
RUN mkdir -p /fetch \
    && printf '%s\n' \
       'module aios-srcfetch' \
       '' \
       'go 1.24' \
       > /fetch/go.mod \
    && printf '%s\n' \
       'package main' \
       '' \
       'import (' \
       '"fmt"' \
       '"io"' \
       '"net/http"' \
       '"os"' \
       ')' \
       '' \
       '// http.Get = http.DefaultClient/DefaultTransport: Proxy = ProxyFromEnvironment' \
       'func main() {' \
       'if len(os.Args) != 3 {' \
       'fmt.Fprintln(os.Stderr, "usage: fetch <url> <output>")' \
       'os.Exit(2)' \
       '}' \
       'resp, err := http.Get(os.Args[1])' \
       'if err != nil {' \
       'fmt.Fprintln(os.Stderr, "fetch:", err)' \
       'os.Exit(1)' \
       '}' \
       'defer resp.Body.Close()' \
       'if resp.StatusCode != http.StatusOK {' \
       'fmt.Fprintln(os.Stderr, "fetch: HTTP", resp.Status)' \
       'os.Exit(1)' \
       '}' \
       'out, err := os.Create(os.Args[2])' \
       'if err != nil {' \
       'fmt.Fprintln(os.Stderr, "fetch:", err)' \
       'os.Exit(1)' \
       '}' \
       'if _, err := io.Copy(out, resp.Body); err != nil {' \
       'out.Close()' \
       'fmt.Fprintln(os.Stderr, "fetch:", err)' \
       'os.Exit(1)' \
       '}' \
       'if err := out.Close(); err != nil {' \
       'fmt.Fprintln(os.Stderr, "fetch:", err)' \
       'os.Exit(1)' \
       '}' \
       '}' \
       > /fetch/main.go \
    && cd /fetch && go build -o /fetch/fetch . \
    && cd /src \
    && /fetch/fetch "${MINIO_SOURCE_URL}" minio-src.tar.gz \
    && tar -xzf minio-src.tar.gz --strip-components=1 \
    && rm minio-src.tar.gz \
    && rm -rf /fetch \
    # go.sum（随 pin commit 检出）是依赖完整性锚点；官方构建口径：
    # CGO_ENABLED=0 + kqueue + trimpath + 显式 release/commit ldflags
    # （ldflags 符号路径已按该 tag 的 buildscripts/gen-ldflags.go 复核；
    # ReleaseTag 必须注入——CLI `--version` 打印的是 cmd.ReleaseTag，
    # 缺注入即回退上游默认 DEVELOPMENT.GOGET（M14-40 supervisor 代理
    # 构建后真实冒烟实测修正，与 Version 同源用 pin 的 MINIO_RELEASE））
    && mkdir -p /out \
    && go build -tags kqueue -trimpath -ldflags "-s -w \
         -X github.com/minio/minio/cmd.Version=${MINIO_RELEASE} \
         -X github.com/minio/minio/cmd.ReleaseTag=${MINIO_RELEASE} \
         -X github.com/minio/minio/cmd.CommitID=${MINIO_COMMIT}" \
         -o /out/minio .

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce

# 非 root 运行 + 可写 /data（命名卷首次挂载时继承镜像内属主 1000:1000；
# 已有 root 属主的生产卷需一次性迁移——见 evidence README 采纳注记）
RUN addgroup -S -g 1000 minio \
    && adduser -S -u 1000 -G minio -h /data -s /sbin/nologin minio \
    && mkdir -p /data \
    && chown -R minio:minio /data

COPY --from=build /out/minio /usr/bin/minio

USER minio:minio
EXPOSE 9000 9001
# compose 用 command 追加子命令（server /data --console-address ":9001"）
ENTRYPOINT ["/usr/bin/minio"]
