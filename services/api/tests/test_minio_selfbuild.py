r"""M14-13 CI R2 契约：MinIO 自建镜像（本地官方源码构建）+ compose 服务面不变。

背景：MinIO 社区版自 2025-10 起停止分发官方 Docker 镜像（source-only 分发），
`minio/minio:latest` 拉取失败使 Docker CI 在项目构建之前即挂。修复 = compose
minio 服务切换到本地源码构建（`infra/minio/Dockerfile`）。本套件把修复钉进
源码契约 —— 纯静态断言：不构建镜像、不拉取、不启动容器、零网络（CI 无
Docker CLI 也可全跑；compose 渲染面由既有 restart/profiles 套件覆盖）。

两层契约：
- Dockerfile：不可变 pin（上游 commit、builder/runtime 基镜像 digest）、
  源码唯一来源 = codeload 官方不可变 commit URL（HTTPS、完整 40 位 SHA 寻址，
  绝无 tag/branch 可移动 ref）、全文件零 apk add（下载 = builder 内现场编译
  的纯标准库 Go fetcher——代理感知 net/http 默认 transport；解压只用自带
  BusyBox tar）、CGO_ENABLED=0、kqueue/trimpath、显式 release
  （Version+ReleaseTag）/commit ldflags、go.sum 依赖完整性（无 GOSUMDB 关闭/
  无 GOPROXY 覆盖/无 -mod=mod/无 -insecure 等 bypass、无 vendor 拷入）、
  GOTOOLCHAIN=local、非 root 运行 + 可写 /data、runtime 只含 minio 二进制。
- compose：minio 服务面（名称/端口/env/卷/restart/healthcheck 节奏）不变；
  镜像从 registry 浮动 tag 换成 `build:` + 上游 RELEASE 锚点 tag；`mc ready
  local`（自建镜像无 mc）换成 Alpine 自带 BusyBox wget 探官方就绪端点
  /minio/health/cluster（`mc ready` 消费的同一信号源）；api depends_on
  minio healthy 与六服务集合不变。

pin 值与 supervisor 核验定版的不可变 ref 互为锁定（见
docs/evidence/m14-13-minio-selfbuild/README.md 的 pins 表）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
MINIO_DIR = REPO_ROOT / "infra" / "minio"
DOCKERFILE = MINIO_DIR / "Dockerfile"

# supervisor 核验定版的不可变 pin（2026-09-12；升级 = 显式改这里 + Dockerfile + compose）
MINIO_RELEASE = "RELEASE.2025-10-15T17-29-55Z"
MINIO_COMMIT = "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
GOLANG_BUILDER = (
    "golang:1.24.8-alpine3.22"
    "@sha256:3d78beb141d98f42337f1252ecf2a5f20374109929a4c3f6817f9e4179cc0ae5"
)
ALPINE_RUNTIME = (
    "alpine:3.22"
    "@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce"
)
# 源码唯一来源：codeload 官方不可变 commit URL（完整 SHA 寻址）
CODELOAD_URL_ARG = (
    "ARG MINIO_SOURCE_URL=https://codeload.github.com/minio/minio/tar.gz/${MINIO_COMMIT}"
)

EXPECTED_HEALTHCHECK_CMD = (
    "wget -q -O /dev/null http://127.0.0.1:9000/minio/health/cluster || exit 1"
)

SIX_SERVICES = {"postgres", "redis", "minio", "api", "livekit", "web"}


@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert DOCKERFILE.is_file(), f"缺自建 MinIO Dockerfile: {DOCKERFILE}"
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML 不可用——跳过 compose 静态断言")
    model = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert isinstance(model, dict) and isinstance(model.get("services"), dict)
    return model


# ---------------------------------------------------------------- Dockerfile


def test_builder_image_digest_pinned(dockerfile: str) -> None:
    """builder 基镜像 = supervisor 核验的 golang:1.26-alpine digest pin。"""
    assert f"FROM {GOLANG_BUILDER} AS build" in dockerfile


def test_runtime_image_digest_pinned(dockerfile: str) -> None:
    """runtime 基镜像 = supervisor 核验的 alpine:3.22 digest pin。"""
    assert f"FROM {ALPINE_RUNTIME}" in dockerfile


def test_every_from_line_is_digest_pinned(dockerfile: str) -> None:
    """所有 FROM 一律 digest pin —— 自建镜像绝不引入浮动基标签。"""
    froms = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert froms, "Dockerfile 无 FROM 指令"
    for line in froms:
        assert "@sha256:" in line, f"基镜像未 digest pin: {line}"


def test_source_pins_declared_as_immutable_args(dockerfile: str) -> None:
    """上游源码 pin（tag + commit）以 ARG 显式声明且值精确等于核验值。"""
    assert f"ARG MINIO_RELEASE={MINIO_RELEASE}" in dockerfile
    assert f"ARG MINIO_COMMIT={MINIO_COMMIT}" in dockerfile


def test_source_fetched_from_official_immutable_commit_url(dockerfile: str) -> None:
    """源码唯一来源 = codeload 官方不可变 commit URL（完整 40 位 SHA 寻址）。

    M14-40 修正轮 2：下载器从 BusyBox wget 换成 builder 内现场编译的纯标准库
    Go fetcher；产物与解压不变——仍写同一 minio-src.tar.gz、仍用 BusyBox tar
    解压（源码来源与供应链面零变化，只换传输器）。
    """
    assert CODELOAD_URL_ARG in dockerfile
    assert f"ARG MINIO_COMMIT={MINIO_COMMIT}" in dockerfile
    # 下载由 Go fetcher 完成（非 BusyBox wget），写同一 minio-src.tar.gz
    assert '/fetch/fetch "${MINIO_SOURCE_URL}" minio-src.tar.gz' in dockerfile
    assert "wget -q -O minio-src.tar.gz" not in dockerfile
    # 解压不变：BusyBox tar，strip 顶层目录，恰好一次
    assert dockerfile.count("tar -xzf minio-src.tar.gz --strip-components=1") == 1
    # 不按 tag/branch 可移动 ref 取源码；绝无明文 http / git 协议
    assert "tar.gz/RELEASE" not in dockerfile
    assert "tar.gz/main" not in dockerfile
    assert "git clone" not in dockerfile
    assert "http://github.com" not in dockerfile  # 仅 HTTPS
    assert "git://" not in dockerfile


def test_source_fetch_is_proxy_aware_go_fetcher(dockerfile: str) -> None:
    """源码下载器 = builder 内现场编译的纯标准库 Go fetcher，代理感知。

    M14-40 修正轮 2 实测动因：三次修正镜像重建（build-20260916-164346/
    -164601/-164823）均败于 BuildKit RUN 层 ``wget: bad address
    'codeload.github.com'``（DNS 不稳定——普通 Docker 容器 DNS 可解）；
    BusyBox wget 无视 HTTPS_PROXY（无 HTTP CONNECT 支持，代理在场也走不了）；
    Docker 内建代理 ``http://http.docker.internal:3128`` 对
    CONNECT codeload.github.com:443 探通（HTTP/1.0 200 OK）。契约：fetcher
    用 net/http 默认 transport（http.Get——Proxy = ProxyFromEnvironment，读
    HTTPS_PROXY/https_proxy），HTTP 非 200 即失败，产物写 minio-src.tar.gz，
    用后连同临时源码包一起删除。
    """
    # fetcher 源码内联于 Dockerfile（printf 落盘 /fetch），由本 builder 的 go 编译
    assert "printf '%s\\n'" in dockerfile
    assert "> /fetch/main.go" in dockerfile
    assert "> /fetch/go.mod" in dockerfile
    assert "go build -o /fetch/fetch ." in dockerfile
    # 纯标准库 + 默认 transport：http.Get（Proxy = ProxyFromEnvironment）
    assert '"net/http"' in dockerfile
    assert "http.Get(os.Args[1])" in dockerfile
    assert "ProxyFromEnvironment" in dockerfile
    # 状态校验 + 产物落盘 + 临时面清理
    assert "resp.StatusCode != http.StatusOK" in dockerfile
    assert "os.Create(os.Args[2])" in dockerfile
    assert "rm minio-src.tar.gz" in dockerfile
    assert "rm -rf /fetch" in dockerfile
    # fetcher 的 go.mod 是 printf 内联生成，非外部拷入
    assert "COPY go.mod" not in dockerfile


def test_source_fetch_adds_no_package_or_bypass_surface(dockerfile: str) -> None:
    """代理感知改造不扩大供应链面：指令面零包安装/零 git/零 curl/零 wget。"""
    # 只扫指令行（注释可叙述历史/违禁词名，指令面才是行为面）
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.strip().startswith("#")
    )
    for banned in (
        "apk add",   # 零包安装（代理感知不靠加装 curl/git）
        "apk ",      # 连 apk 信息查询也不引入
        "curl",      # 不引入 curl 二进制/命令
        "git clone",
        "git ",      # 不引入 git
        "wget ",     # 源码获取不再经 BusyBox wget（指令面零 wget）
        "GOPROXY",   # 不覆盖默认 proxy.golang.org（HTTPS + sumdb 校验链）
        "GOSUMDB=off",
        "-mod=mod",
    ):
        assert banned not in code, f"供应链面扩大/bypass 出现: {banned}"


def test_zero_apk_add(dockerfile: str) -> None:
    """指令面零 apk add —— 下载用 builder 内编译的纯标准库 Go fetcher，解压
    只用自带 BusyBox tar（代理感知改造不引入任何包安装面）。"""
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.strip().startswith("#")
    )
    assert "apk add" not in code


def test_cgo_disabled(dockerfile: str) -> None:
    """CGO_ENABLED=0 —— 静态纯 Go 二进制（与官方社区构建一致）。"""
    assert "CGO_ENABLED=0" in dockerfile


def test_official_build_flags_kqueue_and_trimpath(dockerfile: str) -> None:
    """官方构建参数保留：-tags kqueue + -trimpath（可复现路径）。"""
    assert "-tags kqueue" in dockerfile
    assert "-trimpath" in dockerfile


def test_explicit_release_and_commit_ldflags(dockerfile: str) -> None:
    """ldflags 显式注入 release 与 commit（版本元数据不依赖构建环境猜测）。"""
    assert "-ldflags" in dockerfile
    assert "-X github.com/minio/minio/cmd.Version=${MINIO_RELEASE}" in dockerfile
    assert "-X github.com/minio/minio/cmd.CommitID=${MINIO_COMMIT}" in dockerfile


def test_releasetag_injected_for_cli_version_surface(dockerfile: str) -> None:
    """CLI `--version` 打印的是 cmd.ReleaseTag，必须与 Version 同源注入。

    M14-40 supervisor 代理构建后真实冒烟实测：仅注入 Version/CommitID 时
    `minio --version` 打印上游默认 ``DEVELOPMENT.GOGET``（cmd.ReleaseTag 的
    未注入回退值）——版本核对失败但 commit 正确。回归契约：ReleaseTag 用
    同一 pin 的 MINIO_RELEASE 注入，缺注入即本测试红。
    """
    assert "-X github.com/minio/minio/cmd.ReleaseTag=${MINIO_RELEASE}" in dockerfile


def test_dependency_integrity_via_gosum_without_bypass(dockerfile: str) -> None:
    """依赖完整性只来自随 pin commit 检出的官方 go.sum —— 指令面 bypass 为零。"""
    # 只扫指令行（注释可叙述历史/bypass 名，指令面才是行为面）
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.strip().startswith("#")
    )
    assert "GOFLAGS=-mod=readonly" in code  # 模块图只读（不许改 go.mod/go.sum）
    for banned in (
        "GOSUMDB=off",       # 关闭校验和数据库
        "GONOSUMDB",         # 旧式豁免变量
        "GONOSUMCHECK",
        "GOPROXY",           # 不覆盖默认 proxy.golang.org（HTTPS + sumdb 校验链）
        "-mod=mod",          # 允许改写模块图
        "-insecure",         # 允许明文抓取
        "GOFLAGS=-insecure",
    ):
        assert banned not in code, f"依赖完整性 bypass 出现: {banned}"
    # 无本地 vendor/go.sum 拷入 —— go.sum 只能来自 pin commit 的检出
    for not_copied in ("COPY go.sum", "COPY go.mod", "COPY vendor"):
        assert not_copied not in code


def test_toolchain_pinned_to_builder(dockerfile: str) -> None:
    """GOTOOLCHAIN=local —— 只用 builder 内置 toolchain，不做运行时 toolchain 下载。"""
    assert "GOTOOLCHAIN=local" in dockerfile


def test_runs_non_root_with_writable_data(dockerfile: str) -> None:
    """非 root（minio 1000:1000）运行，/data 存在且属主可写。"""
    assert "USER minio:minio" in dockerfile
    assert "USER root" not in dockerfile
    assert "adduser -S -u 1000 -G minio" in dockerfile
    assert "mkdir -p /data" in dockerfile
    assert "chown -R minio:minio /data" in dockerfile


def test_entrypoint_and_ports(dockerfile: str) -> None:
    """ENTRYPOINT 固定 minio 二进制；API/Console 端口显式 EXPOSE。"""
    assert 'ENTRYPOINT ["/usr/bin/minio"]' in dockerfile
    assert "EXPOSE 9000 9001" in dockerfile


def test_runtime_stage_carries_only_minio_binary(dockerfile: str) -> None:
    """runtime 阶段零 apk 安装、COPY 只来自 build 阶段的单个 minio 二进制（无 mc）。"""
    runtime_stage = dockerfile.rsplit("FROM ", 1)[1]
    assert "apk add" not in runtime_stage, "runtime 阶段应保持最小面（BusyBox 基础）"
    copies = [
        line.strip() for line in dockerfile.splitlines() if line.strip().startswith("COPY ")
    ]
    assert copies, "缺二进制 COPY"
    for line in copies:
        assert line.startswith("COPY --from=build"), f"runtime 拷入了构建外文件: {line}"
    assert len(copies) == 1 and copies[0] == "COPY --from=build /out/minio /usr/bin/minio"
    assert "minio/mc" not in dockerfile


# ------------------------------------------------------------------- compose


def test_minio_builds_locally_from_pinned_dockerfile(compose: dict) -> None:
    """minio 服务改为本地构建（context=infra/minio），不再从 registry 拉取。"""
    build = compose["services"]["minio"]["build"]
    assert build == {"context": "./minio"}
    assert (MINIO_DIR / "Dockerfile").is_file(), "build context 缺 Dockerfile"


def test_local_image_tag_is_upstream_release(compose: dict) -> None:
    """自建镜像 tag 锚定上游 RELEASE（无浮动 tag；升版 = 显式改此值）。"""
    assert compose["services"]["minio"]["image"] == f"aios/minio:{MINIO_RELEASE}"


def test_no_registry_minio_image_reference_left() -> None:
    """零 registry 拉取引用：无任何服务 `image: minio/minio…`（历史名仅存注释）。"""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert re.search(r"image:\s*minio/minio", text) is None
    assert "minio/minio@" not in text


def test_minio_service_interface_preserved(compose: dict) -> None:
    """服务面不变：restart/command/env/端口/卷与切换前逐项一致，且不挂 profile。"""
    svc = compose["services"]["minio"]
    assert svc["restart"] == "unless-stopped"
    assert svc["command"] == 'server /data --console-address ":9001"'
    assert svc["environment"] == {
        "MINIO_ROOT_USER": "aios",
        "MINIO_ROOT_PASSWORD": "aios12345",
    }
    assert svc["ports"] == ["127.0.0.1:9000:9000", "127.0.0.1:9001:9001"]
    assert svc["volumes"] == ["minio-data:/data"]
    assert "profiles" not in svc  # 基础五服务恒启动（M14-06 restart 契约同口径）


def test_healthcheck_is_busybox_wget_cluster_endpoint(compose: dict) -> None:
    """健康检查 = BusyBox wget 探官方就绪端点 /cluster（`mc ready` 同信号源）。"""
    hc = compose["services"]["minio"]["healthcheck"]
    assert hc["test"] == ["CMD-SHELL", EXPECTED_HEALTHCHECK_CMD]
    assert hc["interval"] == "10s"
    assert hc["timeout"] == "5s"
    assert hc["retries"] == 5
    # 官方集群就绪端点（mc ready 消费的信号源），容器内 loopback
    assert "/minio/health/cluster" in hc["test"][1]
    assert "127.0.0.1:9000" in hc["test"][1]
    assert "mc ready local" not in COMPOSE_FILE.read_text(encoding="utf-8")


def test_api_depends_on_minio_healthy(compose: dict) -> None:
    """api 仍以 minio service_healthy 为门（M8-00：首笔上传不撞连接拒绝）。"""
    depends = compose["services"]["api"]["depends_on"]
    assert depends["minio"] == {"condition": "service_healthy"}


def test_six_service_set_unchanged(compose: dict) -> None:
    """六服务集合与各服务 profile 归属不变（minio 属无 profile 基础面）。"""
    assert set(compose["services"]) == SIX_SERVICES
    for name in ("postgres", "redis", "minio", "api", "web"):
        assert "profiles" not in compose["services"][name], name
    assert set(compose["services"]["livekit"]["profiles"]) == {"local", "hybrid", "cloud"}
