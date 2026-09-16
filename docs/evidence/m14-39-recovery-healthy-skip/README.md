# M14-39 production_recovery 健康栈跳过 up + up 前本地镜像预检（恢复边界固化）

> 切片：`fix/m14-39-recovery-minio-preflight`（基于 `main@7f1fe91`（PR #115
> merge）），本 Claude 开发回合独占 worktree、单 local commit、不 push/不建
> PR/不动远程。本回合真实验证**全程只读**：绝不启停/重启/删除任何生产
> 容器（Docker/WSL/FunASR/CosyVoice/voice sidecar/CC Switch/本地代理/
> 计划任务）、不构建 MinIO 镜像、不下载上游工件、不改
> `infra/minio/Dockerfile`、不做生产 MinIO 数据卷迁移、不读取/输出 env
> secret 值；`production_ready=false` 不变。

## 问题（为什么 dry-run 被阻塞）

生产彩排栈 `aios-m14-03-production-rehearsal` 六容器均 healthy，但本机缺
M14-13 起的自建镜像 `aios/minio:RELEASE.2025-10-15T17-29-55Z`（compose
`build: ./minio` + `image:` 锚点，registry 不可拉取；本回合只读实证：

```
docker image inspect aios/minio:RELEASE.2025-10-15T17-29-55Z --format "{{.Id}}"
→ Error response from daemon: No such image: aios/minio:RELEASE.2025-10-15T17-29-55Z
```

旧 dry-run 语义在 pin 9/9 后无条件 `compose up -d --no-build --dry-run`，
计划 Recreate api/livekit/minio 并因缺 MinIO 镜像 rc=1——把「栈已健康」
误报为恢复失败（M14-23 以来既有阻塞，M14-38 记录在案的诚实边界）。

已评估并否决的捷径：`--no-recreate` + `pull_policy: never` 可让 dry-run
rc=0，但会隐藏「计划重建」这一真实漂移信号——恢复工具不得靠隐藏计划
换绿灯（手工验证过可行性，不采纳）。

## 修复设计（恢复边界固化）

1. **健康栈跳过 up**：pin 通过后、up 前做只读 stack health 快照
   （`compose ps --format json`）。六服务全部 healthy/running 时，
   **dry-run 与 enforce 一致跳过 compose up**——明确输出「健康栈无需
   up」，继续语音调和与最终判定。边界语义：startup recovery 是恢复健康，
   不是部署/config drift 收敛；拓扑变更由 M14-38
   `tools/ops/livekit_lan_cutover.py` 显式执行。
2. **非全健康保留原唯一 up 语义**：六服务缺失或不健康时仍为
   `up -d --no-build`（dry-run 附加 compose 原生 `--dry-run`）——不加
   `--no-deps`、不加 `--no-recreate`、不隐藏漂移（恢复路径整栈 up 口径，
   与 M14-38 cutover 的 `--no-deps api livekit` 最小范围重建分工；
   `--no-deps` 是 cutover 的依赖隔断语义，恢复路径不得借它隐藏
   depends_on 漂移）。
3. **up 前只读本地镜像预检（fail-closed）**：仅在将执行 up（栈非全健康）
   时，对 `LOCAL_BUILD_IMAGE_REFS`（现仅 `aios/minio:<RELEASE>`，与
   compose `image:` 锚点契约测试交叉锁定同步）逐个 `docker image
   inspect`（零 pull/build/stop/restart；探测失败同样按缺失处理）。缺失
   → 输出固定词汇 `minio-local-image-missing` + 镜像引用（repo 公开
   compose 锚点，非 env secret），并在 up 之前 fail-closed（enforce 立即
   退出 1、dry-run 退出码镜像 enforce 拒绝）——绝不 pull/build，缺镜像 =
   人工在获准窗口构建后重试。
4. **健康栈不触发预检**：全健康跳过 up 即无预检——恢复结果不因「本机
   未构建 minio 自建镜像」被健康栈误伤（真实场景：本机缺镜像 + 栈健康
   → dry-run 绿灯）。

不变量：PIN_KEYS 九键与 pin 语义零改动；不改生产 env；不输出 secret
（子进程输出写日志前防御性 redact）；单文件纯标准库 + Runner/RunLog 注入
风格；M14-38 `compose_up_limited` 的 `up -d --no-build --no-deps api
livekit` 语义零改动；源码契约新增锁定绝不出现 `--no-deps`/`--no-recreate`/
pull/build 字面量。

## 变更面

- `tools/ops/production_recovery.py`：新增 `LOCAL_BUILD_IMAGE_REFS`/
  `LOCAL_IMAGE_MISSING` 常量、`stack_health_gaps`（健康缺口判定单一口径，
  `wait_stack_healthy` 复用）、`check_local_build_images`（只读预检）；
  `run_recovery` 在 pin 通过后插入「健康快照 → 全健康跳过 up / 非全健康
  预检 + 原 up 语义」分支；dry-run 健康预告复用同一快照。
- `services/api/tests/test_production_recovery.py`：+10 项 M14-39 契约
  测试（含参数化）：全健康跳过 up（dry-run/enforce）、非全健康唯一 up
  语义（`--no-deps`/`--no-recreate`/`--build` 均不在 argv）、MinIO 缺镜像
  且栈非全健康 fail-closed（双模式）、健康栈 + 缺镜像仍 OK、快照只读
  （唯一 `compose ps --format json`）、预检只读 + 固定词汇、预检常量与
  compose 锚点交叉锁定、`stack_health_gaps` 纯函数、M14-38 九键/复用面
  不回归；既有 e2e 按新语义收窄（绿灯 = 跳过 up；up 失败路径预制非健康
  栈）；源码契约测试扩展禁词（`--no-deps`/`--no-recreate`/pull/build）。
- 文档：`tools/ops/README.md`（production_recovery 流程 4/退出码）、
  `docs/PROJECT_STATUS.md`、`docs/ROADMAP.md`、`docs/CHANGELOG.md`、
  本 README。

## 真实验证（2026-09-16，全程只读）

### dry-run（期望：exit 0 / pin 9/9 / 六服务健康 / 跳过 up / 语音 leave / OK）

```
& "D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe" `
  tools\ops\production_recovery.py --dry-run `
  --env-file D:\AI Learning OS\ai-learning-os\infra\env.production-recovery
```

关键输出（日志全文：gitignored
`artifacts/recovery/recovery-20260916-192131.log`）：

```
[recovery] docker engine: 就绪（server 29.7.2）
[recovery] compose config: OK（--quiet 静态校验通过）
[recovery] pin: 一致键 9/9（值不回显）；比对对象: api/web/livekit 容器
[recovery] pin: OK（九键齐全：env 无缺键/占位，且与在线容器逐键一致）
[recovery] stack health 快照（只读）: {"api": "healthy", "livekit": "healthy", "minio": "healthy", "postgres": "healthy", "redis": "healthy", "web": "healthy"}
[recovery] stack health: 6/6 服务 healthy/running——健康栈无需 up（dry-run 与 enforce 同语义跳过）
[recovery] funasr: state=unmanaged-running health=200 → leave（…不触碰（healthy untouched））
[recovery] cosyvoice: state=unmanaged-running health=200 → leave（…不触碰（healthy untouched））
[recovery] === 结果: OK（恢复完成/无需恢复，全部核查通过）===
EXIT=0
```

旧语义在同一栈上会于 pin 9/9 后构造 `up -d --no-build --dry-run` 并因
缺 MinIO 镜像 rc=1；新语义零 up 构造、exit 0。

### 容器不变证据（前后只读 `docker ps` 完全一致）

| 容器 | ID | 前 | 后 |
|---|---|---|---|
| api | `b3e62b355703` | Up 7 hours (healthy) | Up 7 hours (healthy) |
| web | `5be2e19db2e7` | Up 42 hours (healthy) | Up 42 hours (healthy) |
| livekit | `49935f127ca0` | Up About an hour (healthy) | Up About an hour (healthy) |
| postgres | `d17d5a93a079` | Up 42 hours (healthy) | Up 42 hours (healthy) |
| redis | `89f3ed0fd02a` | Up 42 hours (healthy) | Up 42 hours (healthy) |
| minio | `04e7d898bb40` | Up 42 hours (healthy) | Up 42 hours (healthy) |

六个容器 ID/启动时长连续（时长自然增长、无重启）、状态全 healthy——
零启停/重建/删除。

### 测试与静态检查

- 聚焦：`pytest services/api/tests/test_production_recovery.py
  services/api/tests/test_livekit_lan_cutover.py` → **92 passed**（63+29，
  0.35s）；
- 全量：`pytest services/api` → **2929 passed / 0 failed / 33 skipped**
  （3:42；skip 均为既有门控）；
- `ruff check`（改动三文件）All checks passed；`py_compile` 通过；
  `git diff --check` 干净；新增/修改行 secret 扫描 0 真实凭据命中
  （仅伪 secret 标记与公开 compose 镜像锚点/固定词汇）。

## 诚实边界（未完成项）

- **minio 自建镜像仍未在本机构建/采纳**：预检只把失败提前为可见拒绝，
  不解决缺镜像本身——栈一旦真实非全健康且需要重建 minio 时，恢复仍会
  fail-closed，需人工在获准窗口构建镜像并处理 `minio-data` 卷属主迁移
  （M14-13 README 的生产采纳注记：root → uid 1000 一次性迁移），再做
  `up -d --no-build` 固化。本回合不做其中任何一步。
- `minio-local-image-missing` fail-closed 的真实 Docker 路径未在本机触发
  （触发需栈非全健康——本回合绝不制造该状态）；该路径由 FakeRunner
  契约测试双模式覆盖。
- 健康跳过判定以 `compose ps` 的 health/state 为准（与既有健康门口径
  一致），不覆盖 compose 文件相对在线容器的配置漂移检测——那是 pin
  check（九键）与 M14-38 工具的职责；恢复工具不承担 config drift 收敛。
- enforce 真实执行未做（本回合只做只读 dry-run；enforce 的健康跳过分支
  与 dry-run 同码路径，由契约测试双模式锁定）。
- 单次 dry-run + 契约测试不构成 `production_ready=true` 依据；全局
  `production_ready=false` 不变。
