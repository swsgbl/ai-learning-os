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
  socks5h/socks5/http/https + host[:port]，端口语义校验 1–65535，且只向
  构建传入 Docker 预定义 `HTTPS_PROXY` build arg——零 GOPROXY 改动、零
  Dockerfile 改动、零 pin 改动。

### smoke（一次性冒烟）

- 只使用严格 `aios-m14-40-` 前缀的生成式一次性容器/卷名；loopback
  19000/19001（忙端口/名字冲突在任何副作用之前 fail-closed）。
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
- 代理构建未执行；真实 smoke / 真实 preflight 未执行；push / PR / CI
  未执行——以上均为 supervisor 后续动作，本回合零生产操作。
