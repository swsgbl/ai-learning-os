# M14-12 生产监控 readiness（只读采集 + 阈值判定 + 证据报告）— 交付证据归档

- 日期：2026-09-11（交付 + 生产监控执行）/ 2026-09-12（supervisor 评审
  R1 修正 + 生产结果回填）
- 分支：`feat/m14-12-production-monitoring-readiness`（基于 `main@2a33ac5`，
  即 PR #84 merge commit `2a33ac541e7c24341931c4fd3659f9f6288d62f5`，本地
  git 可验证）。本 Claude 开发回合仅做本地 commit；supervisor 审查与
  remote 发布（push/PR/合并）在其后进行。交付已随 **PR #85** 合并 main
  （merged_at **2026-09-11T17:34:05Z**；merge commit
  `52980c637f4e39fec196a6563dbab1875faa6a8f`，feature head
  `80e599d07fb448766d97a105c99d83275a50784a`，本地 git 可验证；远端
  feature 分支已删除）；本回填切片分支
  `docs/m14-12-production-monitoring-results`（基于 main@52980c6，
  docs-only，本 Claude 回合仅做本地 commit——remote 发布由 supervisor
  审查后代行）。
- 状态：**工具 + 聚焦契约测试交付并合并；开发回合未执行任何真实生产
  采集（supervisor 已于合并后执行首次真实只读监控，见下）**。
  本切片是「监控/告警收口」的第一块可控基础：只读采集、阈值判定、
  证据报告；**不接外部告警系统**（本里程碑范围明确排除）。**R1 修正已
  落实（supervisor 评审，2026-09-12，本地追加 commit，随 PR #85 合并）**：
  ① 非有限浮点
  （nan/inf/-inf）在 plan 报告写入/采集之前拒绝；② `--project` 严格
  白名单（ASCII 字母数字开头、仅字母数字/连字符/下划线、≤64——被拒值
  绝不回显）；③ `--artifact-dir` 口径修正（默认目录 gitignored，自定义
  路径为操作者显式自选）；④ 状态文档 commit 措辞修正（本回合口径 =
  仅本地 commit，supervisor 审查与 remote 发布在其后进行）。
  **合并与生产执行状态（supervisor 给定事实，如实回填）**：PR CI run
  `34627846208` 与合并后 main push CI run `34628419347` 均全部 5 job
  （Web/API/Docker/Android/Release tools）SUCCESS；supervisor 已于
  **2026-09-11T17:39:20Z–17:39:21Z** 在 canonical main 上用本工具执行
  一次真实只读生产监控，结果全绿（见「生产监控执行结果」）。
- 入库变更：`tools/ops/production_monitor.py`（单文件、纯标准库、零第三方
  依赖，与 soak_rehearsal.py / production_recovery.py 同款纪律：注入式
  Runner/Transport/Clock、schema 版本化、原子写、fail-closed）、
  `services/api/tests/test_production_monitor.py`（189 项契约测试，含 R1
  修正回归）、本 README，以及 PROJECT_STATUS / ROADMAP / CHANGELOG /
  `tools/ops/README.md` 同步；本回填切片 docs-only（仅上述文档）。
- 结论口径（诚实边界，回填更新）：**工具交付并合并；开发回合未执行
  生产采集（supervisor 已于合并后执行首次真实只读采集，见下）；
  `production_ready=false` 仍不变**。`monitoring_ready` 仅指「一次只读
  采集的阈值判定全绿」，与生产
  就绪是两个概念——本工具绝不宣称生产就绪。

## 产品形态

```
# plan（默认：零 subprocess / 零网络 / 零生产读取——打印计划并落 plan 报告）
python tools/ops/production_monitor.py

# execute（旗标 + 精确确认短语齐备才放行，缺一即 EXIT 2 零采集）
python tools/ops/production_monitor.py --execute \
    --confirm "EXECUTE READ-ONLY PRODUCTION MONITORING"
```

- **双模式门禁（fail-closed）**：默认 plan 零副作用（测试以 socket +
  subprocess 双阻断实证）；execute 需 `--execute` 旗标 + 精确确认短语
  `EXECUTE READ-ONLY PRODUCTION MONITORING`（一字不差）+ 全部阈值在
  硬顶内（R1 起：非有限浮点 nan/inf/-inf 显式拒绝；`--project` 严格
  白名单——ASCII 字母数字开头、仅字母数字/连字符/下划线、≤64，空/
  空白/控制/路径/换行/非 ASCII 一律拒绝且被拒值绝不回显；均在 plan
  报告写入/采集之前）——任一不满足即 EXIT 2 且零采集（Runner/
  Transport 零构造，测试以构造计数器实证）。plan 报告不出现任何
  `overall_status` / `monitoring_ready` 状态宣称。
- **只读采集面（固定画像，不可经 CLI 注入任意目标）**：compose project
  `aios-m14-03-production-rehearsal`（`--profile local`）——
  ① `docker compose ps --format json`（六受管服务 health/state）；
  ② 六受管容器（postgres/redis/minio/api/web/livekit）逐容器
  `docker inspect`（state / health / RestartCount / image / started 五
  事实）；③ 五默认端点 GET（Web `http://127.0.0.1:3011/`、`/login`；
  API `http://127.0.0.1:8000/health`；FunASR `http://127.0.0.1:8010/health`；
  CosyVoice `http://127.0.0.1:8011/health`）状态 + 延迟；④ 容器日志安全
  错误摘要（`docker logs --tail <n>`——只记匹配计数 / 级别 / 安全类别，
  **原文绝不持久化**）。
- **子进程白名单门（结构性）**：一切 docker 命令必经 `ReadonlyRunner` 的
  `is_readonly_docker_command` 校验——仅 `compose ps` / `inspect
  --format` / `logs --tail` 三只读形态放行；`stop`/`rm`/`kill`/
  `restart`/`down`/`exec`/`up`/`logs -f`（跟随）/ 非 docker 程序
  （curl/powershell/cmd）一律在**任何执行之前**拒绝
  （`CommandNotAllowedError`，类别 `command-not-whitelisted`）；Windows 侧
  恒 `CREATE_NO_WINDOW`。
- **loopback 纪律（与 M14-11 soak 同款）**：五端点固定画像仅字面 loopback
  IP（127.0.0.0/8、`::1`）；主机名（含 localhost）一律拒绝（零 DNS）；
  query/fragment/userinfo/缺显式端口一律拒绝。HTTP 用 `http.client`
  直连——该路径从不读取 proxy 环境变量/系统代理（结构性 loopback 旁路，
  测试以「恶意假代理零连接」实证）；proxy env 仅探测**键名存在性**写入
  报告注记，值绝不读取/记录。GET-only、无认证、无 cookie/token、不读
  响应体、不跟随重定向、带超时（0.5–10s，默认 5s）。
- **部分失败如实入档（缺失 ≠ healthy）**：任一采集器失败 → `partial=true`
  + 失败安全类别（仅类别 + 异常类名，绝不保留异常文本）→
  `overall_status=incomplete`；fail-closed：采集失败的项在阈值判定中恒为
  可见 critical alert，绝不计为 healthy。
- **阈值/状态（全部含边界，warn 恒可见）**：compose 6/6 healthy（缺席或
  非 healthy → critical）；五端点恒 200（非 200 → critical）；容器
  health（unhealthy/非 running → critical；starting/none → warn——不可证
  healthy 但未证坏）；RestartCount（默认 warn≥1 / critical≥5）；日志错误
  计数（fatal+error+critical 合计，默认 warn≥5 / critical≥20）；端点延迟
  （默认 warn≥1000ms / critical≥5000ms）。CLI 可调但全部受硬顶
  fail-closed（超顶在 plan 模式同样拒绝）。输出
  `overall_status=ok|warn|critical|incomplete` + 逐项 alert +
  `monitoring_ready`（仅采集完整且零 warn/critical 时 true）。
- **报告**：schema 版本化（`schema_version=1`）JSON + Markdown **原子写**
  （同目录 tmp + fsync + `os.replace`；拒绝 symlink 组件（目标/祖先目录）
  与越界 stem；无 tmp 残留、同 stem 覆盖干净）；**默认目录
  `REPO_ROOT/.verify/artifacts/m14-12-production-monitoring`（gitignored），
  `--artifact-dir` 自定义路径为操作者显式自选覆盖——其位置与 gitignore
  状态由操作者负责**（报告边界注记与 CLI help 同口径，不宣称自定义路径
  恒 gitignored）；含 UTC start/end、
  配置（项目/画像/阈值）、边界注记、collector 状态、阈值结果与计数
  （自洽性测试锁定）；写盘前经防御性脱敏终防线（凭据形态标记值测试实证
  绝不入档）。
- **退出码**：0 = plan 成功 / execute 完整采集且 ok|warn（**warn 恒可见
  不隐藏**——stdout alert 行 + 报告 alerts + `monitoring_ready=false`）；
  2 = 非法/fail-closed（确认缺失、阈值超顶、symlink/越界路径）或采集
  incomplete 或存在 critical（含证据报告写入失败——证据不可失）。
- **本里程碑不做的事**：零外部告警发送（webhook/邮件/IM 等一概没有）；
  不宣称 production ready；不改动任何容器/服务/env/计划任务。

## 本回合验证（2026-09-11 交付 + 2026-09-12 R1 修正，canonical venv Python 3.11.15 / pytest 9.1.1 / ruff 0.16.5）

1. **聚焦契约测试**：`python -m pytest services/api/tests/test_production_monitor.py -q`
   → **189 passed**（连跑两遍全绿；交付回合 149 + R1 修正回归 40）。
   覆盖：plan 零副作用（socket+
   subprocess 双阻断 + plan 报告零状态宣称）、execute 门禁 fail-closed
   （缺旗标/短语不精确 ×6/阈值超硬顶 ×16——含「旗标+短语齐备但超顶」同
   样拒绝，且 Runner/Transport 零构造）、固定画像与 loopback 校验矩阵
   （接受 7 / 拒绝 11）、子进程白名单（接受 3 形态 / 拒绝 18 形态 +
   拒绝发生在任何执行之前 + 异常仅类别+类名）、源码契约（零
   `urllib.request`/`urlopen`/`getproxies`/`ProxyHandler`/第三方 HTTP
   库/`https://`/Cookie/Authorization/Bearer/非 GET 方法字面量；唯一
   `subprocess.run` 执行点 + CREATE_NO_WINDOW；唯一 `connection.request
   ("GET"` 发请求点，仅 UA/Accept 头）、采集器单元（compose ps 三形态
   解析 + 垃圾拒绝 + 无 Service 键 fail-closed 跳过；inspect 五事实解析
   与失败类别 ×4；端点 GET 注入与异常脱敏；日志摘要词边界计数 + 原文
   绝不入摘要）、部分失败 partial/缺失≠healthy、阈值边界（延迟 ×5 /
   restart ×5 / 日志错误 ×5 / 容器 health 矩阵 ×6 / 端点 200 矩阵 ×5 /
   compose 缺席与不 healthy）、precedence（incomplete > critical >
   warn）、计数自洽（34 项清单 + 子集）、报告 schema/边界注记/Markdown、
   脱敏层（标记值绝不落入 JSON/Markdown + 端到端日志标记值零入档）、
   原子写（无 tmp 残留/同 stem 覆盖/symlink 目标/祖先目录/越界 stem ×6/
   monkeypatch 确定性锁定）、proxy env 仅键名存在性（含空值 membership
   语义）、退出码矩阵（happy 0 / warn 0 但恒可见 / incomplete 2 /
   critical 2 / compose ps 失败 2 / 端点不可达 2 / 报告写失败 2）、CLI
   注册（parser 默认值/旗标/README 文档化）、真实 transport 行为（本机
   假服务器 GET-only+仅 UA/Accept 头、恶意假代理零连接实证、不可达安全
   归类）；**R1 修正回归**（非有限浮点 ×7 双路径零产物零采集、项目名
   接受 ×5 / 拒绝 ×15 + 固定词汇拒绝原因 + CLI 双路径 + 被拒值零回显、
   artifact-dir 口径三面锁定（REPORT_BOUNDARIES/CLI help/运行时注记）、
   状态文档措辞 sweep）。
2. **邻居回归（共享模块零改动，纯干扰排查）**：`test_soak_rehearsal.py`
   → **61 passed**；`test_production_recovery.py` → **45 passed**；
   `test_windows_startup_task.py` → **66 passed**。
3. **静态**：`python -m ruff check services/api` 全过（另对工具与测试
   文件单独跑 ruff 亦全过）、`python -m py_compile
   tools/ops/production_monitor.py` 过、`git diff --check HEAD^ HEAD`
   过（R1 提交增量）。

测试中唯一真实 socket 流量：测试自起的 127.0.0.1 ephemeral 假 HTTP 服务
器与计数假代理——**零生产端口流量**（3011/8000/8010/8011 全程未触碰），
**零真实 Docker 命令**（全部经 FakeRunner 注入）。

## 安全边界（本回合零违背）

- 开发回合未执行任何真实 Docker / HTTP 生产采集 / 计划任务 / 恢复任务 /
  语音服务 / 代理操作；下节「生产监控执行结果」为 **supervisor 给定
  事实的如实回填**（本回填回合 docs-only，自身零生产采集）。
- 不启动/停止/重建/构建/拉取/修改任何容器；不触碰恢复 env；零密钥/零
  env 原文/零 token/零 header/零 query/零原始日志行入档；本 Claude
  开发回合仅做本地 commit（supervisor 审查与 remote 发布在其后进行）；
  不触碰 untracked `.claude/`。
- 真实 execute 采集（只读）由 supervisor 在获准窗口执行；运行结果落
  gitignored `.verify/artifacts/m14-12-production-monitoring/` 绝不入库
  （本 README 仅引用文件名与指标）。

## 生产监控执行结果（supervisor 获准窗口执行，2026-09-11；本节为 supervisor 给定事实的如实回填）

**执行形态**：canonical main（PR #85 merge `52980c6` 之后），一次只读
execute（`--execute --confirm "EXECUTE READ-ONLY PRODUCTION MONITORING"`），
**2026-09-11T17:39:20Z → 17:39:21Z**；原始报告为 gitignored
`.verify/artifacts/m14-12-production-monitoring/monitor-20260911-173920.json`
（14185 bytes）与 `monitor-20260911-173920.md`（3330 bytes）——绝不
入库，本节仅引用文件名与指标。

**采集与阈值结果（逐项摘自报告）**：

| 项 | 结果 |
|---|---|
| compose `aios-m14-03-production-rehearsal` | 六服务全部 running healthy；restart_count 全 0 |
| 五端点 GET | 全 200；延迟 ms：web-root 7.088、web-login 12.909、api-health 12.388、funasr-health 24.444、cosyvoice-health 27.641 |
| 容器日志摘要（逐容器扫描） | error_total 全 0 |
| 采集完整性 | partial=false |
| 阈值计数 | ok=34 / warn=0 / critical=0 |
| 总状态 | overall_status=ok；monitoring_ready=true |

**边界（不过度引申）**：本次 `monitoring_ready=true` 仅为**单次只读快照
全绿**——不等于 production_ready；`production_ready=false` 保持不变，
因以下仍未开放：持续/定时采集与调度、外部告警接入、指标历史与留存、
阈值随时间的标定、跨机监控。本回填切片（docs-only）零生产采集/零容器
改动/零计划任务/零 env 触碰/零密钥读取/零生产数据写入——执行方为
supervisor，事实由其给定并在此如实回填。

## 结论边界（不过度引申）

- 本切片交付了监控/告警收口的第一块基础（工具 + 契约测试，已随
  PR #85 合并），并完成**首次单次真实只读生产监控（全绿快照）**。
- 未覆盖（后续切片范围）：外部告警系统接入（webhook/邮件/IM）、持续/
  定时采集与调度、指标历史与留存、阈值随时间的标定、跨机监控。
- **`production_ready=false` 不变**；`monitoring_ready=true` 也仅在单次
  只读采集阈值全绿时出现，不构成生产就绪宣称。
