# M14-127：Production Drift Watch 证据

## 1. 结论与边界

M14-127 交付 `tools/ops/production_drift_watch.py`：只读校验生产栈是否仍
锚定 M14-124 已批准发布镜像，填补 M14-124 生产切换后「健康监控
（M14-12 production_monitor）只证明容器健康、不证明容器运行的镜像没
漂移」的运维缺口。

- 本切片改动面：新增工具 + 契约测试 + 两份文档（本 README 与
  tools/ops/README.md M14-127 段）。**未修改任何生产配置、compose、
  API/Web/Harmony 源码；未触碰任何生产容器。**
- 真实只读 execute 冒烟结论（2026-09-25T02:40:25Z，详见 §4）：
  **drift=false**——七 compose 服务全部 healthy，API/Web 运行 tag、
  运行镜像 ID、锚点 tag 本地解析三重比对全部与 M14-124 已批准锚点
  精确一致。
- `drift=false` 仅表示该时刻采集范围内锚点全部吻合，**不构成生产健康/
  就绪宣称，不解除任何 release gate**；release-approval 仍是 human-only
  门；`production_ready=false` 不变。
- 本工具是漂移检测器，不是漂移修复器：发现漂移时只产出诚实失败证据
  与非零退出码，绝不 stop/start/restart/rebuild/delete 任何容器。

## 2. 镜像锚点（单一事实源）

锚点为 M14-124 已批准并实证的发布镜像（来源：
`docs/evidence/m14-124-production-cutover/README.md` §1/§4），在工具内
烘烤为常量 `EXPECTED_ANCHORS`（绝不取自请求/env/文件，不可经 CLI 注入，
加载时断言 digest 形态）：

| 服务 | 预期 tag | 预期 sha256 digest |
|------|----------|--------------------|
| API | `aios/api:m14-124-production` | `sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f` |
| Web | `aios/web:m14-124-production` | `sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b` |

固定采集画像：compose project `aios-m14-03-production-rehearsal`
（`--profile local` + `--profile search`，七服务 = postgres/redis/minio/
api/web/livekit/searxng）；容器名 `<project>-<service>-1`。

## 3. 检查设计（fail-closed）

execute（需 `--execute` + 精确确认短语
`EXECUTE READ-ONLY PRODUCTION DRIFT WATCH` 一字不差，缺一即 EXIT 2 零
采集）按固定画像执行三面只读采集：

1. `docker compose -f infra/docker-compose.yml -p <project>
   --profile local --profile search ps --format json`——七服务存在性 +
   Health/State（原始行的 Labels/Ports 等字段在解析层即丢弃，绝不保留）；
2. 逐容器 `docker inspect --format <state/health/Config.Image/.Image
   四事实格式串>`——七容器 state=running + Docker health=healthy；
3. API/Web 锚点镜像 `docker image inspect --format {{.Id}} <expected
   tag>`——本地 tag→image ID 解析，抓「tag 被移到新镜像而容器未重建」
   形态的漂移。

判定：七服务 present+healthy、七容器 running+healthy、API/Web
① 运行 tag == 锚点 tag；② 运行镜像 ID（`sha256:<64 位小写 hex>` 精确
形态）逐字符 == 锚点 digest；③ tag 本地解析 ID == 锚点 digest——全部
成立才 `drift=false`（exit 0）。任何采集失败（rc≠0、输出不可解析、
digest 形态非法）或比对不符 → `drift=true` + 固定词汇
`drift_reasons`（exit 2）。**tag 相同绝不冒充 digest 通过；digest 无法
取得时恒判 drift（不可证明无漂移即视为有漂移）。**

子进程白名单（结构性，`is_readonly_docker_command`）：仅上述三只读
形态放行（compose ps 后必须恰为 `--format json`；两处 inspect 的
`--format` 值必须逐字等于模块常量且恰一个对象名）；stop/start/restart/
rm/kill/down/exec/up/build/pull/logs/裸 ps/错格式串/多对象一律在任何
执行之前拒绝；无 shell=True（AST 契约锁定）；Windows 侧恒
CREATE_NO_WINDOW。零网络、零 env 读取（源码级 AST 契约锁定）；绝不读
取/打印容器 env、secret、日志正文、DB/MinIO/voice 数据。

报告：schema v1 JSON + Markdown 原子写（tmp+fsync+os.replace；symlink
组件/越界 stem 拒绝零写入）落 gitignored
`.verify/artifacts/m14-127-production-drift-watch/`；内容含 UTC 时间戳、
配置与锚点、逐项检查、服务健康、镜像 tag/digest/identity 安全摘要与
边界声明；写盘前经 redact_secrets 终防线（防御性脱敏契约测试锁定）。

## 4. 真实只读 execute 冒烟

环境：worktree `m14-127-production-drift-watch`（基点 main
`308b2bcee9c196356b8ad3505d64871e18b0bacd`）；Python 3.11.15（canonical
主仓库 venv）；真实 Docker Desktop 生产栈（零模拟、零 fake）。

### 4.1 plan 冒烟（先行）

```
python tools/ops/production_drift_watch.py
```

结果：exit 0；stdout 打印固定画像（项目/profiles/七容器名/双锚点/
计划检查/确认短语）；plan 报告
`plan-20260925-024059.json` / `.md` 落盘（零状态宣称——无 drift/
checks/counts 键）。plan 路径零 subprocess/零 Docker/零网络/零 env 读取。

### 4.2 execute 冒烟

```
python tools/ops/production_drift_watch.py --execute \
    --confirm "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"
```

结果：**exit 0，drift=false，检查计数 pass=28 / fail=0**。

采集事实（全部来自真实 Docker 只读输出）：

| 检查面 | 结果 |
|--------|------|
| compose ps（local+search）| 7/7 服务 present，Health 全 `healthy` |
| 容器 inspect ×7 | state 全 `running`，health 全 `healthy` |
| API 运行 tag | `aios/api:m14-124-production` == 锚点 |
| API 运行镜像 ID | `sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f` == 锚点 digest（逐字符） |
| API tag 本地解析 | `sha256:c99e28c9…38323781f` == 锚点 digest |
| Web 运行 tag | `aios/web:m14-124-production` == 锚点 |
| Web 运行镜像 ID | `sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b` == 锚点 digest（逐字符） |
| Web tag 本地解析 | `sha256:d596f0c7…a38194b` == 锚点 digest |

结论：截至 2026-09-25T02:40:25Z，生产栈运行镜像与 M14-124 已批准
锚点完全一致，无漂移。若未来某轮出现漂移，工具将以 exit 2 与诚实
drift_reasons 报告，不做任何修复。

工件（gitignored `worktree .verify/artifacts/m14-127-production-drift-watch/`）：

| 文件 | 字节数 | SHA-256 |
|------|--------|---------|
| `drift-watch-20260925-024025.json` | 11023 | `60e772efd61095303d69e56497894ad77f6c31cc164e0169dcb6c6f49f0c0a8b` |
| `drift-watch-20260925-024025.md` | 5762 | `c70559354d3024ba5d08fbc06be6c06bbfc95a8a18ea55c6844e5f0a38230b00` |
| `plan-20260925-024059.json` | — | plan 产物（零状态宣称，见 §4.1） |
| `plan-20260925-024059.md` | — | 同上 |

报告时间窗：started=2026-09-25T02:40:25Z / ended=2026-09-25T02:40:25Z
（单轮快照语义，见 §5 边界 4）。

## 5. 验证记录

在本 worktree、同一 canonical venv（Python 3.11.15）下执行：

- 新增契约测试 `services/api/tests/test_production_drift_watch.py`：
  96 项全过（pytest，`--basetemp` 落 `D:\AI Learning OS\.pytest-tmp\
  m14-127-production-drift-watch\`）。
- 聚焦回归：`services/api/tests/test_production_monitor.py`（同族
  Runner/白名单纪律）与 `services/api/tests/test_post_cutover_watch.py`
  （M14-124 前后工具）全过，详见切片交付报告数字。
- `ruff check` 新增两文件通过；`py_compile` 通过；`git diff --check`
  干净。

## 6. 诚实边界

1. `drift=false` 是 2026-09-25T02:40:25Z 单轮快照结论——本切片不含
   持续调度（未注册计划任务、未接入 M14-14 监控管道）；窗口外的漂移
   不被本证据覆盖。
2. digest 校验的是**本地 image ID（config digest 形态）**与容器运行
   `.Image` 的一致性——这正是 M14-124 记录与验收的锚定口径（本地构建
   镜像无 registry manifest digest）；工具不与任何远程 registry 通信。
3. 基础设施五容器（postgres/redis/minio/livekit/searxng）只校验健康，
   不锚定其镜像 digest（M14-124 亦未重建它们；锚定它们的必要性留给
   supervisor 决策）。
4. 工具对 `docker compose ps`/`docker inspect` 的输出解析依赖 Docker
   CLI 当前字段形态；解析失败一律 fail-closed 判 drift，绝不猜测。
5. 真实 execute 由本切片在 supervisor 任务书授权下运行；工具源码与
   契约测试的开发回合零真实 Docker（FakeRunner 注入）。
6. 本切片不修改 M14-12 production_monitor、不改变任何 release gate
   语义、不代拟任何审批。
