# M14-40 MinIO 本地镜像采纳预检工具（build/smoke/preflight 三模式）

> 切片：`ops/m14-40-minio-local-adoption`（基于 `main@0e4f5c5`（PR #116
> merge）），本 Claude 开发回合独占 worktree、单 local commit、不 push/不建
> PR/不动远程。本回合交付的是**采纳预检工具与契约测试 + 文档**；真实代理
> 构建、一次性冒烟、真实只读 preflight、生产前后容器 ID 证据、push/PR/CI
> 均待 supervisor 执行，本回合一律未做、不宣称通过。**adoption=pass 仍不
> 等于生产就绪；`production_ready=false` 不变。**

## 工具契约（`tools/ops/minio_image_adoption.py`，纯标准库）

三模式（`build` / `smoke` / `preflight`），各模式**默认 plan**（零执行，
只出计划与报告）；真实执行需 `--execute` + 模式专属精确确认短语（一字不
差，缺一或近似即拒绝且零副作用）：

```
python tools/ops/minio_image_adoption.py build     --execute --confirm "EXECUTE MINIO IMAGE BUILD"
python tools/ops/minio_image_adoption.py smoke     --execute --confirm "EXECUTE MINIO IMAGE SMOKE"
python tools/ops/minio_image_adoption.py preflight --execute --confirm "EXECUTE MINIO PREFLIGHT"
```

报告：schema v1 JSON + Markdown，默认落 gitignored
`.verify/artifacts/m14-40-minio-image-adoption/`（`--artifact-dir` 为操作者
显式自选，其位置与 gitignore 状态由操作者负责）。

### build（自建镜像构建）

- 只构建 compose 锚定的 `aios/minio:RELEASE.2025-10-15T17-29-55Z`
  （pin commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`；镜像引用在
  代码四处交叉锁定并与 compose `image:` 锚点契约测试互锁）——恰好一次
  `docker build`（infra/minio），零 compose 项目操作。
- 可选环境变量 `AIOS_MINIO_BUILD_HTTPS_PROXY`：仅接受
  socks5h/socks5/http/https + host[:port]，端口语义校验 1–65535，且向
  构建传入 Docker 预定义 `HTTPS_PROXY` + `https_proxy` **两个** build arg
  （确定性顺序：先大写后小写、同值各恰一次——修正轮 3：BuildKit 可保留/
  注入小写 `https_proxy`，Go `ProxyFromEnvironment` 可优先小写，单写大写
  会让宿主 loopback 死代理胜出）——零 GOPROXY 改动、零 Dockerfile 改动、
  零 pin 改动。

### smoke（一次性冒烟）

- 只使用严格 `aios-m14-40-` 前缀的生成式一次性容器/卷名；loopback
  19000/19001。端口占用探测仅以**真实 HTTP 响应**为占用信号（M14-40 修正：
  Windows 对未绑定 loopback 端口的探测常回 TimeoutError 而非
  ConnectionRefusedError——refused/timeout/http-error 一律放行，后续
  `docker run -p` 的端口绑定是权威裁决，绑定失败即启动失败 + finally 恒
  清理）；真实 HTTP 响应/名字冲突在任何副作用之前 fail-closed。
- 核查 cluster 健康、uid 1000 数据探针与版本后，finally 中精确清理恰这
  两个名字——**清理失败即冒烟整体失败**。
- 冒烟 env 只用仓库公开 compose dev 占位值；绝不读取任何 env secret
  （含 `infra/env.production-recovery`）。

### preflight（只读生产盘点 + 采纳判定）

- 只读盘点：镜像元数据与运行时 uid/版本、compose/Dockerfile 锚点、六容器
  健康、生产卷 driver/size/递归 UID 普查。
- 生产卷探针恒 `:ro`（挂 /probe）挂入 `--network none` 的一次性 `--rm`
  helper 容器，逐次探针后移除；**绝不写生产卷**。
- 采纳判定 fail-closed：镜像缺失、user/entrypoint/version 不符、卷属主
  非 uid 1000 **或普查不确定**、栈非全健康 → adoption=blocked；仅全部
  通过才 adoption=pass（pass 仍不构成生产就绪宣称）。

### 全局安全边界

- `--project` 仅允许 `aios-m14-03-production-rehearsal`；生产面严格只读
  （docker version / image inspect / inspect / volume inspect）——零
  compose mutation、零 pull、零 stop/restart/remove/exec、零 env secret
  读取、零生产卷写；每个 docker argv 必须匹配固定结构白名单，任何偏离
  在执行之前拒绝。
- 退出码统一：`0` 成功；`2` 一切失败（fail-closed：门禁/校验拒绝与真实
  执行失败同码，绝不静默降级）。

## 真实构建尝试（失败，如实记录）

- 2026-09-16T12:51:06Z 直接构建（无代理）：468.7s 后于 Go module 拉取
  阶段失败（`proxy.golang.org` 多模块 `connect: connection refused`；
  构建进程 rc=1，工具统一 EXIT_REJECT=2 如实入档）。证据（gitignored）：
  `.verify/artifacts/m14-40-minio-image-adoption/build-20260916-125106.json`
  / `.md`。
- 由此引入 `AIOS_MINIO_BUILD_HTTPS_PROXY` 显式代理重试路径；代理构建、
  一次性冒烟、真实只读 preflight、生产前后容器 ID 证据、push/PR/CI
  ——全部待 supervisor 执行，本回合未做、不宣称通过。

## supervisor 代理构建与冒烟结果（2026-09-17，如实记录）

- supervisor 以 `AIOS_MINIO_BUILD_HTTPS_PROXY` 代理路径执行 build：
  **构建成功**，镜像 `aios/minio:RELEASE.2025-10-15T17-29-55Z` 落地。
- 随后执行 smoke：**暴露版本元数据缺陷**——二进制 `--version` 打印
  `DEVELOPMENT.GOGET`（commit 输出正确）：初版 Dockerfile ldflags 只注入
  `cmd.Version`/`cmd.CommitID`，而 CLI `--version` 打印的是
  `cmd.ReleaseTag`（未注入即回退上游默认值）→ 版本核对失败
  （`version-not-pinned`），但 **finally 清理成功**（一次性容器/卷零残留）。
- 同轮暴露第二缺陷：Windows loopback 对未绑定 127.0.0.1:19000/19001 的
  探测回 `TimeoutError` 而非 `ConnectionRefusedError`，`smoke_ports_free`
  旧口径（仅 refused 算空闲）在 Windows 上恒误拒。

## 修正回合（2026-09-17，代码级修正 + 契约锁定；零 Docker/零生产操作）

在已接受提交 f8685a4 之上追加第二个 local commit（本 worktree，不 push）：

1. **ReleaseTag 注入**：`infra/minio/Dockerfile` 补
   `-X github.com/minio/minio/cmd.ReleaseTag=${MINIO_RELEASE}`（与
   Version 同源用同一 pin）；`test_minio_selfbuild.py` 新增 ReleaseTag
   注入契约（缺注入即红，docstring 记录 DEVELOPMENT.GOGET 回归事实）。
2. **smoke 端口门修正**：`smoke_ports_free` 仅以真实 HTTP 响应
   （`status is not None`）为占用信号——refused/timeout/http-error 一律
   放行（后续 `docker run -p` 端口绑定是权威裁决，绑定失败即启动失败 +
   finally 恒清理）；`test_minio_image_adoption.py` 补 refused/timeout/
   http-error 放行 + 真实 HTTP 响应占用 + Windows timeout 形态集成回归。

修正回合验证（本 worktree 真实执行，零 Docker/零生产操作）：

- 聚焦采纳套件 `test_minio_image_adoption.py`：**125 passed**；
- selfbuild 套件 `test_minio_selfbuild.py`：**22 passed**；
- 邻居复验（selfbuild + production_recovery + compose_profiles +
  backup_drill）：**92 passed / 3 skipped**；
- `ruff check` / `py_compile`（改动代码文件）、`git diff --check`、diff
  secret 模式扫描——全部通过。

修正后状态（诚实边界）：supervisor **重建镜像、重跑 smoke、真实只读
preflight、生产采纳、push/PR/CI 均待执行**——修正仅为代码级 + 契约测试
锁定，不宣称修正后构建/冒烟已通过。

## 修正回合 2（2026-09-17，源码获取代理感知；零 Docker/零生产操作）

supervisor 用修正后 Dockerfile 三次重建镜像，均失败于源码下载阶段（如实
记录）。在已接受提交 7f4c6b2 之上追加第三个 local commit（本 worktree，
不 push）。

**失败与诊断证据**：

- 三份失败报告（gitignored）：`build-20260916-164346` / `build-20260916-
  164601` / `build-20260916-164823`（`.verify/artifacts/m14-40-minio-image-
  adoption/`），失败点一致：`wget: bad address 'codeload.github.com'`——
  BusyBox wget 解析不了 codeload 域名；
- 诊断 1：**BuildKit RUN 层 DNS 不稳定**——同一宿主上普通 Docker 容器的
  DNS 可解析 codeload.github.com，唯 BuildKit RUN 步骤解析失败；
- 诊断 2：**BusyBox wget 无视 HTTPS_PROXY/https_proxy**（无 HTTP CONNECT
  支持——HTTP 代理在场也走不了），旧下载器结构性无法代理化；
- 探针：Docker 内建代理 `http://http.docker.internal:3128` 对
  `CONNECT codeload.github.com:443` 返回 `HTTP/1.0 200 OK`——代理路径可用，
  缺的只是代理感知的下载器。当前源码获取不感知代理，阻塞既定在线构建
  模式。

**修正**：`infra/minio/Dockerfile` 仅替换源码下载这一步——BusyBox wget →
builder 阶段内**现场编译的纯标准库 Go fetcher**（printf 内联落盘
`/fetch/main.go` + 最小 go.mod，本 builder 已 pin 的 go 编译执行；net/http
默认 transport 语义 = `http.Get`，Proxy 即 `ProxyFromEnvironment`——读
HTTPS_PROXY/https_proxy，HTTPS 经 HTTP CONNECT 出代理且由代理侧解析目标
域名，绕开 BuildKit RUN 层 DNS）。供应链面零变化：仍是同一不可变
`MINIO_SOURCE_URL`、仍校验 HTTP 200、仍写同一 `minio-src.tar.gz`、仍用
BusyBox tar 解压、临时源码包与 `/fetch` 一并删除；**零 apk add、零 git、
零 curl、零 GOPROXY 改动、零新增上游源**，Version/ReleaseTag/CommitID
注入不变。`test_minio_selfbuild.py` 契约同步（22→**24 项**）：源码 URL
测试改锁 fetcher 调用形态，新增代理感知 fetcher 契约 + 指令面零
apk/curl/git/wget/GOPROXY 契约。

修正回合 2 验证（本 worktree 真实执行，零 Docker/零生产操作）：

- 聚焦采纳套件 `test_minio_image_adoption.py`：**125 passed**——工具无需
  改动：其 `AIOS_MINIO_BUILD_HTTPS_PROXY` 路径传入的 Docker 预定义
  `HTTPS_PROXY` build arg 此前只作用于 Go module 拉取，现在同样作用于
  源码 fetcher（测试未暴露真实契约缺口）；
- selfbuild 套件 `test_minio_selfbuild.py`：**24 passed**；
- 邻居复验（selfbuild + production_recovery + compose_profiles +
  backup_drill）：**94 passed / 3 skipped**。

修正后状态（诚实边界）：supervisor **代理重建镜像、重跑 smoke、真实只读
preflight、生产采纳、push/PR/CI 均待执行**——修正仅为代码级 + 契约锁定，
不宣称修正后构建/冒烟已通过。

## 修正回合 3（2026-09-17，小写 https_proxy 双写覆盖；零 Docker/零生产操作）

supervisor 用修正回合 2 的代理感知 fetcher 重建镜像，两次失败于新源码
fetch RUN（如实记录）。在已接受提交 eab02cc 之上追加第四个 local commit
（本 worktree，不 push）。

**失败与诊断证据**：

- 两份失败报告（gitignored）：`build-20260916-171833` / `build-20260916-
  171935`（`.verify/artifacts/m14-40-minio-image-adoption/`），失败点为新
  源码 fetch RUN；
- plain-log 诊断：`fetch: ... container connecting via static system HTTPS
  proxy http://127.0.0.1:7892 ... dial tcp 127.0.0.1:7892: connectex: ...
  refused`——fetcher 读到的是宿主残留的 host-loopback 死代理（宿主上
  7892 不对构建容器可达），不是操作者显式指定的代理；
- 诊断性 pinned-builder RUN **同时**传 `--build-arg
  HTTPS_PROXY=http://http.docker.internal:3128` 与 `--build-arg
  https_proxy=http://http.docker.internal:3128`：两个 env 值均正确注入。
  根因：工具此前只传 Docker 预定义大写 `HTTPS_PROXY`，而 BuildKit 可
  保留/注入小写 `https_proxy`，Go `ProxyFromEnvironment` 可优先小写 →
  宿主 loopback 死代理胜出，显式代理被架空。

**修正**（`tools/ops/minio_image_adoption.py`，唯一行为面）：当
`AIOS_MINIO_BUILD_HTTPS_PROXY` 提供时，build argv 从恰一个
`--build-arg HTTPS_PROXY=<值>` 改为确定性双写——
`--build-arg HTTPS_PROXY=<值> --build-arg https_proxy=<值>`（先大写后
小写、同值各恰一次）；argv 白名单同步收紧为该唯一形态（只写大写/只写
小写/顺序颠倒/单侧值漂移/同大小写重复一律拒绝），未设置 env 时零代理
arg 形态不变。`services/api/tests/test_minio_image_adoption.py` 契约同步
（125→**127 项**）：run_build 精确 argv 断言改双写 + 新增小写覆盖真实
失败回归（含门处拒绝只写大写的旧形态）。

修正回合 3 验证（本 worktree 真实执行，零 Docker/零生产操作）：

- 聚焦采纳套件 `test_minio_image_adoption.py`：**127 passed**；
- selfbuild 套件 `test_minio_selfbuild.py`：**24 passed**（无改动）；
- 邻居复验（selfbuild + production_recovery + compose_profiles +
  backup_drill）：**94 passed / 3 skipped**；
- `ruff check` / `py_compile`（改动代码文件）、`git diff --check`、diff
  secret 模式扫描——全部通过。

修正后状态（诚实边界）：supervisor **以双写代理重建镜像、重跑 smoke、
真实只读 preflight、生产采纳、push/PR/CI 均待执行**——修正仅为工具
argv 构造 + 白名单 + 契约锁定，不宣称修正后真实构建已通过。

## 验证（Stage 1/1b + Stage 2 复验）

- Stage 1 全量 services/api：**3045 passed / 33 skipped**；
- Stage 1b 聚焦采纳套件 `services/api/tests/test_minio_image_adoption.py`：
  **120 passed**；
- Stage 2 邻居套件复验（test_minio_selfbuild + test_production_recovery +
  test_compose_profiles + test_backup_drill）：**91 passed / 3 skipped**；
- `ruff check`（两代码文件）、`py_compile`（两代码文件）、
  `git diff --check`、diff secret 模式扫描、代理端口范围（1–65535 语义
  边界）穷尽检查——全部通过。

## 诚实边界（未完成项）

- **adoption=pass 仍不代表生产就绪**；全局 `production_ready=false` 不变。
- M14-40 **不把自建镜像采纳进生产、不做任何数据迁移**；后续受控任务：
  `minio-data` 卷 root → uid 1000 一次性迁移（M14-13 生产采纳注记），
  再 `up -d --no-build` 固化。
- supervisor 代理构建已成功；smoke 已暴露版本元数据缺陷且清理成功（见
  上「supervisor 代理构建与冒烟结果」）；第一轮修正后的三次重建均败于
  BuildKit RUN 层 DNS / BusyBox wget 无代理支持（见上「修正回合 2」）；
  第二轮修正（Go fetcher 代理感知）后的两次重建又败于小写 `https_proxy`
  宿主残留覆盖显式代理（见上「修正回合 3」）；第三轮修正（HTTPS_PROXY +
  https_proxy 双写覆盖）后的**重建镜像、重跑 smoke、真实 preflight、
  push / PR / CI 未执行**——均为 supervisor 后续动作，三轮修正回合零
  生产操作。
