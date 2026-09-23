# tools/ops —— 生产恢复编排（M14-06）+ soak/并发彩排 harness（M14-11）+ 生产监控 readiness（M14-12）+ 监控历史（M14-13）+ 监控管道/调度 readiness（M14-14）+ 监控历史洞察（M14-15）+ MinIO 镜像采纳预检（M14-40）+ MinIO 卷属主采纳（M14-41）+ 审计锚点 WORM 归档（M14-43/M14-44）+ 审计锚点 WORM 离线第二副本（M14-49）+ 审计归档调度与就绪报告（M14-50/M14-51）+ 审计归档调度面 readiness（M14-53）+ 长稳到期审计/导出 runner（M14-93）+ RC 本地彩排冒烟 runner（M14-96）+ 监控历史时序查询（M14-108）

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
输出一致并被持续管道持续更新——**M14-22 已验收真实调度首轮产出**）；
`monitoring_history_query.py`（M14-108）承担监控历史**时序查询**面
（只读 M14-13 canonical history.jsonl → stdout 有界查询结果：时间窗/
状态过滤 + 服务/端点维度选择 + limit 记录界；零网络/零子进程/零 env
读取/**零文件写入**；**这是查询工具切片，不是生产查询服务**，不构成
provider-smoke 或任何 release blocker 的解除）。

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

**状态（M14-79 日志错误时间界，2026-09-21）**：`log-errors` 阈值判定由
`docker logs --tail` **全尾无时间界计数**改为**当前区间计数**（root
cause：2026-09-21 容器重建事故的 6 条 postgres 陈旧错误（最新
04:29:19Z）只要留在 tail 窗口内就永远计入 error_total，13:15/13:30
样本无新错误仍 warn，恢复被永久阻塞）。语义：`docker logs` 恒带
`--timestamps`（只读输出旗标，白名单放行——布尔/带值旗标拆分校验）；
仅统计时间戳 ≥ 基线（最新合法 prior 完整工件 `started_at_utc`，与
M14-23 restart 基线同源同解析、先于采集解析）的错误行；早于基线的计入
`stale_error_lines` 显式入档（绝不静默丢弃）；**时间戳不可解析 →
fail-closed 恒计入当前区间**（`unparsed_error_lines` 显式计数——recency
无法建立绝不排除，e2e 实证 6 条无前缀错误行触发可见 warn）；基线缺失/
不可用 → 记账基准回退 `full-tail`（= 修复前保守全尾口径）。
`error_total_tail`/`latest_error_at`/`accounting{basis,window_start_at}`
照实入档，check detail 携带完整记账面；阈值（warn≥5/critical≥20）与
退出码零变更；schema 向后兼容（旧工件照常作基线与入档，history/
insights 消费面 `error_total` 键名/类型不变）。e2e 验收：同 6 条陈旧
错误（04:29:19Z）在基线（05:15:01Z）之后 → error_total=0、overall ok
——恢复不再被陈旧错误阻塞；基线后的新错误照常 warn（无遮蔽反向证明）。

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
  history 10–120s（**默认 90s——M14-79 由 45s 上调**：2026-09-21 生产三次
  实测 45.206/45.522/46.955s 刚过 45s 即被杀（工件目录 830+ 份时冷缓存
  重校验偶发超时），90s ≈ 1.9× 最坏观测（46.955s）、正常完成轮实测
  0.3–7.2s；超时事实照常入档 status=timeout，绝不隐藏或改记成功）、
  insights 5–50s（默认 15s，纯本地只读工件
  处理秒级完成即兜底杀停）；默认总和 480+90+15=585s，三步硬顶之和
  540+120+50=710s < 计划任务执行
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

## audit_anchor_archive.py（M14-43 / M14-44）

审计锚点 WORM/对象锁归档：把 M14-42 落地的库外锚文件（锚点 JSONL）
复制进可验证的 WORM 归档并证明归档字节与保留元数据。**preflight /
archive / verify 三命令**，fail-closed 单文件纯标准库；退出码统一
`0` 成功 / `2` 一切拒绝。S3 凭据只认环境变量
`AIOS_AUDIT_ARCHIVE_ACCESS_KEY` / `AIOS_AUDIT_ARCHIVE_SECRET_KEY`
（绝不接受 CLI 值、绝不记录其值）。

```
python tools/ops/audit_anchor_archive.py preflight \
    --anchor-file <锚文件> --endpoint https://<host> --bucket <name>
python tools/ops/audit_anchor_archive.py archive \
    --anchor-file <锚文件> --endpoint https://<host> --bucket <name> \
    --retention-mode COMPLIANCE --retain-until <未来ISO-8601> \
    --confirm "EXECUTE AUDIT ANCHOR WORM ARCHIVE"   # supervisor 获准窗口
python tools/ops/audit_anchor_archive.py verify \
    --anchor-file <锚文件> --endpoint https://<host> --bucket <name> \
    --report <工件目录内归档报告JSON文件名>
```

安全性质（契约测试 `services/api/tests/test_audit_anchor_archive.py`
151 项锁定（Round 3 后）；细节见脚本头注释与
`docs/evidence/m14-43-audit-worm-archive/README.md`）：

- **preflight（零写操作）**：锚链完整校验与 M14-42 生产端
  `app.ops.audit_chain_anchor` 同契约（字段集/类型/canonical JSON/
  anchor_hash 重算/sequence 非负且严格递增——允许跳号不要求 +1
  连续/anchored_at 必须可按 ISO-8601 解析/previous 链接/sequence 0
  head 必须是 genesis 常量/symlink 源拒绝）；endpoint 公网必须
  HTTPS、loopback/RFC1918 可 HTTP、userinfo 内嵌凭据拒绝；凭据
  缺失 → 零 S3 访问；bucket 必须存在且 versioning `Enabled` +
  Object Lock enabled。
- **archive**：先跑全部 preflight 检查；确认短语一字不差 +
  `--retention-mode COMPLIANCE`（不实现可被绕过的 GOVERNANCE）+
  tz-aware 严格未来 `--retain-until` 三道参数门（不满足 → 零执行
  零报告）；对象 key 内容寻址
  `audit-anchor/<sha256>/audit-anchor.jsonl`；已存在对象字节不符
  fail-closed、字节相符则核验 retention 事实后**绝不覆盖**；新对象
  恰好一次 `put_object`（`application/x-ndjson` + SHA-256 checksum
  + COMPLIANCE + retain-until），put 后重读字节与元数据，任何漂移
  fail-closed。
- **verify**：本地校验 + bucket WORM preflight + 归档报告伴生
  `.json.sha256` sidecar 哈希核验（空/非 UTF-8/畸形 → exit 2 报告
  problem 绝不抛异常）+ 报告绑定当前调用事实（status/worm_verified/
  source/endpoint host/bucket/key/对象 hash/size/retention/
  content-type；跨 bucket/endpoint 或失败归档报告一律拒绝）+
  head/get 按报告记录的 version 定向 + 对象逐字节 SHA-256、version
  ID 与报告记录值精确一致、COMPLIANCE/retain-until/content-type/
  size 精确核验。
- **S3 API 白名单**：仅 `head_bucket` / `get_bucket_versioning` /
  `get_object_lock_configuration` / `head_object` / `get_object` /
  `put_object`（read/head/put）——零 delete/copy/create-bucket/
  put-bucket-config 任何 API，源码契约测试锁定；**绝不删除、绝不
  覆盖任何已归档对象**。
- **报告纪律**：原子写 gitignored
  `.verify/artifacts/m14-43-audit-worm-archive/`（**三命令统一三工件
  （M14-58 起）**：JSON + Markdown + 字节精确 `.json.sha256` sidecar
  （sha256sum 形态；失败证据与成功证据同样可被摘要校验）；M14-58 前
  仅 archive 写 sidecar——preflight/verify 报告缺伴生摘要，历史真实
  verify 报告因此被 M14-55 更新器按 `worm-sidecar-missing` fail-closed
  拒绝（历史工件保持原样、绝不回填伪造 sidecar；历史报告补验需
  supervisor 获准窗口真实 WORM 重跑）；字节模式写盘防 Windows
  行尾翻译破坏哈希）；**报告名
  `<command>-<stamp>-<随机后缀>` 真正防碰撞**——每份报告名带
  `secrets.token_hex(16)` CSPRNG 随机后缀（32 位小写 hex chars =
  128 bits，同 command 同秒并发进程撞名概率约 2**-128；残余假设：
  探测与写入之间存在非原子窗口、无全局互斥——概率性抗碰撞而非
  全局互斥保障），存在性探测循环（含残留孤儿工件）仍是
  fail-closed 兜底、递增 `-2`/`-3` 换名，绝不覆盖既有报告工件；
  endpoint 只记 host，绝不含凭据/完整 endpoint/
  原始异常；`redact_secrets` 终防线折叠 key=value / Bearer 形态疑似
  秘密；S3Client/FS/Clock/env/报告名随机后缀生成器全注入（开发回合
  零网络），boto3 仅
  真实执行适配器工厂内懒导入，hex SHA-256 → base64 只在 boto3 边界
  转换（AWS `ChecksumSHA256`）。
- **真实执行状态（M14-44）**：supervisor 已在真实 MinIO Object Lock
  桶 `aios-audit-worm` 完成 `preflight -> archive -> verify`。源锚
  354 bytes、SHA-256
  `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`；
  对象 key `audit-anchor/<sha256>/audit-anchor.jsonl`、version
  `dc704b8d-6ebd-4acb-adb2-2135f89bb703`、`COMPLIANCE` 保留至
  `2036-09-17T19:10:00Z`；三步均 pass，archive/verify
  `worm_verified=true`，verify `problems=[]`。Windows 工作区 tracked
  锚文件因 CRLF 显示 355 bytes / 另一 SHA-256；Git blob 与 `.verify`
  源文件仍为 354 bytes / `d2bf…73aa`，与 WORM 对象一致，不是对象漂移。
- **诚实边界**：M14-43 开发回合的「零真实 WORM 归档执行」是当时
  历史事实，已由 M14-44 真实执行收口；离线介质第二副本、定期归档
  调度、provider smoke、release/cutover 审批、长稳剩余面与 AGC
  签名链仍开放。`pass` 不等于 production ready，
  `production_ready=false` 不变。

## audit_worm_offline_copy.py（M14-49）

审计锚点 WORM 离线第二副本：把 M14-42 锚链文件 + M14-43/M14-44
WORM 归档时绑定的 verify 报告复制到操作者预创建的离线根（可移动
介质/保险库目录），并以确定性 manifest 把三者字节绑定成可复算
整体。**preflight / copy / verify 三命令**，fail-closed 单文件纯
标准库，**零网络 / 零 DB / 零凭据 / 零设备**（源码契约测试锁定）；
退出码统一 `0` 成功 / `2` 一切拒绝。报告只记路径事实（离线根记
操作者显式传入的原始绝对路径字符串，可复现），绝不含凭据形态。

```
python tools/ops/audit_worm_offline_copy.py preflight \
    --anchor-file <锚文件> --verify-report <M14-43 verify 报告JSON> \
    --offline-root <离线根绝对路径>
python tools/ops/audit_worm_offline_copy.py copy \
    --anchor-file <锚文件> --verify-report <M14-43 verify 报告JSON> \
    --offline-root <离线根绝对路径> \
    --confirm "EXECUTE AUDIT ANCHOR WORM OFFLINE COPY"  # supervisor 获准窗口
python tools/ops/audit_worm_offline_copy.py verify \
    --anchor-file <锚文件> --verify-report <M14-43 verify 报告JSON> \
    --offline-root <离线根绝对路径>
```

安全性质（契约测试 `services/api/tests/test_audit_worm_offline_copy.py`
119 项锁定；细节见脚本头注释）：

- **marker 契约**：离线根必须由操作者预先创建（本工具绝不建根），
  且根下预置 marker 文件 `AIOS-OFFLINE-COPY-ROOT.marker`，内容字节
  精确等于 `AIOS audit anchor WORM offline copy root v1\n`——marker
  缺失/内容漂移/symlink/非普通文件一律 fail-closed。marker 是
  「操作者确认此根确为离线副本目标」的物理确认，不是工具产物。
- **离线根门（三命令统一）**：显式绝对路径（拒绝 CWD 相对歧义）、
  必须已存在且为普通目录、根与全部祖先无 symlink、resolve 后不得
  位于仓库内（涵盖 `.verify/artifacts`）、marker 字节精确。
- **preflight（零离线写入）**：锚链校验与 M14-42/M14-43 生产端
  同契约 + verify 报告绑定事实核验 + 离线根门 + 只读观察既有副本
  状态（`absent` / `partial` / `matching` / 字节不符即 `invalid`）。
- **copy**：先跑全部 preflight 检查；确认短语一字不差三道参数门
  （不满足 → 零执行零报告写入）；副本布局内容寻址
  `<root>/audit-anchor/<anchor-sha256>/`，三文件
  `audit-anchor.jsonl` / `verify-report.json` / `manifest.json`
  字节模式原子写（防 Windows 行尾翻译破坏哈希）；**幂等且绝不
  覆盖**——已存在副本逐字节相符则记 `idempotent` 原样保留，任何
  字节不符 fail-closed 拒绝。
- **manifest 确定性绑定**：manifest 是纯输入（锚字节 + 绑定事实 +
  报告字节 + 报告 source_name）的确定性函数，零时钟；幂等 copy 与
  verify 都按同一函数逐字节复算——不同锚/不同报告字节/不同报告名
  必然产出不同 manifest，跨参数副本自然被拒。
- **verify（零离线写入）**：重算锚链 + manifest 复算 + 三文件逐
  字节比对，任何漂移（含换名报告的同字节副本）exit 2。
- **报告纪律**：原子写 gitignored
  `.verify/artifacts/m14-49-audit-worm-offline-copy/`（JSON +
  Markdown + `.json.sha256` sidecar）；报告名
  `<command>-<stamp>-<CSPRNG 随机后缀>` 按 command 前缀作用域防
  碰撞（同 command 撞名才递增 `-2`/`-3`，绝不覆盖既有工件）；
  `redact_secrets` 终防线折叠 key=value / Bearer 形态疑似秘密
  （retain_until 等保留策略时间戳是审计事实，不在秘密名单）；
  FS/Clock/后缀生成器全注入（开发回合零真实介质），异常只记
  `type(exc).__name__` 绝不带原始消息。
- **诚实边界**：本切片开发回合**零真实离线介质执行**——119 项
  契约测试全部在注入 FakeFS 与 `tmp_path` 真实端到端冒烟下通过，
  未在真实可移动介质/网络保险库上落过一份副本；离线介质的物理
  保存策略（防火/异地）、定期重验调度、介质衰减监测仍开放。
  `pass` 不等于 production ready，`production_ready=false`。

## audit_archive_readiness.py（M14-51）

审计归档就绪报告 CLI：M14-50 已合并纯评估器
`audit_archive_scheduler.evaluate_readiness` 的薄封装（评估语义零新增
零改动）——读两个本地 schema-v1 文档（`--state`/`--policy`），把每个
受管任务就 `--now`（缺省当前 UTC；只解析 tz-aware ISO 8601，naive 拒绝）
确定性分类，再原子写 canonical 就绪报告 + SHA-256 sidecar。
**local-only 文件输入/报告输出**，单文件纯标准库，**零网络 / 零 env /
零子进程 / 零 DB/S3 / 零调度器读改 / 零 WORM/离线访问**（源码契约测试
锁定）；退出码 `0` 成功生成报告（含 readiness 判 `due`/`overdue`/
`blocked`——状态在报告 `overall` 字段，工具不算失败）/ `2` 一切
usage/输入/输出拒绝。

```
python tools/ops/audit_archive_readiness.py \
    --state <schema-v1 state JSON> --policy <schema-v1 policy JSON> \
    --output <readiness 报告路径> [--now <tz-aware ISO 8601>]
```

安全性质（契约测试 `services/api/tests/test_audit_archive_readiness.py`
44 项锁定；FakeFS 注入 + `tmp_path` 真实盘双轨）：

- **输入纪律（fail-closed 只读）**：仅普通文件——缺失/目录/symlink/
  未解析不安全路径一律拒绝；单文档保守 1 MiB 上限读前后双检（读中途
  增长仍拒）；严格 UTF-8 + 严格 JSON——重复键、`NaN`/`Infinity`、非
  JSON object 全部拒绝；两个输入绝不变异。
- **报告纪律**：canonical JSON（排序键、紧凑分隔符、UTF-8、恰好一个
  尾随换行）二进制模式写盘（免疫 Windows `\n`→`\r\n` 翻译）——同输入 +
  同 `--now` 产出字节相同报告；报告只记输入的逻辑 role + basename +
  字节数 + SHA-256，**绝不记录绝对本地路径**。
- **输出守卫**：报告 + `<output>.sha256` sidecar（内容
  `<report-sha256>  <output-basename>\n`）经 temp 文件 + `os.replace`
  原子替换，写失败清理 temp 非零退出零残骸；输出、sidecar 及其 tmp
  中间路径**绝不覆盖任一输入**（supervisor review 落实的 tmp 路径
  input-collision 守卫有专项回归测试）；工具绝不创建目录。
- **诚实边界**：开发回合全部为合成文档 + 临时文件验证，未在真实调度
  链路/真实锚链状态上运行；不自动发现证据文件、不集成 Windows Task
  Scheduler、不接触 WORM/离线存储、不执行生产、不访问任何 provider、
  不证明 `production_ready=true`——`production_ready=false` 不变。

## audit_archive_task.py + run_audit_archive_readiness_silent.vbs（M14-53）

审计归档**调度面** readiness 管理器：把 M14-51 的
`audit_archive_readiness.py`（local-only 就绪评估 CLI）经静默 VBS wrapper
挂到隐藏每日 Windows 计划任务上，用固定 canonical gitignored
state/policy/output 路径每日生成一次就绪报告。**本工具只管理调度
readiness，不实现、不执行审计归档本身**。子命令
`plan`（只读预检 + 注册计划）/ `generate`（导出任务 XML 供审查，UTF-16
with BOM 字节、零调度器改动）/ `status`（只读五态）/ `install` /
`uninstall`（均需 `--confirm "EXECUTE AUDIT ARCHIVE READINESS SCHEDULER
CHANGE"` 一字不差，否则零 schtasks 调用；实际注册 supervisor-only）。

任务身份：`AIOS-Audit-Archive-Readiness` /
`urn:aios:m14-53:audit-archive-readiness` + Description 精确归属标记
（点名 M14-53 与本管理文件）；每日 TimeTrigger 重复间隔 `P1D`（无
Duration = 无限期）；`Hidden=true`、`InteractiveToken`/`LeastPrivilege`、
`IgnoreNew`、`StartWhenAvailable=true`、`ExecutionTimeLimit=PT30M`、电池
不禁启不停；Action `wscript.exe //B //Nologo "<repo>\tools\ops\
run_audit_archive_readiness_silent.vbs"`、WorkingDirectory=仓库根。

安全性质（契约测试 `services/api/tests/test_audit_archive_task.py`
53 项锁定；全部经注入 FakeSchtasks，零真实 schtasks）：

- **结构性白名单门（GatedSchtasks）**：仅四形态放行——全量列表查询 /
  单任务 `/XML` 明细 / `/Create /TN <固定名> /XML <单个 .xml 临时件>` /
  `/Delete /TN <固定名> /F`；`/Run`、`/Change`、`/End`、`/Create` 带 `/F`
  等一切其它形态在任何执行之前拒绝；plan/status/generate 恒
  `allow_mutation=False`（结构性零调度器改动）。
- **绝不覆盖同名任务**：install 预检 + 只读 query + 二次全量列表复核
  missing + 临时 XML（UTF-16 with BOM，用后即删）+ 安装后 exact-owned
  复查全过才 `/Create`；uninstall 仅删本工具精确拥有的任务，
  foreign/missing/malformed/unknown 零删除（missing 幂等 OK）。
- **exact-owned 精确校验 + 结构收紧（supervisor 修正）**：URI 两种形态
  之一（写入值或 Task Scheduler 归一化 `\<任务名>`）+ Description 持久
  标记精确相等 + Action 三件套 + 全部安全设置逐项精确；归一化省略的
  三个默认值元素仅在其余字段全精确时按 Windows 默认值认可；**Task 根
  元素必须精确，且恰好一个 `Actions/Exec`、一个 `TimeTrigger`、一个
  `Principal`**——多余条目（可夹带第二动作/另一套调度/另一身份）一律
  malformed 拒绝；`/XML` 输出按四字节形态严格解码（LE BOM / BE BOM /
  UTF-16LE 无 BOM / ASCII-UTF-8 prolog），之外按 unknown fail-closed；
  解析前拒绝 DOCTYPE/ENTITY；路径经 XML 转义。
- **VBS wrapper 纪律**：仓库根自脚本位置推导（无盘符硬编码）；隐藏
  窗口运行 + 等待 + 退出码透传；恒调用 `<repo>\.venv\Scripts\python.exe`
  与 readiness CLI，无任何 Python/脚本覆盖面；固定 canonical
  state/policy/output
  （`.verify\artifacts\m14-53-audit-archive-readiness\`）、不传 `--now`
  （每次调度运行自然用当前 UTC）；canonical 工件目录由 wrapper 唯一
  负责**逐级**创建（`.verify` → `.verify\artifacts` → 叶子目录，干净
  checkout 下 CreateFolder 非递归会失败——supervisor 修正，专项测试
  锁定），预检专用退出码 2/3/4/5（venv python / readiness CLI / repo
  根 / 目录链创建失败）；VBS 内容/注释**纯 ASCII** 且 `artifactsDir`
  恰三级 stepwise `BuildPath` 构建（真实 supervisor cscript 干净仓库
  语法探针修正：非 ASCII 在 cscript 默认代码页下编译失败、多余嵌套
  BuildPath 运行时报 invalid arguments——`isascii()` 与精确构建形态
  契约锁定）。
- **零 secret/零 env/零网络**（源码契约测试锁定）：不读任何 env 值，
  无 `--python` 覆盖；报告/日志绝不回显原始调度器输出或秘密形态值。
- **诚实边界**：开发回合零真实 schtasks 读/写（全部注入 Fake）、零
  安装/零卸载/零注册、未运行真实 readiness、未接触 WORM/离线/S3/
  provider/Docker/生产；实际注册 supervisor-only；
  `production_ready=false` 不变。

生产验收（M14-54 回填记录；supervisor 于 2026-09-18 在 canonical
`main@5ab05c3`（PR #136 merge）真实执行，证据
`docs/evidence/m14-54-audit-archive-scheduler-production/README.md`）：
`plan` 全部 repo/VBS/venv/readiness 预检通过且初始 status `missing` 后，
exact-owned `install` 携确认短语在**非提权** supervisor shell 成功注册
（安装后 status=installed、State Ready、初始 LastTaskResult 267011、
NextRunTime 2026-09-19T00:00:00+08:00）。真实调度**负路径**
（2026-09-18T23:31:30+08:00，state/policy 缺席）：LastTaskResult=2、
仅创建 canonical gitignored 工件目录、无报告——fail-closed 缺输入行为
验证。手工物化 canonical schema-v1 state/policy（内容源自已验证的
M14-42/M14-43/M14-49 事实）后**正路径**
（2026-09-18T23:32:07+08:00）：LastTaskResult=0，readiness.json 与
`.sha256` sidecar 匹配、overall fresh、problems 空、全部任务 fresh。
终态任务 installed/exact-owned，tracked main 树 clean。边界：
state/policy 为**手工物化**样本——自动维持这些输入的 updater 仍缺位，
后续真实运维依赖它补齐；不声称审计归档本身已执行；
`production_ready=false` 不变。

## soak_stability_audit.py（M14-72）

长稳稳定性审计器：只读消费 M14-13
`.verify/artifacts/m14-13-monitoring-history/history.jsonl`，判定是否存在
**真实连续 24 小时稳定窗口**，输出确定性 JSON+Markdown 报告到
gitignored `.verify/m14-72-long-soak-audit/`。与 monitoring_history 同款
纪律：单文件纯标准库、一切 I/O 经 Store 注入、零子进程/零网络/零计划
任务/零 env 读取/零墙钟（生成时间戳取自锚样本，输出逐字节可复现）。

```
# 仓库根执行（canonical venv，纯标准库）
python tools/ops/soak_stability_audit.py                    # 默认输入/输出
python tools/ops/soak_stability_audit.py --history <dir-or-file> --window-minutes 1440
```

分类（fail-closed，固定词汇原因；数据域 blocked 与输入拒绝分离）：

- `pass`（exit 0）：窗口内全部样本 ok 且 partial=false、全局时序/唯一/
  项目单一、间隔 ≤ max-gap、样本数 ≥ 闭区间最小值（window // interval
  + 1，24h/15m = 97，含窗口两端）、覆盖自窗口起点——**绝不从
  总历史跨度/insights 聚合/合成 soak 时长/墙钟推导 pass**。
- `pending`（exit 1）：数据合法但干净覆盖不足或样本数低于闭区间最小值
  （insufficient-clean-coverage / insufficient-sample-count）——96 行
  即使跨度覆盖窗口且间隔合规仍 pending。
- `blocked`（exit 2，数据域审计结论）：窗口内 warn/critical/partial
  （non-ok-status-in-window）或间隔超限（excessive-gap-in-window）——
  输入合法，写出确定性 JSON+Markdown blocked 报告后退出 2（canonical
  真实历史干跑即此：窗口内含 warn，报告落盘）。
- 输入拒绝（exit 2，零输出）：路径缺失/symlink、malformed 行/非法字段
  （含非哈希 overall_status 的受控拒绝）、重复/非时序时间戳、项目冲突、
  行数超硬顶 5000、参数超界、写失败——写出前即拒绝。

留存契约：`--retention`（默认 500，1-5000）取最新 N 行分析，全局校验
覆盖全部行；报告仅含计数/状态/时间戳面与输入 SHA-256——绝无原始日志
行/密钥/secret/env 值/URL/token/主机标识。契约测试
`services/api/tests/test_soak_stability_audit.py` 锁定；本工具只是长稳
审计门禁，不构成真实 24h soak 的完成，也不构成 production readiness
宣称，`production_ready=false` 不变。

**M14-73 发布门接入**：`AUDIT_SCHEMA_VERSION` 升至 **2**，报告顶层新增
`"gate": "long-soak"` 自声明字段——M14-72 语义与输出确定性完全不变。
`release-readiness` 自 M14-73 起把 `long-soak` 作为必需门禁（证据文件
`long-soak.json`，十必需门变十一门），评估器只接受 schema v2 + gate
自声明 + 精确策略（1440/15/20/retention 500）的工具产物：把本工具生成
的 `soak-audit-report.json` **逐字节复制重命名**为
`<evidence-dir>/long-soak.json`（`cp` 后不得手工编辑/重序列化——输入
sha256/bytes 与策略字段任一漂移即 malformed）。

## pipeline_incident_review.py（M14-79）

管道事件复核器：只读交叉复盘 M14-14 管道报告目录
（`.verify/artifacts/m14-14-monitoring-pipeline/`，只认
`pipeline-YYYYMMDD-HHMMSS.json`（execute）与 `plan-*`（只计数））与
M14-13 `history.jsonl`，把「pipeline exit 1」拆成三类语义并判定恢复：
**monitor 非零退出但写出样本工件 = 状态域裁决**（监控正常工作，系统确实
warn/critical——其引发的 history/insights skipped 是设计内门控后果，不是
执行失败）；**monitor 非零退出且无工件 = 执行域失败**（该槽位零样本、
数据真空）；**monitor ok 而 history/insights 超时 = 执行域瞬态失败**
（样本工件已落盘，history 行由后续成功运行增量补录——补录事实如实呈现，
超时绝不改记成功）。逐运行归因（固定词汇 failure_domain ∈
none/execution/status/mixed + execution_failure_kinds + 超时步 + 样本
入史反查）→ 事件窗口聚合（恢复 = 其后首个 overall ok 运行，无恢复即
开放）→ 双面判定（pipeline_execution_state × monitoring_status_state，
绝不合并遮蔽）。零子进程/零网络/零计划任务/零 env 读取/零墙钟
（generated_at 取自输入时间戳，输出逐字节可复现）；输出确定性
JSON+Markdown 到 gitignored
`.verify/artifacts/m14-79-pipeline-incident-review/`。

```
# 仓库根执行（canonical venv，纯标准库）
python tools/ops/pipeline_incident_review.py                     # 默认输入/输出
python tools/ops/pipeline_incident_review.py --runs 20           # 只复盘最新 20 份
```

退出码：0 无开放项 / 1 有开放项（管道最新运行仍失败或 history 最新样本
非 ok——可见结论，报告照常落盘）/ 2 输入拒绝（零输出：未知 stem、
stage 越词汇、monitor skipped（结构不可能）、schema 漂移、started_at
重复/非时序、history malformed/重复/非时序/项目冲突/空文件/行数超顶、
参数越界）。M14-21 前两步形态（无 insights step）可解析，缺席步不计
失败。契约测试 `services/api/tests/test_pipeline_incident_review.py`
锁定；all_clear ≠ production readiness（`production_ready=false` 不变）。

## soak_window_gate.py（M14-79）

长稳窗口锚定/重启门：只读消费 M14-13 `history.jsonl`，把「何时允许重启
一个全新 24h soak 窗口」固化为可审计门禁——尾部 `--consecutive-ok N`
（默认 8 = 15 分钟节奏 2 小时）个连续样本全部 ok 且 partial=false、
相邻间隔 ≤ `--max-gap-minutes`（默认 20，与 soak 审计同口径）才 open；
warn/critical/partial 仍留在尾部即 closed，**逐条列出**每个非干净样本
（collected_at + overall_status + partial——绝不遮蔽、绝不改写历史）。
`--anchor` 在门 open 时原子写出锚定记录 `soak-window-anchor.json/.md`
（锚点 = 最新样本 collected_at，零墙钟；最早可判定时间 = 锚点 + 窗口；
前置指纹；后续审计固定指引）；锚定记录已存在 → anchor-exists 拒绝
（重启窗口须操作者显式归档旧记录，防静默重锚掩盖已破坏的窗口）。

```
# 仓库根执行（canonical venv，纯标准库）
python tools/ops/soak_window_gate.py                     # 检查模式（默认）
python tools/ops/soak_window_gate.py --anchor            # 门 open 时锚定
python tools/ops/soak_window_gate.py --consecutive-ok 8  # 前置可配置
```

退出码：0 门 open / 1 门 closed（门报告落盘、锚定记录零写出——可见
结论）/ 2 输入拒绝（缺失/symlink/malformed/重复/非时序/项目冲突/空
文件/行数超顶/consecutive-ok 超 retention/参数越界/anchor-exists/写
失败——零写入）。**锚定 ≠ soak 通过**：门只证明开窗时尾部干净，窗口
结局由 24h 后 `soak_stability_audit.py` 判定（其报告逐字节复制为
`long-soak.json` 才构成门证据）；`release_ready=false` /
`production_ready=false` 不变。输出 gitignored
`.verify/artifacts/m14-79-soak-window-anchor/`；契约测试
`services/api/tests/test_soak_window_gate.py` 锁定。

## long_soak_release_window.py（M14-93）

长稳到期审计/导出 runner：M14-79 权威锚定窗口（soak-window-anchor.json）
到点后，把「重跑 soak 审计 + 逐字节导出 long-soak 门证据」从人工命令
拼装固化为单一 fail-closed 仓库工具。到期判定零墙钟——**最新历史样本
collected_at** 对比锚定记录 `earliest_audit_collected_at`（绝不读系统钟）：
未到期 → 固定词汇 not-due 拒绝（exit 1，零审计零证据）；到期 → **原样
复用** `soak_stability_audit.py` 的解析/校验/分类语义（固定
release-readiness 接受策略 1440/15/20/500，不暴露任何策略参数——策略
漂移即证据不可用）审计进全新输出目录；pending 拒绝证据导出；只有
pass/blocked 才把 `soak-audit-report.json` **逐字节复制**为
`evidence/long-soak.json`（utf-8 往返预检 + 写后重读复核，任何字节差异
拒绝并移除坏副本——绝不改写/重序列化门证据），runner 报告登记源/副本
双哈希（必须相等）。锚定记录严格校验（九键全集/schema_version/tool/
follow_up_audit_tool 逐字匹配/earliest == anchor + 窗口/generated_at ==
anchor 零墙钟产锚不变式/precondition 取值域——手改锚提前到期或
tail_non_ok_count>0 篡改即拒绝）。

```
# 仓库根执行（canonical venv，纯标准库）
python tools/ops/long_soak_release_window.py             # 默认输入/输出
python tools/ops/long_soak_release_window.py --anchor <soak-window-anchor.json> \
    --history <dir-or-file> --output-dir <fresh-dir>
```

退出码：0 到期 pass 且证据已导出 / 1 not-due（零审计零证据）/ 2 到期
pending（审计已跑、证据导出拒绝）/ 3 到期 blocked（blocked 证据已逐字节
导出——诚实结论非崩溃）/ 4 输入拒绝（锚定/历史 malformed、输出目录已
存在、symlink、哈希不符、写失败——零输出）。**本 runner 不使
long-soak 通过**：blocked 窗口导出的就是 blocked 证据；
`release_ready=false` / `production_ready=false` 不变，发布审批
human-only。输出 gitignored
`.verify/artifacts/m14-93-long-soak-window-runner/`；契约测试
`services/api/tests/test_long_soak_release_window.py` 锁定（锚定记录
由真实 soak_window_gate --anchor 在合成历史上产出后消费）。

## rc_smoke_rehearsal.py（M14-96）

RC 本地彩排冒烟 runner：从当前 worktree 构建唯一 tag
（`m14-96-rc-smoke-<HEAD 40hex>`，绝不覆盖 v0.1.0/生产 tag）的
API/Web 镜像，在任务自有隔离 compose 项目
（`infra/docker-compose.rc-smoke.yml`，项目名 `aios-m14-96-rc-smoke`，
loopback 独占 18096/13096，数据面零宿主端口）里起栈、逐服务健康轮询、
八项 loopback 探针（/health、version==VERSION、auth_enabled、匿名
/papers 401、匿名 /resources/upload 401、Web / 与 /login、CORS
preflight 回显）、只拆除本项目（down --volumes），并以**前后全机
docker ps -a/volume/network/compose-ls 快照等价**证明零外部（生产）
漂移。fail-closed 前置：HEAD==--base-sha、porcelain 脏文件必须在镜像
构建输入面之外、项目零残留、tag 镜像不存在、infra 镜像在库（绝不
pull）、端口可 bind、compose config 渲染精确；compose 子进程 env
全量剥离宿主 AIOS_* 漂移变量；运行期 assert_safe_argv 白名单护栏
（compose 恒 -f 冒烟文件 + -p 任务项目名，stop/rm/push/system 等动词
一律拒绝）。与生产默认零漂移：生产 compose/RC 构建器/冒烟脚本三文件
字节级 sha256 pin（基点 c9de722 git blob），冒烟 compose 默认 no-op。

```
# 仓库根执行（canonical venv，纯标准库）
python tools/ops/rc_smoke_rehearsal.py --base-sha <40-hex>
```

退出码：0 pass / 1 refused（前置 fail-closed）/ 2 failed（构建、起栈、
健康、探针、拆栈或快照等价失败——诚实失败证据照常落盘）/ 3 用法错误。
**本地彩排冒烟，不是 production readiness 声明**：release_ready /
production_ready 不变，发布审批 human-only；不推镜像仓库、不打 git
标签、不发 GitHub Release。输出 gitignored
`.verify/artifacts/m14-96-release-candidate-smoke/`（rc-smoke-report
.json/.md + 逐阶段原始输出 + SHA256SUMS）；契约测试
`services/api/tests/test_rc_smoke_rehearsal.py` 锁定（59 项，含生产
字节级 pin 回归）。执行结论见
`docs/evidence/m14-96-release-candidate-smoke/README.md`（attempt-1
build-failed:api——本机 daemon 出站 mirror+静态代理不可用且本地无
python/node 基镜像，按 supervisor 边界诚实停止；机制零改动可复跑）。

## monitoring_history_query.py（M14-108）

监控历史**时序查询**面：把 `monitoring_history.py`（M14-13）已产出的
canonical `history.jsonl` 变成安全、离线、可测试的只读查询工具。**这是
查询工具切片，不是生产查询服务**——不构成 provider-smoke 或任何
release blocker 的解除，不构成 production readiness 宣称。

```
# 仓库根执行（canonical venv 或任意 Python ≥3.11，纯标准库）
python tools/ops/monitoring_history_query.py                       # 全量摘要
python tools/ops/monitoring_history_query.py --status critical \
    --start 2026-09-11T00:00:00Z --end 2026-09-12T00:00:00Z --limit 20
python tools/ops/monitoring_history_query.py --format json \
    --service redis,api --endpoint api-health
```

- **单一事实源（零平行 schema）**：画像/schema 常量复用同仓
  `monitoring_history`（六服务/五端点/HISTORY_SCHEMA_VERSION/时间戳解析/
  nearest-rank percentile/退出码）；canonical 行级校验**委托**
  `monitoring_insights.parse_history_text`（已测语义：行 schema 严格
  校验、行序严格递增 (collected_at, source_stem)、单一 project、样本量
  界 1–5000——固定词汇拒绝原因原样透传）。本工具未改动两个既有工具的
  任何生产行为。
- **有界参数（全部先于任何读取校验；超界/非法一律拒绝且零读取）**：
  `--start`/`--end`（UTC `%Y-%m-%dT%H:%M:%SZ`，时间窗**两端均含边界**，
  `start > end` 拒绝，可单边）；`--status`（逗号分隔，恒 ∈
  {ok,warn,critical}，空段/重复/词汇外拒绝）；`--service`/`--endpoint`
  （**维度选择**——切片聚合与记录投影，不是记录过滤面：canonical 记录
  是完整栈样本；恒 ∈ 六服务/五端点画像）；`--limit`（记录列表界，默认
  50、1–500，保留**最新** N 条 + 显式 `truncated_older_count`；**聚合恒
  为全窗口口径**，limit 绝不扭曲聚合）。
- **只读纪律**：零网络、零子进程、零 env 读取、零计划任务、零生产
  容器/DB/对象存储接触、**零文件写入**（Store 协议层面即无写面——只读
  输入文件、只写 stdout；绝不改动/删除输入工件）。symlink 目标/现存
  symlink 祖先组件、缺失、目录形态一律拒绝。
- **输出（stdout only）**：`--format summary`（默认人读摘要）或
  `--format json`（单文档机器可读：query echo/window 计数/状态计数/逐
  所选端点延迟 min-p50-p95-max/逐所选服务 restart 与日志 error 总计/
  有界记录投影——仅从合法历史记录派生，绝无原始日志/密钥/secret/env
  值；行级未知额外字段被结构性丢弃）。任何拒绝 → 固定词汇拒绝行 +
  exit 2，JSON/摘要正文**零部分输出**。零墙钟：无生成时间戳，同参数
  两次运行 stdout 逐字节相同。零命中是合法查询结果（如实输出零计数）。
- 退出码：0 查询成功 / 2 任何拒绝。契约测试
  `services/api/tests/test_monitoring_history_query.py` 锁定（57 项：
  结构契约/参数 fail-closed/路径防御/行校验透传/查询语义/输出卫生）。
  切片说明见 `docs/evidence/m14-108-monitoring-history-query/README.md`。
