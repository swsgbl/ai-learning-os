# tools/ops —— 生产恢复编排（M14-06）+ soak/并发彩排 harness（M14-11）+ 生产监控 readiness（M14-12）+ 监控历史（M14-13）+ 监控管道/调度 readiness（M14-14）+ 监控历史洞察（M14-15）+ MinIO 镜像采纳预检（M14-40）+ MinIO 卷属主采纳（M14-41）

本机 Windows 生产彩排栈（Docker Desktop + WSL 语音引擎）的自愈编排与
只读负载彩排。容器面兜底由 `infra/docker-compose.yml` 的
`restart: unless-stopped`（M14-06）承担；本目录的 `production_recovery.py`
承担编排面：等引擎 → 校验 → 幂等 up → 健康核查 → 本地语音受控调和；
`soak_rehearsal.py`（M14-11）承担只读 soak/并发彩排面（fail-closed 五要素
门禁，默认 plan 零网络）；`production_monitor.py`（M14-12）承担只读
监控采集 + 阈值判定 + 证据报告面（默认 plan 零采集，不接外部告警）；
`monitoring_history.py`（M14-13）承担监控历史索引 + 有界留存 + 趋势
摘要面（只读 M14-12 工件，零墙钟确定性输出）；`monitoring_pipeline.py` +
`monitoring_pipeline_task.py` + `run_monitoring_pipeline_silent.vbs`
（M14-14）承担持续/定时采集的**组合管道与调度**面（单次
monitor → history → insights 组合（M14-21 起接入 insights）+ 计划任务
管理器；**M14-22 起真实计划任务三步持续执行（两轮连续调度成功）与
insights 两轮产出/刷新已经 supervisor 验收**，`production_ready=false`
不变）；
`monitoring_insights.py`（M14-15）承担
监控历史**洞察/告警摘要**面（只读 M14-13 history.jsonl 或 M14-12 monitor
工件目录 → 安全 JSON+MD 摘要；仅本地工件洞察，不接外部告警，
`production_ready=false` 不变；M14-21 起默认输入与 history canonical
输出一致并被持续管道持续更新——**M14-22 已验收真实调度首轮产出**）。

## production_recovery.py

```
# 仓库根执行（canonical venv 或任意 Python ≥3.11，纯标准库）
python tools/ops/production_recovery.py --dry-run     # 全程只读体检 + 决策预告
python tools/ops/production_recovery.py               # enforce（需 pin env 就绪）
```

流程与安全性质（细节见脚本头注释与
`services/api/tests/test_production_recovery.py` 契约测试）：

1. **等 Docker 引擎**：轮询 `docker version`（默认 300s，`--engine-wait-seconds`
   可调）——登录自愈场景给 Docker Desktop 留启动时间。
2. **compose 静态校验**：`docker compose config --quiet`（零容器改动）。
3. **pin check（fail-closed）**：`infra/env.production-recovery`（gitignored，
   模板 `infra/env.production-recovery.example`）必须存在、六键齐全
   （`AIOS_IMAGE_TAG/AIOS_WEB_IMAGE_TAG/AIOS_APP_ENV/AIOS_WEB_PORT/
   AIOS_AUTH_SECRET/AIOS_LIVEKIT_API_SECRET`——M14-09 起 web 镜像 tag 独立
   成键：Web-only 升级只改 `AIOS_WEB_IMAGE_TAG`，同 tag 发布两键显式同值）、
   不含模板占位值（`<...>` 包裹或模板原文）；
   在线容器存在时**六键在线事实必须齐全且逐键相等**（api/web 镜像分别
   inspect，缺事实 ≠ 跳过——
   inspect/port 探测不完整同样拒绝）。仅报键名，值绝不回显（子进程输出写
   日志前经防御性 redact）。任一不满足 → enforce 在 `up` 之前可见拒绝——
   防止恢复路径用默认值/漂移值/占位值静默重建容器（tag/端口/密钥轮换）。
4. **up 决策（M14-39 恢复边界）**：pin 通过后先做只读栈健康快照
   （`compose ps --format json`）。六服务（postgres/redis/minio/api/web/
   livekit）全部 healthy/running 时，dry-run 与 enforce **一致跳过
   compose up**（明确输出「健康栈无需 up」，继续语音调和与最终判定）——
   startup recovery 是恢复健康，不是部署/config drift 收敛；拓扑变更由
   `livekit_lan_cutover.py` 显式执行。栈有缺失/不健康时：
   1. **up 前只读本地镜像预检**：compose 自建镜像锚点
      （`aios/minio:RELEASE.2025-10-15T17-29-55Z`，M14-13 起本地构建、
      registry 不可拉取）经 `docker image inspect` 探测（零
      pull/build/stop；探测失败按缺失处理）；缺失 → 输出
      `minio-local-image-missing` 并在 up 之前 fail-closed（本工具绝不
      pull/build——人工在获准窗口构建后重试）。栈全健康时跳过 up 即
      不触发预检（健康栈不因本机未构建自建镜像被误伤）。
   2. **幂等 up**：`docker compose -f infra/docker-compose.yml -p
      aios-m14-03-production-rehearsal --profile local --env-file <pin>
      up -d --no-build`（绝不 `--build`；dry-run 模式附加 compose 原生
      `--dry-run`；不加 `--no-deps`、不加 `--no-recreate`、不隐藏漂移——
      恢复路径整栈 up 口径，与 M14-38 cutover 的最小范围重建分工）。
5. **六服务健康等待**：`compose ps` 轮询至 postgres/redis/minio/api/web/
   livekit 全 healthy（默认 420s）。
6. **本地语音调和（status first）**：经 `tools/voice/voice_service_control.py`
   的只读 inspect 取状态后决策——
   - `unmanaged-running`/`managed-running` 且 `/health` 200 → **不触碰**；
   - `stopped` → 唯一放行动作：经受控 CLI `start --engine <name>`（工具
     自身幂等 + fail-closed 语义不变）；
   - `unknown` / `managed-mismatch`（foreign）/ `port-mismatch` / 既有
     `managed-starting` → **可见失败**：不 spawn、不发信号、不清理 manifest；
   - `unmanaged-running` 非 200 → 不触碰（拒绝误杀边界）+ DEGRADED 退出。

退出码：`0` 完成/无需恢复；`1` 可见失败（含 pin 拒绝、自建镜像缺失
（`minio-local-image-missing`）、健康未达、语音 fail 状态、DEGRADED）；
`2` 参数错误。运行日志落 `artifacts/recovery/`
（gitignored）；Windows 侧子进程恒 `CREATE_NO_WINDOW`。

## 部署 env（pin 事实源）

```powershell
# supervisor 一次性创建（值来自部署事实；文件已 gitignore，绝不提交/回显）
Copy-Item infra\env.production-recovery.example infra\env.production-recovery
# 编辑填入真实值后：
python tools/ops/production_recovery.py --dry-run   # 应见「pin: OK」并给出 up 计划
```

注意：首次 enforce `up -d` 会因新增 `restart: unless-stopped` **一次性重建
六个容器**（compose 对 restart 策略变更的正常反应；之后恢复为 no-op）。该步
骤由 supervisor 在获准窗口执行——编排脚本自身不会在 pin 未就绪时动任何容器。

## windows_startup_task.py（Round 2/3）

隐藏 logon 自愈任务管理器 + 静默执行入口。**install 需提升令牌**：生产实证
（2026-09-11）`schtasks /Create /TN AIOS-Production-Recovery /XML <tmp>` 仅在
elevated token 下成功（非提升被 UAC 拒绝）；在提升的管理终端、主仓库检出上
执行。

```
python tools/ops/windows_startup_task.py dry-run    # 只读预检 + 安装计划（零写操作）
python tools/ops/windows_startup_task.py status     # 只读状态（exit 0/1/2/3/4 = installed/unknown/missing/foreign/malformed）
python tools/ops/windows_startup_task.py install    # 真实安装（提升令牌 + supervisor 审查后；见下）
python tools/ops/windows_startup_task.py uninstall  # 仅删本工具精确拥有的任务
```

语义与安全性质（契约测试 `test_windows_startup_task.py` 锁定；R2.1 + R3 修正）：

- Task：`AIOS-Production-Recovery`；XML 关键项 Hidden=true / LogonTrigger
  (Enabled=true) / Settings/Enabled=true / InteractiveToken+LeastPrivilege
  （当前用户）/ IgnoreNew / StartWhenAvailable / ExecutionTimeLimit=PT2H /
  电池不禁启不停；Action=`wscript.exe //B //Nologo "<repo>\tools\ops\
  run_production_recovery_silent.vbs"`；WorkingDirectory=repo；写入
  `RegistrationInfo/URI=urn:aios:m14-06:production-recovery` +
  `Description=AIOS production recovery (M14-06) …`（持久归属标记）。**XML
  生成时 repo/VBS 路径与 Arguments 经 xml.sax.saxutils.escape 转义**（&、<、
  >、"）——含特殊字符的路径仍产出可解析 XML，解析后字段精确还原。
- **归属判定适配 Task Scheduler 归一化（R3，生产 + COM 快照实证）**：
  schtasks 注册后回读的 XML 与写入不同——URI 被重写为 `\AIOS-Production-
  Recovery`（该形态对**任何**同名任务都会出现，不具区分性）；默认值元素
  （LogonTrigger/Enabled、Settings/Enabled、Principal/RunLevel）被省略；
  调度器自行新增 UserId/IdleSettings 等；Action/Arguments/WorkingDirectory/
  Hidden 与非默认设置逐字保留。exact-owned 判定 = URI 为两种形态之一 **且**
  Description 持久标记精确相等 **且** Command/Arguments/WorkingDirectory 与
  全部安全相关设置逐项精确。省略的默认值元素仅在（a）父节点/触发器类型
  在场、（b）其余必检字段全部精确匹配时按 Windows 默认值认可；显式非默认
  值（Enabled=false / RunLevel=HighestAvailable）仍逐项拒绝。
- **wrapper 调用目标一致（无覆盖面）**：VBS 固定调用
  `<repo>\.venv\Scripts\python.exe`；本工具不提供 `--python` 覆盖——
  preflight/dry-run/install 恒检查 `_repo_paths(repo_root)["venv_python"]`，
  feature worktree 无 `.venv` 即 fail-closed（dry-run/install 均拒绝，
  外部 python 路径无法使其成功）。
- 绝不覆盖同名任务（install 前双重存在性确认，且 install 绝不 /F）；绝不
  Run 子命令。**uninstall 安全删除**：仅当上述 exact-owned 判定全部成立才
  执行 `/Delete /F`（附 /F 是因为 schtasks 无 /F 会交互式确认、capture 管道
  下挂起）；元素缺失即 mismatch（不短路，归一化省略面除外且条件严苛）；
  foreign/missing/malformed/unknown/查询失败/解码失败一律键名-only 拒绝且
  零删除——归一化 URI 单独**绝不**构成归属凭据。
- 存在性判定编码无关（schtasks 错误输出是 OEM 代码页，UTF-16 强解会丢输出
  ——2026-09-11 实证）：先全量列表 `/Query /FO CSV /NH`（任务名 ASCII 跨代码
  页稳定）判存在；存在才取 `/XML` 明细——**原始字节**（`Runner.run_raw`）
  + `decode_schtasks_xml` 按**字节形态**严格解码。/XML 输出编码随捕获通道
  而变（**不宣称单一编码**）：R3 在 PowerShell 管道观测为 UTF-16LE 无 BOM
  （text-mode `encoding='utf-16'` 在读线程抛 UnicodeError 丢输出）；R4 在
  生产机经本工具实际路径（Python `run_raw` 原始捕获）观测为 **ASCII/UTF-8**
  无 BOM（len 1446、`<?xml` 起始、`\n</Task>` 结尾；prolog 仍声明 UTF-16——
  声明与字节可不一致，不作判定依据）。解码器严格接受四形态（UTF-16LE 无
  BOM / LE BOM / BE BOM / UTF-8 `<?xml` 起始），无 BOM 形态要求 prolog 起始；
  四形态之外/解码失败/截断按 unknown fail-closed；XML 解析前拒绝
  DOCTYPE/ENTITY（XXE/实体膨胀加固）。
- pin env 仅存在性检查（install 必需；dry-run 缺失降级为提示——recovery
  自身 fail-closed 兜底），绝不读取/展示值。

静默 wrapper `run_production_recovery_silent.vbs`：仓库根自脚本位置推导（无
盘符硬编码）；`WScript.Shell.Run(..., 0, True)` 隐藏窗口运行
`<repo>\.venv\Scripts\python.exe <repo>\tools\ops\production_recovery.py` 并等
待，退出码经 `WScript.Quit` 透传（Task Scheduler「上次运行结果」可见）；不
弹窗/不开浏览器/不写 secret（日志复用 recovery 的 artifacts/recovery/）。
预检失败专用退出码：2=venv python 缺失、3=recovery 脚本缺失、4=仓库根缺失。

回滚：`python tools/ops/windows_startup_task.py uninstall`（幂等；仅
exact-owned 才 `/Delete /F`，其余状态零删除、无级联）。

## 状态（Round 2 + R2.1 + R3，2026-09-11）

- Round 1 已交付：compose restart 策略、恢复编排 + 契约测试、env 模板/护栏
  （R1.1 修正五键 fail-closed 与占位拒绝）。
- Round 2 已交付：任务管理器 + 静默 wrapper + 契约测试。
- R2.1 修正（supervisor 评审 5 缺陷）：① wrapper/preflight 调用目标一致——
  移除 `--python` 覆盖，恒查 repo 自带 `.venv`；② Task XML 路径/Arguments
  转义；③ WorkingDirectory 缺失即 mismatch；④ 归属校验逐项精确；⑤
  uninstall 仅 exact-owned 才 `/Delete /F`。
- **R3 修正（真实安装回环实证，2026-09-11）**：supervisor 在 canonical 主
  仓库以提升令牌 install 成功（任务 `AIOS-Production-Recovery` 已注册），
  暴露三处真实行为——① `schtasks /Create` 需 elevated token（UAC）；②
  `/Query /XML` 输出编码随捕获通道而变（PowerShell 管道观测 UTF-16LE 无
  BOM，原 text-mode 读取炸读线程）；③ Task Scheduler 归一化存储（URI 重写/
  默认值元素省略/额外元素注入）。
  修复：`Runner.run_raw` 字节面 capture + `decode_schtasks_xml` 鲁棒解码；
  归属判定改为「URI 两形态 + Description 持久标记 + 全字段精确（条件认可
  归一化省略的默认值）」。
- **R4 修正（生产验收 blocker，2026-09-11）**：canonical 5bd3db7 上
  `status` 仍 unknown——生产机经本工具实际路径（`RealRunner.run_raw`
  Python 原始捕获）回读的 /XML 字节为 **ASCII/UTF-8**（len 1446、前缀
  `<?xml version="1`、后缀 `\n</Task>`），与 R3 在 PowerShell 管道观测的
  UTF-16LE 无 BOM 是**两种并存形态**。`decode_schtasks_xml` 改为按字节
  形态严格判定：接受 UTF-16LE 无 BOM / LE BOM / BE BOM / UTF-8 `<?xml`
  起始四形态，拒绝垃圾/截断/未观测形态（fail-closed 不变）。测试 66 项
  （FakeSchtasks，零真实 schtasks 写路径）。
- 边界不变：foreign/malformed/unknown 永不 force、永不删除；本目录工具
  绝不触碰 Docker/8010/8011。

## soak_rehearsal.py（M14-11）

生产 soak/并发彩排 harness：**只读 GET、loopback-only、fail-closed**。
开发/排障默认零网络；真实执行仅由 supervisor 在获准窗口运行。

```
python tools/ops/soak_rehearsal.py                # plan（默认：零网络，打印计划 + plan 报告）
python tools/ops/soak_rehearsal.py --execute \
    --confirm "EXECUTE READ-ONLY LOOPBACK SOAK" \
    --duration-seconds 60 --concurrency 4 --max-requests 300   # execute（五要素齐备才放行）
```

安全性质（契约测试 `services/api/tests/test_soak_rehearsal.py`，60 项锁定；
细节见脚本头注释与 `docs/evidence/m14-11-production-soak/README.md`）：

- **五要素门禁**：`--execute` 旗标 + 精确确认短语（一字不差）+ 有界
  duration（1–120s）+ 有界 concurrency（1–8）+ 总请求上限（1–2000）；
  缺一或任一超硬顶 → EXIT_USAGE 零请求（fail-closed，超顶在 plan 模式
  同样拒绝）。worker 节拍 0.05–5s（聚合速率上限 = concurrency/pace）、
  语音目标最小间隔 1–60s（默认 2s，每语音目标 ≤0.5 rps）。
- **固定目标画像**（不可经 CLI 注入任意 URL）：Web `http://127.0.0.1:3011/`
  与 `/login`、API `http://127.0.0.1:8000/health`；`--include-voice` 才加
  FunASR/CosyVoice 低频 GET `/health`（8010/8011）——绝无音频/推理请求。
- **loopback 纪律**：仅字面 loopback IP（127.0.0.0/8、`::1`）；主机名一律
  拒绝（零 DNS）；query/fragment/userinfo/缺显式端口一律拒绝。
  `http.client` 直连从不读取 proxy 环境变量/系统代理（结构性旁路，
  「恶意假代理零连接」测试实证）；proxy env 仅探测键名存在性。
- **零副作用**：GET-only、无认证、无 cookie/token、无 DB/对象写、无房间/
  会话创建、无 LLM/provider 调用、不跟随重定向、不读响应体。
- **报告**：schema 版本化 JSON + Markdown 落 gitignored
  `.verify/artifacts/m14-11-production-soak/`（plan-* / soak-*）；含
  start/end UTC、限制、每目标计数/status 分布/latency 分位/吞吐、安全
  归类错误（仅类别+异常类名）、停止原因、`completed_after_deadline`
  （deadline 后零新发、在途限于单请求超时、部分结果如实入档）。绝无
  header/body/query/凭据/env 值入档（写前防御性脱敏兜底）。
- 退出码：0 plan 成功 / execute 完成且有成功样本；1 execute 完成但零成功
  样本（栈疑似未起）；2 用法/门禁拒绝。
- 本工具零子进程、零容器面/计划任务命令（源码契约锁定）；绝不触碰
  8010/8011 进程状态（仅当 `--include-voice` 时对其发低频 GET `/health`）。

## production_monitor.py（M14-12）

生产监控 readiness：**只读采集 → 阈值判定 → 证据报告**（监控/告警收口的
第一块可控基础；本里程碑**不接外部告警系统**）。开发/排障默认零采集；
真实采集仅由 supervisor 在获准窗口运行。

状态（回填 2026-09-12）：交付已随 **PR #85** 合并 main（merged_at
2026-09-11T17:34:05Z，merge `52980c6`、feature `80e599d`，本地 git 可
验证，远端 feature 分支已删除）；PR CI run `34627846208` 与合并后 main
push CI run `34628419347` 均全部 5 job（Web/API/Docker/Android/Release
tools）SUCCESS。**supervisor 已于 2026-09-11T17:39:20Z–17:39:21Z 在
canonical main 执行一次真实只读生产监控**（gitignored
`monitor-20260911-173920.json`（14185B）/`.md`（3330B））：compose 六
服务全部 running healthy、restart_count 全 0，五端点全 200（延迟
7.088–27.641ms），逐容器日志 error_total 全 0，`partial=false`，
ok=34/warn=0/critical=0，`overall_status=ok`、`monitoring_ready=true`
——**单次只读快照全绿 ≠ production_ready；`production_ready=false`
不变**（持续/定时采集与调度、外部告警接入、指标历史与留存、阈值随时
间的标定、跨机监控仍未开放）。指标与边界细节见
`docs/evidence/m14-12-production-monitoring/`。

```
python tools/ops/production_monitor.py                # plan（默认：零 subprocess/零网络/零生产读取）
python tools/ops/production_monitor.py --execute \
    --confirm "EXECUTE READ-ONLY PRODUCTION MONITORING"   # execute（旗标+精确短语齐备才放行）
```

**状态（M14-23 restart 增量语义，✅ 2026-09-13 已随 PR #100 合并 main
（merge `6efcdfd`，feature head `5926172` 含 supervisor R1 加固）并经
真实计划任务自然调度两轮验收——18:00 与 18:15（+08:00）均 Last
Result=0、三步全 ok，**37 连 warn 在新代码首轮按设计恢复 ok（api 累计
restart_count=1 不变、同容器实例 → 当轮增量 0、基线取自 17:45 旧 v1
工件）并在第二轮保持**；证据
`docs/evidence/m14-23-restart-delta-production-acceptance/`；两轮验证恢复
语义与短期稳定性，不构成 production readiness 宣称）**：
`container-restarts` 阈值判定由静态累计 RestartCount 改为**当轮新增增量**
（当前累计 − 基线累计）。基线 = 本轮工件目录内**最新合法的 prior 完整**
monitor JSON 工件（R1 加固后的合法身份 = schema/tool/**milestone**/mode
四件套 + `partial` 恒布尔 `False` + `started_at_utc` 为 canonical 形态
（`%Y-%m-%dT%H:%M:%SZ` 且日历合法——畸形/缺失值绝不复制进
baseline.collected_at）+ 六容器事实齐全）；解析纯本地只读 fail-safe：零
shell=True/零网络/零写盘/零额外生产读取；**symlink 防御（R1）**——
symlinked 工件目录/祖先 → `missing/artifact-dir-unreadable` 绝不跟随，
symlinked 候选文件计 `invalid_skipped` 而不跟随；非法候选显式计入
`invalid_skipped_count`，绝不静默当作零基线。语义：同容器实例
（started_at 一致）且增量 0 → ok——健康栈不再因历史存量（如 api
restart_count=1，即 M14-22 实证的 17 连 warn 形态）永久 warn；增量达
warn/critical（默认 ≥1/≥5，含边界）→ 可见告警；`started_at` 变化 = 容器
重建、累计下降 = 计数重置 → 各发**一轮可见 warn**（负增量绝不静默映射
为零），下一稳定轮恢复 ok；无基线时 count 0 → ok、达阈值 → 一次性
baseline-missing 可见告警（下一轮以本轮工件为基线即恢复）。累计
`restart_count` 采集事实照实入档不变；报告加法字段
`threshold_results.restart_evaluation`（baseline 元数据 + 逐服务
state/reason/delta，reason 固定词汇 stable/delta/baseline-missing/
container-recreated/counter-reset/facts-missing）；check_id 仍为
`container-restarts`；schema 向后兼容——v1 旧工件（无该字段）照常作
基线与入档。

**状态（M14-27 监控语音健康来源切换契约，2026-09-15，基于 PR #106
merge `ec60a093`）**：monitor 新增 `--voice-health-source {loopback,
sidecar}` sidecar 来源——仅读 canonical M14-26 manifest（严格校验：
symlink/非常规文件拒绝、64KiB 大小双检（stat 预检 + 读后复检）、
schema/service/正整数 PID/固定 18010/18011 端口/RFC1918 字面 IPv4 bind），
语音健康 URL 恒派生为 `http://<bind>:18010/health` 与 `:18011/health`，
任何 manifest 失败在 Runner/Transport/报告之前 fail-closed 退出、**零
回退**；web/api 端点仍为固定字面 loopback。M14-27 契约测试 35 passed、
监控家族六套件最终回归 635 passed（聚焦三套件过程口径 332 passed）。**警告：默认 `loopback` 保持直连 8010/8011
的兼容行为；pipeline 的 sidecar 模式要求存在合法的 M14-26 manifest，
否则 fail-closed 拒绝；真实生产切换尚未执行（留 M14-28 受控验收），
`production_ready=false` 不变**。

安全性质（契约测试 `services/api/tests/test_production_monitor.py` 锁定；
细节见脚本头注释与 `docs/evidence/m14-12-production-monitoring/README.md`）：

- **双模式门禁**：默认 plan 零副作用（socket/subprocess 双阻断下照常出
  计划与 plan 报告，且 plan 报告不出现任何状态宣称）；execute 需
  `--execute` 旗标 + 精确确认短语 `EXECUTE READ-ONLY PRODUCTION
  MONITORING`（一字不差）+ 全部阈值在硬顶内——缺一即 EXIT 2 零采集。
  R1 起：非有限浮点（nan/inf/-inf）与非法 `--project`（严格白名单：
  ASCII 字母数字开头、仅字母数字/连字符/下划线、≤64；被拒值不回显）
  同样在 plan 报告写入/采集之前拒绝。
- **只读采集面**（固定画像）：compose project
  `aios-m14-03-production-rehearsal`（--profile local）——compose ps
  （六受管服务 health/state）、六容器 docker inspect（state/health/
  RestartCount/image/started）、五默认端点 GET（Web 3011 `/`+`/login`、
  API 8000 `/health`、FunASR 8010/CosyVoice 8011 `/health`）状态+延迟、
  容器日志安全错误摘要（`docker logs --tail`——只记匹配计数/级别/安全
  类别，**原文绝不持久化**）。
- **子进程白名单门（结构性）**：一切 docker 命令经 `ReadonlyRunner` 的
  `is_readonly_docker_command` 校验——仅 compose ps / inspect --format /
  logs --tail 三形态放行，stop/rm/kill/restart/down/exec/up/logs -f 等
  一律在任何执行之前拒绝；Windows 侧恒 CREATE_NO_WINDOW。
- **loopback 纪律**（与 soak 同款）：五端点固定画像仅字面 loopback IP；
  `http.client` 直连零代理面（proxy env 仅键名存在性入注记）；GET-only、
  无认证/cookie/token、不读响应体、带超时。
- **部分失败如实入档**：任一采集器失败 → `partial=true` + 安全类别
  （仅类别+异常类名）→ `overall_status=incomplete`；**缺失绝不当作
  healthy**（fail-closed：failed 项恒为可见 critical alert）。
- **阈值**（全部含边界，warn 恒可见）：compose 6/6 healthy、五端点恒
  200、容器 health/state、RestartCount（默认 warn≥1/critical≥5）、日志
  错误计数（默认 warn≥5/critical≥20）、端点延迟（默认 warn≥1000ms/
  critical≥5000ms）；CLI 可调但受硬顶 fail-closed。
- **报告**：schema 版本化 JSON + Markdown **原子写**（同目录 tmp +
  os.replace；拒绝 symlink 组件/越界 stem）；**默认落 gitignored
  `.verify/artifacts/m14-12-production-monitoring/`，`--artifact-dir`
  自定义路径为操作者显式自选覆盖——其位置与 gitignore 状态由操作者
  负责**（报告边界注记/CLI help/运行时注记同口径）；含 UTC 时间、配置、
  边界注记、collector 状态、阈值结果；`monitoring_ready` 仅采集完整且
  零 warn/critical 时 true——**≠ production ready，本工具绝不宣称生产
  就绪**；本里程碑零外部告警发送。
- 退出码：0 plan 成功 / execute 完整采集且 ok|warn（warn 恒可见不隐藏）；
  2 非法/fail-closed（含确认缺失、阈值超顶、非有限浮点、非法项目名、
  symlink/越界路径）或采集 incomplete 或存在 critical（含证据报告写入
  失败——证据不可失）。

## monitoring_history.py（M14-13）

监控历史索引：M14-12 monitor JSON 工件 → **有界留存 history.jsonl + 趋势
摘要**（只读源工件，绝不改动/删除任何 M14-12 原始产物）。零子进程/零
网络/零容器面/零计划任务/零 env 读取/零墙钟。

状态（回填 2026-09-12）：交付已随 **PR #87** 合并 main（merged_at
2026-09-12T08:13:50Z，merge `d2ffc49b27708912…c7e2`、feature head
`b1201d6d041ae3ea…7b02`，本地 git 可验证，远端 feature 分支已删除）；
PR CI run `34682507203` 与合并后 main push CI run `34682734884` 均全部
5 job（Web/API/Docker/Android/Release tools）SUCCESS。**首次真实历史
构建已由结果回填回合从 canonical main `d2ffc49` 独立执行（`py -X utf8`
连跑两遍）**：canonical 源 `monitor-20260911-173920.json`（SHA-256
`740c031e…4a458`，运行前后不变）→ gitignored `history.jsonl`（SHA-256
`a71ff7e2…ac5ae0`）+ `history-summary.md`（SHA-256 `80535c3a…1a7be`），
两遍输出逐字节相同且与 supervisor 期望值一致；恰 1 行
`overall_status=ok`/`partial=false`/哈希链匹配，ok=34/warn=0/critical=0、
六服务 healthy+restart 0、五端点全 200、日志 error 全 0——**单样本全绿
≠ production_ready；`production_ready=false` 不变**（持续/定时采集与
调度、外部告警接入、指标时序存储/查询、阈值随时间的标定、跨机监控仍未
开放）。指标与边界细节见
`docs/evidence/m14-13-monitoring-history/`。

状态（M14-20 修复，2026-09-13）：**历史 incomplete 工件跳过类**——M14-14
管道进入真实定时运行后，默认源目录持续混有 2026-09-12 时期（栈修复期间）
monitor 自产的 `partial=true + overall_status=incomplete` 工件，旧「任何
incomplete 一律整体拒绝」语义令 history 步骤永久 EXIT 2、监控历史零索引。
修复 = 识别该**窄类**（完整 monitor 身份 + incomplete/partial 共现对——
monitor 契约中二者恒共现，采集器事实可合法含 failed，无法经完整校验）→
**整件跳过不入档**（`skipped_incomplete_count` 显式于摘要与 stdout；源文件
未改动）；完整 ok/warn/critical 样本照常严格校验入档；其余任何
incomplete/partial 形态（身份不符、incomplete+partial=false、完整状态+
partial=true 等矛盾组合）仍 fail-closed；候选全为 incomplete →
`no-complete-sources` 拒绝。真实 canonical 源目录实证（39 工件 = 31 历史
incomplete + 8 完整）：exit 0、8 条完整记录（1 ok + 7 warn）入档、跳过 31
计数显式、源文件逐字节不变、两遍输出逐字节相同。细节见
`docs/evidence/m14-20-monitoring-history-historical-incomplete/`。

```
python tools/ops/monitoring_history.py                 # 默认源/输出目录
python tools/ops/monitoring_history.py --retention 200
```

**状态（M14-23，✅ 2026-09-13 已随 PR #100 合并 main 并经真实计划任务
两轮验收——18:15 刷新后 history 40 样本（ok=3/warn=37），最新行携带
restart_evaluation（api state ok / reason stable / delta 0，与累计
restart_counts=1 同档并存）；证据
`docs/evidence/m14-23-restart-delta-production-acceptance/`）**：入档
monitor 加法字段 `threshold_results.restart_evaluation`——
缺省 = v1 旧工件（合法入档，记录不带新键，旧记录/旧消费者零破坏）；在场
即严格校验（固定词汇 state/reason/baseline status、六服务全集、delta 为
int≥0 或 None、baseline_source_stem stem 白名单、baseline_collected_at
时间戳格式；**R1 baseline 元数据一致性**——status=ok 恒 reason=None +
白名单 stem + canonical collected_at，status=missing 恒固定词汇 reason
{artifact-dir-missing/artifact-dir-unreadable/no-prior-artifacts/
baseline-not-resolved} + 空元数据，status=unusable 恒
no-usable-prior-artifacts + 空元数据；任何违规 `restart-evaluation`
fail-closed 输出零写入）。记录累计 `restart_counts` 口径不变，另存规范化
`restart_evaluation`（baseline 元数据 + states/reasons/deltas——重建/重置
事件清晰留痕，不可比增量恒 None）；摘要新增 `restart_delta_totals` /
`restart_event_totals` / `restart_delta_sample_count`（诚实区分「无增量
数据」与「测得为零」）与 Markdown 增量评估行，**累计 restart_totals 口径
不重定义**。

安全性质（契约测试 `services/api/tests/test_monitoring_history.py` 锁定；
细节见 `docs/evidence/m14-13-monitoring-history/README.md`）：

- **输入面（固定画像）**：默认 gitignored
  `.verify/artifacts/m14-12-production-monitoring/`（`--source-dir` 可覆
  盖，只读）。仅发现 `monitor-*.json`；stem 严格白名单
  `monitor-YYYYMMDD-HHMMSS`（含日历合法性）——glob 命中但 stem 不合规
  fail-closed 且被拒名不回显；plan-*、md、无关文件忽略。
- **严格校验（fail-closed，输出零写入）**：schema_version/tool/
  milestone/mode=execute/project 白名单/双 UTC 时间戳格式与顺序/
  overall_status∈{ok,warn,critical}（incomplete 拒绝）/partial 恒
  false/阈值计数/六 compose 服务/六容器事实（RestartCount）/五端点
  状态+延迟（有限数值）/六日志 error_total；not-json 同拒。**唯一例外
  （M14-20）**：识别 M14-12 monitor 自产的历史 incomplete 工件类（完整
  monitor 身份 + incomplete/partial=true 共现对）→ 整件跳过不入档
  （计数显式，源文件未改动）；其余任何 incomplete/partial 形态仍
  fail-closed；候选全为 incomplete → no-complete-sources 拒绝。
- **去重/排序/留存**：逐文件 SHA-256；同哈希去重（保留 (collected_at,
  stem) 最小者，duplicate_count 显式）；同 (project, collected_at) 不同
  哈希 = conflicting-duplicate 拒绝；唯一样本按 (collected_at, stem)
  确定性排序；保留最新 N 条（默认 500、硬顶 5000、下限 1，CLI 超界拒
  绝），显式 omitted_older_count 与 oldest/newest 保留边界；**源文件
  永不改动/删除**。
- **输出（默认 gitignored
  `.verify/artifacts/m14-13-monitoring-history/`，`--output-dir` 为操作
  者显式自选）**：`history.jsonl`（每行一条紧凑记录：artifact_sha256/
  source_stem/collected_at/project/overall_status/partial/
  threshold_counts/compose 服务 health/restart 计数/端点状态+延迟/日志
  error 总计——**绝无原始日志行/密钥/secret**）+ `history-summary.md`
  （状态计数/availability/degraded/critical、first/last、逐端点延迟
  min/p50/p95/max（nearest-rank）、逐服务 restart/日志 error 总计、
  duplicate/omitted 计数、留存边界）。**生成时间戳取自最新源样本
  collected_at——零墙钟，输出逐字节可复现**；两文件同目录 tmp+fsync+
  os.replace 原子落盘（失败清理 tmp）；仅在全部输入校验通过后才写。
- 路径防御：源文件/源目录/输出路径/输出祖先的 symlink 一律拒绝；
  退出码 0 成功 / 2 任何拒绝（含零源、零完整样本、源目录缺失、写失败）。

## monitoring_pipeline.py（M14-14 / M14-21）

持续/定时监控采集管道 readiness：**单次组合** M14-12 monitor → M14-13
history → M14-15 insights（history 仅在 monitor exit 0 后运行；insights
仅在 history status=ok 后运行，输入恒为 history canonical 输出目录——
M14-21 起默认源常量三方 resolve 全等，单一事实源；任何失败/跳过固定词汇
入档，前置步骤事实绝不遮蔽）。开发/排障默认零执行；真实执行仅由
supervisor 在获准窗口运行。

**状态（M14-22 真实调度验收，2026-09-13，含 R1 修正）**：计划任务
`AIOS-Monitoring-Pipeline` 在 PR #98 合并后**两轮连续真实调度成功**
（12:30 与 12:45（+08:00），均 Last Result=0）——两轮三步全 ok、exit 0
（第一轮 duration 1.756/3.055/0.432s、第二轮 1.27/0.13/0.143s），lock
正常获取释放，**insights 产物首次由真实计划任务产出（12:30）并在第二
轮（12:45）刷新**；监控家族（M14-14/M14-20/M14-21）自此经真实调度端到
端验收——两轮成功证明重复调度执行，不构成长期稳定性证明。两轮 monitor
步 `overall_status=warn`——6/6 服务 healthy、5/5 端点 200，**唯一告警 =
api 容器静态累计 restart_count=1 达 restart_warn=1**（两轮同一静态累计
值；阈值语义问题而非栈故障；语义修复建议 M14-23——**已由 M14-23 交付
并闭环：PR #100 合并后 2026-09-13 18:00/18:15 两轮自然调度均 Last
Result=0、三步全 ok（monitor 4.068/4.947s、history 0.364/0.31s、
insights 0.282/0.201s），37 连 warn 首轮恢复 ok 并保持，证据
`docs/evidence/m14-23-restart-delta-production-acceptance/README.md`**）。
事实与工件安全
摘要见
`docs/evidence/m14-22-monitoring-pipeline-production-acceptance/README.md`。

**状态（M14-27 monitor argv 固定 sidecar，2026-09-15，基于 PR #106
merge `ec60a093`）**：管道固定命令白名单中的 monitor 精确形态固定追加
`--voice-health-source sidecar`——语音健康采集走 M14-26 sidecar 旁路，
要求存在合法的 canonical manifest，否则 monitor 在 Runner 之前
fail-closed 零回退（校验细节见上方 production_monitor 的 M14-27 状态）。
M14-27 契约测试 35 passed、监控家族六套件最终回归 635 passed（聚焦三套件过程口径 332 passed）。**警告：该切换
仅为代码/契约口径——默认 `loopback` 兼容直连路径不受影响，真实 sidecar
启动与生产监控切换尚未执行（留 M14-28 受控验收），`production_ready=
false` 不变**。

```
python tools/ops/monitoring_pipeline.py                        # plan（默认，零执行）
python tools/ops/monitoring_pipeline.py --execute \
    --confirm "EXECUTE READ-ONLY MONITORING PIPELINE"          # execute（单次组合）
```

安全性质（契约测试 `services/api/tests/test_monitoring_pipeline.py` 锁定；
细节见脚本头注释与 `docs/evidence/m14-14-monitoring-pipeline/README.md`、
`docs/evidence/m14-21-monitoring-insights-pipeline/README.md`）：

- **双模式门禁**：默认 plan 完全惰性（零 subprocess/零网络/零生产读取/
  零调度器改动，Runner 零构造——计数工厂测试锁定）；execute 需
  `--execute` + 精确确认短语 `EXECUTE READ-ONLY MONITORING PIPELINE`
  （一字不差），缺一/近似即 EXIT 2 且零 Runner 构造/调用；三步超时
  非有限浮点/超硬顶同样拒绝（plan 同样校验）。
- **固定命令白名单门（结构性）**：仅三个精确固定形态——
  `<python> production_monitor.py --execute --confirm "EXECUTE READ-ONLY
  PRODUCTION MONITORING"`（与 monitor 自身短语逐字一致，回归测试锁定）、
  `<python> monitoring_history.py`（全默认参数）与
  `<python> monitoring_insights.py --execute --confirm "EXECUTE READ-ONLY
  MONITORING INSIGHTS"`（与 insights 自身短语逐字一致；**恒不带
  --source**——输入恒为其默认源 = history canonical 输出目录）；任何其它
  argv（含 --source/--event-limit 注入）在执行之前拒绝；无 shell=True、
  无用户可注入命令/URL/env 展开；子进程输出只取 returncode，stdout/stderr
  绝不持久化/回显。
- **超时预算**：monitor 60–540s（默认 480s，覆盖 monitor 内部最坏 ~445s）、
  history 10–120s（默认 45s）、insights 5–50s（默认 15s，纯本地只读工件
  处理秒级完成即兜底杀停）；三步硬顶之和 540+120+50=710s < 计划任务执行
  时限 PT12M=720s < 重复间隔 PT15M——调度器绝不先于内部超时杀整任务。
- **重叠保护**：gitignored 工件目录内 `pipeline.lock`（O_CREAT|O_EXCL）；
  已存在即可见拒绝零执行；**本轮零 stale-lock 清理**（陈旧锁操作者人工
  处置）；锁体仅安全事实；symlink 全路径拒绝。
- **报告**：schema v1 JSON+MD 原子写（tmp+fsync+os.replace），仅安全事实
  （状态/退出码/时长/固定命令身份（无绝对本机路径）/脱敏错误类别类名/
  产物名+SHA-256+字节数（差集发现、每步 ≤8 个 hash、超界记数；history
  与 insights 各两固定名））；
  报告写入失败 = 证据不可失 → EXIT 2。
- 退出码：0 plan 成功 / execute 三步全 ok；1 execute 已执行但有可见失败；
 2 门禁/数值/锁/路径/报告写入拒绝。

## monitoring_pipeline_task.py + run_monitoring_pipeline_silent.vbs（M14-14）

隐藏周期计划任务 readiness 管理器 + 静默执行入口（把上面的管道按固定间隔
挂到 Task Scheduler）。**install 需提升令牌（M14-06 生产实证）+ 精确确认
短语；实际注册 supervisor-only——开发回合零安装/零卸载/零注册。**

```
python tools/ops/monitoring_pipeline_task.py plan      # 只读预检 + 注册计划（零写）
python tools/ops/monitoring_pipeline_task.py generate  # 导出任务 XML（UTF-16 with BOM，外部解析器可直接加载；零调度器改动）
python tools/ops/monitoring_pipeline_task.py status    # 只读五态（0/1/2/3/4）
python tools/ops/monitoring_pipeline_task.py install   --confirm "EXECUTE MONITORING SCHEDULER CHANGE"
python tools/ops/monitoring_pipeline_task.py uninstall --confirm "EXECUTE MONITORING SCHEDULER CHANGE"
```

安全性质（契约测试 `services/api/tests/test_monitoring_pipeline_task.py`
79 项锁定，含 VBS wrapper 契约、M14-06 任务身份不冲突 pin 与 generate
产物 UTF-16 BOM 字节级回归——supervisor R2 修正：旧实现以 UTF-8 写出声明
UTF-16 的 XML 致 System.Xml 拒载，现与声明及 install 临时字节一致）：

- 固定任务身份 `AIOS-Monitoring-Pipeline` /
  `urn:aios:m14-14:monitoring-pipeline` + Description 持久归属标记；保守
  重复间隔 **PT15M**（无 Duration=无限期，每小时 4 次只读采集）+
  ExecutionTimeLimit PT12M + IgnoreNew + Hidden + InteractiveToken/
  LeastPrivilege + 电池不禁启不停；Action=
  `wscript.exe //B //Nologo "<repo>\tools\ops\run_monitoring_pipeline_silent.vbs"`；
  WorkingDirectory=repo。与 M14-06 `AIOS-Production-Recovery` 零身份冲突。
- **结构性 schtasks 白名单门**：读路径恒零 mutation（仅两查询形态放行，
  /Run//Change//End 一律拒绝）；mutation 仅放行 `/Create /TN <固定名> /XML
  <单个 .xml>` 与 `/Delete /TN <固定名> /F` 两精确形态（create 绝不 /F；/XML 后唯一 token 是值位置——POSIX tempfile 绝对路径放行，旗标形态仍拒）。
  install/uninstall 各自需精确短语 `EXECUTE MONITORING SCHEDULER CHANGE`，
  缺一即零 schtasks 调用。
- 绝不覆盖同名任务（双重存在性确认）；uninstall 仅 exact-owned 才
  `/Delete /F`；归属判定适配 M14-06 实证归一化（URI 两形态 + Description
  逐字 + 全字段精确 + 条件认可省略的默认值）；/XML 按字节形态严格解码
  四形态；DOCTYPE/ENTITY 解析前拒绝（XXE 加固）；XML 生成侧路径转义。
  **诚实边界（M14-22 已实证注册态）**：注册后归一化已经真实安装实证
  ——canonical `status` 返回 installed 且 Action/参数/cwd/Hidden/触发器/
  间隔/时限逐项匹配（2026-09-13 验收（含 R1 修正）：Last Run 12:30:01
  与 12:45:01（+08:00）两轮均 Last Result=0、下一轮 13:00；见
  `docs/evidence/m14-22-monitoring-pipeline-production-acceptance/`）。
- 静默 VBS：仓库根自脚本位置推导（无盘符硬编码）；隐藏窗口
  `Run(..., 0, True)` + 退出码透传；调用目标恒 repo 自带
  `.venv\Scripts\python.exe`（无覆盖面，preflight 恒查）；携带
  `--execute --confirm "EXECUTE READ-ONLY MONITORING PIPELINE"`（公开门禁
  常量，非 secret）。
- **readiness ≠ 持续运行证明——持续运行已由 M14-22 真实调度首轮验收
  （installed + 首轮 12:30 三步全 ok + 首轮 insights 产出），但单机单轮
  验收不扩大为生产就绪；`production_ready=false` 不变。**

## monitoring_insights.py（M14-15）

监控历史洞察/告警摘要第 1 切片：M14-13 `history.jsonl`（或 M14-12
monitor 工件目录）→ **安全 JSON+MD 洞察摘要**（只读输入，绝不改动/删除
任何输入工件）。零子进程/零网络/零容器面/零计划任务/零 env 读取/零墙钟；
**仅本地工件洞察——不接外部告警系统，不构成 production readiness 宣称**。

**状态（M14-22 验收，2026-09-13，含 R1 修正）**：insights 产物首次由
真实计划任务（管道 insights 步）产出（12:30）并在第二轮（12:45）刷新，
落 canonical 输出目录（insights.json / insights-summary.md，两轮 SHA-256
与字节数见
`docs/evidence/m14-22-monitoring-pipeline-production-acceptance/README.md`）；
当前历史（12:45 刷新后）18 样本 ok=1/warn=17/critical=0、warn 连续
17 条——唯一告警为 api 容器 restart_count=1 静态阈值（服务全 healthy、
端点全 200），告警语义修复建议 M14-23——**已闭环：M14-23 合并后
（PR #100）18:00/18:15 两轮自然调度，warn 连（终值 37）首轮恢复 ok、
insights 刷新至 40 样本 ok=3 / 当前 ok 连胜 ×2（见下方 M14-23 状态与
`docs/evidence/m14-23-restart-delta-production-acceptance/`）**。

```
python tools/ops/monitoring_insights.py                 # plan（默认，零读取/零写入）
python tools/ops/monitoring_insights.py --execute \
    --confirm "EXECUTE READ-ONLY MONITORING INSIGHTS"   # execute（只读洞察）
```

**状态（M14-23，✅ 2026-09-13 已随 PR #100 合并 main 并经真实计划任务
两轮验收——18:15 刷新后 insights 40 样本 ok=3、当前 ok 连胜 ×2、最长
non-ok 连败 37 恰终止于旧语义最后一轮（09:45:02Z）、恢复转移恰 1 次
@ 10:00:02Z，api 增量/事件/恢复计数全 0（累计 restart_total=39 与增量
口径并存）；证据
`docs/evidence/m14-23-restart-delta-production-acceptance/`）**：行级可选
加法字段 `restart_evaluation`（缺省 = 旧 history 行，照常可读——增量键
在场为 0，诚实区分「无数据」与「测得为零」；在场即严格校验，词汇/形态
与 monitoring_history 单一事实源一致——**含 R1 baseline 元数据一致性
联动校验**）。`service_summary` 逐服务区分**累计**
`restart_total`（容器累计 RestartCount 求和，口径不变）与当轮
`restart_delta_total`（增量求和，不可比 None 不计）/
`restart_delta_samples` / `restart_event_count`（当轮增量评估非 ok 的
样本数——增量告警/重建/重置/baseline-missing）/
`restart_recovery_count`（事件→非事件转移数；legacy 行后转移不可证
不计）；Markdown 服务表分列渲染（总计（累计）/增量/事件/恢复）+ 口径
注记。不构成 production readiness 宣称。

安全性质（契约测试 `services/api/tests/test_monitoring_insights.py` 锁定；
细节见 `docs/evidence/m14-15-monitoring-insights/README.md`）：

- **双模式门禁**：默认 plan 完全惰性（零读取/零写入/零 Store 构造）；
  execute 需 `--execute` + 精确确认短语 `EXECUTE READ-ONLY MONITORING
  INSIGHTS`（一字不差），缺一/近似即 EXIT 2 且零读取；`--event-limit`
  （默认 50、1–500）超界 plan/execute 同样拒绝。
- **输入三形态（固定画像）**：`--source` 可为 ① `history.jsonl` 文件本体；
  ② 含它的目录（M14-21 起默认 = 同仓 `monitoring_history.py` 的 canonical
  输出目录 `.verify/artifacts/m14-13-monitoring-history/`，gitignored，
  **常量直接引用非平行定义**——持续管道 monitor → history → insights
  持续更新，本目录恒有写入者）；③ M14-12 monitor
  工件目录（发现/严格校验/去重/排序**委托同仓 M14-13
  `monitoring_history.py` 已测函数**——schema 单一事实源，拒绝原因固定
  词汇透传）。同目录混入两形态 → mixed-inputs 拒绝；空目录/缺源/非
  history.jsonl 文件名拒绝；symlink（源文件/源目录/输出/祖先）一律拒绝。
- **严格校验（fail-closed，输出零写入）**：history 行必须为 M14-13 记录
  schema（schema_version/artifact_sha256 64 位十六进制/stem 白名单/UTC
  时间戳/project 白名单/overall_status（incomplete 拒绝）/partial 恒
  false/阈值计数/六服务 health+restart/五端点 http_status+**有限非负**
  延迟/六日志 error_total）；行序严格递增 (collected_at, source_stem)——
  **时间乱序/重复行拒绝**；跨 project 混档拒绝；样本数 1–5000（超界
  拒绝，绝不静默截断）。
- **洞察最小集**：时间范围+样本数+时长、overall_status 计数、
  availability（计数+比率）、degraded/critical 事件列表（有界，超界截断
  计数显式、保最新 N 条）、逐端点延迟 min/p50/p95/max（nearest-rank）
  +非 200 计数、逐服务 restart/日志 error 总计+非 healthy 样本数、连续
  失败/恢复（当前连胜/最长 non-ok 连败区间/失败恢复转移计数+时间戳/
  逐端点当前连续失败）、最近样本状态。
- **输出（默认 gitignored
  `.verify/artifacts/m14-15-monitoring-insights/`，`--output-dir` 为操作者
  显式自选）**：`insights.json` + `insights-summary.md`；同目录 tmp+fsync+
  os.replace 原子落盘；仅在全部输入校验通过后才写。生成时间戳取自最新
  样本 collected_at——**零墙钟，两次运行逐字节相同**。报告卫生：
  stdout/输出**绝无绝对本机路径**（路径显示恒为仓库相对或纯名）、原始
  日志行、密钥/secret、生产容器 ID；被拒值不回显。
- 退出码：0 plan 成功 / execute 成功；2 任何拒绝（门禁、参数超界、源
  缺失/symlink、零样本、malformed、乱序、混档、超 5000、写失败）。

## minio_volume_adoption.py（M14-41）

MinIO 生产卷属主采纳（root → uid 1000）：plan（计划）/ execute（备份 +
chown + 重建）/ rollback（恢复）三阶段，各阶段产出 JSON+MD 报告。契约
测试 `services/api/tests/test_minio_volume_adoption.py`；细节见脚本头
注释与 `docs/evidence/m14-41-minio-volume-adoption/README.md`。

```
python tools/ops/minio_volume_adoption.py plan
python tools/ops/minio_volume_adoption.py execute --confirm "EXECUTE MINIO VOLUME ADOPTION"
python tools/ops/minio_volume_adoption.py rollback --backup-file <execute-generated-tar> --confirm "EXECUTE MINIO VOLUME ROLLBACK"
```

- **属主迁移**：root → uid 1000（MinIO 运行用户），方向固定。
- **备份强制**：chown 前必做 tar + SHA-256 + manifest（记录旧镜像 ID 与
  迁移前属主）；rollback 先校验备份工件与镜像 ID（不符即拒绝）。
- **变更面最小**：仅 MinIO 容器被变更，其余五容器只读基线采集。
- **一次性 helper 容器**：`--user 0:0` + 最小化挂载，停止容器前有
  compose/env 元数据门禁（--env-file + --profile local）。
- **开发零 Docker/零生产；生产采纳已闭环（2026-09-17）**：工具已随 PR #118
  合并 main `5bc74c4`（合并前跨平台测试修正，PR CI run `35172658746` 五 job
  全部通过；本地 Windows 卷+镜像组合测试 233 passed）；合并后 main 真实
  `plan` exit 0；真实 `execute` stamp `20260917-020446` 判定 pass——root
  普查迁移 uid 1000、备份 tar（57344 bytes，SHA-256 入档）与
  tar/.sha256/.manifest/report 工件齐备、MinIO 以容器 `8e4f3d855ffb` 重建
  （本地 pin 镜像 `sha256:0f1c79afdb0b5fcdd49e385c46bca065f97cdd89917522593b84436a2e61bcd6`）
  healthy、其余五容器 ID 不变且 healthy；采纳后 M14-40 preflight stamp
  `20260917-020606` 全部检查通过（采纳边界解除）、`production_recovery
  --dry-run` exit OK（pin 9/9、六服务 healthy、健康栈跳过 up、FunASR/CosyVoice
  managed-running health 200 且未触碰）。rollback 未在真实生产演练（仅契约
  测试覆盖）；execute pass 与 preflight pass 不等于 production_ready，
  `production_ready=false` 不变（证据
  `docs/evidence/m14-41-minio-volume-adoption/README.md`）。

## minio_image_adoption.py（M14-40）

MinIO 本地镜像采纳预检：**build / smoke / preflight 三模式**，各模式
默认 plan（零执行，只出计划与报告）；真实执行需 `--execute` + 模式
专属精确确认短语（一字不差，缺一或近似即拒绝且零副作用）。本回合
交付工具与契约测试；真实代理构建、一次性冒烟、真实只读 preflight
待 supervisor 在获准窗口执行——2026-09-16T12:51:06Z 无代理直接构建
已真实尝试，468.7s 后于 Go module 拉取阶段失败（proxy.golang.org
connection refused，docker build rc=1，如实入档 gitignored
`.verify/artifacts/m14-40-minio-image-adoption/build-20260916-125106.json/.md`）。

```
python tools/ops/minio_image_adoption.py build     # plan（默认，零执行）
python tools/ops/minio_image_adoption.py build --execute \
    --confirm "EXECUTE MINIO IMAGE BUILD"          # 真实构建（supervisor）
python tools/ops/minio_image_adoption.py smoke --execute \
    --confirm "EXECUTE MINIO IMAGE SMOKE"          # 一次性冒烟（supervisor）
python tools/ops/minio_image_adoption.py preflight --execute \
    --confirm "EXECUTE MINIO PREFLIGHT"            # 只读生产盘点（supervisor）
```

安全性质（契约测试 `services/api/tests/test_minio_image_adoption.py`
120 项锁定；细节见脚本头注释与
`docs/evidence/m14-40-minio-local-image/README.md`）：

- **build**：只构建 compose 锚定的
  `aios/minio:RELEASE.2025-10-15T17-29-55Z`（pin commit
  `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`，代码四处交叉锁定并与
  compose `image:` 锚点契约测试互锁）——恰好一次 `docker build`
  （infra/minio），零 compose 项目操作。可选
  `AIOS_MINIO_BUILD_HTTPS_PROXY`（socks5h/socks5/http/https +
  host[:port]，端口语义校验 1–65535）只向构建传 Docker 预定义
  `HTTPS_PROXY` build arg——零 GOPROXY/Dockerfile/pin 改动。
- **smoke**：严格 `aios-m14-40-` 前缀生成式一次性容器/卷名（忙端口/
  名字冲突在任何副作用之前 fail-closed）；loopback 19000/19001；核查
  cluster 健康、uid 1000 数据探针与版本后 finally 精确清理恰两名
  ——**清理失败即冒烟整体失败**；env 只用仓库公开 compose dev 占位值，
  绝不读取 env secret（含 `infra/env.production-recovery`）。
- **preflight（只读生产盘点 + 采纳判定）**：`--project` 仅
  `aios-m14-03-production-rehearsal`；只读 docker version / image
  inspect / inspect / volume inspect（镜像元数据与运行时 uid/版本、
  compose/Dockerfile 锚点、六容器健康、生产卷 driver/size/递归 UID
  普查）；生产卷探针恒 `:ro`（挂 /probe）挂入 `--network none`
  一次性 `--rm` helper 容器逐次移除，**绝不写生产卷**。采纳判定
  fail-closed：镜像缺失、user/entrypoint/version 不符、卷属主非
  uid 1000 **或普查不确定**、栈非全健康 → adoption=blocked；
  **adoption=pass ≠ production readiness**（`production_ready=false`
  不变；chown 迁移 + `up -d --no-build` 采纳是后续受控切片）。
- **argv 结构白名单**：每个 docker argv 必须匹配固定结构形态（常量
  镜像引用/派生生产名/生成式一次性名），任何偏离在执行之前拒绝；
  零 stop/rm/restart/exec、零 `docker compose`、零 pull、零 env
  secret 读取；报告 schema v1 JSON+Markdown 落 gitignored 默认工件
  目录（`--artifact-dir` 为操作者显式自选，其位置与 gitignore 状态
  由操作者负责）。
- 退出码统一：`0` 成功；`2` 一切失败（fail-closed，门禁/校验拒绝与
  真实执行失败同码，绝不静默降级）。
