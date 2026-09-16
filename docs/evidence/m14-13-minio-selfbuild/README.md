# M14-13 CI R2：MinIO 镜像停发——本地自建 pin 源码镜像（供应链证据）

> 切片：`feat/m14-13-monitor-history-retention` 分支上的第二个独立 commit
> （M14-13 监控历史开发切片之后）。本回合只改源码/测试/文档并做本地
> commit：**不构建镜像、不下载上游工件、不启动或停止任何容器/服务、不
> 触碰 VPN、生产 secret 与生产容器**；`production_ready=false` 不变。

## 问题（为什么 Docker CI 挂了）

MinIO 社区版自 2025-10（安全发布窗口）起停止分发官方 Docker 镜像，社区版
转为 **source-only** 分发：

- `minio/minio:latest` 已不可拉取 → CI docker job（`.github/workflows/ci.yml`
  的 `docker compose -f infra/docker-compose.yml --profile local up -d --build`）
  在项目构建之前的镜像拉取阶段即失败；
- 即使改钉某个仍留在 registry 的旧 tag，它也不再收到安全补丁——等于钉住
  一个被放弃的二进制面，且不可从源码重建、不可审计。

## 方案选择

| 选项 | 结论 |
|---|---|
| 钉旧 tag（registry 仍存的历史镜像） | 否：无安全补丁、不可重建、供应链不可审计 |
| 第三方社区 rebuild 镜像 | 否：供应链不受 supervisor 核验、不可审计 |
| **本地自建官方 pin 源码**（本切片） | 是：全部构建输入不可变 pin、可重建、可审计 |
| 立即换存储后端（Garage/SeaweedFS/…） | 否：超出本 CI 修复切片；列为长期未决（下文） |

## Pin（supervisor 核验定版，2026-09-12）

| 输入 | 值 |
|---|---|
| MinIO 源码 URL | `https://codeload.github.com/minio/minio/tar.gz/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a` |
| 对应官方 tag | `RELEASE.2025-10-15T17-29-55Z`（官方最后一个社区发布；tag↔commit 同指已独立核验） |
| 源码 go.mod | `go 1.24.0` + `toolchain go1.24.8` |
| builder 基镜像 | `golang:1.24.8-alpine3.22@sha256:3d78beb141d98f42337f1252ecf2a5f20374109929a4c3f6817f9e4179cc0ae5`（与 `toolchain go1.24.8` 精确对齐） |
| runtime 基镜像 | `alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce` |

源码寻址用**完整 40 位 commit SHA 的 codeload URL**，不用 tag/branch——
可移动 ref 一律不作为源码地址；commit 是内容寻址的不可变量。

## 供应链完整性模型

1. **源码**：唯一来源 = 上表 codeload 官方不可变 commit URL（HTTPS-only；
   下载器 = builder 阶段内现场编译的纯标准库 Go fetcher——M14-40 修正轮 2
   起代理感知，见下方修正注记 2）。**如实说明**：本回合未附 tarball 的
   SHA-256（回合约束「不下载上游工件」，无法本地取得校验和）——源码面
   完整性 = 不可变 commit 寻址 URL + TLS + GitHub 官方源；如需更强可后续
   由 supervisor 获准下载一次补算 SHA-256 并在 Dockerfile 中 fail-closed
   校验（升级 runbook 见下）；
2. **依赖**：完整性由源码树内官方 `go.sum` 承担——`GOFLAGS=-mod=readonly`
   （模块图只读，不许改写 go.mod/go.sum）、`GOSUMDB` 保持默认开启、
   `GOPROXY` 不覆盖（默认 `proxy.golang.org` HTTPS + sumdb 校验链）；指令面
   零 bypass（`GOSUMDB=off`/`-mod=mod`/`-insecure` 等一律不出现，契约测试
   锁定）；无 vendor 目录、无本地 go.sum 拷入；
3. **工具链**：builder 镜像版本与 go.mod `toolchain go1.24.8` 精确一致 +
   `GOTOOLCHAIN=local` —— 只用 builder 内置 go1.24.8，零运行时 toolchain
   下载；
4. **基镜像**：builder/runtime 一律 digest pin（与 `infra/coturn/` 同款
   纪律），无浮动 FROM；
5. **最小面**：全文件零 `apk add` —— 下载用 builder 阶段内现场编译的纯标准库
   Go fetcher（代理感知），解压只用自带 BusyBox tar，不引入 git/curl 等
   额外包面。

构建参数对齐官方社区构建口径（并行验证会话按 pin tag 读取上游
`buildscripts/gen-ldflags.go` 与 Makefile 复核）：`CGO_ENABLED=0`、
`-tags kqueue`、`-trimpath`、显式 release/commit ldflags
（`-X …cmd.Version=<RELEASE>`、`-X …cmd.ReleaseTag=<RELEASE>` 与
`-X …cmd.CommitID=<COMMIT>` 符号路径均已确认在该 tag 存在且为官方注入集
的子集；该 tag 无版本缺失 fatal guard，版本元数据仅展示用）。

**修正注记（2026-09-17，M14-40 supervisor 代理构建后真实冒烟实测）**：
初版 Dockerfile 只注入 `cmd.Version`/`cmd.CommitID`——代理构建成功、
commit 输出正确，但 CLI `--version` 打印的是 `cmd.ReleaseTag`（未注入时
回退上游默认 `DEVELOPMENT.GOGET`），版本核对失败。即初版「官方注入集
子集」的复核结论在 CLI 打印面上不完整。修正：补注入
`-X …cmd.ReleaseTag=<RELEASE>`（与 Version 同源用 pin 的 MINIO_RELEASE），
`test_minio_selfbuild.py` 契约同步新增 ReleaseTag 注入断言锁定。

**修正注记 2（2026-09-17，M14-40 supervisor 修正镜像三次重建实测）**：
ReleaseTag 修正后的三次重建（build-20260916-164346/-164601/-164823）均败
于 BusyBox wget 的 `bad address 'codeload.github.com'`——BuildKit RUN 层
DNS 不稳定（同一宿主普通 Docker 容器 DNS 可解析），且 BusyBox wget 无视
HTTPS_PROXY（无 HTTP CONNECT 支持，代理在场也走不了）；Docker 内建代理
`http://http.docker.internal:3128` 对 `CONNECT codeload.github.com:443`
探通（HTTP/1.0 200 OK）。修正：下载器换成 builder 阶段内现场编译的纯标准库
Go fetcher（net/http 默认 transport = ProxyFromEnvironment，printf 内联落盘
`/fetch`、用后连同临时源码包删除）——源码 URL/HTTP 200 校验/BusyBox tar
解压/供应链面全部不变（零 apk add、零 git、零 curl、零 GOPROXY 改动）。
修正后的重建复验待 supervisor 执行（M14-40 修正回合 2 零 Docker/零生产
操作）。

## 变更面

- `infra/minio/Dockerfile`（新增）：上述两阶段构建。runtime 阶段最小面：
  仅 `COPY --from=build` 的单个 `/usr/bin/minio` 二进制（**无 mc**、零
  `apk add`）；非 root 运行（`minio` uid/gid 1000，Alpine 自带
  addgroup/adduser），`/data` 存在且属主 minio；`ENTRYPOINT
  ["/usr/bin/minio"]` + `EXPOSE 9000 9001`。
- `infra/docker-compose.yml`：minio 服务 `image: minio/minio:latest` →
  `build: ./minio` + `image: aios/minio:RELEASE.2025-10-15T17-29-55Z`（自建
  镜像锚点，与 api/web 同款回滚约定；升版 = 显式改此 tag + Dockerfile ARG，
  无浮动 tag 漂移面）。**服务面不变**：服务名 minio、端口
  `127.0.0.1:9000/9001`、env（`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` 开发
  占位不变）、卷 `minio-data:/data`、`restart: unless-stopped`；
  healthcheck 从 `mc ready local`（自建镜像无 mc）换成 Alpine 自带
  BusyBox wget 探官方就绪端点
  `http://127.0.0.1:9000/minio/health/cluster`（`mc ready` 消费的同一信号
  源：mc 经 madmin Health API → GET /minio/health/cluster，HTTP 200 = 可
  接受请求；interval/timeout/retries 10s/5s/5 不变）；api
  `depends_on: minio: service_healthy` 不变；六服务集合与 profile 归属
  不变。
- `services/api/tests/test_minio_selfbuild.py`（新增，21 项）：纯静态源码
  契约（零构建/零网络/零容器，CI 无 Docker 也可全跑）——Dockerfile pin/
  codeload 不可变 URL/零 apk add/bypass 面/非 root/最小 runtime 面 +
  compose 服务面/健康检查/依赖门/六服务集合。

## 限制与未验证面（如实）

- **本回合未构建镜像**（回合约束：不 build、不启动容器、不下载上游工件）
  ——镜像构建的首次真实执行在下一次 CI docker job（或 supervisor 获准
  窗口）；「实际构建耗时」「tarball 可取性」「版本元数据渲染（容器内
  `minio --version`）」以首次 CI 构建为准，本回合不宣称。
  （2026-09-17 更新：M14-40 supervisor 代理构建成功 + 真实冒烟已实测
  版本渲染——暴露 ReleaseTag 缺注入缺陷（见上修正注记）；该修正后的三次
  重建又均败于 BuildKit RUN 层 DNS / BusyBox wget 无代理支持（见修正
  注记 2），第二轮修正（Go fetcher 代理感知）后的重建复验待 supervisor
  执行，两轮修正回合零 Docker/零生产操作。）
- tarball 无 SHA-256 fail-closed 校验（见上「供应链完整性模型」第 1 条的
  如实说明与补强路径）。
- 自建后 MinIO 的安全响应责任在本仓：上游社区版可能不再有新发布——后续
  安全修复要么人工 pin 升级（若上游再发布，supervisor 核验新 commit 后
  四处同改：Dockerfile ARG/URL、compose tag、契约测试常量、本 README
  pins 表），要么走迁移路线（见下）。
- CI docker job 变慢：MinIO 是大型 Go 项目，源码构建增加数分钟（无源码层
  cache；如需可用 buildx `cache-from`，超出本切片）。
- runtime 无 mc：管理面走 Console（`127.0.0.1:9001`）或宿主侧 mc 指向
  S3 endpoint；healthcheck 已相应切换。
- **生产采纳注记（supervisor 执行，本回合不动生产）**：生产 `minio-data`
  卷现存数据为 root 属主（旧官方镜像以 root 运行）；自建镜像以 uid 1000
  运行——采纳时需一次性迁移（先备份，再对卷数据 `chown -R 1000:1000` 或
  重建卷），随后按 `aios/minio:RELEASE.2025-10-15T17-29-55Z` 固化
  `up -d --no-build`（与 api/web recovery pin 同款纪律）。CI 与全新卷无此
  问题（命名卷首次挂载继承镜像内属主）。
- 健康检查语义：`/minio/health/cluster` 是 `mc ready` 消费的集群就绪端点
  （HTTP 200 = 可接受请求），语义与旧检查对齐而非降级；liveness 变体
  `/minio/health/live` 未用于就绪门。
- 平台面：CI 构建 linux/amd64；跨架构构建未演练。

## 长期替代方案（未决，授权后评估）

自建 pin 源码镜像解决了 CI 可构建性，但把 MinIO 的安全维护责任收进本仓。
应用对 S3 的实际使用面很小（boto3 + endpoint-style + 单 bucket 的
put/get/presign），长期候选按本栈需求（单机 Docker、Windows 宿主、
loopback 数据面、可源码 pin 构建）评估：

| 候选 | 形态 | 备注 |
|---|---|---|
| Garage | Rust 单二进制 S3 兼容存储 | 轻量、活跃；本仓所需 S3 子集覆盖待实测 |
| SeaweedFS | Go（S3 gateway + filer） | 成熟、功能面大；部署面比单二进制重 |
| Ceph RGW | 生产级分布式对象存储 | 对单机栈明显过重 |
| OpenIO | — | 开源活动基本停滞（2019 年并入 OVHcloud 后沉寂），不建议 |
| 托管/商业（AWS S3、MinIO 商业订阅） | 外部依赖 | 移除自托管责任，引入外网/成本/隐私面 |

决策留给后续切片；在此之前本仓以 pin 自建维持现状。

## 验证（本回合，2026-09-12）

- `pytest services/api/tests/test_minio_selfbuild.py` → **21 passed**（纯静态）；
- 邻居 compose 契约回归（restart/profiles/ownership/objectstore/coturn/
  versioning/security/production_recovery/backup_drill/monitoring_history，
  265 passed / 6 skipped——skip 均为既有门控：真实 S3/PG env、全栈冒烟旗标、
  supervisor env 文件、路径白名单临时目录）；
- `docker compose -f infra/docker-compose.yml` `config` 渲染 × {无 profile/
  local/hybrid/cloud} 全部 exit 0（零容器改动；minio 渲染为本地 build +
  `aios/minio:RELEASE.2025-10-15T17-29-55Z` + wget cluster 健康检查）；
- `ruff check`、`py_compile`、`git diff --check` 过。

## 边界

本回合零镜像构建、零上游工件下载、零容器启动/停止、零生产服务/secret/VPN
触碰；仅做**一个独立本地 commit**，不 push、不合并；`production_ready=false`
不变。上游事实（tag↔commit 同指、alpine digest、ldflags 符号路径、官方
构建命令、`mc ready` 端点语义）由并行验证会话独立复核后采用，本仓不重复
下载上游工件。
