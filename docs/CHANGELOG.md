# Changelog

All notable changes to the AI Learning OS project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/), versions follow semver.

## [Unreleased]

### Added
- M14-15 监控历史洞察/告警摘要第 1 切片（`tools/ops/monitoring_insights.py` +
  `services/api/tests/test_monitoring_insights.py` 90 项契约测试；本 Claude
  开发回合仅做本地 commit，supervisor 审查与 remote 发布在其后进行；
  **开发回合零生产执行、零 canonical 仓库/`.verify` 触碰——全部验证用
  合成样本，不等于外部告警接入，`production_ready=false` 不变**）。本地
  只读工件 → 安全 JSON+MD 洞察摘要：输入三形态（`history.jsonl` 文件 /
  含它的目录（默认 gitignored
  `.verify/artifacts/m14-13-monitor-history-retention/`——本切片指定新
  输入位，仓库现有工具尚无写入者）/ M14-12 monitor 工件目录——校验/
  去重/排序**委托同仓 M14-13 `monitoring_history.py` 已测函数**，schema
  单一事实源，固定词汇拒绝原因透传）；洞察最小集（时间范围+样本数+
  时长、overall_status 计数、availability 计数+比率、degraded/critical
  事件列表（非 healthy 服务+非 200 端点明细，`--event-limit` 默认 50、
  1–500，超界截断计数显式保最新）、逐端点延迟 min/p50/p95/max
  （nearest-rank）+非 200 计数、逐服务 restart/日志 error/非 healthy
  样本汇总、连续失败/恢复（当前连胜+当前连续 non-ok、最长 non-ok 连败
  区间、失败/恢复转移计数+有界时间戳、逐端点当前连续失败）、最近样本
  状态）；**fail-closed**——行序非严格递增（乱序/重复/同时间戳逆序）、
  跨 project 混档、样本数超 5000、schema 不完整、partial/incomplete、
  非有限非负延迟一律拒绝且**输出零写入**；默认 **plan 完全惰性**（零
  读取/零写入/零 Store 构造），**execute** 需 `--execute` + 精确确认短语
  `EXECUTE READ-ONLY MONITORING INSIGHTS`（一字不差），缺一/近似即
  EXIT 2 零读取；输出 `insights.json` + `insights-summary.md` 同目录
  tmp+fsync+os.replace **原子写**（仅校验全过后才写、零 tmp 残留、
  symlink 全路径拒绝含 mkdir 穿越防御）；**零墙钟**（生成时间戳取自
  最新样本，两遍逐字节相同）；**报告卫生：stdout/输出绝无绝对本机路径
  （恒仓库相对或纯名）/原始日志行/密钥/secret/生产容器 ID，被拒值不
  回显**；零子进程/零网络/零容器面/零计划任务/零 env 读取（源码契约
  token 锁定 + socket/subprocess 双阻断端到端）。验证：聚焦 **90
  passed**；M14-13 回归 **164 passed** 零回归；监控全家族五套件合并
  **484 passed**；ruff/py_compile/`git diff --check` 全过；合成数据端到
  端演示（M14-13 → 默认源位 → 全默认 plan+execute，exit 0、确定性逐
  字节相同）。诚实边界：仅本地工件洞察，不接外部告警（无发送/通知/
  webhook），canonical 真实历史当前仍单样本（趋势列单点值如实呈现）；
  证据见 `docs/evidence/m14-15-monitoring-insights/`。
- M14-14 持续/定时监控采集 + 历史管道 readiness（`tools/ops/monitoring_pipeline.py`
  + `tools/ops/monitoring_pipeline_task.py` +
  `tools/ops/run_monitoring_pipeline_silent.vbs`；本 Claude 开发回合仅做本地
  commit，supervisor 审查与 remote 发布在其后进行；**开发回合零真实管道
  执行（execute 模式从未运行）、零计划任务注册/改动、零 Docker/零生产
  HTTP/零 env 读取——交付的是 readiness，不证明持续运行**）。管道把既有
  M14-12 `production_monitor.py` 与 M14-13 `monitoring_history.py` 安全组合
  为单次执行：默认 **plan 完全惰性**（零 subprocess/零网络/零生产读取/零
  调度器改动，Runner 零构造）；**execute** 需 `--execute` + 精确确认短语
  `EXECUTE READ-ONLY MONITORING PIPELINE`（一字不差），缺一/近似即 EXIT 2
  且零 Runner 构造/调用（fail-closed）；**固定命令白名单门（结构性）**——
  仅两个精确固定形态（monitor `--execute --confirm "EXECUTE READ-ONLY
  PRODUCTION MONITORING"`（与 monitor 自身短语逐字一致，回归测试锁定）；
  history 全默认参数），任何其它 argv 在执行之前拒绝，无 shell=True、无
  用户可注入命令/URL/env 展开，子进程输出只取 returncode（stdout/stderr
  绝不持久化/回显）；**序列** monitor → history——history 仅在 monitor
  exit 0 后运行，失败如实保留绝不遮蔽；有界超时（monitor 60–540s 默认
  480s、history 10–120s 默认 45s；硬顶之和 660s < 计划任务执行时限
  PT12M=720s < 重复间隔 PT15M——调度器绝不先于内部超时杀整任务）；
  **fail-closed 重叠锁** `pipeline.lock`（O_CREAT|O_EXCL；本轮零
  stale-lock 清理）；schema v1 JSON+MD **原子报告**仅安全事实（状态/退出
  码/时长/固定命令身份（无绝对本机路径）/脱敏错误类别类名/产物名+
  SHA-256+字节数（差集发现、每步 ≤8 个 hash、超界记数）），写前
  redact_secrets 终防线。计划任务 readiness 管理器：固定身份
  `AIOS-Monitoring-Pipeline`/`urn:aios:m14-14:monitoring-pipeline`（与
  M14-06 恢复任务零身份冲突）+ PT15M 保守重复间隔（无 Duration=无限期）
  + IgnoreNew/Hidden/InteractiveToken/LeastPrivilege/电池不禁启不停；
  plan/generate/status/install/uninstall 五子命令，**install/uninstall 各
  自需精确短语 `EXECUTE MONITORING SCHEDULER CHANGE`**（缺一即零
  schtasks 调用）；结构性 schtasks 白名单门（读路径恒零 mutation，仅两
  查询形态；mutation 仅 `/Create /TN <固定名> /XML <单个 .xml>` 与
  `/Delete /TN <固定名> /F` 两精确形态——create 绝不 /F）；绝不覆盖同名
  任务（双重存在性确认）；uninstall 仅 exact-owned 才删除；归属判定适配
  M14-06 实证归一化 + /XML 字节形态四形态解码 + DOCTYPE/ENTITY 解析前
  拒绝（XXE 加固）+ XML 生成侧转义；**实际注册 supervisor-only（提升
  令牌）——本回合零安装/零卸载/零注册**。静默 VBS 入口：仓库根自脚本位置
  推导、隐藏窗口 Run(...,0,True)、退出码透传、恒调 repo 自带
  `.venv\Scripts\python.exe` 携带管道门禁旗标。**supervisor R2 阻断缺陷
  修正（同分支 amend）**：`cmd_generate` 旧以 UTF-8 写出声明 UTF-16 的
  任务 XML——外部解析器（System.Xml `XmlDocument.Load`）报「no Unicode
  byte order mark」拒载；修正为 **UTF-16 with BOM 字节**（与 XML 声明及
  install 临时文件字节完全一致）+ 落盘后回读原始字节经字节形态解码与
  归属校验复核，并新增逐字节契约测试（UTF-16 BOM 起始 + 声明一致 +
  解码/归属校验通过——旧 UTF-8 字节形态必失败，纯文本 round-trip 不足
  以锁定）。契约测试
  `test_monitoring_pipeline.py` **52 项** + `test_monitoring_pipeline_task.py`
  **79 项**（FakeRunner/FakeSchtasks/临时目录，含 monitor/history/
  startup-task 既有契约回归 pin、VBS 契约与上述 R2 字节级回归）；邻居回归
  `test_monitoring_history.py` **74** / `test_production_monitor.py` **189** /
  `test_windows_startup_task.py` **66** 全 passed（五套件合并 **460
  passed**）；ruff/py_compile/`git diff --check` 过。**诚实边界：
  readiness ≠ 持续运行证明；管道真实首跑与计划任务首次注册均待
  supervisor 获准窗口；TimeTrigger/Repetition/StartBoundary 注册后归一化
  未经真实安装实证（首注册若漂移按 malformed fail-closed）；stale-lock
  清理本轮零实现（外部强杀留锁 → 下次可见拒绝，操作者人工删）；
  `production_ready=false` 不变**；详见
  `docs/evidence/m14-14-monitoring-pipeline/README.md`。
- M14-13 监控历史索引 + 有界留存 + 趋势摘要 + 首次真实历史构建结果
  （`tools/ops/monitoring_history.py`，**工具交付并合并（PR #87）；
  开发回合未执行真实历史构建——结果回填回合已从 canonical main
  独立执行首次真实构建（见本条末尾结果）**；本 Claude 开发回合仅做本地
  commit，supervisor 审查与 remote 发布在其后进行）：对既有 M14-12 monitor JSON
  工件（默认 gitignored `.verify/artifacts/m14-12-production-monitoring/`，
  `--source-dir` 可覆盖）建立**只读**安全索引——仅发现 `monitor-*.json`
  （stem 严格白名单 `monitor-YYYYMMDD-HHMMSS` 含日历合法性，被拒名不
  回显）；严格 schema 校验（version/tool/milestone/mode/project 白名单/
  双 UTC 时间戳格式与顺序/overall_status∈{ok,warn,critical}/partial 恒
  false/阈值计数类型/六 compose 服务/六容器 RestartCount/五端点状态+
  延迟（有限数值）/六日志 error_total；incomplete/partial/malformed 一律
  fail-closed 输出零写入）；SHA-256 逐文件去重（同哈希保留 (collected_at,
  stem) 最小者，duplicate_count 显式；同 (project, collected_at) 不同
  哈希 = conflicting-duplicate 拒绝）；(collected_at, stem) 确定性排序；
  保留最新 N 条（默认 500、硬顶 5000）+ 显式 omitted_older_count 与
  oldest/newest 边界，**源工件永不改动/删除**；输出原子
  `history.jsonl`（紧凑记录：hash/stem/collected_at/project/
  overall_status/partial/threshold_counts/compose health/restart/端点
  状态+延迟/日志 error 总计——绝无原始日志行/密钥）+
  `history-summary.md`（状态计数/availability/degraded/critical、
  first/last、逐端点延迟 min/p50/p95/max（nearest-rank）、逐服务
  restart/error 总计、duplicate/omitted 计数），**生成时间戳取自最新
  源样本 collected_at——零墙钟、输出逐字节可复现**；symlink 全路径
  拒绝（源文件/源目录/输出/祖先）；tmp+fsync+os.replace 原子写（失败
  清 tmp 零残留）；零子进程/零网络/零容器面/零计划任务/零 env 读取
  （源码契约锁定）；一切 I/O 经 Store 注入。73 项契约测试 + 组合回归
  262 passed（M14-12 套件零回归）；开发回合零生产执行、零 canonical
  `.verify` 写入（全部验证用合成样本临时目录）。交付随 **PR #87**
  合并 main（merged_at 2026-09-12T08:13:50Z，merge commit
  `d2ffc49b2770891227b52aea0307fb969969c7e2`，feature head
  `b1201d6d041ae3ea492ee918d4572584a6c47b02`，远端 feature 分支已删除；
  PR CI run `34682507203` 与合并后 main push CI run `34682734884` 均全部
  5 job（Web/API/Docker/Android/Release tools）SUCCESS）。**首次真实
  历史构建（2026-09-12，结果回填回合从 canonical main `d2ffc49` 独立
  执行，`py -X utf8` 连跑两遍）**：源 canonical
  `monitor-20260911-173920.json`（SHA-256
  `740c031eb3cd452a98ef0e0b70be92f0a2a8bc33d417ff0c72436ee25ed4a458`，
  两次运行前后逐字节不变）→ gitignored
  `history.jsonl`（SHA-256
  `a71ff7e2816e55b1a2c964024cdeb8b00ba4fff6de482b3165ae86556dac5ae0`）
  与 `history-summary.md`（SHA-256
  `80535c3a41699137bd52fdb37ed06b2c5c57f3cd1bbdafdab7bb306c351a7be`，
  两文件两遍输出 `cmp` 逐字节相同，均与 supervisor 给定期望值一致）；
  恰 1 行合法 JSON：`overall_status=ok`、`partial=false`、行内
  `artifact_sha256` 与源哈希匹配、ok=34/warn=0/critical=0、六服务
  healthy+restart 0、五端点全 200（7.088–27.641ms）、日志 error 全 0。
  **仅闭环历史数据面工具证据 + 单样本首次真实构建——持续/定时采集与
  调度、外部告警接入、指标时序存储/查询、阈值随时间的标定、跨机监控
  仍属未决；`production_ready=false` 不变**；证据见
  docs/evidence/m14-13-monitoring-history/
- M14-12 生产监控/告警 readiness 开发切片 + 首次真实只读生产监控结果：
  只读采集 + 阈值判定 + 证据
  报告（`tools/ops/production_monitor.py`，**工具交付并合并（PR #85）；
  开发回合未执行生产采集——supervisor 已于合并后执行首次真实只读
  监控（见本条末尾结果）**；
  本回合不接外部告警系统；本 Claude 开发回合仅做本地 commit，supervisor
  审查与 remote 发布在其后进行）：默认 plan 零
  subprocess/零网络/零生产读取（plan 报告零状态宣称）；execute 需
  `--execute` 旗标 + 精确确认短语 `EXECUTE READ-ONLY PRODUCTION
  MONITORING` + 全部阈值在硬顶内（R1 起非有限浮点 nan/inf/-inf 拒绝；
  `--project` 严格白名单——ASCII 字母数字开头、仅字母数字/连字符/
  下划线、≤64，被拒值不回显；均在 plan 报告写入/采集之前），缺一即
  EXIT 2 零采集；只读采集面
  固定画像（compose project `aios-m14-03-production-rehearsal`：compose
  ps + 六受管容器 inspect 五事实（state/health/RestartCount/image/
  started）+ 五默认端点 GET（Web 3011 `/`+`/login`、API 8000 `/health`、
  FunASR 8010/CosyVoice 8011 `/health`）状态+延迟 + `docker logs
  --tail` 容器日志安全错误摘要——只记匹配计数/级别/安全类别，原文绝不
  持久化）；一切 docker 命令经只读白名单门（仅 compose ps / inspect
  --format / logs --tail 三形态，stop/rm/kill/restart/down/exec/up/
  logs -f 等在任何执行之前拒绝；Windows 恒 CREATE_NO_WINDOW）；
  loopback 字面 IP/GET-only/`http.client` 直连零代理面与 M14-11 同款；
  采集器部分失败 `partial=true` + 安全类别（仅类别+异常类名）→
  `overall_status=incomplete`，缺失绝不当作 healthy；阈值全部含边界且
  warn 恒可见（compose 6/6 healthy、五端点恒 200、容器 health、
  RestartCount、日志错误计数、端点延迟 warn/critical）；schema v1
  JSON+Markdown 原子写（同目录 tmp+fsync+os.replace，拒绝 symlink
  组件/越界 stem）默认落 gitignored
  `.verify/artifacts/m14-12-production-monitoring/`（`--artifact-dir`
  自定义路径为操作者显式自选覆盖，位置与 gitignore 状态由操作者负责）；
  `monitoring_ready`
  仅采集完整且零 warn/critical 时 true——**≠ production ready，本工具
  绝不宣称生产就绪**；189 项契约测试锁定上述边界（含 supervisor 评审
  R1 修正回归：非有限浮点双路径、项目名白名单与零回显、artifact-dir
  口径、状态文档措辞 sweep；邻居回归 soak 61 /
  recovery 45 / startup 66 全绿）。交付随 **PR #85** 合并 main
  （merged_at 2026-09-11T17:34:05Z，merge commit
  `52980c637f4e39fec196a6563dbab1875faa6a8f`，feature head
  `80e599d07fb448766d97a105c99d83275a50784a`，远端 feature 分支已删除；
  PR CI run `34627846208` 与合并后 main push CI run `34628419347` 均全部
  5 job（Web/API/Docker/Android/Release tools）SUCCESS——CI 真实运行
  全绿）。**supervisor 于 2026-09-11T17:39:20Z–17:39:21Z 在 canonical
  main 执行首次真实只读生产监控**（gitignored
  `monitor-20260911-173920.json`（14185B）/`.md`（3330B），不入库）：
  compose `aios-m14-03-production-rehearsal` 六服务全部 running healthy、
  restart_count 全 0，五端点 GET 全 200（延迟 ms web-root 7.088/
  web-login 12.909/api-health 12.388/funasr-health 24.444/
  cosyvoice-health 27.641），逐容器日志 error_total 全 0，
  `partial=false`，阈值计数 ok=34/warn=0/critical=0，
  `overall_status=ok`，`monitoring_ready=true`——**单次只读快照全绿
  ≠ production ready**。本条目结果部分为 supervisor 给定事实的 docs-only
  回填（本 Claude 回合未执行监控/未部署/未重启服务/未触碰计划任务/
  未改 env/未读密钥/未写生产数据）。**仅闭环单次只读监控证据——持续/
  定时采集与调度、外部告警接入、指标历史与留存、阈值随时间的标定、
  跨机监控仍属未决；`production_ready=false` 不变**；证据
  见 docs/evidence/m14-12-production-monitoring/
- M14-11 生产 soak/并发彩排 harness + 有界只读生产 soak 执行结果
  （`tools/ops/soak_rehearsal.py`，开发回合不执行生产负载）：默认 plan
  零网络；execute 五要素门禁（`--execute` + 精确确认短语 +
  duration≤120s + concurrency≤8 + 总请求≤2000，超顶 fail-closed 零请求）；
  固定目标画像（Web 3011 `/`+`/login`、API 8000 `/health`，
  `--include-voice` 才加 8010/8011 低频 GET `/health`）；
  GET-only/unauthenticated/仅字面 loopback IP（零 DNS）/`http.client`
  直连零代理面；schema 版本化 JSON+MD 报告落 gitignored
  `.verify/artifacts/m14-11-production-soak/`（含每目标计数/分位延迟/
  吞吐/安全归类错误/deadline 语义，绝无 header/body/query/凭据/env 值
  入档）；61 项契约测试锁定上述边界。交付随 **PR #83** 合并 main（merge
  `14d7e2f`、feature `bc3b5ce`）；supervisor 于 2026-09-11 获准窗口执行
  三轮有界只读生产 soak **全部零失败**（120/300/2000 请求，concurrency
  2/4/4，p99 12.929–13.362ms，吞吐 18.462–36.644 rps，零
  `completed_after_deadline`，每目标全 200）；执行前后基线逐项一致
  （compose 6/6 healthy、五端点 200、api/web 容器 ID 与 started 不变、
  voice PID 1183/2061 不变、零恢复任务触发），语音面零负载（不宣称语音
  soak 或真实用户负载）；PR/main CI（run `34612290177`/`34612382356`）
  为已知外部 0-step 形态失败。仅闭环有界本地彩排 soak 证据——>60s 长稳、
  真实客户端负载形态、跨机等仍属未决；production_ready=false 不变；
  证据见 docs/evidence/m14-11-production-soak/
- M9-01 多用户与认证基座：users 表（alembic 0022）+ bcrypt 密码哈希 + JWT（HS256）
  + register/login/me/status 端点 + 全业务路径 Bearer 门禁；
  AUTH_SECRET 未配置 = 认证关闭且 status 如实透出（存量客户端零破坏）
- M8-00 冒烟脚本与 Docker CI 门禁
- M10-01 LLM 接入：OpenAI 兼容 gateway + rubric LLM judge（fail-closed 进复核，
  默认关闭保持确定性判分；真实端点冒烟脚本 infra/smoke_llm.sh）

### Fixed
- M14-16 CosyVoice bootstrap 依赖解析降级回归（真实生产冷启动实证，本地
  commit 待 supervisor 审查发布）：最小运行时清单的 PyPI 依赖解析
  （`lightning==2.2.4` 官方 pin 链）把 torch 降级到 2.3.1 而留下预装
  torchaudio 2.11.0+cu128——cu128 轮 METADATA 不声明 torch 约束，**`pip
  check` 对该混合 ABI 报「No broken requirements found」，不能作为一致性
  依据**，导入才在 torchaudio `_extension` 崩（`OSError: ... undefined
  symbol: aoti_torch_abi_version`）。`bootstrap_cosyvoice_wsl.sh` 修法：
  ① 清单分支（最小/官方完整回退）后从同一 cu128 index 以 `--no-deps`
  显式回写已验证三件套（`torch==2.11.0+cu128` / `torchaudio==2.11.0+cu128`
  / `torchcodec==0.11.1+cu128`——pin 已满足即 no-op，不做 force-reinstall
  全量重写环境）；② 回写后、CosyVoice import 探针前新增运行期一致性探针
  （torch/torchaudio 基础版本一致 + 双 `+cu128` 同源 + torchcodec 可导入，
  fail-closed 点名失败——**pip check 在此混合 ABI 状态下不可信**，不得回退
  为依赖它做门禁）；契约测试新增顺序锁定（初始 cu128 同命令安装 → 清单
  分支 → 终局回写 → 一致性探针 → import/WAV 探针，见
  `test_bootstrap_torch_reconciliation_order_contract`），既有「torch 安装
  命令唯一」契约经 `--no-deps` 前缀区分保持指初始安装行。验证：聚焦
  `test_voice_local_scripts.py` **31 passed** + `test_voice_service_control.py`
  回归 **64 passed** + ruff + `bash -n` + `git diff --check` 全过（canonical
  venv 解释器仅执行，零 canonical 检出改动）。**本切片只修复可复现
  bootstrap 契约：未重跑 bootstrap、未改生产 venv/进程/模型缓存，不构成
  生产运行恢复宣称——受控部署验证由 supervisor 合并后执行；
  `production_ready=false` 不变**。
- M14-13 CI R2：MinIO 社区版自 2025-10 起停止分发官方 Docker 镜像
  （source-only 分发），`minio/minio:latest` 拉取失败使 CI docker job 在项目
  构建之前即挂——改为本地自建官方 pin 源码镜像：新增 `infra/minio/Dockerfile`
  （源码 = codeload 官方不可变 commit URL
  `…/tar.gz/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`（= tag
  `RELEASE.2025-10-15T17-29-55Z`）、builder
  `golang:1.24.8-alpine3.22@sha256:3d78beb1…cc0ae5`、runtime
  `alpine:3.22@sha256:14358309…5dce` 全 digest pin；`CGO_ENABLED=0` +
  kqueue/trimpath + 显式 release/commit ldflags；依赖完整性只靠源码树内官方
  go.sum（`-mod=readonly`、GOSUMDB 默认开，零 bypass）；`GOTOOLCHAIN=local`；
  全文件零 `apk add`（BusyBox wget/tar 取源）；非 root（minio 1000:1000）+
  可写 /data；runtime 只含 minio 二进制）；compose minio 切到该本地构建
  （服务名/端口/env/卷/restart 不变，镜像锚点
  `aios/minio:RELEASE.2025-10-15T17-29-55Z`），healthcheck 由 `mc ready local`
  换 BusyBox wget 探 `/minio/health/cluster`（`mc ready` 消费的同一就绪信号
  源）；api depends_on minio healthy 与六服务集合不变。21 项静态契约测试
  （`services/api/tests/test_minio_selfbuild.py`）+ 邻居 compose 契约套件零回归
  （265 passed / 6 skipped）。本回合零镜像构建/零上游工件下载/零容器操作/
  零生产触碰（镜像首次真实构建在下一次 CI），production_ready=false 不变；
  供应链 rationale/pins/limitations/长期替代见
  docs/evidence/m14-13-minio-selfbuild/README.md

### Security
- M14-10 生产 Web 安全镜像上产（M14-05 + M14-09 的生产落地，与代码/配置合并
  分开记录）：代码/配置面已先行合并——M14-05（next 16.3.4）随 **PR #76**
  （head `539dbe1`、merge `ebbb700`）、M14-09（独立 `AIOS_WEB_IMAGE_TAG` 锚点）
  随 **PR #81**（feature `321fb50`、merge `433d018`）；2026-09-11 生产 env 补
  `AIOS_WEB_IMAGE_TAG=m14-05-security`（`AIOS_IMAGE_TAG=m14-03-prod-rehearsal`
  不变，supervisor 先仓库外备份 gitignored 真实 env，仅此一次非密钥键变更），
  canonical compose `up -d --no-build --no-deps web` 单独替换 web 容器
  （m14-03-prod-rehearsal → m14-05-security，运行时自报 Next.js 16.3.4），
  api/数据面/语音引擎零触碰（api 容器 ID 与启动时间不变；FunASR/CosyVoice
  owner PID 不变、两次恢复均 untouched，未重启）；升级后 Web `/`、`/login` 与
  API/FunASR/CosyVoice `/health` 全 200、compose 6/6 healthy；恢复任务 dry-run
  与真实 `Start-ScheduledTask` 双绿（六键 pin 6/6、`up` 幂等 no-op、语音
  untouched、LastTaskResult=0）。PR #81 CI run `34582538887` 为外部 0-step
  形态失败（非代码回归，CI 未运行不隐藏）。回滚锚点：`AIOS_WEB_IMAGE_TAG`
  改回 `m14-03-prod-rehearsal` 重跑同命令。单机生产栈口径，
  production_ready=false；证据见 docs/evidence/m14-10-production-web-upgrade/
- M14-09 Web 镜像 tag 独立发布/回滚锚点：compose web 镜像改读独立
  `AIOS_WEB_IMAGE_TAG`（默认 local，不再跟随 `AIOS_IMAGE_TAG`——只设
  AIOS_IMAGE_TAG 不改变 Web tag，消除 Web-only 升级时同 tag 混用两代镜像/
  破坏 recovery pin 的隐患）；production_recovery pin 扩为六键（新增
  AIOS_WEB_IMAGE_TAG，web 容器镜像独立在线事实，仍键名-only 不回显值）；
  build_release_candidate 同 tag 显式双变量（发布包语义不变）；env 模板与
  文档同步。**不改变当前生产容器**；合并后真实 recovery env 须补
  AIOS_WEB_IMAGE_TAG 键（否则 enforce 按缺必需键可见拒绝）。
- M14-05 Web 生产依赖安全修复：next 15.5.24 → 16.3.4（连带 eslint-config-next
  16.3.4），消除 next 内嵌 postcss@8.4.31 的 1 high（≤8.5.22 系列 GHSA：XSS/
  sourceMappingURL 任意文件读取/路径穿越）+ next 自身 1 moderate；
  `npm audit --omit=dev --registry=https://registry.npmjs.org` 归零，
  无 overrides/忽略脚本/手工篡改 lock；eslint.config.mjs 迁移 flat config、
  react-hooks v7 新诊断降 warn（业务组件零改动）；本地验证清单新增依赖安全
  门禁命令，证据见 docs/evidence/m14-05-web-security/；
  2026-09-11 rebase 至 main@cae7aa0 后全门禁复验通过（audit 0 漏洞 /
  install 无锁漂移 / lint / typecheck / build 全 exit 0，next 16.3.4 +
  嵌套 postcss 8.5.23 复核，standalone 产物与 Dockerfile 吻合）

## [0.1.0] - 2026-09-02

### Added — Foundation (M0)
- Production monorepo: FastAPI + Next.js + PostgreSQL + Redis + MinIO + LiveKit compose stack
- PostgreSQL repository + Alembic migrations (upgrade / downgrade roundtrip in CI)
- CI gates (GitHub Actions): web typecheck/lint/build + api ruff/pytest (real PG service container + migration roundtrip)
- Secrets & privacy config surface (.env.example, MinIO healthcheck, compose stack healthy + data survives restart)

### Added — Content (M1)
- Source Registry: seed 6-tier sources, CRUD + verify, duplicate 409, migration roundtrip
- License state machine: migration matrix, admission + storage guards (UNKNOWN/PROHIBITED fail-closed)
- Upload dedup by SHA-256 content hash, content-addressed object storage, license snapshot on upload
- Parser adapter framework + security fixes (license snapshot, chunked upload, dedup re-bind)
- Layout normalize pipeline (LaTeX symbols, tables, slides, formula blocks)
- Chunk & Evidence with page-level traceability, re-parse idempotent replacement
- Parse task queue (DB table + asyncio worker, retry/reset-restart recovery)
- Parse quality report (pages/blocks/formulas/tables/OCR/anomalous pages)

### Added — Exam + Grading (M2)
- QuestionSpec/PaperSpec discriminated-union schema + paper JSON import (all-or-nothing)
- ExamSession FSM (6 legal edges, 19 illegal rejections, dual-repo wiring)
- Server-authoritative timing (spoofed client time fields dropped)
- Append-only answer events (sequential + idempotent + concurrent-safe)
- Autosave & reconnect recovery (next_sequence authority)
- Idempotent submit & timeout settle (unique constraint convergence)
- Objective grader v2 (33 golden cases, rule_version provenance)
- Numeric/math grader (tolerance, same-dimension conversion, sympy whitelist, review tri-state)
- Subjective rubric pipeline (keyword-v1 judge, dual-review, evidence gate)
- Exam report aggregation (per-question four-branch scoring, concept scores, remediation)

### Added — Student Model (M3)
- LearningEvent projection from append-only events (attempt/time/concept/difficulty)
- Concept DAG versioned snapshots (publish/validation/version backtracking)
- StudentConceptState BKT-like explainable state (recompute idempotent)
- Misconception candidate → confirmed lifecycle (distinct-question evidence)
- FSRS-like review scheduler (difficulty modulated, early/late review factors)
- Daily planner (review/mistake_retry/new_learning with explainable reasons)
- Selection strategy (retry/weak/advanced, difficulty & history aware)

### Added — Voice (M4)
- LiveKit server/token boundary (exp required, student grants fixed, expiry testable)
- ASR/TTS adapter protocol + VOICE_MODE local/hybrid/cloud routing
- VoiceSession FSM (8-state matrix, barge-in never commits half answers)
- Deterministic intent parser (slots/commands/ambiguity, zero LLM)
- Answer normalizer (server re-parses transcript, event_id idempotency)
- Barge-in & playback control (pause/resume self-loops, semantic refusal matrix)
- Disconnect recovery (server state authority, committed answers preserved)
- Voice report (projects M2-11 judgment, layered playback)
- Latency tracing (7 stages, client/server source, honest absence)

### Added — Search & Governance (M5)
- Search provider abstraction (local-corpus + cloud-web, unavailable honestly disclosed)
- Query planner (slot recognition + multi-source plan/execute separation)
- SSRF/robots/rate-limit gate (private ranges, dangerous protocols, 30/min default)
- Result dedup/rank (official > oer > platform > community, reasons visible)
- Course importer (admission gate + concept extraction + human review queue)
- Paper extractor (section rules, page/score provenance, review queue)
- Course generation workflow (8-stage deterministic pipeline)
- Variant question generator (solution-preserving whitelist transforms)

### Added — Eval & Safety (M6)
- Golden grading set (28 cases, byte-identical reports, transparent mismatch)
- Voice eval set (30 cases, six categories, parser gap fixes)
- Citation eval (sampled evidence validity >= 90% gate)
- Prompt injection suite (license/time/score/voice invariants)
- Security suite (SSRF metadata/DNS-rebind, sandbox escape, injection fail-closed, secrets scan, privilege)
- Backup/restore drill (three-piece backup, manifest-verified restore, independent drill DB)
- Load & reliability (p95 <= 500ms budget, N+1 fix, crash-safe submit retry)

### Added — Release (M7)
- Production compose profile (--profile local one-command stack)
- Onboarding guide + executable walkthrough (<10min path, doc-implementation consistency guard)
- Privacy disclosure (docs/PRIVACY.md + config-item bidirectional guard)
- License report (deps/web/models/content sources/derived objects with honest UNKNOWN)
- Release checklist (nine acceptance gates, live checks fail honestly without a running API)
- Versioning & rollback (VERSION single source, /version endpoint, CHANGELOG, db-rollback CLI, AIOS_IMAGE_TAG anchor)

### Security
- sympy 字符白名单防 eval 注入；SSRF 私网/元数据端点全拒；上传内容寻址无路径成分；
  语音 event_id 跨会话隔离；判分 fail-closed 不清洗注入尾巴；.providers 视图无密钥形态
