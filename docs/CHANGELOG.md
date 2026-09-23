# Changelog

All notable changes to the AI Learning OS project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/), versions follow semver.

## [Unreleased]

### Added

- M14-116 current-main 发布证据刷新（ci-main + release-check 真实重跑 + provider-smoke 首次 stage + long-soak 只读复用 + cockpit 零 blocker 聚合）（证据刷新切片，ci-main/release-check 本轮真实重执行 + provider-smoke/long-soak 只读哈希锁定复用 + 文档，零应用代码/配置/工具改动；分支 `ops/m14-116-current-main-release-evidence`（独立 worktree，基于 main `2619ea77f3db291901e48eacdeea064b6f9b6fdb`（PR #202 merge = M14-115 合入，精确基点），单 commit，push 分支并开 PR（任务书指令，与 M14-105/M14-107 不 push 口径不同））：M14-107 证据 @ 22a9cbf 对 2619ea7 已 stale（区间多个 PR merge 含 M14-115 真实代码变更——preflight search 超时 30s 分档 + 1 契约测试）——本切片刷新代码绑定两门、**首次 stage provider-smoke 真实 pass 证据**（M14-115 supervisor 生产恢复轮 2026-09-23T18:57Z 三步冒烟聚合产物，SHA256 `899feecb…` 564 bytes 逐字节一致复用，零冒烟执行/零 provider 接触/源文件零改动）、long-soak 沿 M14-107 同款只读复用（同哈希 `d939c652…` 817 bytes，M14-106 运维轮 24h 窗口 2026-09-22T02:00:01Z→2026-09-23T02:00:01Z、97 ok/0 warn/0 critical、max gap 17.25m≤20、span 恰 1440），以 M14-91 cockpit 重新聚合；绝不手改 gate JSON、绝不合成 pass、绝不搬运哈希不符旧 JSON 冒充 current。ci-main：`gh api` 归档 head_sha=2619ea7 的 runs/jobs 原始 JSON（raw/gh-runs-2619ea7.json、raw/gh-jobs-35921247552.json），断言（push/main 唯一 run、conclusion=success、5/5 jobs 精确集合 API/Docker/Release tools/Android/Web、无重复、run_number 525）全过后按 `_eval_ci_main` 契约程序化派生 `ci-main.json`（run **35921247552**，created 2026-09-23T21:15:02Z，1193 bytes）。release-check：worktree 从零 uv venv CPython 3.12.14（59 packages）+ npm ci 411 packages，运行前复核 HEAD==2619ea7 且 porcelain 空（declared-head 诚实性前提），`release-check-isolated --json` **exit 0 / all_green=true / 10/10 pass**（generated_at 2026-09-23T21:45:36Z；api-test **pytest 4433 passed, 33 skipped, 1 warning in 269.80s**——较 M14-107 的 4233 恰 +200 = 22a9cbf..2619ea7 区间各切片新增测试累计对账，实测吻合非硬编码；migration head==0027_audit_chain/backup tables:30 files:1/voice local 17324 bytes/license/e2e 5 步 1003 ms 全过；临时 API 127.0.0.1 动态端口已收尾），原始产物逐字节复制为 canonical `release-check.json`（2491 bytes）。cockpit：docs 编辑前、HEAD 干净时重新聚合（6 个生产状态源文件 staging 前 sha256 复核与 M14-91 登记值全 MATCH 6/6：audit-chain-anchor/anchor-companion=M14-87、production-preflight/legacy-papers/draft-ownership=M14-83、backup-restore=M14-85），显式声明 current-head 与 release-check head=2619ea7。结果：**evaluator pass=9 / pending=0 / blocked=0 / missing=2 / malformed=0 / tampered=0**，**cockpit_ready=true、cockpit_blockers=[]、required_not_staged=[]、exit 0——M14-91 cockpit 落地以来首次**（M14-105 pass=7→M14-107 pass=8→本轮 pass=9，blocker 2→1→0）；ci-main/release-check stale=**current**、provider-smoke 首次 staged 即 pass（local 拓扑三槽位 pass）、long-soak staged pass；**release_ready=false、production_ready=false 不变**——not_pass_required 恰为 `release-approval`（human-only 从未发生，cockpit 按策略永不接受、不计 blocker——「无技术 blocker」与「不可放行」同时成立），not_pass_optional 为 `turn-tls`（optional 不阻断）；staged 10 文件（9 门 + anchor companion）工具内建断言 + 外部独立复核 **10/10 IDENTICAL**（清单归档 canonical staged-inventory.txt）。验证：聚焦契约八套件（cockpit 三件套 + release 三件套 + provider 三件套）**407 passed, 1 warning**（6.83s；Windows 坑如实记录：pytest --basetemp 不自动创建缺失父目录链，先 mkdir -p 再跑，否则 tmp-fixture 测试全部 setup-error）+ `ruff check services/api` 全绿（零 Python 改动基线复核）+ canonical 完整性 `tr -d '\r' < SHA256SUMS | sha256sum -c -` **18/18 OK**（SHA256SUMS CRLF 行尾，M14-50 已知形态）+ JSON 契约断言脚本 **53 项全过** + canonical 18 文件秘密扫描五类模式 **0 命中** + `git diff --check` 干净（docs-only）。证据 `docs/evidence/m14-116-current-main-release-evidence/README.md`（唯一入库证据文件；gitignored canonical `.verify/artifacts/m14-116-current-main-release-evidence/` 18 文件 + SHA256SUMS：四 gate JSON、raw gh-runs/gh-jobs、cockpit-report、staged-inventory、八份执行日志、两份空 stderr 捕获）。诚实边界：cockpit_ready=true ≠ 可发布（release-approval human-only 缺席即不放行）；provider-smoke/long-soak 均为各运维轮时点证据不承诺窗口外状态；production-state 五门为历史 canonical 快照呈现、效力判断留 supervisor；生产仍运行 m14-70 镜像，零生产触碰、不授权任何部署；代码绑定门只对 2619ea7 成立、main 再前移即再 stale。同步更新 ROADMAP（M14-116 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-116 接替顶部、M14-115 移为次席）。
- M14-112 provider-smoke 前置只读预检与失败归因面（实现/测试/文档切片，新 ops 模块 + CLI 子命令 + 契约测试 + 文档，不 push；分支 `ops/m14-112-provider-smoke-preflight`（独立 worktree，基于 main `2b81ec841a9a9f30917961a2058f0f33688cd5b4`（PR #199 merge = M14-111 合入，精确基点），单 local commit）：把 M14-104 实证的「冒烟失败先于冒烟脚本即可判定」（SearXNG 在线但上游引擎全数不可达、Ollama 端点进程缺席、注册表代理劫持 loopback 探测）固化为**可检视的只读预检面**——新模块 `services/api/app/ops/provider_smoke_preflight.py` + CLI `python -m app.ops.cli provider-smoke-preflight [--voice-mode {local,hybrid,cloud}] [--search-endpoint/--asr-endpoint/--tts-endpoint/--llm-endpoint/--llm-model] [--json]`：对三个门 provider 做有界只读 HTTP 预检（10s 固定超时、不追 redirects、GET-only）——search/SearXNG 只读 `/search?format=json` 形状判定 + `unresponsive_engines` 上游归因（新/旧自报形态都解析）；local 拓扑 ASR/TTS `/health` listener 就绪（与 smoke_local_voice 同契约）；cloud/hybrid 拓扑语音槽位**不检凭据**如实 `not_probed/external_credentials_not_inspected`；LLM/Ollama 兼容网关 `/api/ps` 模型驻留探测，清晰区分端点缺席/模型缺席/超时/HTTP 失败/响应畸形。固定词汇闭集（status ready/not_ready/not_probed、overall pass/blocked/partial、11 种 reason、6 种全部指向**外部**动作的 recommendation、确定映射）；providers 键序与语音拓扑**直接 import** release-readiness `SMOKE_PROVIDERS`/provider-smoke-evidence `VOICE_MODES` 权威常量（零复制防漂移，测试交叉锁定）；loopback 探测恒 `trust_env=False`（M14-100/106 同口径）且显式观测 ambient 代理压力（getproxies/proxy_bypass 布尔判定，**代理值绝不进入输出**——可能内嵌凭据；endpoint URL 含 userinfo（user-only 与 user:password 两形态）/query//fragment 一律**先于探测 fail-closed 拒绝**且不回显——凭据绝不进入探测请求与报告；远端自报字符串（unresponsive 引擎名/错误类、驻留模型名）进入报告前逐项剔除控制字符并截断到 128 码点，叠加名单条数上限——任意外形/体量的 provider 响应都有确定的报告体量上界（supervisor 修正 Round 1/2）。只读与诚实护栏：stdout-only 零子进程/零文件写入/零服务生命周期变更/零 secret 读取（源码级 ast 守卫测试锁定）；不生成 provider-smoke.json、不触碰任何 release gate——报告恒 `production_ready=false`/`release_readiness_evidence=false` 自声明，喂给 release-readiness provider-smoke 门评估器必 `MalformedEvidence` 拒收（测试锁定），摘要含「预检 pass 不代表 provider-smoke 已通过」固定边界；预检 pass 只代表前置可观测就绪。既有 provider-smoke 行为零改动（provider_smoke_evidence.py 与三个冒烟脚本零触碰）。验证：新契约测试 `tests/test_provider_smoke_preflight.py` **54 passed**（零网络零子进程——fake get 替身 + MockTransport + socket 拨号禁令）+ 邻域回归十一套件 **359 passed** + CLI 依赖面六套件 **234 passed, 2 skipped** + ruff（默认与 `--select F,E9`）+ py_compile + `git diff --check` 全净；并在当前 main 基点真实只读预检（修正前后各一次、归因一致；默认端点，零环境注入）：voice ready（8010/8011 双 200）、search not_ready/upstream_failure（SearXNG 200 但五引擎全 `HTTP connection error` → repair_upstream_network_externally）、llm not_ready/endpoint_absent（11434 连接被拒 → start_externally_then_rerun）、overall blocked exit 1——与 M14-104 blocker 归因一致且先于冒烟即得；全程零服务变更。**诚实边界：预检 pass != provider-smoke 通过；本切片未运行任何冒烟、未生成 provider-smoke.json、不解除任何 release gate；search/llm 的解除是外部运维动作留 supervisor 决策；`production_ready=false` 不变。**证据 `docs/evidence/m14-112-provider-smoke-preflight/README.md`（真实运行原始证据 gitignored SHA256SUMS 锚定）。同步更新 DEVELOPMENT（M14-112 章节）、ROADMAP（M14-112 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-112 接替顶部、M14-111 移为次席）。
- M14-111 监控管道四步序列自然调度验收（docs-only 证据回填，零生产触碰、零代码/测试/CI/调度配置/脚本/tracked JSON 改动，不 push；分支 `ops/m14-111-monitoring-pipeline-natural-acceptance`（独立 worktree，基于 main `196da05a6e1e3c0971c1f93c84d9bcb1d46f6e62`（PR #198 merge = M14-110 合入，精确基点），单 local commit）：把 M14-110 四步管道（monitor → history → insights → calibration）**合入后的第一个自然调度轮**回填为可审计仓库证据——计划任务 `AIOS-Monitoring-Pipeline` 于 2026-09-23T13:00:01Z–13:00:03Z 自行执行，本切片未运行任何管道/生产工具，仅对 supervisor 已验收事实在 canonical gitignored 证据目录只读复核（全部一致）后回填文档。验收事实：`schema_version=1`；`config.sequence` 精确 `[monitor, history, insights, calibration]`；`overall_status=ok`；各步 monitor ok/exit 0/1.210s、history ok/0/0.297s、insights ok/0/0.146s、calibration ok/0/0.138s；管道锁 acquired+released；calibration 命令身份精确 `['<python>','tools/ops/monitoring_threshold_calibration.py','--format','json']`（M14-110 固定形态，零校准注入）；`calibration.json` 8909 字节、SHA-256 `2b53fe9b82eea5534b44f4d31128b5c28ea78563a442bd7238da71b3d07ed9f0`、`persisted-from-stage-stdout`、ASCII-safe（零非 ASCII——M14-110 ensure_ascii 修复在真实调度链路按设计生效）——**第四步首次由真实调度轮产出**；六个被引用工件（`monitor-20260923-130001.json` 19005 B/`76aed8fe…69e4`、`history.jsonl` 755728 B/`8edcf9d4…14a2`、`history-summary.md` 1892 B/`4912daa0…8385d`、`insights.json` 12180 B/`51fb7900…d0a1`、`insights-summary.md` 4704 B/`378b57d2…9af3`、`calibration.json` 8909 B/`2b53fe9b…ed9f0`）哈希/尺寸独立重算全 MATCH（复核完成于 13:15 后续自然轮之前；固定名工件（history.jsonl/insights.*/calibration.json）为**最新成功轮产物**、后续成功轮按设计覆写——13:00 首轮产物的权威绑定 = 13:00 报告内 SHA-256/bytes，后续轮覆写致当前固定名文件哈希不同**属预期、非篡改**；后续 2026-09-23T13:15:01Z–13:15:03Z 自然轮亦已全步 ok 并触发该预期覆写）；标定窗口 scanned 500/取最新 200（ok=200/warn=0/critical=0、newest 2026-09-23T13:00:01Z），建议仅咨询性（advisory）、**零阈值变更**；13:00 轮为该目录首个四步报告（12:45 及更早均三步口径）。**诚实边界：仅证明 M14-110 合入后第一个自然四步轮端到端 ok（单轮 ≠ 长期稳定性，不证明调度连续性/重建/重置路径）；不解除 provider-smoke、release approval 或任何 release gate；`production_ready=false` 不变，不授权任何部署；M14-110 切片自身零 execute 的历史边界保持不变（自然轮互补非改写）。**验证：自然轮报告 JSON 字段逐键断言 + 六工件 SHA-256/bytes 独立重算 MATCH + ASCII-only 复核 + `git diff --check` 干净（docs-only，仅五个预期 docs 路径）。证据 `docs/evidence/m14-111-monitoring-pipeline-natural-acceptance/README.md`（唯一入库证据文件；canonical 工件 gitignored 只读核验不入库）。同步更新 ROADMAP（M14-111 状态更新）、PROJECT_STATUS 顶部任务结构（M14-111 接替顶部、M14-110 移为次席）与 tools/ops/README.md（仅把 M14-110 状态文本中「真实调度轮尚未发生」更新为自然验收事实，`production_ready=false` 保留）。
- M14-110 监控阈值标定管道集成（实现/测试/文档切片，不 push；分支 `ops/m14-110-monitoring-calibration-pipeline`（独立 worktree，基于 main `abdde29c700dd15ea5ad15a703c2009e3962d579`（PR #197 merge = M14-109 合入，精确基点），单 local commit）：把 M14-109 `monitoring_threshold_calibration.py` 集成为 `monitoring_pipeline.py` 的**显式可测试第四步**——序列升级 monitor → history → insights → calibration。**管道集成契约**：calibration 仅前三步全 ok 后运行（任一前置失败/跳过 → skipped + 固定词汇原因 `insights-status-<status>`，前置事实绝不遮蔽）；固定命令白名单第四形态 = `<python> monitoring_threshold_calibration.py --format json`（仅形态旗标——JSON 产物契约所必需；恒不带 --history/--samples/阈值参数，输入面全部经校准工具既有默认值生效；管道 CLI 结构性零校准注入面）；**唯一 stdout 捕获步**——stdout 捕获为内存产物 → JSON 产物契约校验（顶层对象且 schema_version/tool 与受支持契约**精确匹配**，管道侧常量经回归测试与 standalone 工具常量交叉 pin；malformed/版本或身份不匹配 = 可见失败 `calibration-output-not-json`，绝不静默接受）→ redact 终防线 + symlink 拒绝 + 原子写固定名 `calibration.json`（管道工件目录内；**管道独占持久化**——校准工具零文件写入契约不变，history.jsonl 绝不改动）；非零退出/超时/执行错误/写入拒绝均为可见失败，失败/跳过轮绝不写、绝不引用旧 calibration.json；超时预算 calibration **1–5s（默认 5s，supervisor Round 1 边界——不把 PT12M 用满）**：四步硬顶之和 540+120+50+5=715s < PT12M=720s 且**恒留 ≥5s** 给管道自身开销（启动/锁/证据报告/调度器余量），三步既有硬顶 710s pin 不变，默认总和 590s；报告 schema **保持 v1**（add-stage-keep-version，M14-21 加第三步同款先例——stages/config 加键扩展、既有键语义零变化）；plan 惰性不变（四步全 planned、零 Runner 构造、零 calibration 产物）。**standalone 工具唯一调整**：json 模式输出恒 **ASCII-safe**（ensure_ascii 转义——JSON 文档语义逐键等值，M14-109 既有 57 项测试零改动全过；summary 人读模式不变）——根因（supervisor 冒烟实证）：真实调度链路在 Windows ACP=cp936 且无 PYTHONUTF8 时子进程以 GBK 写含中文 JSON、管道 UTF-8 捕获必然字节损坏，ASCII-safe 使输出任意子进程编码下字节恒定（回归测试 pin 全 ASCII + 语义还原 + 无 PYTHONUTF8 真实子进程两轮字节逐位相同）。**消费面最小同步**：`pipeline_incident_review.py` stages 键集 +calibration（子集校验——旧两步/三步与新四步报告同面可解析）+ 归因循环涵盖第四步 + calibration「退出 0 失败」按 stage failure_category 原固定词汇引用。**验证**：监控家族九套件全绿（supervisor 终验命令口径）+ 真实 standalone 冒烟（supervisor 已执行：无 PYTHONUTF8、canonical history.jsonl 只读——输入 SHA-256 前后不变，输出两轮 SHA-256 相同 `FEAFBDE8…494DF`、9061 字节、零非 ASCII）+ py_compile + ruff（默认与 `--select F,E9`）+ `git diff --check` 全净。**本切片零真实管道 execute、零调度注册/改动；第四步真实调度轮尚未发生（切片时点快照；已由 M14-111 自然轮验收补齐：2026-09-23T13:00Z 首个自然四步轮全 ok）；不构成 production readiness 宣称，不解除任何 release gate；`production_ready=false` 不变。**证据 `docs/evidence/m14-110-monitoring-calibration-pipeline/README.md`。同步更新 tools/ops/README.md（管道节四步 + M14-110 状态 + M14-109 管道集成注记）、ROADMAP（M14-110 状态更新）与 PROJECT_STATUS（M14-110 接替顶部任务）。
- M14-109 监控阈值标定/评估工具（实现/文档切片，新 ops 工具 + 契约测试 + 文档，不 push；分支 `ops/m14-109-monitoring-threshold-calibration`（独立 worktree `m14-109-monitoring-threshold-calibration`，基于 main `ef95ab699f900c416850a62762521b69681a549f`（PR #196 merge = M14-108 合入，精确基点），单 local commit）：填补 monitoring 家族「阈值随时间的标定」工程缺口——把 M14-13 `monitoring_history.py` 已产出的 canonical `history.jsonl` 固化为**离线、只读、fail-closed 的监控阈值标定/评估面**——新工具 `tools/ops/monitoring_threshold_calibration.py`（CLI：`--samples` 有界样本窗口（默认 200、界 1–5000，窗口 = 最新 N 条实际观测样本 + 显式 truncated_older_count；**全文件行级校验恒先行**——窗口外交替 malformed 行仍整体拒绝，不伪造时序/不填充缺失点）、评估配置 `--latency-warn-ms`/`--latency-critical-ms`/`--log-error-warn`/`--log-error-critical`（缺省 = production_monitor 既有默认阈值 source=existing-defaults，显式提供 source=explicit；有限性/配置界/严格 warn<critical 校验）；标定面 = 五端点 latency_ms + 六服务 log_error_totals 的分位数 min/p50/p90/p95/p99/max（**nearest-rank**，rank=ceil(fraction·n)）+ 候选阈值建议（warn=窗口 p95/critical=窗口 p99，`applicable` 机械判定——严格 p95<p99 且双双落在 production_monitor 配置界内，不适用固定词汇原因 degenerate-window/below-minimum/above-maximum，建议值如实保留绝不抬升）+ 阈值评估（告警语义 `value >= warn`/`>= critical` 含边界与 production_monitor 同口径；coverage_rate/alarm_rate/critical_rate；**ok_status_conflict = 阈值评估结果与历史 overall_status==ok 样本的代理分歧**（系统自身历史聚合分类的代理口径，不是误报率/真值标注），ok_status_conflict_rate 分母 = 窗口样本总数）；配置阈值与建议阈值双评估并排 = 建议与既有阈值对比；**restart 阈值为 M14-23 增量语义，累计 restart_counts 不作标定输入——诚实排除**）；stdout-only 双 format（summary 人读 / json 单文档机器可读，边界注记双形态恒包含）；绝无原始日志/密钥/secret/env 值/绝对本机路径；任何拒绝固定词汇 + exit 2 + **零部分输出**；零墙钟（同参数两次运行 stdout 逐字节相同）。单一事实源（零平行 schema）：画像/schema 常量与行级加载复用同仓 `monitoring_history` + `monitoring_insights.parse_history_text` 已测语义（M14-108 同款），**既有阈值基准与配置界复用同仓 `production_monitor` 常量**（仅导入常量定义，从不调用其任何采集/网络/子进程面）——三既有工具零行为改动（监控家族回归 300 passed 实证）。只读纪律：零网络/零子进程/零 env 读取/零计划任务/零生产容器·DB·MinIO·secrets 接触/零服务启停/**零文件写入**（只读 Store 协议层面即无写面——exists/is_dir/is_symlink/read_bytes 四方法，只写 stdout；绝不改动/删除输入工件）；symlink 目标/现存 symlink 祖先/缺失/目录形态一律拒绝；malformed/乱序/重复/混档/超 5000 一律拒绝。新契约测试 `services/api/tests/test_monitoring_threshold_calibration.py` **57 项**（结构契约 4——源码禁止副作用 token + socket/subprocess 双阻断端到端 + 零新增文件 + ops README 文档化 + 单一事实源常量复用（含 production_monitor 阈值基准）；参数 fail-closed 17 参数化（参数拒绝路径 RealStore 零构造结构性证明）；路径防御 5（真实面 symlink + FakeStore）；行校验透传 13；标定语义 14（nearest-rank 精确值/建议规则与 applicable 三原因/含边界评估语义/默认评估=既有阈值/窗口取最新+全文件校验先行/无 ok 参考时 ok 分歧恒 0/restart 不入标定面）；输出卫生 4（summary 与 json 逐字节可复现/投毒不泄漏/边界注记双形态/拒绝零正文）），tmp_path/FakeStore 模式与既有 test_monitoring_history_query.py 风格一致。验证：聚焦契约测试 **57 passed** + 监控家族回归（calibration/history/insights/query 四套件）**357 passed** + ruff（默认规则与 `--select F,E9`，新脚本+新测试）全绿 + py_compile 通过 + 真实 CLI 端到端 smoke（canonical venv 直跑，fixture 经 producer 函数 `monitoring_history.validate_report`+`build_record` 生成 canonical 形状行——标定面与产出面同构实证，summary/json 双形态 rc=0，nearest-rank 分位数精确对账 p50=64/p95=73/p99=74、建议 warn=73/critical=74 applicable=true、既有阈值评估告警 0/20、建议阈值评估告警 2/20（ok 分歧率 0.100000——代理分歧口径，非误报率）——建议与既有阈值对比完整可见）+ `git diff --check` 干净。证据 `docs/evidence/m14-109-monitoring-threshold-calibration/README.md`。**诚实边界：这是离线标定/评估工具，不改变生产阈值、不接外部告警、不解除 provider-smoke/long-soak/release gates，不授权任何部署；本切片从未对真实生产 history.jsonl 执行过标定/评估（全部验证基于合成 fixture——真实生产延迟低于 production_monitor 配置下界时建议恒 below-minimum 如实呈现）；production_ready=false 不变。**同步更新 tools/ops/README.md（M14-109 章节）、PROJECT_STATUS 顶部任务结构（M14-109 接替顶部、M14-108 移为次席）与 ROADMAP（M14-109 状态更新）。
- M14-108 监控历史时序查询工具（实现/文档切片，新 ops 工具 + 契约测试 + 文档，不 push；分支 `ops/m14-108-monitoring-history-query`（独立 worktree `m14-108-monitoring-history-query`，基于 main `9729fa5b1b5bddba1331ef599911e9d2c832c15a`（PR #195 merge = M14-107 合入，精确基点），单 local commit）：把 M14-13 `monitoring_history.py` 已产出的 canonical `history.jsonl` 固化为**安全、离线、可测试的只读查询面**——新工具 `tools/ops/monitoring_history_query.py`（CLI 查询：`--start`/`--end` UTC 时间窗（**两端含边界**，start>end 拒绝）、`--status` 恒 ∈ {ok,warn,critical}、`--service`/`--endpoint` 维度选择（恒 ∈ 六服务/五端点画像——切片聚合与记录投影而非记录过滤，canonical 记录是完整栈样本）、`--limit` 默认 50 界 1–500（保留最新 N 条 + 显式 truncated_older_count；**聚合恒全窗口口径**，limit 只界记录列表）；stdout-only 双 format（summary 人读 / json 单文档机器可读——仅从合法历史记录派生的聚合与有界记录投影，绝无原始日志/密钥/secret/env 值/绝对路径）；有界参数全 fail-closed 且先于任何读取（参数拒绝路径 RealStore 零构造结构性证明）；任何拒绝固定词汇 + exit 2 + **零部分输出**；零墙钟（同参数两次运行 stdout 逐字节相同）。单一事实源（零平行 schema）：schema/画像常量复用同仓 `monitoring_history`，canonical 行级校验**委托** `monitoring_insights.parse_history_text` 已测语义（行 schema 严格校验/行序严格递增 (collected_at, source_stem)/单一 project/样本量界 1–5000，固定词汇拒绝原因透传）——两既有工具零行为改动（监控家族回归 300 passed 实证）。只读纪律：零网络/零子进程/零 env 读取/零计划任务/零生产容器·DB·MinIO·secrets 接触/零服务启停/**零文件写入**（只读 Store 协议层面即无写面——exists/is_dir/is_symlink/read_bytes 四方法，只写 stdout；绝不改动/删除输入工件）；symlink 目标/现存 symlink 祖先/缺失/目录形态一律拒绝。新契约测试 `services/api/tests/test_monitoring_history_query.py` **57 项**（结构契约——源码禁止副作用 token + socket/subprocess 双阻断端到端 + 零新增文件 + 常量单一事实源；参数 fail-closed 21 参数化；路径防御 6（真实面 symlink + FakeStore）；行校验透传 13；查询语义 14（happy/json 结构/含边界/组合/limit 截断/空命中合法/restart_evaluation/投毒不泄漏）；输出卫生 2（逐字节可复现/拒绝零正文）），tmp_path/FakeStore 模式与既有 monitoring_history 测试风格一致。验证：聚焦契约测试 **57 passed** + 监控家族回归（query/history/insights 三套件）**300 passed** + ruff（新脚本+新测试）全绿 + py_compile 通过 + 真实 CLI 端到端 smoke（canonical venv 直跑，fixture 经 producer 函数 validate_report+build_record 生成——查询面与产出面同构实证，summary/json 双形态正确 rc=0）+ `git diff --check` 干净。诚实边界：**这是查询工具切片，不是生产查询服务**；本切片从未对真实生产 history.jsonl 执行过查询（全部验证基于合成 fixture，canonical `.verify` 零触碰）；不构成 provider-smoke、release-check、long-soak 或任何 release blocker 的解除；`production_ready=false` 不变，不授权任何部署。证据 `docs/evidence/m14-108-monitoring-history-query/README.md`；同步更新 tools/ops/README.md（M14-108 章节）、ROADMAP（M14-108 状态更新）、PROJECT_STATUS 顶部任务结构（M14-108 接替顶部、M14-107 移为次席）。

- M14-107 current-main 发布证据刷新（ci-main + release-check + 首次 staged long-soak + cockpit 聚合）（证据刷新切片，ci-main/release-check 真实重跑 + long-soak 只读校验复制 + 文档，零应用代码/配置/工具改动、不 push；分支 `ops/m14-107-current-main-evidence-refresh`（独立 worktree `m14-107-current-main-evidence-refresh`，基于 main `22a9cbf89472faf8cbcef3bbfc2fb2329371287e`（PR #194 merge = M14-106 合入，精确基点），单 local commit）：M14-105 证据 @ e5d5d3a 对 22a9cbf 已 stale（PR #193 证据 docs + PR #194 M14-106 **真实代码变更**——本地语音 provider 与冒烟健康探测 loopback `trust_env=False` 加固 + 新增 6 个契约测试）——本切片刷新代码绑定两门、**首次 stage supervisor 已复核的 24h long-soak pass 证据**、以 M14-91 cockpit 重新聚合；绝不搬运旧 JSON、绝不手改 gate JSON、绝不合成 pass。ci-main：`gh api` 归档 head_sha=22a9cbf 的 runs 与 jobs 原始 JSON，断言（push/main 唯一 run、conclusion=success、5/5 jobs 精确集合 API/Docker/Release tools/Android/Web、无重复）全过后按 `_eval_ci_main` 契约程序化派生 `ci-main.json`（run **35811421324**/run_number 509，created 2026-09-23T02:42:25Z；断言脚本 ruff 通过）。release-check：从零 uv venv CPython 3.12.14 + api/dev requirements（`NO_PROXY='*'` 直连镜像）+ npm ci 411 packages，运行前复核 HEAD==22a9cbf 且 porcelain 空，`release-check-isolated --json` **exit 0 / all_green=true / 10/10 pass**（generated_at 2026-09-23T03:04:52Z；api-test **pytest 4233 passed, 33 skipped, 1 warning in 296.02s**——较 M14-105 的 4227 恰 +6 = M14-106 新增 6 个测试的预期对账，实测吻合非硬编码；migration/backup tables:30 files:1/voice local 17324 bytes/license/e2e 全过），原始产物逐字节复制为 canonical `release-check.json`（byte-identical=True）。long-soak：**不重跑、零生产/计划任务接触**——只读校验主仓 `.verify/tmp/m14-106-supervisor-long-soak-rerun/evidence/long-soak.json` 源 SHA256 = `D939C6528B9FE739270FE6EA706837FFA9DC13115665DC90740E6DB53A1056EC`（与任务指定值一致）后逐字节复制为本切片 canonical（817 bytes 同哈希 + JSON 契约断言：audit_schema_version=2、策略 1440/15/20/500、classification=pass）；窗口事实：**24h 2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z，97 ok / 0 warn / 0 critical，max gap 17.25m ≤ 20**。evidence-cockpit：tracked docs 编辑前、HEAD 干净时重新聚合（6 个生产状态源文件 staging 前 sha256 复核与 M14-91 登记值全 MATCH 6/6：audit-chain-anchor/anchor-companion=M14-87、production-preflight/legacy-papers/draft-ownership=M14-83、backup-restore=M14-85），显式声明 current-head 与 release-check head=22a9cbf；**provider-smoke 不 stage**（本基点最新真实执行即 M14-104 的 search/llm 失败轮，aggregate 未运行、无 provider-smoke.json，required 未 stage 按收紧语义如实阻断，不搬运 M14-88/M14-98 旧 JSON 冒充 current）。结果：evaluator **pass=8 / pending=0 / blocked=0 / missing=3 / malformed=0 / tampered=0**，**cockpit_blockers = `provider-smoke:not-staged-required`（唯一，较 M14-105 双 blocker 收窄——long-soak 缺口已由 supervisor 复核的 pass 证据闭合）**；ci-main/release-check stale=**current**、long-soak staged pass（真实 24h 稳定窗口经 `_eval_long_soak` 全语义校验）；release_ready=false、production_ready=false、cockpit_ready=false、exit 1（诚实预期，不强制 0）；staged 9 文件（8 门 + anchor companion）工具内建断言 + 外部独立复核 **9/9 IDENTICAL**（清单归档 canonical staged-inventory.txt）。验证：cockpit 三件套 **145 passed**（2.17s）+ release-check 三件套 **137 passed, 1 warning**（2.86s）+ `ruff check`（4 个断言/复核脚本 + services/api 全量）全绿 + canonical 8 份 JSON `json.load` 通过 + SHA256 复核（release-check canonical==隔离原始、long-soak canonical==M14-106 源）+ `git diff --check` 干净（docs-only）。证据 `docs/evidence/m14-107-current-main-release-evidence/README.md`（唯一入库证据文件；gitignored canonical `.verify/artifacts/m14-107-current-main-release-evidence/` 13 文件 + SHA256SUMS：三 gate JSON、raw gh-runs/gh-jobs、cockpit-report、staged-inventory、执行日志）。诚实边界：long-soak pass 是 M14-106 运维轮时点证据（窗口外状态不承诺）；provider-smoke 解除（出站网络 + Ollama 在场 + GPU 空闲窗口重跑 + aggregate）是后续运维动作留 supervisor 决策；release-approval human-only 从未发生；production-state 五门为历史 canonical 快照呈现、效力判断留 supervisor；生产仍运行 m14-70 镜像，零生产触碰、不授权任何部署。同步更新 ROADMAP（M14-107 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-107 接替顶部、M14-105 移为次席）。
- M14-105 current-main 代码绑定门证据刷新 + evidence-cockpit 聚合（证据刷新切片，真实重跑 + 文档，零应用代码/配置/工具改动、不 push；分支 `ops/m14-105-current-main-release-evidence`（独立 worktree `m14-105-current-main-release-evidence`，基于 main `e5d5d3a9580d355458a8159e5fa936d30e0b2a73`（PR #192 merge = M14-104 合入，精确基点），单 local commit）：M14-97 证据 @ b374aa5 对 e5d5d3a 已 stale（b374aa5..e5d5d3a 9 个 PR merge、32 文件 +5049/−80，services/api 测试集大量真实新增）——本切片对 current main 只刷新代码绑定两门并以 M14-91 cockpit 重新聚合，绝不复制 M14-97 产物（全部本轮新执行）、绝不手改 gate JSON、绝不搬运 M14-88/M14-98 旧 provider-smoke JSON 冒充 current。ci-main：`gh api` 归档 head_sha=e5d5d3a 的 runs 与 jobs 原始 JSON，断言（push/main、唯一 run、conclusion=success、5/5 jobs 精确集合 API/Docker/Release tools/Android/Web、无重复）全过后按 `_eval_ci_main` 契约程序化派生 `ci-main.json`（run **35805140338**/run_number 505，created 2026-09-23T01:09:40Z；断言脚本 ruff + py_compile 通过）。release-check：从零 uv venv CPython 3.12.14 + api/dev requirements（`NO_PROXY='*'` 直连镜像）+ npm ci 411 packages，运行前复核 HEAD==e5d5d3a 且 porcelain 空，`release-check-isolated --json` **exit 0 / all_green=true / 10/10 pass**（generated_at 2026-09-23T01:32:37Z；api-test **pytest 4227 passed, 33 skipped, 1 warning in 313.06s**——较 M14-97 的 4158 恰 +69 预期对账；migration/backup tables:30 files:1/voice local 17324 bytes/license/e2e 1446 ms 全过），原始产物逐字节复制为 canonical `release-check.json`（byte-identical=True）。evidence-cockpit：tracked docs 编辑前、HEAD 干净时重新聚合（6 个生产状态源文件 staging 前后两次 sha256 复核与 M14-91 登记值全 MATCH：audit-chain-anchor/anchor-companion=M14-87、production-preflight/legacy-papers/draft-ownership=M14-83、backup-restore=M14-85）；**provider-smoke 本轮不 stage**——本基点最新真实执行即 M14-104 的 search/llm 失败轮（aggregate 未运行、无 provider-smoke.json），required 门未 stage 按收紧语义如实阻断。结果：evaluator **pass=7 / missing=4 / blocked=0 / malformed=0 / tampered=0**，**cockpit_blockers = `long-soak:not-staged-required` + `provider-smoke:not-staged-required`**（较 M14-97 多出 provider-smoke——诚实语义从"呈现 M14-88 旧 pass"切换为"required 未 stage 阻断"）；ci-main/release-check stale=**current**；release_ready=false、production_ready=false、cockpit_ready=false、exit 1（诚实预期，不强制 0）；staged 8 文件工具内建断言 + 外部独立复核 **8/8 IDENTICAL**。验证：cockpit 三件套 **145 passed**（2.24s）+ release-check 三件套 **137 passed, 1 warning**（3.88s）+ `ruff check services/api` 全绿 + canonical 7 份 JSON `json.load` 通过 + `git diff --check` 干净（docs-only）。证据 `docs/evidence/m14-105-current-main-release-evidence/README.md`（唯一入库证据文件；gitignored canonical `.verify/artifacts/m14-105-current-main-release-evidence/` 13 文件 + SHA256SUMS：ci-main/release-check gate JSON、raw gh-runs/gh-jobs、cockpit-report、staged-inventory、执行日志）。零生产触碰（容器/计划任务/DB/MinIO/语音/代理/soak/审批/部署零接触；唯一网络访问 = GitHub 只读 API + 包安装）。同步更新 ROADMAP（M14-105 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-105 接替顶部、M14-104 移为次席）。
- M14-104 current-main provider-smoke 门证据刷新（证据刷新切片，真实重跑 + 诊断 + 文档，零应用代码/配置/工具改动、不 push；分支 `ops/m14-104-current-main-provider-smoke`（独立 worktree `m14-104-current-main-provider-smoke`，基于 main `aa4bb55397dba7317f632cfae485867360b85d49`（PR #191 merge = M14-99 合入，精确基点），单 local commit）：M14-98 后 main 前移六个 PR、M14-100 交付的 llm 超时/预算控制（`LLM_TIMEOUT_SECONDS`/`LLM_SMOKE_MAX_TOKENS` + gateway loopback `trust_env=False`）尚未经任何 live 重跑验证——本切片在精确基点从零 worktree venv（uv Python 3.12.14）以仓库既有 `provider-smoke-export` 按 M14-98 同拓扑真实重跑三件套（绝不复用旧 JSON、绝不手改 gate JSON）。结果（窗口 2026-09-23T00:37:12Z–00:49:05Z）：search **fail**（exit 1/5407ms/0 条结果——SearXNG 127.0.0.1:8878 自身 200 但五上游引擎 brave/duckduckgo/google cse/wikidata/wikipedia 全 "HTTP connection error"，宿主直连 google 超时/baidu 200 = 境外网络不可达、出站代理未运行且约束禁启，确定性复现故不重试）；local-voice attempt1 **fail**（4376ms，两 `/health` 报不可达而 curl 同时 200——新发现：Windows 注册表系统代理 `127.0.0.1:7892`（未运行）经 `urllib.getproxies()` 回退劫持 httpx `trust_env` 回环调用，httpx 默认 3/3 ConnectError 10061 vs `trust_env=False` 3/3 200，python 环境变量零代理项）→ attempt2 **pass**（exit 0/29284ms，ASR local-funasr 8926ms/42 字节真实中文转写、TTS local-cosyvoice 19769ms/226604 字节 RIFF WAV；仅以 `NO_PROXY=127.0.0.1,localhost` 调用环境绕过——M14-66/M14-100 既有控制类，理由先录后跑，零代码/服务/判定变更，M14-98 同步为 97934ms）；llm 单次真实尝试 **fail**（exit 1/2575ms `LLM 端点不可用: LLM 传输失败: ConnectError`——Ollama 11434 无监听无进程、服务未运行；M14-100 控制显式套用并回显 `LLM_TIMEOUT_SECONDS=300`/`LLM_SMOKE_MAX_TOKENS=256` 但端点缺席未获实测；重试前置条件"只读核验模型已驻留"无法满足（/api/ps 不可达）故无第二次尝试，启动 Ollama 为安全约束所禁；GPU 100%/15837/16303 MiB 争抢背景如实记录）。**aggregate 未运行、无 provider-smoke.json**（任一步失败即停止）；`release_ready=false`/`production_ready=false` 不变。验证：聚焦契约六件套（provider_smoke_evidence/release_readiness/smoke_llm/smoke_search/smoke_voice_local/provision_ollama_model script）**226 passed**（5.14s）+ ruff（services/api）全绿 + `git diff --check` 干净。证据 `docs/evidence/m14-104-current-main-provider-smoke/README.md`（唯一入库证据文件；gitignored 原始证据 28 文件 + SHA256SUMS，含 attempt1 失败留档、重试理由、注册表代理诊断与运维注记——首轮 `../` 相对输出误落 `services/.verify/`，三 gate JSON 以 mv 字节原样迁回、零内容编辑）。同步更新 ROADMAP（M14-104 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-104 接替顶部、M14-99 降级为前一任务）。
- M14-103 CI-only Compose 断言回归修复（test-only，零生产/compose/runner 改动、不 push；分支 `fix/m14-103-ci-smoke-tag-assertion`（独立 worktree `m14-103-ci-smoke-tag-assertion`，基于 main `0c750aa9eb6a87c92606867d8bd1b142cbb62976`（PR #189 merge = M14-102 合入，精确基点），单 local commit）：GitHub main API job `106986557842` 失败——`services/api/tests/test_rc_smoke_rehearsal.py::test_smoke_compose_render_fails_closed_without_tag_env` 断言 stderr 必含 `AIOS_RC_SMOKE_API_TAG`，但 CI 的 Docker Compose（2.337 时代）首个报错缺失变量为 `AIOS_RC_SMOKE_WEB_TAG`（本机 Compose v5.5.0 实证两变量都报、API 在前——本地恒过，故 CI-only）。fail-closed 行为本身正确（非零退出码成立），测试过度耦合 compose 报错顺序。窄修复：`returncode != 0` 断言原样保留（不弱化），stderr 断言放宽为「必点名 `AIOS_RC_SMOKE_API_TAG` 或 `AIOS_RC_SMOKE_WEB_TAG` 至少其一」。验证：焦点测试 1 passed + 全文件 `test_rc_smoke_rehearsal.py` **59 passed**（3.58s）+ ruff（test 文件）全绿 + py_compile + `git diff --check` 干净。同步更新 ROADMAP（M14-103 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-103 接替顶部、M14-102 移为次席）。
- M14-102 监控自然窗口证据回填（docs-only，零生产触碰、零代码/测试/工作流改动、不 push；分支 `docs/m14-102-monitoring-natural-evidence`（独立 worktree `m14-102-monitoring-natural-evidence`，基于 main `3bb95a17898d3e4aea71570deee9d173945b5f80`（PR #188 merge = M14-101 合入，精确基点），单 local commit）：把 M14-101 合入（2026-09-23 06:45:08 +0800 = 2026-09-22T22:45:08Z）15 分钟后计划任务 `AIOS-Monitoring-Pipeline` 的第一次自然调度运行（2026-09-22T23:00:01Z，本地 07:00:01 GMT+8，PT15M :00 边界）回填为可审计仓库证据——**本切片未触发任何运行**（被动观测回填：工件 mtime 2026-09-23 07:00:04 +0800 与报告 ended_at 精确一致，由该轮自身写出；未运行/未查询/未修改计划任务）。canonical 工件（gitignored，只读核验不入库）`.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260922-230004.json`：**5871 bytes / SHA-256 `F913CEE7078DC308F7E77DB8954011FC865B7C94C3933981B107DFDCE01BFC04`**（wc -c + sha256sum 与 Python hashlib 双轨复核一致）；`overall_status=ok`，monitor ok/exit 0/**1.375s**（预算 480s）、history ok/exit 0/**1.263s**（预算 90s，未超时）、insights ok/exit 0/**0.208s**（预算 15s），lock acquired+released（23:00:01Z），全部步骤产物带 SHA-256 引用。接续判定：history 步回到 M14-79 正常轮量级（0.3–7.2s），M14-101 针对的 ~90s 冷缓存超时形态未再出现；报告 boundaries 含 M14-101 同轮 provenance 边界原文（该文本只存在于 M14-101 后的 `PIPELINE_BOUNDARIES`）+ 固定名产物以 SHA-256 引用（非 stale-preexisting-not-cited）+ PR #188 合并早于运行 15 分钟——该轮运行的是合入后管道代码；超时数值未放宽（history 仍 90s）。诚实边界：单轮 ok ≠ production readiness、不授权 cutover；`release_ready=false`/`production_ready=false` 不变，发布门状态不因本切片改变（long-soak:not-staged-required 口径不变）；生产仍运行 m14-70 镜像（current main 生产切换未另行完成则不变）；M14-101 本身未运行计划任务的历史事实保持（本切片是其合入后自然窗口观测，互补非改写）。验证：工件哈希/字节独立重算 + JSON 字段逐键断言全过；`git diff --check` 干净。证据 `docs/evidence/m14-102-monitoring-natural-evidence/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-102 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-102 接替顶部、M14-101 移为次席）。
- M14-101 监控历史聚合性能修复 + 管道固定名产物同轮新鲜度（实现/文档切片，分支 `ops/m14-101-monitoring-history-performance`（独立 worktree `m14-101-monitoring-history-performance`，原基于 main `a583f918fba4ea6223b08afc275d909fefef152b`，后受控 rebase 到 main `df24125f131de64c8c8d4b5c675b228646b9ea8a`（PR #187 / M14-100 已合入；rebase 仅解决三台账 docs 冲突，代码零变更），单 local commit，不 push）：修复计划任务 `AIOS-Monitoring-Pipeline` LastTaskResult=1 背后的两处代码级根因（monitor ok、history 聚合 ~90.202s 超时；源 ~946 份 monitor JSON）——① `tools/ops/monitoring_history.py` 算法浪费消除：基线实现对每个候选文件重复 walk 同一祖先 symlink 检查链（每文件 ~17 次 stat × 946 ≈ 1.7 万次 stat 代理调用，~89% 纯冗余；冷缓存毫秒级/次即 ~90s——与生产超时定量吻合）；新增 `PathSafetyCache`（run 内单一缓存贯穿发现与写出，「已确认存在且非 symlink」的祖先不再重复 stat，逐文件祖先检查摊还 O(1)，`O(N·d)→O(N+d)`），fail-closed 语义逐项保留（缓存只记正向结论；不存在路径永不缓存；mkdir 后 TOCTOU 复查恒走无缓存完整检查；`symlink-target`/`symlink-in-path`/`symlink-source` 拒绝词汇与双层检查不变——既有 symlink 契约测试全数通过）；实测 N=946 stat 代理 17,044→~1,900（−89%）、ancestor_checks=8（与 N 无关）、N=1200 全程 0.172s（热缓存）；**零超时数值变更**（history 默认 90s 保持，修复针对根因而非放宽上限）。② `tools/ops/monitoring_pipeline.py` 固定名产物同轮新鲜度：基线实现 history/insights 步产物发现无 per-run 基线——超时/失败轮会把上轮遗留的 `history.jsonl` 等固定名文件列为该步 artifacts（带 SHA-256），报告无法证明该文件是失败轮产出（supervisor 观测确认的歧义）；修复为步骤前对每固定名采集 `(size, mtime_ns)` 指纹基线（`Fs` 注入点新增 `stat_fingerprint`），步骤后仅新建/指纹变化者带 SHA-256 引用，指纹未变者记 `stale-preexisting-not-cited`（sha=null，绝不引用为本轮产物），另有 `created-this-run`/`removed-this-run`/`symlink-not-hashed`/`baseline-unreadable-not-cited` 固定词汇——mtime 指纹可区分「重写产出逐字节相同内容」（history 零墙钟可复现，内容哈希不可区分）与「没写」；monitor 步差集基线语义不变；超时半写（已原子落盘一半）如实分列。新增确定性回归/性能契约测试 `test_m14_101_monitoring_history_performance.py` **16 passed**（1000+ 合成 monitor JSON 规模端到端/排序/malformed 拒绝/去重/冲突；N=300 vs N=900 操作计数断言 exists 调用次数完全相等且 ancestor 检查不随候选数重复；同轮 provenance 全场景含超时+预存旧产物零引用；零子进程/零网络/零 env/零墙钟）。验证：聚焦回归六件套（history/pipeline/insights/pipeline_task/m14-101/production_monitor）**652 passed**（10.15s）；ruff + py_compile + `git diff --check` 全绿。**诚实边界：真实 Windows 计划任务本切片未运行未触碰**（冷缓存推算为合成证据）；零生产触碰（容器/计划任务/DB/MinIO/语音/secrets/日志/代理零变更，canonical `.verify` 证据历史零接触）。证据 `docs/evidence/m14-101-monitoring-history-performance/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-101 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-100 降级为前一任务）。
- M14-100 本地 LLM 冒烟超时与生成预算显式化（实现/测试/文档切片，分支 `ops/m14-100-local-llm-timeout-budget`（独立 worktree `m14-100-local-llm-timeout-budget`，基于 main `a583f918fba4ea6223b08afc275d909fefef152b`（PR #186 merge = M14-98 合入，本地全 SHA 复核一致），单 local commit，不 push）：把 M14-98 实证的 llm 冒烟失败边界修复为显式、可配、离线可测的契约面——**零 live provider/GPU 探测、零生产变更**。① `LlmGateway(timeout_seconds=…)`（>0 校验、bool 拒绝、与显式 transport 注入互斥）+ `Settings.llm_timeout_seconds`（env `LLM_TIMEOUT_SECONDS`，空串归一 None = gateway 既有默认 30s，cloud 语义零漂移；docker-compose.yml/rc-smoke/.env.example 同槽位 `AIOS_LLM_TIMEOUT_SECONDS:-` 空默认接线）+ main.py/build_llm_judge 装配全透传；② `HttpxTransport` 对 loopback URL（localhost/127.0.0.0/8/::1，`ipaddress.is_loopback` 判定）构造 client 强制 `trust_env=False`——Windows 系统代理环境变量不得劫持本机 LLM 请求（M14-98 attempt1 traceback 含 httpcore http_proxy 帧的根因），云端/内网 URL 保持 httpx 默认 trust_env（部署代理配置照常生效，语义零漂移）；③ httpx `TimeoutException`/`HTTPError` 统一映射 `LlmUnavailable`（此前 ReadTimeout 裸穿透 `except LlmUnavailable`；消息只含异常类型名与超时秒数，不含 URL/headers——api_key 绝不进异常文本，redaction 测试锁定）；④ `infra/smoke_llm.sh` 简单探针有界输出预算默认 256（`LLM_SMOKE_MAX_TOKENS` 可覆写；2048 已实证在饱和 GPU 上生成 >30s（M14-98）、32 级小预算有 thinking-only 空 content 风险（M14-71 教训）——256 为未实测折中，真实重跑切片可按实测调整），rubric judge 探针不传 max_tokens 保持 gateway 生产默认 1024（冒烟不改变生产 chat 语义）；两探针透传 `LLM_TIMEOUT_SECONDS` 且构造包 `except ValueError` 干净 FAIL（bash 侧只拦非数字形态，>0 值语义由 gateway 构造校验兜底）。API 契约决策：留在 OpenAI 兼容 `/v1` + 既有 `max_tokens` 字段——预算已被本地端点实证消费（max_tokens=8 探针 200/6.4s）；未实证的 reasoning 控制字段不进 payload（M14-71 options.num_ctx「Ollama /v1 不可靠消费」教训），native `/api/chat` think 适配器留待带 live 验证的后续切片。验证：新契约套件 `services/api/tests/test_llm_gateway_timeout_budget.py` **27 passed**（超时构造校验/互斥、默认构造零参数（30s 零漂移）、显式超时传播 gateway→transport→httpx.Client、settings 空串归一与非法值拒绝、装配透传、max_tokens 预算序列化、loopback 四形态强制 trust_env=False、云端三形态保持默认、ReadTimeout/ConnectError/非 JSON/HTTP 500 → LlmUnavailable、全失败形态 api_key 不泄漏——全部 fake transport/monkeypatched httpx.Client/本地 Response，零网络零 GPU）+ 脚本契约 `test_smoke_llm_script.py` 7 passed + gateway/provider-smoke/config/compose/rc-smoke 聚焦回归全绿 + 全量离线套件 **4211 passed / 33 skipped** + ruff（services/api）全绿 + py_compile（gateway/rubric_judge/config/main + 三测试文件）+ `bash -n infra/smoke_llm.sh` + `git diff --check` 干净；M14-96 生产文件 pin（docker-compose.yml）按机制**显式更新**（新增空默认槽位，生产渲染零漂移；另两文件 pin 未动）。证据 `docs/evidence/m14-100-local-llm-timeout-budget/README.md`（设计/M14-98 事实基础/API 契约依据/测试矩阵/边界全录）。**边界：本切片未重跑任何 live LLM/provider/GPU 探测——llm provider-smoke 步在当前机器拓扑下仍为未验证状态**，真实重跑（GPU 空闲窗口 + 运维选定 `LLM_TIMEOUT_SECONDS`/`LLM_SMOKE_MAX_TOKENS`）与（若仍有界预算不足）reasoning 控制评估是后续运维/切片动作（M14-98 §4 纪律不变）。
- M14-99 Harmony 登录回归（实现/文档切片，分支 `harmony/m14-99-post-login-regression`（独立 worktree `m14-99-harmony-post-login-regression`，原基于 main `841b36f`（PR #185 merge = M14-95 合入，精确基点），后受控 rebase 到 main `0982c24`（PR #190 / M14-103 已合入；rebase 仅解决三台账 docs 冲突，代码零变更），单 local commit，不 push）：① `tools/harmony_release/auth_smoke.py` 新增 `_poll_home_zones_settled`（有界轮询布局 dump，判定 Home 六区是否加载完成，post-login 回归路径下防止持续 LOADING 状态误判）；② `tests/harmony_release/test_auth_smoke.py` 新增 6 个测试（4 个 `_poll_home_zones_settled` 场景 + 2 个默认工厂 wire mock 用例，49 → 55）。Harmony 模拟器 `127.0.0.1:5555` 真实执行：HAP 533946 bytes SHA256 `91596B12DA6871C41B8581B7B3D6D51549B352585B701DFB9C99FFA67701C21F`（未签名）；12 步 = 11 ok + 0 failure + 1 not_run（唯一 `auth_off_local`/`auth_phase_skip`）；warnings=0、request_failures=0、mutation=true、cleanup=true；launcher status=executed；宿主后端 OS 动态 loopback 端口 57250（事实，非秘密）、设备侧 `http://10.0.2.2:57250/`；首次 launcher 尝试 `server_start_failed` → 重跑成功。证据 SHA256 锚定 `auth_smoke_launcher.json` `801f62ec9e3035aadca3058a6e35d6e8b3bc33b65a3d6f53404096476902d518`（6065 bytes）与 `auth_smoke_on.json` `4ed6c7248fdb5555a6df8def1235c738269ac565dc269adcad8092d444e1ad0c`（5294 bytes）（原始证据 gitignored）。验证：`test_auth_smoke.py` **55 passed**、`test_auth_smoke_launcher.py` **20 passed**、py_compile/ruff（F,E9,W605）/`git diff --check` 全绿。诚实边界：未签名 HAP；Harmony 模拟器 `127.0.0.1:5555`（非真机、未触碰 Android 真机）；零生产容器/DB/MinIO 接触；`production_ready=false` 不变。证据 `docs/evidence/m14-99-harmony-post-login-regression/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-99 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-95 降级为前一任务）。
- M14-98 current-main provider-smoke 门证据刷新（实现/文档切片，分支 `ops/m14-98-current-provider-smoke`（独立 worktree `m14-98-current-provider-smoke`，基于 main `841b36f48378108a1dd51d39896922ab58c1eb82`（PR #185 merge = M14-95 合入，精确基点，fetch 后 origin/main 全 SHA 复核一致），单 local commit，不 push）：对 current main 重新真实执行易失 provider-smoke 三件套；绝不复用 M14-88 JSON 冒充当前、绝不手改 gate JSON、绝不合成 pass；零生产触碰（容器/计划任务/DB/MinIO/语音/secrets/代理生命周期零变更，对既有端点只读使用）。环境：worktree 从零 uv venv CPython 3.12.14 + api/dev requirements，canonical `.venv` 零触碰；执行窗口 2026-09-22T16:54:20Z–17:07:08Z（本地 2026-09-23 00:54–01:07 GMT+8），窗口内端点预检全绿（SearXNG/FunASR/CosyVoice/Ollama alias/7892/ASR 样例）。真实结果：**search PASS**（exit 0，6449ms，results=5，authorities=community——当前真实返回，非 M14-88 旧证据）；**local-voice PASS**（exit 0，97934ms，ASR local-funasr 5102ms/transcript 42 bytes，TTS local-cosyvoice 92410ms/218924 bytes RIFF WAV，`asr=PASS tts=PASS`）；**llm 两跑真实 FAIL**（exit 1，32402ms/30506ms，`httpx.ReadTimeout`）：attempt1 模型冷加载 >300s（16GB GPU 与语音/训练/VMware 进程共存争抢）+ gateway 默认 30s 超时 + 当次 shell NO_PROXY 参数展开传递异常（traceback 含代理帧，留档）；attempt2 字面量 NO_PROXY 直连（代理帧消失）且模型已驻留 VRAM 后仍超时——thinking 模型 max_tokens=2048 在 GPU 100% 争抢下生成 >30s（warm 旁证：max_tokens=8 直连 200/6.4s）。判定为**本地资源/超时边界**（非 provider 不可用、非工具/脚本缺陷、非代理阻断），未重启任何进程（服务不可用是 blocker 不是重启理由）；吞吐多档测量被中止未完成（如实记录）。**provider-smoke-aggregate 未运行**（链未全 pass 按纪律停止；无 provider-smoke.json），provider-smoke 门本轮未通过；本切片不刷新 ci-main/release-check、不重新聚合 cockpit，`release_ready=false`/`production_ready=false` 不变，发布审批 human-only。验证：聚焦契约六件套（provider_smoke_evidence/release_readiness/searxng_local_provider/smoke_search/smoke_voice_local/provision_ollama_model）**235 passed**（4.61s，全部离线不依赖 live provider）；ruff（services/api）全绿（零 Python 变更基线复核，无 py_compile 触及范围）；`git diff --check` 干净。证据 worktree `.verify/artifacts/m14-98-current-provider-smoke/` 13 文件 sha256 锚定（4 个工具生成 JSON 含 2 份 llm 失败证据 + stdout/window 留档 + 脱敏诊断/预检记录），README `docs/evidence/m14-98-current-provider-smoke/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-98 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-95 降级为前一任务）。
- M14-97 current-main 代码绑定门证据刷新 + evidence-cockpit 聚合（实现/文档切片，分支 `ops/m14-97-current-main-release-evidence`（独立 worktree `m14-97-current-main-release-evidence`，基于 main `b374aa5227aafbbbc94282fcb948dcf60223602a`（PR #183 merge = M14-96 RC 彩排冒烟合入，精确基点，fetch 后 origin/main 复核一致），单 local commit，不 push）：对 current main `b374aa5` 只刷新**代码绑定门**（ci-main/release-check）并以 M14-91 evidence-cockpit 重新聚合发布证据；沿用 M14-94/M14-92 证据契约/流程，绝不手改 gate JSON、不合成 pass、不复制 M14-94 产物（CI run/隔离环境/release-check 运行全部本轮新执行）、不搬运旧生产状态证据冒充新执行；零生产触碰（零容器/计划任务/DB/MinIO/语音/secrets/soak 历史/审批，零部署，零 VPN/代理状态操作；唯一网络访问=GitHub 只读 `gh api` + fetch（经宿主已在跑的 sing-box 回环端口以 shell 环境变量借用）+ worktree 从零包安装）。基点增量（001af38→b374aa5，4 commits/9 文件 +2847/−1）：PR #182 M14-94 证据（docs）+ PR #183 M14-96 RC 彩排冒烟（**真实代码**：rc_smoke_rehearsal.py +957/tests +1126/compose.rc-smoke.yml +154）——services/api 测试集 +59，代码绑定门必须真实重新执行。刷新（canonical `.verify/artifacts/m14-97-current-main-release-evidence/` 6 文件 sha256 锚定）：① **ci-main** `gh api`（runs?head_sha=b374aa5… + runs/35738367558/jobs，raw 归档）命中真实 push/main run **35738367558**（该 SHA 唯一 run，run_number 486；created 2026-09-22T14:09:40Z，conclusion=success，**5/5 jobs success**——API/Docker/Release tools/Android/Web，job 名集合精确断言），按 `_eval_ci_main` 契约由断言脚本（失败即非零退出不产出，ruff+py_compile 通过）从 raw 事实程序化派生（1123 bytes，未复用 M14-94 run 35719771321）；② **release-check** 从零重建环境（uv venv CPython 3.12.14 + npm ci 411 packages）新工作区跑 `release-check-isolated` full——运行前前置核验执行树 `git rev-parse HEAD`==b374aa5 且 porcelain 为空（declared-head 诚实性前提），**all_green=true 10/10 pass**（pytest **4158 passed/33 skipped** in 261.05s，较 M14-94 的 4099 恰 +59=PR #183 新增 rc-smoke 测试的预期对账；migration head==0027_audit_chain；backup/voice(17324 bytes)/license/e2e(5 步 3301 ms) 全过），产物逐字节复制改名（byte-identical=True）；③ **evidence-cockpit 聚合**（docs 编辑前、HEAD 仍为干净 b374aa5 时运行；canonical 源路径逐一取自 M14-91/M14-94 staged-inventory 登记，staging 前 7 个既有源文件 sha256 与登记值逐一 MATCH）：staged 本切片新 ci-main/release-check + 既有 M14-83/85/87/88 canonical 六门 + anchor companion，`--gate-declared-head release-check=b374aa5`（执行树核验成立）——**pass=8/pending=0/blocked=0/missing=3/malformed=0/tampered=0**，ci-main（embedded）/release-check（flag）stale=**current**，**blockers 仅剩 `long-soak:not-staged-required`**，`release_ready=false`/`production_ready=false`/cockpit_ready=false、**exit 1 如实**（不以任何方式强制 exit 0）；staged 9 文件外部复核 **9/9 IDENTICAL**。验证：cockpit 三件套 **145 passed**（3.02s）+ release-check 三件套 **137 passed, 1 warning**（3.09s）；canonical 五文件 json.load 通过 + staged/canonical 哈希一致性复核；`git diff --check` 干净（docs-only）。诚实边界：long-soak 24h 审计未发生（M14-79 权威锚最早审计 2026-09-22T15:00:01Z 未届满，cockpit 生成于 14:39:55Z 早于该时点约 20 分钟；已合入的 M14-93 runner 到期后对真实 history 的执行是后续运维动作，本切片零 soak 接触）；release-approval human-only 从未发生；production-state 六门为各切片时点快照呈现不 block（效力判断留 supervisor + 声明通道；provider-smoke 易失性如 M14-88 记录）；turn-tls optional；生产仍运行 m14-70 镜像；M14-96 RC 彩排冒烟已随基点合入（CI/release-check 覆盖其合入后状态，镜像构建/起栈结论以 M14-96 canonical 为准）；代码绑定门只对执行时点树成立、main 再前移即再 stale。证据 `docs/evidence/m14-97-current-main-release-evidence/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-97 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-96 降级为前一任务）。
- M14-96 RC 本地彩排冒烟（实现/文档切片，分支 `ops/m14-96-release-candidate-smoke`（独立 worktree `m14-96-release-candidate-smoke`，基于 main `c9de72214fbe07f41f5d6706e1c72ccb7e60cac3`（PR #182 merge = M14-94 证据合入，精确基点），单 local commit，不 push）：与运行中生产栈（`aios-m14-03-production-rehearsal`）并存的隔离 RC 彩排机制 + 一次真实执行（**结论 build-failed:api——诚实失败**）。新增 ① `infra/docker-compose.rc-smoke.yml`：任务自有 compose（name `aios-m14-96-rc-smoke`，恰五服务 pg/redis/minio/api/web，零 build 段 + 全服务 `pull_policy: never`，api/web tag 必填变量注入（缺 env 即渲染失败），数据面零宿主端口、边缘面仅 127.0.0.1:18096/13096（与生产 compose 端口全集零交集，契约测试静态断言），零 restart、卷仅项目级、api environment 逐键复制生产 compose（唯一差异 CORS 指向 13096），默认 no-op（生产 compose/RC 构建器/冒烟脚本零引用，测试断言；不用 overlay 的原因：compose ports 合并为拼接、无法摘除生产硬编码端口）；② `tools/ops/rc_smoke_rehearsal.py`：M14-93 同款单文件纯标准库 fail-closed runner（子进程/HTTP/bind/时钟注入可测）——前置校验（HEAD==--base-sha、porcelain 脏文件必须在镜像构建输入面之外、项目零残留、唯一 tag `aios/{api,web}:m14-96-rc-smoke-<HEAD>` 不存在（绝不覆盖 v0.1.0/生产 tag）、infra 镜像在库绝不 pull、端口 bind 预检、compose config 渲染精确）→ 唯一 tag 构建（Web build-arg 指向冒烟 API）→ 隔离 up（env 全量剥离宿主 AIOS_*、只注入纯 tag 后缀）→ 五服务健康轮询 → docker port loopback 独占证明 → 八项 loopback 探针（/health、version==VERSION、auth_enabled、匿名 /papers 401、匿名 /resources/upload 401（既有 require_user 契约）、Web / 与 /login、CORS 回显）→ 恒拆本项目（down --volumes）+ 残留复核 → 前后全机 ps/volume/network/compose-ls 快照等价证明（零外部漂移即失败）→ 证据 + SHA256SUMS；运行期 assert_safe_argv 白名单护栏（compose 恒 -f 冒烟文件 + -p 项目名，stop/rm/push/system 等动词拒绝）；退出码 0/1 refused/2 failed/3 用法。真实执行（2026-09-22T13:46:06–10Z，canonical gitignored `.verify/artifacts/m14-96-release-candidate-smoke/` 7 文件 + SHA256SUMS）：前置校验全绿（HEAD==c9de722、porcelain 恰 3 个 tooling 新文件且全部在镜像构建输入面外=build_inputs_clean、真实 docker compose config 渲染通过、全机 before 快照（生产 7 容器 healthy Up 33h））、构建在 `FROM python:3.12-slim` metadata 解析失败——本机 daemon registry mirror（daocloud）经静态系统代理 127.0.0.1:7892（当时无监听）不可达，且本地镜像库无 python/node 基镜像（只读核查）→ 离线构建不成立；按 supervisor 纠正边界（禁启停 VPN/代理/daemon）诚实停止，不再重试。fail-closed 按设计生效：compose 从未 up、零容器/零卷创建（复核项目 label 0）、无清理面、生产零触碰；执行暴露并已修复真实缺陷（env 注入完整 ref 造成 compose 镜像名双重前缀——修复为纯 tag 后缀 + 回归测试锁定；栈未起零影响）。零生产触碰（未停/未启/未改任何生产容器/卷/服务/计划任务/语音/代理/daemon；未读 env.production-recovery 或任何 secret；不推仓库/不打 tag/不发 Release）；`release_ready=false`/`production_ready=false` 不变，发布审批 human-only。验证：聚焦契约 **59 passed**（compose 静态 15 + 真实渲染 3 + 生产字节级 pin/语义回归 5 + runner 36）+ 邻域回归 **176 passed, 3 skipped**（release_candidate/release_check_isolated/compose_profiles/compose_restart_policy 零回归）+ `ruff check services/api tools/ops/rc_smoke_rehearsal.py` 全绿 + py_compile + `git diff --check` 干净。生产面三文件（docker-compose.yml/build_release_candidate.sh/smoke_docker.sh）LF 归一化字节级 sha256 pin 到基点 c9de722 blob（任何改动击穿 pin）。诚实边界：起栈/健康/探针/拆栈证明未发生（构建失败是前置）；运行时生产并存未被执行证明（静态契约+快照+零容器事实支撑）；VPN 事件披露（纠正前误启 vpn-manager 拉起 sing-box 7892 监听，纠正后零 VPN 操作，Stop 钩子兜底）。证据 `docs/evidence/m14-96-release-candidate-smoke/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-96 状态更新）、PROJECT_STATUS 顶部任务结构（M14-94 降级为前一任务）与 tools/ops/README.md 工具注册。
- M14-95 Harmony 真实认证冒烟（实现/文档切片，分支 `harmony/m14-95-real-auth-smoke`（独立 worktree `m14-95-harmony-real-auth-smoke`，真实执行/代码基点 = main `001af38e8786c9578076143351978b96d0332874`（PR #181 merge = M14-89 合入）；单 local commit 后受控 rebase 至 current origin/main `c0610e9e2ed13864a0bd00df2e213ab13b4830d8`（PR #184 merge = M14-97 合入）——rebase 仅解三台账 docs 冲突零代码变更，不 push）：把 M14-89 认证会话冒烟的认证面对象从 mock 服务器升级为**仓库真实 FastAPI 认证后端**——① 新模块 `services/api/app/ops/auth_smoke_server.py`（`AuthSmokeServer`：直接复用 `create_app()`（M9-01/M9-04/M9-06 门禁 + M10-03 cookie 行为）零重复造认证，隔离 SQLite 文件库不碰主库，`bind(0)` OS 分配任务自有 loopback 端口绝不写死，种子合成用户走**真实 bcrypt 登录路径**，受保护 `GET /api/v1/smoke/protected` 证明业务门禁真实生效，AUTH_SECRET 显式提供且 ≥32 字节否则拒绝启动，全输出对 secret/口令/token 脱敏，冷重启=同库+同 secret 新进程）；② 新工具 `tools/harmony_release/auth_smoke_launcher.py`（默认 **plan-only 零副作用**（不执行时 server 工厂从不调用、不触 sys.path），`--execute` 才起后端并把动态 base 注入冒烟，`finally` 只停自有后端，报告永不携带口令/令牌/secret/绝对私有路径（db_path→`<task-work-dir>`），直接脚本导入回退 + `--help` 零副作用）；③ M14-89 工具增强 `device_api_base()`（从已验证 loopback `--api-base` 动态端口派生设备侧 10.0.2.2 URL，fail-closed 拒绝一切未验证形态，Settings 输入/断言改用派生值）。轮次史（全部真实）：B3 失败——相对 db_path 在宿主种子 cwd 与 uvicorn 子进程 cwd 间漂移致登录 401（遗留 `services/api/m14-95-auth-smoke.db` gitignored 未入库）；B4 修复相对 db_path 解析到 work_dir 下；B5 复跑仍失败——work_dir 本身可为相对路径仍漂移（证据 `server_start_failed`/RuntimeError）；B6 修复——work_dir 构造时 `Path.resolve()` 绝对化 + sqlite URL 保持 `Path.as_posix()` + launcher 直接脚本导入回退 + 零副作用 `--help`；**B7 真实 Harmony 模拟器复跑通过**——exit 0，**12 步 = 11 ok + 1 not_run**（唯一 `auth_off_local`/`auth_phase_skip`），install/start/settings/home/wrong-password/login/cold-restart/logout/background/uninstall 全 ok，宿主契约 7/7 matched（**`login_200_token` 200 usable_token=True token_type_bearer=True**），warnings=0、request_failures=0、mutation/cleanup=true；后端 127.0.0.1:52439（OS 动态端口）设备侧派生 `http://10.0.2.2:52439/`；HAP 533946 bytes SHA256 `A8348FDEF303A63F95F4C380F32F016B6F6F4E047624A3FB5736BBB5893F78BB`（未签名）；证据 SHA256 锚定 `auth_smoke_launcher.json` `6E9B300FC688D7649643155ABB4ED14ADFABB8C1CB65823122572F6C34F1B166`（5658 bytes）与 `auth_smoke_on.json` `1E87FA99AAA08BFDBEF155570F9013629CAE0B1B449B0461A8B8E1E814E22818`（4573 bytes）；server log 0 字节如实记录；布局证据=摘要哈希+节点数（content_recorded=false，无截图不虚构）。边界：突变仅限目标 HAP 且收尾已卸载，Android 真机 EYFBB22923201473 未触碰，模拟器与生产服务未重启/停止/触碰；真实后端≠生产后端（loopback 一次性隔离 SQLite+合成凭据）；未签名 HAP 不构成发布授权；`production_ready=false` 不变。验证（canonical venv）：API 聚焦 **24 passed**、launcher 聚焦 **20 passed**、`test_auth_smoke.py` **49 passed**（基线 34+15 新参数化项）；py_compile/ruff（F,E9,W605）/`git diff --check` 全绿（rebase 后复跑同绿）。证据 `docs/evidence/m14-95-harmony-real-auth-smoke/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-95 状态更新）与 PROJECT_STATUS 顶部任务结构（前一任务降级）。
- M14-94 current-main 代码绑定门证据刷新 + evidence-cockpit 聚合（实现/文档切片，分支 `ops/m14-94-current-main-release-evidence`（独立 worktree `m14-94-current-main-release-evidence`，基于 main `001af38e8786c9578076143351978b96d0332874`（PR #181 merge = M14-89 auth session 合入，精确基点，fetch 后 origin/main 复核一致），单 local commit，不 push）：对 current main `001af38` 只刷新**代码绑定门**（ci-main/release-check）并以 M14-91 evidence-cockpit 重新聚合发布证据；沿用 M14-92/M14-90 证据契约/流程，绝不手改 gate JSON、不合成 pass、不复制 M14-92 产物（CI run/隔离环境/release-check 运行全部本轮新执行）、不搬运旧生产状态证据冒充新执行；零生产触碰（零容器/计划任务/DB/MinIO/语音/secrets/soak 历史/审批，零部署；唯一网络访问=GitHub 只读 `gh api` + fetch + worktree 从零包安装）。基点增量（ddec7df→001af38，6 commits/18 文件 +5529/−43）：PR #179 M14-92 证据（docs）+ PR #180 M14-93 runner（**真实代码**：long_soak_release_window.py +554/tests +686）+ PR #181 M14-89 harmony auth session（**真实代码**：AuthPane/AuthSession/TokenVault + auth_smoke.py +1925/harmony_mock +465/test_auth_smoke +520）——services/api 测试集 +45，代码绑定门必须真实重新执行。刷新（canonical `.verify/artifacts/m14-94-current-main-release-evidence/` 6 文件 sha256 锚定）：① **ci-main** `gh api`（runs?head_sha=001af38… + runs/35719771321/jobs，raw 归档）命中真实 push/main run **35719771321**（该 SHA 唯一 run，run_number 482；created 2026-09-22T11:08:11Z，conclusion=success，**5/5 jobs success**——API/Docker/Release tools/Android/Web，job 名集合精确断言），按 `_eval_ci_main` 契约由断言脚本（失败即非零退出不产出，ruff+py_compile 通过）从 raw 事实程序化派生（1120 bytes，未复用 M14-92 run 35670971994）；② **release-check** 从零重建环境（uv venv CPython 3.12.14 + npm ci 411 packages）新工作区跑 `release-check-isolated` full——运行前前置核验执行树 `git rev-parse HEAD`==001af38 且 porcelain 为空（declared-head 诚实性前提），**all_green=true 10/10 pass**（pytest **4099 passed/33 skipped** in 238.33s，较 M14-92 的 4054 恰 +45=PR #180 新增 soak-window runner 测试的预期对账；migration head==0027_audit_chain；backup/voice(17324 bytes)/license/e2e(5 步 1179 ms) 全过），产物逐字节复制改名（byte-identical=True）；③ **evidence-cockpit 聚合**（docs 编辑前、HEAD 仍为干净 001af38 时运行；canonical 源路径逐一取自 M14-91 README/staged-inventory/cockpit-smoke 登记，staging 前 7 个既有源文件 sha256 与 M14-91 登记值逐一 MATCH）：staged 本切片新 ci-main/release-check + 既有 M14-83/85/87/88 canonical 六门 + anchor companion，`--gate-declared-head release-check=001af38`（执行树核验成立）——**pass=8/pending=0/blocked=0/missing=3/malformed=0/tampered=0**，ci-main（embedded）/release-check（flag）stale=**current**，**blockers 仅剩 `long-soak:not-staged-required`**，`release_ready=false`/`production_ready=false`/cockpit_ready=false、**exit 1 如实**（不以任何方式强制 exit 0）；staged 9 文件外部复核 **9/9 IDENTICAL**。验证：cockpit 三件套 **145 passed**（1.44s）+ release-check 三件套 **137 passed, 1 warning**（3.22s）；canonical 六文件 json.load 通过 + staged/canonical 哈希一致性复核；`git diff --check` 干净（docs-only）。诚实边界：long-soak 24h 审计未发生（M14-79 权威锚最早审计 2026-09-22T15:00:01Z 未届满，cockpit 生成于 11:30Z 早于该时点；已合入的 M14-93 runner 到期后对真实 history 的执行是后续运维动作，本切片零 soak 接触）；release-approval human-only 从未发生；production-state 六门为各切片时点快照呈现不 block（效力判断留 supervisor + 声明通道；provider-smoke 易失性如 M14-88 记录）；turn-tls optional；生产仍运行 m14-70 镜像；代码绑定门只对执行时点树成立、main 再前移即再 stale。证据 `docs/evidence/m14-94-current-main-release-evidence/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-94 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-93 降级为前一任务）。
- M14-93 长稳到期审计/导出 runner（实现/文档切片，分支 `ops/m14-93-long-soak-window-runner`（独立 worktree `m14-93-long-soak-window-runner`，基于 main `0bfd560`（PR #179 merge = M14-92 证据合入，精确基点），单 local commit（含 supervisor correction R2 amend），不 push）：新工具 `tools/ops/long_soak_release_window.py` + 契约测试 `services/api/tests/test_long_soak_release_window.py`（45 项）——把 M14-79 权威锚定窗口到期后的「重跑 soak 审计 + 逐字节导出 long-soak 门证据」从人工命令拼装固化为单一 fail-closed 仓库工具：① 锚定记录严格校验（九键全集/schema_version/tool/follow_up_audit_tool 逐字匹配、window_minutes 必须等于审计策略窗口 1440、earliest_audit_collected_at 必须恰等于锚点+窗口、generated_at==锚点零墙钟产锚不变式、precondition 取值域（tail_non_ok_count==0——锚只可能产自 open 门）、boundaries 与产锚工具逐字一致——手改锚提前到期或篡改前置即拒绝）；② 到期判定零墙钟（最新历史样本对比 earliest_audit_collected_at，绝不读系统钟；未到期固定词汇 not-due 拒绝=零审计零证据，exit 1；样本早于锚点本身=history-before-anchor 拒绝）；③ 审计语义零重复实现（路径解析/逐行严格解析/全局校验/留存/分类全部直接调用既有 soak_stability_audit 函数，策略固定 release-readiness 接受口径 1440/15/20/500 且不暴露策略参数）；④ 导出纪律（pending 拒绝导出 exit 2；只有 pass/blocked 把 soak-audit-report.json **逐字节复制**为 `evidence/long-soak.json`——utf-8 往返预检 + 写后重读复核 + 双 sha256 必须相等 + 审计输入哈希后置断言，绝不改写/重序列化门证据；blocked 照常导出诚实 blocked 证据 exit 3；pass exit 0）；⑤ 输出仅安全元数据，CLI stdout 与 argparse help 同纪律（R2：help 只作通用输入描述、绝不插值 DEFAULT_* 绝对路径值，stdout 只打印文件名/状态/时间戳/固定词汇原因、绝不回显 args 路径——默认路径行为不变、默认值仍注册并被测试断言）、全新输出目录强制（已存在/symlink 拒绝）、零子进程/零网络/零计划任务/零 env 读取/零生产触碰、生成时间戳取自最新样本（同输入重跑逐字节相同）。测试锚定记录 fixture 由真实 `soak_window_gate --anchor` 在合成历史上产出；覆盖四态/not-due/篡改锚/损坏写入 read-back 拒绝/历史拒绝面/输出目录护栏/零墙钟重跑一致/隐私标记不泄漏/CLI 默认值/R2 回归（成功与拒绝路径 CLI stdout+stderr 及 parser help 逐字扫描：无盘符/绝对路径形态、无 DEFAULT_* 插值、无 REPO_ROOT）。验证：聚焦 **45 passed**（0.96s）+ 邻域（test_soak_window_gate/test_soak_stability_audit/test_release_readiness）**157 passed** 零回归（合计 202）；py_compile/ruff（工具与测试双绿）/`git diff --check` 干净。诚实边界：本 runner 不使 long-soak 通过（blocked 导出的就是 blocked 证据）；真实 24h 审计未发生（全部验证基于合成 fixture，M14-79 锚 2026-09-22T15:00:01Z 到期后的真实执行是后续运维动作）；`release_ready=false`/`production_ready=false` 不变；发布审批 human-only。证据 `docs/evidence/m14-93-long-soak-window-runner/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-93 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-92 降级为前一任务）。
- M14-92 current-main 代码绑定门证据刷新 + evidence-cockpit 聚合（实现/文档切片，分支 `ops/m14-92-current-main-code-evidence`（独立 worktree，基于 main `ddec7df463d2ae7d751e82a0b4fd319c1439c391`（PR #178 merge，精确基点，fetch 后 origin/main 复核一致），单 local commit，不 push）：对新 main 只刷新**代码绑定门**（ci-main/release-check），并以新合入的 M14-91 evidence-cockpit 首次作为 current 刷新聚合器重新汇总发布证据；绝不手改 gate JSON、不合成结果、不复制 M14-90 产物（CI run/隔离环境/release-check 运行全部本轮新执行）、不搬运旧生产状态证据冒充新执行；零生产触碰（零容器/计划任务/DB/MinIO/语音/secrets/soak/审批，零部署；唯一网络访问=GitHub 只读 `gh api` + fetch + worktree 从零包安装）。基点增量（7cca73f→ddec7df，5 commits/8 文件 +1923）：PR #177 M14-90 证据（纯 docs）+ PR #178 M14-91 cockpit（**真实运行时改动**：evidence_cockpit.py +507/cli.py +155/tests +723）。刷新（canonical `.verify/artifacts/m14-92-current-main-code-evidence/` 6 文件 sha256 锚定）：① **ci-main** `gh api`（runs?head_sha=ddec7df… + runs/35670971994/jobs，raw 归档）命中真实 push/main run **35670971994**（该 SHA 唯一 run；created 2026-09-22T00:12:27Z，conclusion=success，**5/5 jobs success**——API/Docker/Release tools/Android/Web），按 `_eval_ci_main` 契约由断言脚本（失败即非零退出不产出，ruff+py_compile 通过）从 raw 事实程序化派生，未复用 M14-90 run 35642190403；② **release-check** 从零重建环境（uv venv CPython 3.12.14 + npm ci 411 packages）新工作区跑 `release-check-isolated` full——运行前前置核验执行树 `git rev-parse HEAD`==ddec7df 且 porcelain 为空（declared-head 诚实性前提），**all_green=true 10/10 pass**（pytest **4054 passed/33 skipped** in 238.32s，较 M14-90 的 4024 恰 +30=PR #178 新增 cockpit 测试的预期对账；migration head==0027_audit_chain；backup/voice(17324 bytes)/license/e2e(5 步 1259 ms) 全过），产物逐字节复制改名（byte-identical=True）；③ **evidence-cockpit 聚合**（docs 编辑前、HEAD 仍为干净 ddec7df 时运行；canonical 源路径逐一取自 M14-91 README/staged-inventory/cockpit-smoke 登记，staging 前 7 个既有源文件 sha256 与 M14-91 登记值逐一 MATCH）：staged 本切片新 ci-main/release-check + 既有 M14-83/85/87/88 canonical 六门 + anchor companion，`--gate-declared-head release-check=ddec7df`（执行树核验成立）——**pass=8/missing=3/blocked=0/malformed=0/tampered=0**，ci-main/release-check stale=**current**（M14-91 冒烟的两项 stale blocker 消失），**blockers 仅剩 `long-soak:not-staged-required`**，`release_ready=false`/`production_ready=false`/cockpit_ready=false、**exit 1 如实**（不以任何方式强制 exit 0）；staged 9 文件外部复核 **9/9 IDENTICAL**。验证：cockpit 三件套 **145 passed**（1.59s）+ release-check 三件套 **137 passed, 1 warning**（2.64s）；canonical 六文件 json.load 通过；`git diff --check` 干净（docs-only）。诚实边界：long-soak 24h 审计未发生（M14-79 权威锚最早审计 2026-09-22T15:00:01Z 未届满，cockpit 生成于 00:3xZ 早于该时点）；release-approval human-only 从未发生；production-state 六门为各切片时点快照呈现不 block（效力判断留 supervisor + 声明通道）；turn-tls optional；生产仍运行 m14-70 镜像；M14-89 无 PR 未合入不预判；代码绑定门只对执行时点树成立、main 再前移即再 stale。证据 `docs/evidence/m14-92-current-main-code-evidence/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-92 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-91 降级为前一任务）。
- M14-89 Harmony 认证会话闭环（实现/文档切片，worktree `m14-89-harmony-auth-session`，基于 main `5ff1e68`（PR #174 merge），单 local commit，不 push）：新增认证会话 UI 冒烟工具 `tools/harmony_release/auth_smoke.py`（计划/确认两态、`--confirm-mutation` 才触设备、凭据/令牌永不入档、fail-closed 契约继承 device_smoke/backend_smoke）+ mock 认证服务 `--auth` 模式（登录/门禁/401 契约 + 契约测试 79/79）+ 应用侧 AuthPane/AuthSession/TokenVault（安全存储会话恢复/登出）。验证：android mock pytest 36 passed；全量 `tests/harmony_release` 通过；compileall/ruff（F,E9,W605）通过；release 构建 exit 0 → unsigned HAP 219684 bytes SHA256 `D1FB2C30…EFC1`；真实 auth-on 冒烟（emulator 127.0.0.1:5555 + 本机 mock 8765）两轮全绿——R13（ok=11/12）与 R14d 新 HAP 复跑（exit 0；total=12，ok=11，failure=0，not_run=1（唯一 auth_off_local/auth_phase_skip）；warnings=0；request_failures=0；mutation_performed=true；cleanup_attempted=true，HAP 哈希与 R14b 产物一致）。诚实边界：未签名 HAP（未触发 sign_hap 回退、零签名材料接触）；认证针对 mock 合成凭据（非生产认证）；production_ready=false，不声明 Harmony 生产就绪；零生产容器/DB/MinIO/语音/secret 接触。证据 docs/evidence/m14-89-harmony-auth-session/README.md（唯一入库证据文件），同步更新 ROADMAP（M14-89 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-84 降级为前一任务）。
- M14-84 Harmony 模拟器真实 API 后端冒烟（实现/文档切片，未签名、模拟器专用，分支 `harmony/m14-84-emulator-real-api-smoke`（独立 worktree，基于 main `ddcaa229d308a8e5a46e46dae3a9d7a10ac6640e`（PR #173 merge）），单 local commit，不 push）：新增设备驱动后端冒烟工具 `tools/harmony_release/backend_smoke.py`（默认 dry-run 仅宿主预检零设备触碰，`--confirm-mutation` 才触设备，`--api-base` 仅接受 loopback 起点）+ 全 fake 注入单测 `tests/harmony_release/test_backend_smoke.py` 22 项；真实 confirm 于本地模拟器 `127.0.0.1:5555` 对**本机 loopback 开发后端**（宿主 uvicorn 开发面，非生产容器栈）——**`status=ok exit=0`，7 步全部 ok**（host_preflight/install/start/settings_ui/home_view/background/uninstall，cleanup ok 已卸载），Home 断言 **`"0.1.0": 1`、`"请求失败 (HTTP 401)": 3`**（真实后端诚实答案：version 渲染 0.1.0，auth-gated 端点未认证态如实 401）；冒烟并真实抓出并修复应用侧 `AiosApi.ets getJson` 双斜杠 URL 拼接缺陷（base 尾 `/` + endpoint 头 `/` → `//health` → FastAPI 404，首次 confirm home_assertion_missed 如实失败，重建 HAP 后全绿）。验证：聚焦 22 passed（2026-09-22 复跑）、全量 `tests/harmony_release` 449 passed/1 skipped、ruff F,E9,W605 通过、`git diff --check` 干净（全量为 2026-09-21 执行窗口记录）。诚实边界：未签名 HAP（signedness_verified=false）、仅本地模拟器、仅本机开发后端——不声明 Harmony 生产就绪、不构成发布/部署授权；零生产容器/DB/MinIO/语音/secret 接触。证据 `docs/evidence/m14-84-harmony-real-api-smoke/README.md`（唯一入库证据文件，原始证据 gitignored sha256 锚定）。
- M14-86 current-main 代码绑定门证据刷新（实现/文档切片，分支 `ops/m14-86-current-main-code-evidence`（独立 worktree，基于 main `f1dfcbbbb2a64746aebd89d10ce38d4c65539e82`（PR #172 merge，精确基点）），单 local commit，不 push）：按 M14-81 精确契约/流程对新 main 基点只刷新**代码绑定门**（ci-main / release-check）并重新聚合 readiness，绝不手改 gate JSON、不合成 pass、不搬运旧生产状态证据冒充 current、不伪称 production readiness（`release_ready=false`/`production_ready=false` 全程不变）。零生产触碰（零容器/计划任务/DB/MinIO/语音/生产日志/secrets/soak 历史/审批接触，零部署；唯一网络访问=GitHub 只读 `gh api`（直连）+ worktree 从零包安装 uv/npm）。基点增量（e1f128b→f1dfcbb，7 commits/7 文件 +1127/−0）：PR #169 M14-81 证据切片 + PR #170 M14-82 harmony 冒烟 + PR #171 M14-83 生产只读证据 + PR #172 M14-85 backup-restore 演练——**全部为 docs**（台账+四份 evidence README），运行时服务面零改动，但代码绑定门证据只对执行时点的树成立，docs-only 增量同样如实重推导（不以"仅文档"为由跳过）。刷新（全部真实执行，canonical `.verify/artifacts/m14-86-current-main-code-evidence/` 5 文件 sha256 锚定）：① **ci-main** `gh api`（runs?head_sha=f1dfcbb… + runs/35632399209/jobs，raw 响应归档）命中真实 push/main run **35632399209**（created 2026-09-21T17:30:05Z，conclusion=success，**5/5 jobs success**——API/Docker/Release tools/Android/Web），按 `_eval_ci_main` 契约由断言脚本（断言失败即非零退出不产出）从 raw 事实程序化派生 ci-main.json（904 bytes）；② **release-check** 于 f1dfcbb worktree 从零环境（uv venv CPython 3.12.14 + npm ci 411 packages）跑 `app.ops.cli release-check-isolated` full（一次性 SQLite + 127.0.0.1 临时 API + 环境剥离，零生产面）——**all_green=true 10/10 pass**（generated_at 2026-09-21T17:45:54Z；pytest **4024 passed/33 skipped** in 239.41s，与 M14-81 @ e1f128b 完全一致——纯 docs 增量零测试变更的预期对账；migration current==head==0027_audit_chain；backup tables:30；voice local 17324 bytes；license；e2e 5 步 1390 ms），产物逐字节复制改名（程序化比对 byte-identical=True，2491 bytes）；③ **release-readiness** 聚合 M14-86 canonical 证据目录（只放两个真实重推导的代码绑定门）——**pass=2（ci-main/release-check）/ missing=9**（required 八项：preflight/backup-restore/audit-chain-anchor/legacy-papers/draft-ownership/long-soak/provider-smoke/release-approval；optional turn-tls），malformed=0/tampered=0，manifest 内嵌 sha256 与实际文件哈希程序化交叉核验一致，**`release_ready=false`、exit_code=1 如实**。验证：聚焦契约三件套 **137 passed, 1 warning**（2.63s，release_readiness/release_check_isolated/release_checklist）；canonical 五 JSON 解析/契约消费通过、release-check 与隔离产物逐字节一致；`git diff --check` 干净（docs-only，无 Python 改动）。诚实边界：八个生产状态门 + long-soak + release-approval 全部如实 missing（各门最新真实记录见 M14-83（@ 5829ad9，provider-smoke blocked 如实）与 M14-85（@ f47a1e4，backup-restore verified）各自 canonical，不搬运冒充 current）；M14-79 权威 soak 锚最早审计时点 2026-09-22T15:00:01Z 未届满、24h 审计未发生，long-soak 不预宣称；M14-83 发现的 search/llm provider 失败无恢复记录；生产仍运行 m14-70 镜像，不授权任何部署。证据 `docs/evidence/m14-86-current-main-code-evidence/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-86 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-85 降级为前一任务）。
- M14-85 current-main backup-restore 发布门演练（实现/文档切片，分支 `ops/m14-85-backup-restore-drill`（独立 worktree，基于 main `f47a1e460db4a6aa06a815488300a2a4af200444`（PR #171 merge，精确基点）），单 local commit，不 push）：按 M14-83 证据 README §7 精确安全计划，用仓库既有工具（M6-06 backup/restore CLI + M11-04 backup-restore-evidence + M10-11 release-readiness）执行真实生产备份→一次性隔离恢复演练→门证据导出。生产边界全程遵守：生产 DB（ai_learning_os）只读（备份反射 SELECT + audit-chain-verify 前后对照 byte-identical=零写入佐证）；恢复只落一次性隔离库 `ai_learning_os_drill_m14_85`（test_gate 白名单前缀变体；恢复前三重隔离验证=URL 白名单纯解析 + current_database() 回连 + 空库断言；guarded DROP 用后即删，pg_database 复原为演练前精确集合）；对象备份纯读（list_keys/get，恢复侧零 S3 参数零 put）；配置备份只取公开文件（compose + env.example，真实 secret 文件 env.production-recovery 零接触零回显，S3 凭据从 compose 程序化提取经 env 注入）；零容器启停/重建（演练前后 docker ps + StartedAt 七容器 ID/镜像/启动时刻逐项相同）；绝不覆盖 m14-75 既有备份（新目录 artifacts/m14-85-backup）。执行结果（全部真实，canonical `.verify/artifacts/m14-85-backup-restore/` 2 文件哈希锚定）：① 三件套备份 exit 0——30 表/总行数 32（与 m14-75 演练一致，生产数据未变）/对象 1 件（同款唯一对象）/公开配置 2 件，manifest sha256 `3678f9d5…59459e4b4c`；② 恢复演练——首次对未迁移空目标 plain restore 被 fail-closed 拒绝（BackupIntegrityError 目标库缺表，缺表检查先于任何写入、目标复核零触碰，如实记录）；正确顺序 alembic upgrade head（0027_audit_chain）→ restore exit 0 回灌 32 行；逐表 COUNT 对账 **30/30 表与 manifest 全等**；③ `backup-restore-evidence` exit 0——**verified=true、inserted_rows=32**，manifest_sha256 与备份 manifest 字节哈希绑定复核一致；④ release-readiness 聚合 M14-85 canonical 目录——**pass=1（backup-restore）+ required missing=9 + optional missing=1**，malformed=0/tampered=0，**`release_ready=false`、exit 1 如实保留**（其余门属独立切片 scope，不搬运旧 canonical 冒充 current）。验证：聚焦契约测试 **108 passed, 3 skipped**（backup_drill/backup_restore_evidence/release_readiness 三件套；3 skip=真实 PG 门控项未设 env 按设计跳过，真实 PG 全链路已由本切片在隔离库真跑）；canonical 两 JSON 解析+交叉断言通过；`git diff --check` 干净（docs-only 零代码改动）。诚实边界：证据只覆盖演练时点备份（非持续可恢复）；恢复演练只回灌 DB（对象/配置完整性由 verify_manifest 哈希覆盖，端到端对象回传未演练）；生产 PG 存在历史遗留隔离库（drill/drill_m14_75，本切片未触碰）；备份三件套在 worktree gitignored 目录（长期归档需运维决策）；生产仍运行 m14-70 镜像，不授权任何部署。证据 `docs/evidence/m14-85-backup-restore/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-85 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-83 降级为前一任务）。
- M14-83 current-main 生产只读证据刷新（实现/文档切片，分支 `ops/m14-83-production-evidence-refresh`（独立 worktree，基于 main `5829ad9c7dbdfbcf0fc71728dbc2ecec2012135d`（PR #170 merge，精确基点）），单 local commit，不 push）：对可在不改变生产状态的前提下重推导的生产状态门做 current 刷新——零容器启停/重建、零计划任务、零 Ollama/WSL/代理触碰（Ollama 发现 inactive 未代启）、零 DB 写入、零锚文件写入（verify-only）、零 secrets/env 读取（DB 连接串取自 `infra/docker-compose.yml` 第 100 行仓库公开硬编码值 + 宿主 `127.0.0.1:5433` 固定 loopback 映射，未打开 env.production-recovery，零值回显）。刷新结果（全部真实执行，canonical `.verify/artifacts/m14-83-production-read-only-evidence/` 17 文件哈希锚定）：① `production-preflight --phase post-migration`（current main 代码 + 生产 DB 只读 + canonical m14-42 锚副本 verify-only 交叉校验）**5/5 pass、0 pending/fail**（alembic current==head==0027_audit_chain、审计链 valid 0 entries、治理三项计数 0、锚 up-to-date）；② `legacy-paper-report`/`draft-owner-report` total=0 → `governance-evidence` 推导两门 **pending_count=0**（pass）；③ `audit-chain-verify` valid（0 entries/0 audit rows）+ `audit-chain-anchor --verify-only` up-to-date（written=false 零写入，raw 归档）；④ 本地拓扑 provider 冒烟如实三态——local-voice **pass**（FunASR 真实转写 2911ms + CosyVoice RIFF WAV 241,964 bytes）、search **fail**（SearXNG 存活但上游引擎 brave/duckduckgo/google/wikidata/wikipedia 全部出站连接错误，按边界不动代理/容器）、llm **fail**（127.0.0.1:11434 连接拒绝，WSL Ollama inactive）→ 聚合 provider-smoke 门 blocked；⑤ `release-readiness` 对 M14-83 canonical 目录聚合——**pass=3（preflight/legacy-papers/draft-ownership）+ blocked=1（provider-smoke）+ required missing=6（ci-main/release-check/backup-restore/audit-chain-anchor/long-soak/release-approval）**，malformed=0/tampered=0，`release_ready=false`、exit 1 如实保留；audit-chain-anchor 门 missing（verify-only 佐证在 raw，完整 worm 三块形态需运维窗口，不手工拼装 gate JSON）；preflight 证据按 M14-78 ci-main 同款程序化转录模式（tool/gate/step 头 + 与 raw 工具报告逐键断言一致）。验证：聚焦契约测试 **461 passed, 4 skipped**（八文件：readiness/governance/provider-smoke/preflight/audit-chain×2/legacy/draft）；`git diff --check` 干净（docs-only）。README 含精确 backup-restore 演练独立窗口安全计划（只读预检→备份→一次性恢复库回灌→backup_restore_evidence 导出→中止条件）。证据 `docs/evidence/m14-83-production-read-only-evidence/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-83 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-82 降级为前一任务）。
- M14-82 Harmony current-main 模拟器未签名发布链验证（verify-only 文档切片，零生产触碰、零发布链工具改动、不 push；worktree `m14-82-harmony-current-main-smoke`，分支 `harmony/m14-82-current-main-smoke`，基于 main `e1f128be80fee736d5326272bcecebc1c729f0fb`（PR #168 merge），单 local commit）：在 PR #167（M14-80 sign_hap claimed-signed fail-closed 收口）与 PR #168（M14-79 soak 恢复门）合并后，真实重跑模拟器面未签名发布链并刷新 current-main 证据。链路（全部真实执行，原始证据 gitignored `.verify/m14-82-harmony-current-main-smoke/` 16 文件 SHA256SUMS 锚定）：① 模拟器状态：`hdc list targets` 两 loopback 目标中选定 `127.0.0.1:5555`（`const.product.name=emulator`、API 24、`bootevent.boot.completed=true` + foundation/appspawn 进程佐证；`127.0.0.1:15566` 为 Kaihong BotBook VM，不在范围）；② preflight `--expect-unsigned` exit 0、`status=blocked_by_external_materials`（signing_configs_count=0、三材料 env 未设置），`--require-materials` 变体 exit 2——诚实未签名边界语义保持；③ release_build clean/assemble 双 exit 0，unsigned HAP 188,984 bytes / SHA-256 `584E5D47…B341`（与 M14-76 大小相同、内容哈希不同——HAP zip 归档时间戳已知非确定性，不声称字节级一致）；④ device_smoke 先 plan-only（5 mutation step 全 not_run）后 `--confirm-mutation` 真实冒烟——**6/6 命令过**（install / aa start / dumpLayout / file recv / force-stop / uninstall），cleanup+卸载成功，layout 落盘 53,798 bytes；⑤ device_preflight plan-only exit 0（loopback 在 check/true-device 模式被拒绝的语义保持，未跑 true-device）；⑥ pytest `tests/harmony_release` **427 passed, 1 skipped**（与 M14-80 持平）+ mock contract **54/54 passed**（installed UI 网络降级契约）。诚实边界：**未签名 / 无 AGC / 无已签名 HAP / 不声明生产就绪**（未设置任何 AIOS_HARMONY_* 材料变量、未接触签名材料）；布局漂移如实记录（82 原始节点 vs M14-76 76 attr 节点——模拟器采样时点动态内容差异，两口径均真实）；模拟器 UI 网络降级态为预期（无后端）。证据 `docs/evidence/m14-82-harmony-current-main-smoke/README.md`（唯一入库证据文件），同步更新 ROADMAP（M14-82 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-81 降级为前一任务）。
- M14-81 current-main release 证据刷新·实现/文档切片（分支 `ops/m14-81-current-main-release-evidence`（独立 worktree，基于 main `e1f128b`（PR #168 merge，精确基点）），单 local commit，不 push）：沿用 M14-78 模式只刷新**代码绑定门**并重新聚合，零生产触碰、零 secrets/soak 历史/审批接触、零部署（唯一网络访问 = GitHub 只读 `gh api` + worktree 从零包安装）。基点增量：b7db88c→e1f128b 共 10 commits/20 文件（PR #165 M14-78 证据 + PR #166 M14-79 harmony gate-drift + PR #167 M14-80 sign-claim 加固 + PR #168 M14-79 soak 恢复门——运行时服务面零改动，但代码绑定门必须真实重执行）。① **ci-main** 刷新为真实 push/main run **35589879598** @ e1f128b（created 2026-09-21T10:39:12Z，conclusion=success，**5/5 jobs**——Web/API/Docker/Android/Release tools；raw `gh api` 响应归档且断言由脚本复核 raw 数据而非仅信简报）；② **release-check** 于 e1f128b worktree 从零环境（uv venv 3.12 + npm ci）跑 `release-check-isolated`（一次性 SQLite + 127.0.0.1 临时 API + 敏感环境剥离）——**all_green 10/10**（pytest **4024 passed**/33 skipped in 228.85s，较 M14-78 的 3939 +85 恰为 M14-79 ops 面新契约测试对账；migration head 0027_audit_chain；backup/voice/license/e2e 全过），产物逐字节复制改名（sha256 复核一致）；③ **release-readiness** 聚合 M14-81 canonical 证据目录（只放两个真实重推导的代码绑定门，生产状态门不搬运防过度宣称）——**pass=2 / missing=9**（required 八项：preflight/backup-restore/audit-chain-anchor/legacy-papers/draft-ownership/long-soak/provider-smoke/release-approval；optional turn-tls），malformed=0/tampered=0，**release_ready=false、exit 1 如实**。验证：聚焦契约三件套 **137 passed**；canonical 五 JSON 解析/契约消费通过、release-check 与隔离产物逐字节一致；`git diff --check` 干净（docs-only 切片，无 Python 改动）。诚实边界：生产仍运行 m14-70 镜像；supervisor 已运行 `soak_window_gate --anchor` 完成**正式锚定**（权威锚点 2026-09-21T15:00:01Z、最早审计时点 2026-09-22T15:00:01Z、尾部 8 干净样本——锚定记录阻止重锚、后续样本不移动锚点，14:45:01Z 仅为锚定前检查显示值）——修复后干净尾部与 M14-79 预期恢复方向一致、非独立因果证明；**锚定 ≠ long-soak 通过、不授权 readiness**，24h 审计未发生故 long-soak 门仍如实 missing；`release_ready=false` / `production_ready=false` 不变。证据 `docs/evidence/m14-81-current-main-release-evidence/README.md`（唯一入库证据文件；canonical 5 文件 SHA-256+bytes 锚定于 gitignored `.verify/artifacts/m14-81-current-main-release-evidence/`），同步更新 ROADMAP（M14-81 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-78 降级为前一任务）。
- M14-79 生产 soak 恢复与发布门证据推进（分支 `ops/m14-79-soak-recovery-gates`（独立 worktree，基于 main `376c4ee`（PR #165 merge）），首个 commit + supervisor 复核后**一个 follow-up commit**，不 push）：把 2026-09-21 管道超时/恢复事件转为可审计证据并固化 soak 窗口重启门禁，零生产触碰（零容器/零计划任务/零 secrets/零审批接触）。新增 `tools/ops/pipeline_incident_review.py`——pipeline 报告 × history.jsonl 交叉只读复盘，把「pipeline exit 1」拆为三类语义：monitor 非零但有样本工件 = **状态域裁决**（skipped 是设计内门控后果）、非零且无工件 = **执行域失败**（槽位零样本）、history/insights 超时 = **执行域瞬态**（样本由后续运行增量补录，超时不改记成功）；逐运行固定词汇归因 + 事件窗口恢复归因 + 双面判定（pipeline × 状态，绝不合并遮蔽）；canonical 取证：pipeline=recovered / status=degraded（尾随 warn 未消退），exit 1 如实。新增 `tools/ops/soak_window_gate.py`——尾部 `--consecutive-ok N`（默认 8）连续 ok 且间隔 ≤ max-gap 才允许锚定全新 24h 窗口（锚点 = 最新样本，零墙钟）；warn/critical/partial 残留即 closed 并逐条列出；`--anchor` 原子写锚定记录、已存在即拒绝（防静默重锚）；canonical 取证 closed（尾部 8 行全非干净 + gap 超限）——当前拒绝开窗的原因即该清单。**follow-up（supervisor 复核整改）**：① `production_monitor.py` log-errors 根因修复——`docker logs --tail` 全尾计数无时间界致陈旧错误永久阻塞恢复（postgres tail 78 行同 6 条陈旧错误、最新 04:29:19Z，13:15/13:30 无新错误仍 warn）；改为**时间戳感知当前区间记账**：`docker logs` 恒带 `--timestamps`（只读输出旗标入白名单，布尔/带值旗标拆分校验），仅计时间戳 ≥ 基线（最新合法 prior 完整工件 started_at_utc，与 M14-23 restart 基线同源）的错误行，早于基线计入 `stale_error_lines` 显式入档，**时间戳不可解析 fail-closed 恒计入当前区间**（`unparsed_error_lines` 计数），基线缺失回退 full-tail（= 修复前保守口径）；`error_total_tail`/`latest_error_at`/`accounting` 照实入档，阈值/退出码零变更，schema 向后兼容；一并修复评审发现的 `DOCKER_LOG_TS_RE` 捕获组无 Z 却按含 Z 格式解析的实现 bug（专用 `DOCKER_LOG_TS_FORMAT`）；e2e 验收同 6 条陈旧错误在基线后 error_total=0 恢复 ok、无前缀错误行触发可见 warn、基线后新错误照常 warn（无遮蔽双向证明）。② history 步超时边界漂移（12:00 45.206s / 12:45 46.955s / 13:45 45.522s 三次刚过 45s 即杀）有依据上调 `HISTORY_TIMEOUT_DEFAULT` 45s→**90s**（≈1.9× 最坏观测、正常轮 0.3–7.2s；硬顶 120 不变；默认总和 585s < PT12M=720s，调度器内层超时仍恒先于外层时限；超时事实照常入档绝不隐藏；零计划任务改动——任务调用管道脚本、管道默认值生效；新增 pin 测试）。③ 修复 `test_pipeline_incident_review.py` 三处 ISC004 隐式拼接。测试：新增两件套 **69** + follow-up 轮聚焦回归九件套 **831 passed**（monitor 243 含时间界 15 新增 / pipeline 70 含超时 pin / pipeline_task 79 / history 119 / insights 125 / incident_review 38 / soak_gate 31 / soak_audit 51 / release_readiness 75）；ruff（含 `--select ISC` 复核）/py_compile/`git diff --check` 干净。诚实边界：锚定 ≠ soak 通过（窗口结局由 24h 后 soak_stability_audit 判定）；生产仍运行 m14-70 镜像，log-errors 修复与超时上调均为待合并代码变更（合并前 warn 尾部与超时事实不自愈）；long-soak 门仍 blocked（待尾部干净→锚定→真实 24h）；12:30 运行实况为 monitor 无工件（history 超时在 12:00/12:45/13:45 三轮，证据以工件为准）；`release_ready=false` / `production_ready=false` 不变。证据 `docs/evidence/m14-79-soak-recovery-gates/README.md`（唯一入库证据文件）。
- M14-78 current-main release 证据刷新·实现/文档切片（分支 `ops/m14-78-release-evidence-refresh`（独立 worktree，基于 main `b7db88c`（PR #164 merge，精确基点）），单 local commit，不 push）：m14-75 收口证据 14 文件逐文件 staleness 审计（代码绑定门 ci-main/release-check stale——已真实刷新；生产状态绑定门 11 项 blocked——快照仍最新但重推导需生产访问，不搬运冒充）；ci-main 刷新为真实 push/main run 35548392898 @ b7db88c（conclusion=success，5/5 jobs，raw `gh api` 响应归档）；release-check 以 `release-check-isolated` 于 b7db88c 从零环境重跑 all_green 10/10（pytest 3939 passed）；release-readiness 聚合 pass=2/missing=9、`release_ready=false`（exit 1 如实）。零生产触碰、零 secrets/soak/审批接触、零部署；聚焦契约测试 137 passed。证据 `docs/evidence/m14-78-release-evidence-refresh/README.md`（唯一入库证据文件）。
- M14-77 语音健康 sidecar 看护计划任务 readiness（分支 `ops/m14-77-voice-sidecar-resilience`（独立 worktree，基于 main@2a0e911（PR #162 merge）），单 local commit，不 push）：**韧性修复开发切片，全部加法式——零改动 production_monitor / monitoring_pipeline / voice_health_sidecar{,_control}.py / 既有 VBS 与任务契约；零生产触碰（零注册、零 wsl.exe、零引擎/sidecar 启停）**。触发事实（2026-09-21 生产）：FunASR/CosyVoice 引擎均 managed-running、health 200，但 sidecar PID 6660 于 01:00 后静默退出——15 分钟监控管道（monitor 固定 argv `--voice-health-source sidecar`，manifest 仅静态校验不核验进程存活）因 sidecar 端点不可达连续约 7 小时 monitor exit 2，history/insights 连锁 skipped，long-soak 被污染。根因缺陷＝**死亡后无自恢复**：sidecar 是生产拓扑中唯一「spawn 一次、无看护」的未托管组件，控制器 `start` 幂等自愈早已存在（stale manifest → 清理 → 全新启动）但生产中无任何周期性调用者（PID 漂移 701→6660 旁证历史死亡-重启循环；01:00 直接诱因离线不可定证，H1 WSL VM/中继回收最可能、H2 OOM、H3 信号三假设如实列举，均收敛同一修复）。交付：① `tools/voice/voice_sidecar_watchdog_task.py`（M14-14 monitoring_pipeline_task 同款纪律：GatedSchtasks 四形态结构性白名单、install/uninstall 精确确认短语 `EXECUTE VOICE SIDECAR WATCHDOG SCHEDULER CHANGE`（与 M14-14 隔离）、绝不覆盖同名、归属判定 fail-closed（归一化省略默认值条件认可）、/XML 字节形态严格四形态解码、DOCTYPE/ENTITY 解析前拒绝、generate UTF-16 with BOM + 回读复核）；② `tools/voice/run_voice_sidecar_watchdog_silent.vbs`（与 M14-14 VBS diff 结构同构：静默幂等 ensure 调既有 controller `start`——活着跳过/死了以全部既有生产保护核验重启/WSL 不可用 rc 3 可见失败，退出码原样透传）。任务参数（测试交叉 pin）：`AIOS-Voice-Sidecar-Watchdog`、TimeTrigger PT5M（死亡暴露窗收敛到一个看护周期且 < 监控管道 PT15M）> ExecutionTimeLimit PT4M > 90s 单轮 ensure 预算（与控制器常量交叉 pin）；独立任务独立时限不挤占管道 PT12M/710s 预算链；IgnoreNew + ControlLock 双重防重叠。**production_monitor 只读/fail-closed/不伪造健康语义原样保留**——sidecar 死亡轮 monitor 照样如实 exit 2，本切片只把断档从「人工介入前无限」收敛到「注册后 ≤5 分钟」。测试：新增 `test_voice_sidecar_watchdog_task.py` **77 passed**（Task XML 关键字段/预算链交叉 pin/verify 四态含归一化/decode 四形态/白名单门含 POSIX tempfile 回归/plan/generate/status 五态/install-uninstall 短语门禁与精确形态/VBS 契约/与既有任务零身份冲突/源码契约）；聚焦回归五件套 **566 passed**（watchdog 77 + sidecar 113 + pipeline task 79 + pipeline 69 + production monitor 228）；ruff/py_compile/`git diff --check` 全过。诚实边界：交付的是 readiness——真实注册（supervisor 获准窗口七步指引见证据 §5）、受控破坏-自愈演练与长期观察均未发生（看护当前不在生产运行）；`production_ready=false` 不变。证据 `docs/evidence/m14-77-voice-sidecar-watchdog/README.md`，同步更新 ROADMAP（M14-77 状态更新）、PROJECT_STATUS 顶部任务结构（M14-74 降级为前一任务）与 tools/voice/README.md（M14-77 节）。
- M14-74 provider 收口拓扑调和（分支 `ops/m14-74-provider-closure-topology`（基于 main@b05a449），单 local commit，不 push）：production-evidence-gap 的 provider-smoke 类别 steps 由演练步改为 **provider 槽位**（voice/search/llm，与 `release_readiness.SMOKE_PROVIDERS` 交叉锁定、与演练 step id 不交），类别状态 **defer 到 release-readiness 的 provider-smoke 门**（固定映射：pass/pending/blocked 同名透传、missing→not_executed、malformed/tampered→blocked；**missing/malformed/tampered/fail 保持 fail-closed**），顶层 provider_smoke 块透出权威门状态与拓扑选轨（local→local-voice-smoke、hybrid/cloud→cloud-voice-smoke，拓扑未知如实为 null）；release_closure_manifest 仍只消费 gap/readiness 产物（零语义重复，source-guard 测试实证），下一步指引按 M14-70 拓扑形态修正（`--voice`/`--voice-mode`、双语音导出轨道）并由七条扩为**八条**；cutover-rehearsal **13 步时间线不变**（新增聚合文件兼容性测试）。验证：focused **248 passed**（supervisor 复核）/ Claude focused **422 passed**；ruff/py_compile/npm typecheck/npm lint（11 既有 warning、0 error）/npm build 全过；全量 API **3859 passed/33 skipped**（3 例本地 WSL-bash 环境失败，改选 Git Bash 后 test_smoke_search_script.py 7/7 通过）。supervisor 真实形状只读验证（真实脱敏 M14-71 证据 + 真实 M14-73 soak 报告）：provider 门 pass/local、long-soak blocked（97/94/3/0）、cutover-approval not executed、closure **production_ready=false（12 blockers）**。诚实边界：`production_ready=false` 不变，不声称 release approval/生产切换。证据：`docs/evidence/m14-74-provider-closure-topology/README.md`（唯一入库证据文件，输入/输出 sha256/bytes 如实记录）。
- M14-73 long-soak 发布门（分支 `ops/m14-73-long-soak-release-gate`（基于 main@05c7aea），单 local commit，不 push）：把 M14-72 长稳审计接入 `release-readiness` 为 **fail-closed 必需门 `long-soak`**——证据文件 `long-soak.json`（= 工具报告 `soak-audit-report.json` 逐字节复制/改名，不得手改/重新序列化），门序在 draft-ownership 之后、provider-smoke 之前。工具侧 `tools/ops/soak_stability_audit.py` `AUDIT_SCHEMA_VERSION` 升 2 并新增顶层 `gate="long-soak"` 自声明（M14-72 v1 报告不再被门接受），确定性与其余语义零变更。评估器 `_eval_long_soak` 严格校验：gate 自标识、audit_schema_version=2、tool 路径、策略四值恰为 1440/15/20/retention 500、输入 SHA-256+字节数、行数不变式（analyzed+omitted==row_count、omitted==max(0,row_count-500)、selected≤analyzed）、锚点−窗口起点恰 1440 分钟、状态计数恰 ok/warn/critical 三键且非负求和==selected、non_ok∈[warn+critical, selected]；分类映射 pass→pass / pending→pending / blocked→blocked 仅在全部语义检查通过之后——语义外未知/矛盾（如 pass 带 reasons、pending 带非干净原因、blocked 声称 non-ok 但 non_ok=0、策略漂移、计数矛盾、96/97 边界不足）一律 malformed。blocked 校验为单方向蕴含（与工具 if/elif 单原因发射一致——non-ok 与 gap 同时成立时仅报 non-ok 是合法工具输出）。GATE_IDS/审批哈希绑定/`not_pass_required` 自动扩展为**十一门**（十 required + 一 optional），`release_ready` 在 long-soak 真实 pass 前恒 false；`release-closure-manifest` 下一步指引扩为七条（新增：离线跑 soak 审计后把 `soak-audit-report.json` 复制/改名为 `<evidence-dir>/long-soak.json`），manifest 保持只读聚合器零语义重复。canonical 真实历史只读干跑（主仓 M14-13 `history.jsonl`，396 行/578635 bytes/SHA-256 `65470fc6…783c`，锚点 2026-09-20T06:15:01Z）：**blocked（non-ok-status-in-window，exit 2，报告已落盘）**——窗口内 97 行 ok=94/warn=3/critical=0，span 1439.617 分钟、max-gap 15.033 分钟；干跑报告被 `_eval_long_soak` 直调消费判 **blocked**（非 malformed）——真实工具输出与门语义闭环互证；绝不伪造 pass。测试：`test_soak_stability_audit.py`（v2 gate 绑定契约）+ `test_release_readiness.py`（6b 专节：pass 门语义/release_ready 需该门、pending/blocked 各原因变体、30 例 malformed 矩阵、审批篡改 binding）+ `test_release_closure_manifest.py`（十一门口径）→ 三套件 **162 passed**；ruff/py_compile/`git diff --check` 干净。诚实边界：本切片只是把长稳审计接入发布门——canonical 真实历史仍无连续 24h 干净窗口（blocked 如实），不构成真实 24h soak 的完成，也不构成 production readiness 宣称（`production_ready=false` 不变）。证据 `docs/evidence/m14-73-long-soak-release-gate/README.md`（唯一入库证据文件，干跑输入/输出以 SHA-256+字节数锚定），同步更新 ROADMAP（M14-73 状态更新）、PROJECT_STATUS 顶部任务结构（M14-72 降级为前一任务）、DEVELOPMENT.md（release-readiness 节门矩阵/收口节步数）与 tools/ops/README.md（M14-72 节补 M14-73 门绑定）。

- M14-72 长稳审计（分支 `ops/m14-72-long-soak-audit`（基于 main@8559c24），单 local commit，不 push）：新增离线只读审计工具 `tools/ops/soak_stability_audit.py`——消费 M14-13 `history.jsonl`，判定真实连续 24h 稳定窗口：分类 pass（exit 0）/ pending（exit 1）/ blocked（exit 2），全程 fail-closed（固定词汇原因：non-ok-status-in-window / excessive-gap-in-window / insufficient-clean-coverage / insufficient-sample-count / duplicate-timestamp / non-chronological-history / conflicting-project / malformed-row / row-limit-exceeded / empty-history / history-path-missing / symlink-target 等）；schema 复用 `monitoring_history` 单一事实源（常量/时间戳解析/project 白名单/Store 协议/RealStore 原子写/symlink 拒绝；非 str 的 overall_status 先 isinstance 再成员测试——list/dict 落入受控拒绝而非 TypeError）；零子进程/零网络/零计划任务/零 env 读取/零墙钟（窗口起点与报告时间戳取自锚样本，两次运行输出逐字节相同）；输出确定性 JSON+Markdown 至 gitignored `.verify/m14-72-long-soak-audit/`，绝无原始日志行/密钥/secret/env 值/URL/token/主机标识。**数据域 blocked（warn/critical/partial 在窗口内、间隔超限）= 合法审计结论，照常写出报告后 exit 2；输入拒绝（缺失/symlink/解析/重复/非时序/项目冲突/行数/参数/写失败）= 零输出 exit 2。** 诚实边界：pass 仅源于窗口内逐样本 ok/partial=false + 覆盖自窗口起点 + 相邻间隔 ≤ max-gap + 样本数 ≥ 闭区间最小样本数（window//interval + 1，24h/15m = 97；96 行即使跨度覆盖窗口仍 pending），绝不从总历史跨度/insights 聚合/合成 soak 时长/墙钟推导；canonical 真实历史干跑 = **blocked（non-ok-status-in-window，exit 2，报告已落盘）**（输入 391 行/571084 bytes/SHA-256 `4e416bbc…b0af4`，锚点 2026-09-20T05:00:01Z，窗口内 ok=94/warn=3，最后一次 warn 后仅 465 分钟干净）——绝不宣称 24h pass；本工具只是长稳审计门禁，不构成真实 24h soak 的完成，也不构成 production readiness 宣称（`production_ready=false` 不变）。测试 `services/api/tests/test_soak_stability_audit.py` 604 行契约测试 + 既有 monitoring_history 契约全绿（169 passed），ruff/py_compile/`git diff --check` 干净。证据 `docs/evidence/m14-72-long-soak-audit/README.md`（真实干跑输入以 SHA-256+字节数锚定，输出文件哈希/字节数如实记录），同步更新 ROADMAP（M14-72 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-71 降级为前一任务）。

- M14-71 本地真实 provider 冒烟闭环（分支 `ops/m14-71-provider-smoke-local-trial`，单 local commit）：本地三槽位真实冒烟全过并产出工具生成证据——search（SearXNG 真实端点）、local-voice（FunASR ASR + CosyVoice TTS）、llm（固定别名 `aios-qwen3.5-9b-4096` 冷启动，rubric judge 结构化判定）；`provider-smoke-aggregate --voice-mode local` 全 pass，`release-readiness` 消费后 provider-smoke 门禁 = pass（其余门禁 missing、`release_ready=false` 如实）。新增 `infra/provision_ollama_model.ps1`：幂等、fail-closed 的 Ollama 固定模型别名供给脚本（aios-qwen3.5-9b-4096 = FROM qwen3.5:9b + 模型层 num_ctx 4096；qwen3:4b 显式拒绝；不隐式 pull；create 后独立 show 复核）。新增离线测试：供给脚本与 `infra/smoke_llm.sh` 文本契约（`test_provision_ollama_model_script.py` / `test_smoke_llm_script.py`）。

### Changed

- M14-71 LLM_NUM_CTX 语义纠正：Ollama /v1 实证不可靠消费顶层 `options.num_ctx`（OpenAI 规范外字段）——compose/config/gateway/rubric_judge/main/smoke 脚本注释措辞统一改为「provider 特定可选请求 Hint，兼容性不保证」，不据此声称生效；本地冒烟固定走模型层别名并保持 LLM_NUM_CTX 未设置。`infra/smoke_llm.sh` 新增 thinking 模型空 content 守卫（0 字节/thinking-only 不算通过），简单探针 max_tokens 提至 2048。`test_llm_gateway.py` 扩展：num_ctx payload 携带/省略、非法值拒绝、Settings 空白/非法归一、build_llm_judge 透传（全零网络）。证据 `docs/evidence/m14-71-provider-smoke-local-trial/README.md`（唯一入库证据文件，5 份 gitignored 源证据 sha256/bytes 锚定）。

- M14-70 production current-main 切换证据回填（docs-only，零生产触碰、零代码/测试/工作流改动、不 push、不建 PR；分支 `docs/m14-70-production-cutover` 基于 `main@976798f2567018552b2e892bc334a66098cb951a`（PR #156 merge，provider smoke voice topology 合入后 final main），单 local commit）：把 supervisor 完成的第三次生产最小化切换转为可审计仓库证据（证据 `docs/evidence/m14-70-production-cutover/README.md`，唯一入库证据文件）。切换事实：切换前后 git HEAD `976798f2` tracked-clean 双锚；构建 `aios/api:m14-70-production`（digest `sha256:9a4e4e16…9179c8`）与 `aios/web:m14-70-production`（digest `sha256:083a525c…f19006`）；compose 仅重建 api/web 两容器（api `3ddde9463a1f`→`95e45740cc08`、web `1dc9ec94435d`→`fcce394b1a56`，均 healthy），postgres/redis/livekit/minio/searxng 五容器 ID 未动且 healthy（七容器全绿）；匿名端点 6 GET 全 200 + `POST /api/v1/sources` 预期 401；真实 Chromium 双视口（desktop 1440x900 + mobile 390x844）验收 OVERALL=PASS；`production_recovery.py --dry-run` exit 0；生产监控一轮 ok=34/warn=0/critical=0；源证据 MANIFEST 35 条目独立核验 35/35 哈希/大小一致。诚实边界：不声称真实 provider 冒烟、release/cutover 审批、soak 长稳；Web 镜像内容与 M14-69 字节等价（构建区间 `apps/web` 零改动、BuildKit 全层缓存命中，sha256 差异仅 tag/label 元数据），升级点仅在 api 侧；`production_ready=false` 不变。同步更新 ROADMAP（M14-70 状态更新与 M14 开放项台账）与 PROJECT_STATUS 顶部任务结构（M14-69 降级为前一任务）。
- M14-66 本地 SearXNG provider 栈（开发切片，零真实 secret 入库、零生产触碰、不 push、不建 PR；worktree `m14-66-searxng-local-provider` 分支 `feature/m14-66-searxng-local-provider` 基于 `origin/main@13fdcbe`，单 local commit）：compose 新增 `searxng` 服务（挂独立 `search` profile——与语音 profile 相互独立，显式部署控制）——官方镜像 digest 精确 pin、仓库配置 `infra/searxng/settings.yml` 只读挂载（`use_default_settings: true` + `formats: [html, json]`——json 未启用 JSON API 即 403；`limiter/public_instance: false` 私有实例语义）、命名缓存卷、`/healthz` 健康检查、宿主暴露恒 `127.0.0.1:8878:8080`（loopback-only，本机 8080 被无关进程占用绝不映射）、`unless-stopped`；secret 注入链 `AIOS_SEARXNG_SECRET:-SEARXNG_SECRET:-dev 占位` 嵌套插值（占位在 compose 与 settings.yml 是同一字面量，跨文件漂移锁强制成对修改）；api 接线是部署侧显式注入（`AIOS_SEARCH_MODE=cloud` + `AIOS_SEARCH_CLOUD_ENDPOINT=http://searxng:8080`），fail-closed 三门判定与默认空槽位原样保留——非搜索路径零隐式出站；修复 `infra/smoke_search.sh` WSL 继承代理下 loopback 请求被发给系统代理的缺陷（对 NO_PROXY/no_proxy 双变量幂等追加回环条目，仅追加不删改、不触碰代理变量本体）。测试：聚焦六件套 96 passed 2 skipped（静态契约 16 项 + 渲染面 6 项 + 冒烟契约 3 行为 + restart/minio 扩展）+ ruff / `git diff --check` / compose config 双形态；live 验证（任务自有隔离容器，gitignored `.verify/`）：`/healthz` 200、JSON API 未 403、真实 CloudWebProvider 冒烟两段如实（直连出站 engine timeout → 宿主代理形态重建后 `results=5` PASS）+ 假代理敌意环境复跑仍 PASS（绕过修复对抗性证明），容器/卷清理零残留。**监督修正轮（出站代理显式透传产品化）**：compose 新增 `AIOS_SEARXNG_HTTP_PROXY` / `AIOS_SEARXNG_HTTPS_PROXY` / `AIOS_SEARXNG_NO_PROXY` 三槽位——默认恒空 = 直连出站（空值被 urllib getproxies 忽略，不产生代理行为）；AIOS 单链、无通用回落（宿主 shell 代理 env 绝不隐式进容器）；仅大写单形（SearXNG 出站栈 httpx 经 urllib getproxies 大小写不敏感读取即全量生效；镜像 busybox wget 只读小写故容器内回环健康检查恒不经代理）；compose 零硬编码代理地址/端口（静态 + 渲染测试双锁）；`smoke_search.sh` 回环旁路原样未改。修正轮 compose 形态 live 验证（任务自有隔离 compose 项目 `--profile search` 只拉 searxng，gitignored `.verify/m14-66-searxng-local-provider-compose-live/`，真实代理值全程未回显）：默认直连渲染 healthy@20s + 容器代理 env 全空 + 全新查询 results=19（本机直连当前可通——与初始轮相反，边界随网络姿态漂移如实记录）；显式代理注入（部署 env 注入，容器内透传且无小写镜像）healthy@20s + healthz 200 + results=20 + 真实 CloudWebProvider 冒烟全过 EXIT=0；`AIOS_SEARXNG_NO_PROXY` 槽位原样透传实证；`down -v` 清理零残留、生产容器全程未动。诚实边界：本机出站边界随系统级网络/代理姿态漂移（初始轮直连全引擎 timeout、修正轮同机直连可通）——本机部署需要显式代理设置时，具体地址/端口只放部署 env/secret，绝不入库、不回显；compose 全栈真启动冒烟是门控测试本环境跳过；`production_ready=false` 不变。证据：`docs/evidence/m14-66-searxng-local-provider/README.md`
- M14-65 云端 TTS 音色配置（开发切片，零真实 provider 调用、零生产触碰、不 push、不建 PR；worktree `m14-65-cloud-tts-voice` 分支 `feature/m14-65-cloud-tts-voice` 基于 `origin/main@cdd27ae`，单 local commit）：新增端到端 `AIOS_TTS_CLOUD_VOICE`（默认 `tongtong`——BigModel 官方 `glm-tts` 预置音色，OpenAI 兼容 `/audio/speech` 请求体 `voice` 字段）——`config.py` Settings `tts_cloud_voice` 剥空白归一（空=请求不带 voice，端点侧默认音色）；`CloudOpenAiTtsProvider` 构造与共用 `_openai_compatible_synthesize` 加可选 `voice`（非空才写键——本地 CosyVoice 请求体逐字节不变，云端音色不发给本地 provider）；`_build_tts` 云端分支布线；compose `TTS_CLOUD_VOICE: ${AIOS_TTS_CLOUD_VOICE:-tongtong}`（未设/置空均回落，与 `TTS_CLOUD_MODEL` 同款）；`smoke_voice_cloud.sh` 第三个可选覆盖（置空回落 tongtong，不回显）。测试：provider 聚焦 35 passed（+5 新增）、smoke 脚本契约 + 隐私守卫 13 passed、compose 渲染 8 passed 1 skipped（门控真启动，环境性跳过）；真实端点冒烟留给运维显式执行。证据：`docs/evidence/m14-65-cloud-tts-voice/README.md`
- M14-63 Release Candidate 独立验收证据回填（docs-only，零生产触碰、零代码/测试/工作流改动、不 push、不建 PR；分支 `docs/m14-63-release-candidate-evidence` 基于 `main@6ef12f878e1e3c774404478dc80099208edcbb57`（PR #145 merge——功能提交 `b5d458ef66d1d7e780f066ca18658904c6ea22a8` 为 RC builder 本地 MinIO 镜像修复，MinIO 不入 API/Web 归档与 manifest；PR #145 checks run `35425541504` 与合并后 main CI run `35425724400` 两组五项 job 全部 success），本 Claude 回填回合独占 worktree 单 local commit 不 push）：把 supervisor 完成的 v0.1.0 Release Candidate 构建与独立文件级校验转为可审计仓库证据（证据 `docs/evidence/m14-63-release-candidate-verification/README.md`，唯一入库证据文件）。验收事实：RC workflow run `35425921605`（`Release Candidate (manual only)` 手动触发，head=main `6ef12f8…`，tag `v0.1.0`，2026-09-19T06:11:08Z→06:15:53Z）conclusion success，唯一 job `Build RC package (local scope, no publish)` 的构建、独立校验（no docker load）、上传 artifact 步骤全 success；artifact `10579069779` `release-candidate-v0.1.0` outer 197,012,661 bytes，GitHub API digest `sha256:097b324a…c46b0` 与本地 gh API 原样下载实测 SHA-256 逐字节互证一致；包内恰 4 文件逐档哈希锚定：`aios-api-v0.1.0.tar` 331,864,576 bytes / `61881604…4402`、`aios-web-v0.1.0.tar` 239,172,096 bytes / `8adf4466…9f50`、`release-manifest.json` 2,022 bytes / `03d5113b…a4e5`、`SHA256SUMS` 260 bytes / `b7ea75b2…def93`（两个 `.tar` 未打开未解析，内容事实全部来自 manifest 与哈希互证）；manifest 自述 version 0.1.0 / tag v0.1.0 / git_commit `6ef12f8…` / scope `local-release-candidate`，compose smoke passed。supervisor 独立校验（主仓 canonical venv，于 services/api）：`python -m app.ops.cli release-candidate verify` exit 0、`ok=true`、`problems=[]`、6/6 checks pass（version_tag_consistency、version_file_consistency、checksums_coverage、archive_hash ×3），未加载任何 Docker 镜像（verify 设计即文件级校验；镜像可运行性以 RC workflow 内 compose 冒烟 smoke passed 为准，本回填不重复执行）。诚实边界：RC 是 workflow artifact 而非 GitHub Release——无 registry push、无 git tag 创建、无 GitHub Release 发布、无发布审批；GitHub artifact 保留期 expires_at 2026-10-03T06:15:30Z，到期后需重新触发 RC workflow 再验收；local Release Candidate 口径不是 production readiness、不授权任何部署；真实 provider 凭证与冒烟、soak 长稳、release readiness / 发布审批与 Harmony 真机收口仍开放；`production_ready=false` 不变。源证据为 gitignored `.verify/m14-62-release-candidate/run-35425921605/` 下 8 个小文件，外层 `.zip` 与两个 `.tar` 按纪律未打开、未读取任何 env/backup/secret。同步更新 ROADMAP（M14-63 状态更新与 M14 开放项台账）与 PROJECT_STATUS 顶部任务结构（M14-61 降级为前一任务）。
- M14-61 production current-main 切换证据回填（docs-only，零生产触碰、零代码/测试/工作流改动、不 push、不建 PR；分支 `docs/m14-61-production-current-main-cutover` 基于 `main@44af53f`（PR #143 merge `44af53fa`，合入后 main CI run `35417197684` 5 job 全部 success——API ruff/pytest/migration、Web test/typecheck/lint/build、Docker compose build+healthy+smoke、Android unit/lint/assemble、Release tools Python tests；PR CI run `35416912719` 全 success 为 supervisor 交接口径，证据目录不含该 run 工件），本 Claude 回填回合独占 worktree 单 local commit 不 push）：把 supervisor 在 current main 完成的生产栈最小化切换转为可审计仓库证据（证据 `docs/evidence/m14-61-production-current-main-cutover/README.md`，唯一入库证据文件）。切换事实：生产 API/Web 容器切至 `aios/api:m14-60-production`（digest `sha256:8e7e16e7…345c8`）与 `aios/web:m14-60-production`（digest `sha256:7cf12f0d…5eb99c`），新容器 `44076753e543` / `bb6ca07483f6` 均 healthy（捕获时 Up 2 minutes）；postgres `f928410e404e` / redis `6006a4c551e1` / livekit `4aa604546c80` / minio `8e4f3d855ffb` 未动（Up 30–45 hours、healthy）。切换后验证：9 URL 端点全 200（API `/health`、`/api/v1/version`，Web `/`、`/login`，FunASR 直连 8010、CosyVoice 直连 8011、sidecar 18010/18011 与 `/health/live`）；未认证 POST `/api/v1/search/queries`、`/api/v1/voice/sessions` 均 401；真实浏览器验收（标题 `AI Learning OS`、首页关键标题与登录控件、未认证 `/api/v1/auth/me` 401 ×2、0 阻塞页面错误）；`production_recovery.py --dry-run` 只读 OK（docker engine 29.7.2、compose config OK、pin 9/9 值不回显、stack 6/6 healthy 健康栈跳过 up、FunASR/CosyVoice healthy untouched leave）；生产监控 34 ok / 0 warn / 0 critical（2026-09-19T03:04:59Z–03:05:01Z **固定源快照口径、非持续状态**——其后 11:15 自然轮 warn-only：仅 API/Web 容器重建预期差异 container-recreated/started_at_changed、六服务五端点仍 healthy，supervisor 交接口径）；审计归档 readiness overall fresh、problems 空（anchor/offline_copy/worm_archive next_due 2026-10-17/2027-03-18/2026-12-18 全 fresh）。生产 env 仅改 `AIOS_IMAGE_TAG` 与 `AIOS_WEB_IMAGE_TAG` 两键为 `m14-60-production`（备份 `artifacts/ops/m14-60-production-cutover/20260919-110341/env.backup` SHA-256 `8CA2DA1F…DD349`、更新后 env SHA-256 `2AEFCE97…933D4`；env 与备份含密钥，回填全程零读取零打开）。源证据 16 文件 / 52,203 bytes 逐文件 SHA-256 锚定（gitignored `.verify/m14-61-production-current-main-cutover/`；不设聚合清单哈希）。诚实边界：部署 API 的 SEARCH/LLM/云 ASR/云 TTS 凭证与端点 11 键全 UNSET（`checked_key_names_only=true`）——不声称真实 provider 冒烟；无 soak / 长稳证据；release readiness / cutover 审批与 Harmony 真机收口仍开放；`production_ready=false` 不变。同步更新 ROADMAP（M14-61 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-60 降级为前一任务）。

- M14-60 Harmony current-main 设备预检（dry-run）证据回填（docs-only，零设备运行、零生产触碰、零代码改动；分支 `docs/m14-60-harmony-current-main-preflight` 基于 `main@1233ef0`（PR #142 merge），本 Claude 回填回合独占 worktree 单 local commit 不 push）：把 supervisor 在 current main 完成的 Harmony 设备预检（PR #139 引入的 `tools/harmony_release` 只读预检工具）只读干跑验证转为可审计仓库证据（证据 `docs/evidence/m14-60-harmony-current-main-preflight/README.md`，唯一入库证据文件）。验证事实：plan 干跑（目标 `127.0.0.1:5555`）mode plan / status planned / exit 0、2 条命令 planned / 0 attempted / 0 executed、`hardware_touched=false`；带 bundle `com.ailearningos.app` 的 plan 干跑 3 planned / 0 attempted / 0 executed；check 门对同目标 blocked、exit 2、failures `signature_report_unreadable` + `target_loopback_forbidden`、0 条命令执行——loopback 目标不可覆盖 fail-closed 拒绝的安全语义实证；`hdc list targets` 仅 `127.0.0.1:15566` 与 `127.0.0.1:5555` 两个 loopback 目标（本机无非 loopback 物理设备）；stderr 0 bytes。源码等价：`2839114..1233ef0` 恰 2 提交（`7e2d54f` docs(android) + `1233ef0` merge PR #142）对 `apps/harmony`/`tools/harmony_release` 区间 diff 为空——预检即 current-main 口径。回填前只读复核：5 个源证据文件逐文件 SHA-256 锚定（plan 5,067 bytes / `12bd5454…b496`、plan-with-bundle 5,075 bytes / `854ffa38…4a95`、check 3,100 bytes / `acaabc5b…8e0c`、hdc-list-targets 33 bytes / `091c7de8…e0f0`、stderr 0 bytes / 空串标准值；不设聚合清单哈希）。诚实边界：无 AGC / 签名 / 签名 HAP、无非 loopback 物理真机、零设备命令执行（plan 与 check 均 0 executed——无设备身份/日志采集、无安装/卸载、无运行时验收）、零生产访问；`production_ready=false` 不变。同步更新 ROADMAP（M14-60 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-59 降级为前一任务）。

- M14-59 Android current-main 真机物理冒烟证据回填（docs-only，零设备运行、零生产触碰、零代码改动；分支 `docs/m14-59-android-current-main-physical-smoke` 基于 `main@028e4bb`（PR #140 merge），本 Claude 回填回合独占 worktree 单 local commit 不 push）：把 supervisor 于 2026-09-19 04:03:13–04:05:34（GMT+8，duration 140.267316 s、exit_code 0）在验证 worktree（detached `main@38de33f`，PR #139 squash，接手时 tracked-clean，与回填同 worktree）完成的第三次 Android current-main 真机物理冒烟转为可审计仓库证据（证据 `docs/evidence/m14-59-android-current-main-physical-smoke/README.md`，唯一入库证据文件）。冒烟判定 **PASS**：Huawei MGA-AL00（serial `EYFBB22923201473`）、被测包 `com.ailearningos.app`、18/18 阶段全 passed、mock 契约 19 expected / 0 unexpected / 19 total（17 GET + 2 POST，task-owned mock 仅绑宿主 `127.0.0.1:8110` 经 `adb reverse` 通道，零生产请求）、logcat 4,744 行 fatal/ANR/本包 crash 全 0、`has_blocking_issue=false`（`androidruntime_crash_count=42` 为 uiautomator 工具进程 I/D 级噪音，E 级 AndroidRuntime 0 行）。源码等价：`38de33f..028e4bb` 恰 2 提交，对 `apps/android`/`tools/android_smoke`/`tests/android_smoke` 区间 diff 为空——冒烟即 current-main 口径。回填前独立复核（只读、不重跑设备）：源证据 19 文件 / 1,815,238 bytes 逐文件 SHA-256 重derive + 确定性聚合清单 SHA-256 `74cc3883…a8708f`、summary.json 3,455 bytes / `b480c774…1dce`（`"passed"` 恰 18 处）、APK 11,454,463 bytes / `da54763f…c570`（与 M13-04/M14-48/M14-50 已归档哈希相同，构建可复现）；与 M14-50 已归档 run 以 summary 哈希、logcat 4,744 vs 5,291 行、duration、执行窗口、mock 端口 8110 vs 8100 区分（本 run 无 REPORT.md；8 份布局 XML 中 7 份与 M14-50 逐字节相同，仅 05 因端口差异）。诚实边界：本冒烟仅证明 USB 真机 loopback mock 回归口径——不证明真实 provider / 生产 API / 生产 DB / 云语音检索 LLM / 长期稳定性；`production_ready=false` 不变。同步更新 ROADMAP（M14-59 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-54 降级为前一任务）。

- M14-54 审计归档调度器生产验收证据回填（docs-only，零代码/测试/工作流改动、零生产触碰、本回填回合零调度器操作；分支 `docs/m14-54-audit-archive-scheduler-production` 基于 `main@5ab05c3`（PR #136 merge，merge 后 main CI run `35362172745` 五项 job 全部 success），本 Claude 回填回合独占 worktree 单 local commit 不 push）：把 supervisor 于 2026-09-18 在 canonical main 检出完成的审计归档调度器真实 Task Scheduler 生产验收转为可审计仓库证据（证据 `docs/evidence/m14-54-audit-archive-scheduler-production/README.md`，唯一入库证据文件）。验收事实：`plan` 全部 repo/VBS/venv/readiness 预检通过且初始 status `missing`；exact-owned `install` 携一字不差确认短语在**非提权** supervisor shell 成功注册隐藏每日任务 `AIOS-Audit-Archive-Readiness`（安装后 status=installed、State Ready、初始 LastTaskResult 267011、NextRunTime 2026-09-19T00:00:00+08:00）；真实调度**负路径**（2026-09-18T23:31:30+08:00，state/policy 缺席）LastTaskResult=2、仅创建 canonical gitignored 工件目录、无报告——fail-closed 缺输入行为验证；手工物化 canonical schema-v1 输入（state.json 896 bytes / policy.json 164 bytes，30/90/180 天间隔 + 24h 宽限，内容源自已验证的 M14-42/M14-43/M14-49 事实）后真实调度**正路径**（2026-09-18T23:32:07+08:00）LastTaskResult=0，readiness.json 955 bytes 与 `.sha256` sidecar 匹配、overall fresh、problems 空、全部任务 fresh（报告/输入 SHA-256 全锚定于证据 README）；终态任务 installed/exact-owned、tracked main 树 clean。诚实边界：state/policy 为手工物化样本（自动维持输入的 updater 仍缺位，后续真实运维依赖）；不声称审计归档本身已执行、未触碰 WORM/离线/S3/provider/Docker/生产数据；`production_ready=false` 不变。同步更新 ROADMAP（M14-54 状态更新）、PROJECT_STATUS 顶部任务结构（M14-53 降级为前一任务）与 `tools/ops/README.md`（M14-53 章节追加生产验收段）。

- M14-53 审计归档调度面 readiness 管理器（开发切片，零真实 schtasks 读/写、零生产触碰；分支 `ops/m14-53-audit-archive-scheduling` 基于 `main@92c8abc`（PR #135 merge），本 Claude 开发回合独占 worktree 单 local commit 不 push，remote 发布/CI/合并由 Codex 负责）：新增 `tools/ops/audit_archive_task.py`（Windows Task Scheduler readiness 管理器，单文件纯标准库）、`tools/ops/run_audit_archive_readiness_silent.vbs`（隐藏调度 wrapper）与 `services/api/tests/test_audit_archive_task.py`（53 项聚焦契约测试，全部经注入 FakeSchtasks）。**只管理调度 readiness，不实现/不执行审计归档本身**：管理隐藏每日计划任务 `AIOS-Audit-Archive-Readiness`（URI `urn:aios:m14-53:audit-archive-readiness` + Description 精确归属标记；TimeTrigger 重复间隔 `P1D` 无限期；`Hidden=true`、`InteractiveToken`/`LeastPrivilege`、`IgnoreNew`、`StartWhenAvailable=true`、`ExecutionTimeLimit=PT30M`、电池不禁启不停；Action `wscript.exe //B //Nologo "<repo>\tools\ops\run_audit_archive_readiness_silent.vbs"`、cwd=仓库根），把 M14-51 就绪 CLI 经 VBS 挂到每日触发，恒用 repo venv Python + canonical gitignored state/policy/output、不传 `--now`。纪律：结构性白名单门仅放行四形态 schtasks 命令（`/Run`/`/Change`/`/End`/带 `/F` 的 `/Create` 等一律执行前拒绝；plan/status/generate 恒只读门=零调度器改动）；install/uninstall 各需 `--confirm "EXECUTE AUDIT ARCHIVE READINESS SCHEDULER CHANGE"` 一字不差否则零调用；绝不覆盖同名任务（预检+双重 missing 复核+安装后 exact-owned 复查）；uninstall 仅删精确拥有任务（五态 installed/missing/foreign/malformed/unknown，其余零删除、missing 幂等）；exact-owned 校验含 URI 归一化条件认可 + 全字段逐项精确 + **结构收紧**（Task 根元素精确，恰好一个 Exec/TimeTrigger/Principal，多余条目一律 malformed 拒绝）；XML 生成/导出/临时件均为 UTF-16 with BOM 字节且落盘回读复核；`/XML` 四字节形态严格解码，解析前拒 DOCTYPE/ENTITY；零 env/零网络/零 `--python` 覆盖（源码契约测试锁定）。监督者（Codex）四项修正全部落实：① VBS canonical 工件目录逐级创建（`.verify` → `.verify\artifacts` → 叶子；干净 checkout 下 CreateFolder 非递归失败——专项测试锁定 + 专用退出码 5）；② 测试文件去除 Windows 绝对路径字面量；③ exact-owned 结构收紧 + 专项 malformed 测试；④ 文档补齐并重跑全部验证；⑤ 真实 supervisor cscript 干净仓库语法探针修正——VBS 内容/注释全 ASCII 重写（非 ASCII 注释在 cscript 默认代码页下编译失败 missing statement，任何预检前即失败；ASCII+LF 解析安全）+ `artifactsDir` 恰三级 stepwise `BuildPath` 构建（消除多余嵌套的 invalid BuildPath arguments），新增 `isascii()` 与精确构建形态契约。验证（canonical venv）：聚焦 **53 passed**、scheduler+readiness 联跑 **78 passed**（零回归）、`ruff check services/api tools/ops/audit_archive_task.py` 全过、双文件 `py_compile` 通过、`git diff --check` 干净。诚实边界：开发全程零真实调度器交互（全部 Fake 注入）、零安装/零卸载/零注册、未运行真实 readiness/归档、未接触 WORM/离线/S3/provider/Docker/生产，实际注册 supervisor-only；`pass` 不等于 production ready，`production_ready=false` 不变。证据 `docs/evidence/m14-53-audit-archive-scheduling/README.md`，同步更新 `tools/ops/README.md`（标题行 + 新工具章节）、ROADMAP（M14-53 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-52 降级为前一任务）。

- M14-52 移动端验证证据回填（docs-only 上下文损坏恢复回合，零设备运行、零生产触碰、零代码改动；分支 `docs/m14-52-mobile-harmony-evidence-backfill` 基于 `main@fba3bb1`（PR #134 merge），本 Claude 回填回合独占 worktree 单 local commit 不 push）：把 supervisor 于 2026-09-18 在独立验证 worktree（detached `main@b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，PR #129 merge，起始 tracked-clean）完成的两轮移动端验证转为可审计仓库证据，两组证据各自独立入库 README。**Harmony Stage C 模拟器终验**（源 gitignored `.verify/m14-50-harmony-emulator-stage-c/`）：六项 phase 全 PASS——URL 持久化（真实 UI 长按→全选→剪切→输入→保存 `http://10.0.2.2:8765/`，post-save dump 确认 TextInput 与状态行）、五 pane 正向（Home/Study/Search/Voice/Governance 对 task-owned mock `aios-mock-android-smoke` v0.12.5-mock 渲染服务地址与 mock 数据、无 pane 卡加载）、受控 mock 停机→Study 出真实错误态+「重试」→新 mock 起后真实点击重试→三篇论文 ≤10 s 恢复且全程无 App 重启、稳定性/故障扫描（hilog dump 883,213 bytes 中 `cppcrash`/`jscrash`/`appfreeze`/`FaultLog` 计数全 0）、卸载证明（`bm dump -n` not-found + `bm dump -a` 0 次 ailearningos）、task-owned mock 树三 PID 终结且 `:8765` 监听归零；App PID **30005**（uid 20020069）贯穿全部 phase 稳定无重启；被测 HAP `entry-default-unsigned.hap` 188,984 bytes、SHA-256 `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`（未签名、cleanup 时点复算一致）；模拟器保持运行，未触碰其他设备/进程。**Android current-main 真机第二次物理冒烟**（源 gitignored `.verify/android-smoke-20260918-main-d613667/`，同日独立于已归档首次冒烟）：2026-09-18 12:22:43–12:25:01 GMT+8（duration 138.344335 s、exit_code 0）、Huawei MGA-AL00 serial `EYFBB22923201473`、**18/18 阶段全 passed**、mock 契约 **19 expected / 0 unexpected / 19 total**（零生产请求）、logcat 5,291 行 fatal/anr/package_crash 全 0（`androidruntime_crash_count=49` 为 uiautomator 工具进程 I/D 级噪音、E 级 AndroidRuntime 为 0）、收尾 `adb reverse --list` 空、App 保留；APK 11,454,463 bytes、SHA-256 `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`（与 M13-04/M14-48/首次冒烟同哈希，构建可复现）；与首次冒烟区分事实：summary.json 哈希 `f69c8e4d…418b` vs `fccca9a2…0496`、logcat 5,291 vs 5,769 行、duration 138.344 vs 135.092 s、执行窗口 UTC 04:22–04:25 vs 02:58–03:01。回填前独立复核（只读、不重跑设备）：Android 源 20 文件 / 1,885,407 bytes 逐文件 SHA-256 重derive + 确定性聚合清单 SHA-256 `a7a35fcd78bf8c112b25372bbd05317da90d80c25ae855d092630b21cb24e393`、summary.json 3,455 bytes / `"passed"` 恰 18 处、mock_requests.log 2,581 bytes / 19 行、logcat 行数实测一致、REPORT.md 3,407 bytes；Harmony Stage C 事实对源 REPORT 与工件逐项复核一致；git 区间核验 `b9e8cc8..fba3bb1` 10 提交对 `apps/harmony`/`apps/android`/`tools/harmony_release`/`tools/android_smoke`/`tests/android_smoke` 区间 diff 为空（验证源码与回填基线一致）。诚实边界：Harmony 侧 mock-only + 未签名模拟器路径（无 AGC/签名/真机）、Android 侧 USB 真机 loopback mock 回归口径——均不构成真实 provider / 生产 API / 生产 DB / 长稳 / 生产就绪证明；`production_ready=false` 不变。证据 `docs/evidence/m14-50-harmony-emulator-stage-c/README.md` 与 `docs/evidence/m14-50-android-current-main-physical-smoke/README.md`（各为该验证唯一入库证据文件，原始产物 gitignored 不入库），同步更新 ROADMAP（M14-52 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-51 降级为前一任务）。

- M14-51 审计归档就绪报告 CLI（开发切片，零生产触碰、零调度器读改、零 WORM/离线访问；分支 `ops/m14-51-audit-readiness-input` 基于 `main@965eb91`（PR #133 merge），本 Claude 开发回合独占 worktree 单 local commit 不 push，remote 发布/CI/合并由 Codex 负责）：新增 fail-closed `tools/ops/audit_archive_readiness.py`（单文件纯标准库）与 `services/api/tests/test_audit_archive_readiness.py`（合计 978 行）——M14-50 已合并纯评估器 `audit_archive_scheduler.evaluate_readiness`（PR #132）的薄 CLI 封装，评估语义零新增零改动。**local-only 文件输入/报告输出**：读两个本地 schema-v1 文档（`--state`/`--policy`，仅普通文件，缺失/目录/symlink/不安全路径拒绝；单文档 1 MiB 上限读前后双检；严格 UTF-8 + 严格 JSON——重复键/`NaN`/`Infinity`/非 object 拒绝；输入绝不变异），就 `--now`（缺省当前 UTC，只解析 tz-aware ISO 8601）逐任务确定性分类，原子写 canonical 就绪报告（排序键、紧凑分隔符、恰好一个尾换行、二进制模式免疫 Windows 换行翻译——同输入 + 同 `--now` 字节相同）+ `<output>.sha256` sidecar（`<report-sha256>  <output-basename>\n`）——两者经 temp + `os.replace` 原子替换，写失败清 temp 非零退出零残骸；输出、sidecar 及其 tmp 中间路径绝不覆盖任一输入（supervisor review 落实的 tmp 路径 input-collision 守卫有专项回归测试）；绝不创建目录；报告只记逻辑 role + basename + 字节数 + SHA-256，绝不记绝对本地路径。退出码 `0` 成功生成报告（含 readiness 判 `due`/`overdue`/`blocked`——状态在报告 `overall` 字段）/ `2` 一切 usage/输入/输出失败。纪律：FS 与时钟全注入、零网络、零 env 访问、零子进程、零 DB/S3（源码契约测试锁定）。验证：聚焦套件 **44 passed**（FakeFS + `tmp_path` 真实盘双轨）、scheduler+readiness 联跑 **78 passed**、worm 邻居回归 `test_audit_anchor_archive.py` + `test_audit_worm_offline_copy.py` **270 passed**（零回归）、`ruff check services/api` 全过、两文件 `py_compile` 通过、`git diff --check` 干净、staged 新增行 secret 扫描 0 命中。诚实边界：全部验证为合成文档 + 临时文件，未跑真实调度链路/真实锚链状态；不自动发现证据、不集成 Windows Task Scheduler、不接触 WORM/离线存储、不执行生产、不访问任何 provider；`pass` 不等于 production ready，`production_ready=false` 不变。证据 `docs/evidence/m14-51-audit-readiness-input/README.md`，同步更新 `tools/ops/README.md`（标题行 + 新工具章节）、ROADMAP（M14-51 状态更新）与 PROJECT_STATUS 顶部任务结构（M14-50 Harmony 回填降级为前一任务）。

- M14-50 Harmony 模拟器 Stage B 交互验证证据回填（docs-only，零设备运行、零生产触碰、零代码改动；分支 `docs/m14-50-harmony-stage-b-backfill` 基于 `main@d613667`，PR #131 merge）：回填 supervisor 于 2026-09-18 11:54–12:01（GMT+8）在验证 worktree `m14-50-harmony-current-smoke`（detached `main@b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，PR #129 merge，起始 clean）完成的 Stage B 判定 **PASS**。`b9e8cc8..d613667` 共 6 提交（全为 docs + 归档调度器工具/测试），对 `apps/harmony`/`tools/harmony_release` 区间 diff 为空——验证源码与回填基线一致。核心事实（gitignored `.verify/m14-50-harmony-emulator-stage-b/`）：预构建未签名 HAP `entry-default-unsigned.hap` 188,984 bytes、SHA-256 `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`（回填回合 certutil 独立重哈希一致；与 Stage A 移动端冒烟回填同一 HAP）普通 `hdc install -r` 即成功（无签名、无绕行），bundle `com.ailearningos.app` v1.0.0 确认；全程唯一目标 `127.0.0.1:5555`（所有 hdc 调用带 `-t`）；PID 3198（uid 20020069）单进程稳定无重启，Mission #43 focused，生命周期 onCreate→onWindowStageCreate→onForeground→WMSFocus→首帧→pages/Index；六个 tab（首页/学习/搜索/语音/设置/治理）经 `uinput -T -c` 真实点击，7 份 dumpLayout 哈希/尺寸互异（tab6 重试后转储同尺寸异哈希，布局确有变化）；治理页「重试」点击（12:00:29）触发全新 `GET /api/v1/version`（PID 3198 BUSSINESS_ISSUE_HTTP `effective_method:"GET"`、`curl_code:7`/`os_errno:111`、NETSTACK `errCode:2300007`；URL 在 hilog 中以 netstack CHR 逐字符隔星脱敏骨架呈现，明文 `api/v1` 0 次出现，掩码逐位还原+时间戳/PID/方法互证）；`127.0.0.1:8000` 在模拟器内解析到模拟器自身 → 全程预期 `2300007` 连接拒绝（只读失败态正确展示，未改任何 API base URL，host 侧 8000 从未被本验证接触）；hilog 全缓冲 `cppcrash`/`jscrash`/`appfreeze`/`FaultLog` 全 0，faultlogger 目录为空（freeze/ shell 无权限列出，如实记录，hilog 扫描补偿）；卸载后 `bm dump` 错误输出与安装前逐字节相同（bundle 不存在验证）、无残留进程、模拟器保持运行。独立复核：源证据 44 文件 / 4,190,967 bytes 逐文件 SHA-256 全锚定 + 确定性聚合清单 SHA-256 `3ff6f7b5ac951fc4baf9f1a2ec2942afdc71524717fccdd52d2aac52d01a9f09`（回填回合只读重derive 一致），关键原始产物（安装/卸载输出、PID 行、崩溃计数、时间线）抽查全吻合。诚实边界：无成功 API 数据渲染（模拟器内服务器不可达）、无 tab 栏动画验证、无深层流程（考试查询/设置保存未执行）、无签名/AGC/Harmony 真机、`see_image` 无文字描述（dumpLayout ground truth + 截屏留档）、治理「审计日志」区块在折叠线下未直接观测；`production_ready=false` 不变。证据 `docs/evidence/m14-50-harmony-emulator-stage-b/README.md`。

- M14-50 移动端 current-main 冒烟证据回填（docs-only，零设备运行、零生产触碰、零代码改动；分支 `docs/m14-50-mobile-smoke-backfill` 基于 `main@15580f2`，PR #130 merge）：回填 supervisor 于 2026-09-18 在两个独立验证 worktree（均 detached `main@b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，PR #129 merge）完成的移动端双冒烟。`b9e8cc8..15580f2` 仅 2 个 docs 提交（`849f49f` + merge），对 `apps/harmony`/`apps/android`/`tools/harmony_release` 区间 diff 为空——移动端源码与验证基线一致。Harmony（gitignored `.verify/m14-50-harmony-current-smoke/`，模拟器 `127.0.0.1:5555` 单目标、无 discovery）：release build ok；HAP `entry-default-unsigned.hap` 188,984 bytes、SHA-256 `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`（未签名、零签名材料、`signedness_verified: false`；与 M13-16 的 452,446 bytes 尺寸差已解释——对方含 sourceMaps.map 119,689 + modules.abc 328,372，当前无 source map、modules.abc 184,708，`7c0f7c2..b9e8cc8` 无源码 diff）；device smoke `--confirm-mutation` 6/6 命令（install → 启动 EntryAbility → dumpLayout+recv → force-stop → uninstall）；layout 2,708 节点/53,798 bytes/SHA-256 `B31AECB7…CC2F1`，与 M13-16 语义对比 82 节点/26 文本节点结构一致（23 个字节相同、3 个仅时钟 `08:31`→`11:00`）；hilog 882,948 bytes，cppcrash/jscrash/appfreeze/FaultLog 全 0；卸载后 `bm dump` 证实 bundle 不存在；模拟器保持运行（修正轮已删除无凭据的主屏恢复声明）。Android（gitignored `.verify/android-smoke/`，USB 真机 Huawei MGA-AL00 serial `EYFBB22923201473`）：APK `app-debug.apk` 11,454,463 bytes、SHA-256 `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`（与 M13-04/M14-48 归档哈希相同，构建可复现）；`overall_status=passed`（135.09s）、18/18 阶段、mock 契约 19 expected / 0 unexpected / 19 total、logcat FATAL 0 / ANR 0 / 本包 crash 0（`androidruntime_crash_count=47` 为 uiautomator 诊断噪音）、收尾 `adb reverse --list` 空、App 按物理冒烟 teardown 策略保留。回填前独立复核：HAP/APK 哈希与字节数实测一致；Harmony 清单 11/11、Android 清单 19/19（含 `summary.json` SHA-256 `fccca9a2…0496`）逐条重验全 OK（两清单 CRLF 行尾，核验需 `tr -d '\r'`）；secret 扫描通过。诚实边界：Harmony 仅为模拟器 + 未签名 HAP（无 AGC、无真实签名、无真机）；Android 仅为 USB 真机 loopback mock 回归口径——均不构成真实 provider / 生产 API / 生产 DB / 云语音检索 LLM / 长稳 / 生产就绪证明；`production_ready=false` 不变。证据 `docs/evidence/m14-50-current-main-mobile-smoke/README.md`。

- M14-49 审计锚点 WORM 离线第二副本真实执行记录（docs-only，零生产触碰、零工具重跑、零 G: 访问；分支 `docs/m14-49-audit-worm-offline-execution` 基于 `main@b9e8cc8`）：回填 supervisor 在真实离线根上完成的 `preflight -> copy -> verify` 闭环（执行窗口 2026-09-18T02:51:22Z–02:52:06Z，执行基点 `main@b9e8cc8`）。工具 `tools/ops/audit_worm_offline_copy.py` 已随 **PR #129** 合并 main（merge commit `b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`、feature commit `2461306`）。三步均 pass、problems=[]：preflight existing absent；copy `offline_verified=true`、`created=true`；verify `offline_verified=true`、existing matching（`audit-anchor.jsonl`/`verify-report.json`/`manifest.json` 三文件全在位）。离线根 `G:\AI-LearningOS-Audit-Offline\worm-root-v1`，根 marker `AIOS-OFFLINE-COPY-ROOT.marker` SHA-256 `0fabda6da8344649d870c050a7bf4ef914e41a5b8b8c57c48767280c008215fa`（内容 + 单个尾随 `0A` 字节经 hexdump 证实）；布局 `audit-anchor/d2bfd877…73aa/` 下 `audit-anchor.jsonl`（SHA-256 `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`）、`verify-report.json`（`ff2494a8e922203f2f6ceb140e06922279ec966b47e4ffd9311c844d38fc7ddd`）、`manifest.json`（`fc8886237ea7fb0045aba37bf177cf51167f419937b57e3fdab9d8139cf105f7`）哈希与源逐字节一致。原始快照 gitignored `.verify/m14-49-audit-worm-offline-execution/raw/`，其 MANIFEST.txt 经独立核验 16 条目、0 哈希失配（自身 SHA-256 `dca39fa2e8fa09c7a93a3c135a085619d05bfacc2426dc707a4525102408be83`），secret 扫描通过。文档如实记录介质诚实边界：G: 为独立物理 Disk 1（NVMe UNIS SSD S3 1TB，Healthy）而 D:/E: 同属 Disk 0、F: 为 Ventoy 可移动介质（health warning，未用于本副本）——本副本是独立物理磁盘上的仓库外**在线**第二副本，不是真正断开/可移动轮换/离站介质，不满足严格 3-2-1 的「1 份离线/离站」；真正离线/离站副本、定期归档调度、provider smoke、发布审批、长稳剩余面与 AGC 签名链仍开放，`production_ready=false` 不变。证据 `docs/evidence/m14-49-audit-worm-offline-execution/README.md`。

- M14-44 审计锚点 WORM 真实执行记录（docs-only，零生产触碰；分支 `docs/m14-44-audit-worm-execution-record` 基于 `main@9c21311`）：回填 supervisor 在真实 MinIO Object Lock 桶上的 `preflight -> archive -> verify` 闭环。PR #121 feature head `456181e` CI run `35262666887` 与合并后 main CI run `35263086947` 均 5/5 SUCCESS；源锚 354 bytes / SHA-256 `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`；新 bucket `aios-audit-worm` 以 Object Lock 创建，原 `aios-objects` 未修改；对象 key `audit-anchor/<sha256>/audit-anchor.jsonl`、version `dc704b8d-6ebd-4acb-adb2-2135f89bb703`、`COMPLIANCE` 保留至 `2036-09-17T19:10:00Z`；archive 与 verify 均 pass、`worm_verified=true`、problems=[]；archive 报告 SHA-256 `66703e41a9287ea2ab1fdab15eaa778d1db2618664e3cc1cb4c2b3d9b9dc4b96`。文档同时记录 Windows 工作区 CRLF 哈希差异与 Git blob/原始锚文件一致性，避免误判对象漂移。边界：离线第二副本、定期归档调度、provider smoke、发布审批、长稳剩余面与 AGC 签名链仍开放，`production_ready=false` 不变。证据 `docs/evidence/m14-44-audit-worm-execution/README.md`。

- M14-43 审计锚点 WORM/对象锁归档工具（开发切片，零真实 WORM 执行；worktree `ops/m14-43-audit-worm-archive` 基于 `35c0875`，本 Claude 开发回合独占 worktree 单 local commit，remote 发布/CI/合并由 Codex 负责）：新增 fail-closed `tools/ops/audit_anchor_archive.py`（单文件纯标准库 + boto3 懒导入；S3Client/FS/Clock/env 全注入）与 `services/api/tests/test_audit_anchor_archive.py`——把 M14-42 落地的生产审计锚点 JSONL 归档进可验证的 WORM/对象锁桶并证明归档事实。三命令退出码统一 0/2：`preflight` 本地锚链完整校验（与 M14-42 生产端同契约）+ endpoint 策略（公网必须 HTTPS、loopback/RFC1918 可 HTTP、拒绝 userinfo）+ 凭据只认环境变量（缺失零 S3 访问）+ bucket 存在/versioning `Enabled`/Object Lock enabled，全程零写；`archive` 确认短语一字不差 + COMPLIANCE-only + tz-aware 严格未来 retain-until 三道参数门（fractional 秒合法——报告 canonical UTC 表示保留非零微秒，archive→verify 精确往返），内容寻址 key `audit-anchor/<sha256>/audit-anchor.jsonl`，新对象恰好一次条件创建 put（显式 `if_none_match="*"` 经注入协议传递、仅在真实适配器边界映射为 PutObject `IfNoneMatch="*"`——head 判不存在后被并发抢占时服务端拒绝、fail-closed 绝不覆盖竞态写入），已存在对象字节/retention 核验后绝不覆盖，put 后重读字节与元数据任何漂移 fail-closed；`verify` 归档报告 sidecar 哈希核验（空/非 UTF-8/畸形 exit 2 绝不抛异常）+ 报告绑定当前调用事实（跨 bucket/endpoint/失败报告一律拒绝）+ 按记录 version 定向 head/get 逐字节核验。报告名防碰撞：每份报告名带 `secrets.token_hex(16)` CSPRNG 随机后缀（32 位小写 hex = 128 bits，同 command 同秒并发撞名概率约 2**-128；探测与写入非原子，属概率性抗碰撞而非全局互斥），对候选名全部工件后缀做存在性探测（孤儿工件也算碰撞，兜底递增 `-2`/`-3`），绝不覆盖既有报告证据；报告原子写 gitignored `.verify/artifacts/m14-43-audit-worm-archive/`（JSON+MD+`.json.sha256` sidecar，字节模式写盘不受 Windows 换行翻译破坏）；endpoint 只记 host，凭据值/完整 endpoint/原始异常绝不入报告，`redact_secrets` 终防线。S3 API 白名单六类只读/put（源码契约测试锁定无 delete/copy/bucket-config）。supervisor Review Round 1（11 项）+ Round 2（3 项：fractional retain-until、报告名防碰撞、条件创建强化）全部落实。验证：聚焦套件 151 passed（canonical venv）、邻居回归 90 passed/3 skipped、ruff/py_compile/`git diff --check`/diff secret 扫描干净。诚实边界：**零真实 WORM 归档执行**——未连接任何真实 S3/MinIO、未触碰生产，真实 preflight/archive/verify 由 supervisor 在获准窗口进行；契约测试不能替代真实 Object Lock 服务端语义；`pass` 不等于 production ready，`production_ready=false` 不变。新增证据 `docs/evidence/m14-43-audit-worm-archive/README.md`，同步更新 `tools/ops/README.md`（新工具章节）、DEVELOPMENT.md（锚文件 WORM 归档 runbook 节）、ROADMAP（M14-43 完成条目）与 PROJECT_STATUS 顶部任务结构（M14-42 降级为前一任务）。

- M14-41 MinIO 生产卷属主采纳生产闭环回填（docs-only，零代码/零生产触碰；分支 `docs/m14-41-production-adoption-record`）：把 supervisor 获准窗口的真实生产采纳事实回填入库，收口 M14-41 开发切片遗留边界「真实 plan/execute/rollback 生产采纳等待 supervisor 执行」——工具已随 **PR #118 合并 main `5bc74c4`**（合并前跨平台测试修正，PR CI run `35172658746` 五 job 全部通过；本地 Windows 卷+镜像组合测试 233 passed）；合并后 main 真实 `plan` exit 0；真实 `execute` stamp `20260917-020446` 于 2026-09-17T02:05:03Z 判定 pass——root 普查迁移为 uid 1000，备份 `minio-data-backup-20260917-020446.tar`（57344 bytes，SHA-256 `c56298300087f1493f59c257f9f65206231aca10c82a4c7f35a6f3806f8a38b8`）与 tar/.sha256/.manifest/report 工件齐备，MinIO 以容器 `8e4f3d855ffb` 重建（本地 pin 镜像 `sha256:0f1c79afdb0b5fcdd49e385c46bca065f97cdd89917522593b84436a2e61bcd6`）healthy，API `b3e62b355703`/Web `5be2e19db2e7`/LiveKit `49935f127ca0`/Postgres `d17d5a93a079`/Redis `89f3ed0fd02a` 容器 ID 不变且 healthy；采纳后 M14-40 preflight stamp `20260917-020606` 全部检查通过（采纳边界解除）；采纳后 `production_recovery --dry-run` 于合并后 main exit OK（compose config OK、pin 9/9、六服务 healthy、健康栈跳过 up、FunASR/CosyVoice managed-running health 200 且未触碰）。诚实边界：rollback 未在真实生产演练（仅契约测试覆盖）；execute pass 与 preflight pass 不等于 production_ready，`production_ready=false` 不变。更新 `docs/evidence/m14-41-minio-volume-adoption/README.md`（生产采纳章节 + 边界收口）、PROJECT_STATUS（当前任务收口）、ROADMAP（M14-41 条目生产闭环 + MinIO 未决项收口为仅长期替代评估）与 `tools/ops/README.md`（M14-41 章节状态）。

- M14-41 MinIO 生产数据卷属主采纳工具（root → uid 1000，plan/execute/rollback；worktree `ops/m14-41-minio-volume-adoption` 单 local commit，remote 发布/CI/合并由 Codex 负责——开发时点状态；生产采纳已由上方回填条目收口）：新增 fail-closed `tools/ops/minio_volume_adoption.py`（纯标准库）与 `services/api/tests/test_minio_volume_adoption.py`（106 项契约测试）——chown 前强制 tar + SHA-256 + manifest 备份，rollback 先校验备份工件与镜像 ID（不符即拒绝）；仅 MinIO 容器被停止/重建，其余五容器只读基线采集；一次性 helper 容器 `--user 0:0` + 最小化挂载；停止容器前 compose/env 元数据门禁（--env-file + --profile local）；工件路径 fail-closed（含符号链接组件检查）；argv token 白名单（未知参数一律拒绝）。验证：卷采纳套件 106 + 镜像采纳套件 127 + Codex 合并 233 passed，ruff/py_compile/`git diff --check` 干净。开发全程零 Docker/零生产（mock 契约测试）。新增证据 `docs/evidence/m14-41-minio-volume-adoption/README.md`，同步更新 `tools/ops/README.md`（新工具章节）、ROADMAP（M14-41 完成条目）与 PROJECT_STATUS 顶部任务结构（M14-40 降级为前一任务）。

- M14-40 MinIO 本地镜像采纳预检工具 build/smoke/preflight 三模式（分支 `ops/m14-40-minio-local-adoption` 基于 `main@0e4f5c5`（PR #116 merge），本 Claude 开发回合独占 worktree 单 local commit，remote 发布/CI/合并由 Codex 负责——开发时点状态；零 Docker/生产操作）：新增 `tools/ops/minio_image_adoption.py`（纯标准库）与 `services/api/tests/test_minio_image_adoption.py`（120 项契约测试）——M14-39 遗留「minio 自建镜像仍未本机构建/采纳」的接续工具切片。三模式各默认 plan（零执行），真实执行需 `--execute` + 模式专属精确确认短语（`EXECUTE MINIO IMAGE BUILD`/`EXECUTE MINIO IMAGE SMOKE`/`EXECUTE MINIO PREFLIGHT`，一字不差）：build=恰好一次 docker build（只构建 compose 锚定 `aios/minio:RELEASE.2025-10-15T17-29-55Z`，pin commit 四处交叉锁定；可选 `AIOS_MINIO_BUILD_HTTPS_PROXY` 仅 socks5h/socks5/http/https + host[:port]、端口 1–65535 语义校验，只向构建传 Docker 预定义 `HTTPS_PROXY` build arg——零 GOPROXY/Dockerfile/pin 改动）；smoke=严格 `aios-m14-40-` 前缀生成式一次性容器/卷名、loopback 19000/19001、uid 1000 数据探针、finally 精确清理恰两名（清理失败即整体失败）、绝不读取 env secret（含 `infra/env.production-recovery`）；preflight=只读生产盘点（镜像元数据与运行时 uid/版本、compose/Dockerfile 锚点、六容器健康、生产卷 driver/size/递归 UID 普查——卷探针恒 `:ro` 挂 `--network none` 一次性 `--rm` helper、绝不写生产卷），采纳判定 fail-closed：镜像缺失/user/entrypoint/version 不符/卷属主非 uid 1000 或普查不确定/栈非全健康 → adoption=blocked。全局：`--project` 仅 `aios-m14-03-production-rehearsal`；生产面严格只读（零 compose mutation/pull/stop/restart/remove/exec/env-secret-read）；每个 docker argv 固定结构白名单，偏离执行前拒绝；报告 schema v1 JSON+MD 落 gitignored `.verify/artifacts/m14-40-minio-image-adoption/`；退出码统一 0 成功/2 一切失败（fail-closed）。真实构建尝试如实入档：2026-09-16T12:51:06Z 无代理直接构建 468.7s 于 Go module 拉取阶段失败（proxy.golang.org connection refused，docker build rc=1——gitignored build-20260916-125106.json/.md），代理重试路径已备；真实代理构建、一次性冒烟、真实只读 preflight、push/PR/CI 待 supervisor 执行，不宣称通过。验证：全量 services/api 3045 passed/33 skipped、聚焦 120 passed、邻居复验（selfbuild/recovery/compose/backup 四套件）91 passed/3 skipped、ruff/py_compile/`git diff --check`/secret 扫描/代理端口穷尽检查全过。诚实边界：adoption=pass ≠ 生产就绪，`production_ready=false` 不变；不把自建镜像采纳进生产、零数据迁移（minio-data root→uid 1000 迁移 + `up -d --no-build` 固化为后续受控任务）。新增证据 `docs/evidence/m14-40-minio-local-image/README.md`，同步更新 `tools/ops/README.md`（新工具章节）、ROADMAP（M14-40 完成条目）与 PROJECT_STATUS 顶部任务结构（M14-39 降级为前一任务）。

- M14-39 production_recovery 健康栈跳过 up + up 前本地镜像预检（恢复边界固化；分支 `fix/m14-39-recovery-minio-preflight` 基于 `main@7f1fe91`（PR #115 merge），本 Claude 开发回合独占 worktree 单 local commit，remote 发布/CI/合并由 Codex 负责——开发时点状态）：修复 `production_recovery --dry-run` 被本机缺失 `aios/minio:RELEASE.2025-10-15T17-29-55Z` 自建镜像（M14-13，registry 不可拉取）阻塞——生产栈六容器均 healthy 而旧语义在 pin 9/9 后无条件 `compose up -d --no-build --dry-run` 计划 Recreate api/livekit/minio 因缺镜像 rc=1（M14-23 起既有、M14-38 记录在案的诚实边界）；手工验证 no-recreate+pull never 可 rc=0 但隐藏计划重建，评估后不采纳。`tools/ops/production_recovery.py` 修法：① pin 通过后 up 前只读 stack health 快照（`compose ps --format json`）——六服务全部 healthy/running 时 dry-run 与 enforce **一致跳过 compose up**（明确输出「健康栈无需 up」，继续语音调和与最终判定；startup recovery 恢复健康、不做部署/config drift 收敛，拓扑变更由 M14-38 `tools/ops/livekit_lan_cutover.py` 显式执行）；② 六服务缺失或不健康时保留原唯一 up 语义 `up -d --no-build`（dry-run 附加原生 `--dry-run`）——不加 `--no-deps`、不加 `--no-recreate`、不隐藏漂移；③ up 前只读本地镜像预检（仅 up 路径触发）：新增 `LOCAL_BUILD_IMAGE_REFS` 常量（现仅 aios/minio:<RELEASE>，与 compose `image:` 锚点契约测试交叉锁定）逐个 `docker image inspect`（零 pull/build/stop/restart；探测失败同样按缺失）——缺失且栈非全健康 → 输出固定词汇 `minio-local-image-missing` 并在 up 之前 fail-closed（enforce 立即退出 1、dry-run 退出码镜像 enforce 拒绝，绝不 pull/build）；④ 健康栈跳过 up 即不触发预检（本机缺镜像 + 栈健康 → dry-run 绿灯）。不变量：PIN_KEYS 九键与 pin 语义零改动、不改生产 env、不输出 secret、单文件纯标准库 + Runner/RunLog 注入风格、新增 `stack_health_gaps` 单一健康缺口判定口径（`wait_stack_healthy` 复用）、M14-38 `compose_up_limited` 的 `up -d --no-build --no-deps api livekit` 语义与复用面零改动；源码契约禁词扩展（`--no-deps`/`--no-recreate`/pull/build 字面量绝不出现于恢复工具）。TDD：`services/api/tests/test_production_recovery.py` +10 项契约测试（全健康跳过 up 双模式、非全健康唯一 up 语义、MinIO 缺镜像+非全健康 fail-closed 双模式、健康栈+缺镜像仍 OK、健康快照只读、预检只读+固定词汇、常量与 compose 锚点交叉锁定、`stack_health_gaps` 纯函数、M14-38 九键/复用面不回归），既有 e2e 按新语义收窄。真实验证全程只读：主仓 venv 真实 dry-run **exit 0**（pin 9/9、六服务健康快照、零 up 构造、funasr/cosyvoice leave、最终 OK——旧语义同栈将在 pin 9/9 后 rc=1）；验证前后 `docker ps` 生产六容器 ID/时长/状态完全不变；只读实证本机确缺 aios/minio 镜像；聚焦 `test_production_recovery.py + test_livekit_lan_cutover.py` 92 passed（63+29）、全量 services/api 2929 passed/0 failed/33 skipped（3:42）、ruff/py_compile/`git diff --check`/增行 secret 扫描全过。诚实边界：minio 自建镜像仍未在本机构建/采纳（预检只把失败提前为可见拒绝——栈真实非全健康需重建 minio 时仍 fail-closed，人工构建镜像与 minio-data 卷属主迁移另办）；`minio-local-image-missing` 真实 Docker 路径与 enforce 真实执行未在本机触发（契约测试双模式覆盖）；不构成 `production_ready=true` 依据，全局 `production_ready=false` 不变。新增证据 `docs/evidence/m14-39-recovery-healthy-skip/README.md`，同步更新 `tools/ops/README.md`（流程 4/退出码）、ROADMAP（M14-39 完成条目 + M14-38 遗留 dry-run 阻塞收口标注 + MinIO 未决项更新）与 PROJECT_STATUS 顶部任务结构（M14-38 降级为前一任务并收口 PR #115 合并事实）；原始日志 gitignored `artifacts/recovery/recovery-20260916-192131.log` 不入库。

- M14-38 LiveKit LAN cutover 生产收口（执行事实回填 + 工具固化；分支 `ops/m14-38-livekit-lan-cutover` 基于 `main@902dca2`（PR #114 merge），本 Claude 开发回合独占 worktree 单 local commit，remote 发布/CI/合并由 Codex 负责——开发时点状态）：supervisor 已于 2026-09-16 在生产彩排栈真实执行 LiveKit LAN cutover——信令/媒体面切本机 LAN IP `192.168.8.3`（`infra/env.production-recovery`（gitignored）原子更新 `AIOS_LIVEKIT_BIND_IP`/`AIOS_PUBLIC_LIVEKIT_URL` 两键，`AIOS_BIND_IP` 未动仍 loopback——M14-37 媒体面独立绑定能力落地），切换前备份 `artifacts/ops/livekit-cutover/20260916-122542-env.backup`，最小范围重建仅 api/livekit 两容器（web/redis/postgres/minio 未触碰）六容器 healthy，API 实测 `ws_url=ws://192.168.8.3:7880`，**真实默认浏览器 3/3 通过**（default 模式绝不注入 loopback flag——M14-35 遗留 loopback 间歇性 ICE 失败随之收口），切换后只读监控 34 ok/0 warn/0 critical；本回合零生产操作，交付三面代码收口：① `infra/verify_web_livekit_client.py` LiveKit 信令探测去硬编码（契约派生优先——由 `POST /api/v1/voice/token` 响应 ws_url 派生同源健康 URL，loopback/LAN 两类拓扑零改动覆盖；`AIOS_PUBLIC_LIVEKIT_URL` 显式 override 严格校验，空串/非法 ENV-BLOCKED fail-closed），契约测试 72/72；② production recovery pin 六→九键（新增拓扑键 `AIOS_BIND_IP`/`AIOS_LIVEKIT_BIND_IP`/`AIOS_PUBLIC_LIVEKIT_URL`——防恢复路径把 LAN 拓扑静默重建回 loopback；在线事实取 api 容器 env 与 `docker port livekit 7880` 宿主绑定段，空串也是事实，输出恒仅键名），`infra/env.production-recovery.example` 模板同步九键，契约测试 53/53；③ 新增 `tools/ops/livekit_lan_cutover.py`（533 行）cutover/rollback 可重复 runbook 工具（plan 只读 delta 预览且恒零文件系统写——main 级不建 artifacts 目录、不写日志文件，stdout 照常；apply：gate→备份+sha256→原子更新仅拓扑键→compose config 校验失败即恢复零重建→up -d --no-build --no-deps 仅 api livekit（--no-deps 隔断 compose 依赖解析，绝不连带重建 depends_on 依赖）→九键复核必须全绿；rollback：伴生 sha256 校验先行（路径由备份名 -env.backup→-env.sha256 推导；缺失/格式非法/文件名不匹配/哈希不匹配任一即拒绝且零副作用——不读备份 pin 值、不建 safety 备份、不调 Docker、不写 env）→备份九键校验→回滚前快照→原子恢复→同样最小范围重建+复核；执行门 `--confirm-rebuild rebuild-api-livekit` 精确值；源码契约锁定绝不 stop/rm/kill/down/restart/reset/--build、绝不触碰 web/redis/postgres/minio、secret 永不落日志），契约测试 29/29。本地验证：聚焦三文件 154 passed（72+53+29）、全量 services/api 2919 passed/0 failed/33 skipped（3:35）、ruff All checks passed、`py_compile` 通过、`git diff --check` 干净、增行 secret 扫描 0 凭据。诚实边界：`production_recovery --dry-run` 仍被 MinIO 缺镜像阻塞（M14-23 既有，未被本次改变）；cutover 工具已完成两阶段真实 Docker 验收（supervisor 执行）——首轮真实 apply（彼时缺 --no-deps）暴露依赖隔离缺陷：compose 仍试图连带重建 minio 且因 aios/minio 镜像缺失失败，六容器原样保持 healthy（可见失败、零隐性副作用）；env 随后采纳第九键 AIOS_BIND_IP、plan 达 9/9；修复补 --no-deps 后，20260916-181457 真实 apply 成功（`up -d --no-build --no-deps api+livekit`——api `b3e62b355703` 与 web/redis/postgres/minio 容器 ID 全部不变，livekit 重建为 `49935f127ca0` 并 healthy，复核 pin 9/9）；修复后默认浏览器 LiveKit 验收通过 `.verify/m14-38-production-lan-default-r4-post-nodeps`（voice-token-contract `ws://192.168.8.3:7880`，token/connect/data/mic/cleanup 全过，console/pageerror 0）；不构成 `production_ready=true` 依据，全局 `production_ready=false` 不变；远程设备/跨 NAT 仍属 M10-05 TURN 后备域。新增证据 `docs/evidence/m14-38-livekit-lan-cutover/README.md`，同步更新 ROADMAP（M14-38 完成条目 + M14-37「运维显式执行切换」遗留项收口标注）与 PROJECT_STATUS 顶部任务结构（M14-37 降级为前一任务）；原始证据 gitignored `.verify/m14-38-production-lan-default-run1..3` 与 `.verify/m14-38-production-monitor-post-cutover` 不入库。

- M14-35 Web 真实 LiveKit 客户端连接验收第一切片（真实浏览器 + 当前部署栈；分支 `feat/m14-35-web-livekit-client` 基于 `main@a109c973`（PR #111 merge，本地 git 可验证），本 Claude 开发回合独占 worktree 单 local commit，remote 发布/CI/合并由 Codex 负责——开发时点状态）：apps/web 新增 voice 首页「LiveKit 连接检测」卡片与 `livekit-check.ts` 检测状态机（五步 token/connect/data/mic/cleanup、outcome 分级 passed/passed-with-skip/failed、麦克风错误分类降级、JWT 脱敏截断、withTimeout；R1 每次检测唯一随机房间 `web-check-<16 位随机字符>`——Web Crypto+拒绝采样，消除固定房间并发互听；22 项 vitest TDD RED→GREEN），`api.voiceToken` 对齐既有 `POST /api/v1/voice/token` 契约（零后端/API 改动），`next.config.ts` env 门控 `/api/*` 同源 rewrite（R1：`AIOS_ACCEPTANCE_API_PROXY` 经 `acceptance-proxy.ts` 结构化校验——仅 http/https、去尾斜杠、拒查询串/片段，非法值构建期 fail-closed 报错，+7 项 vitest；未设置时与默认构建完全一致——routes-manifest rewrites 为空实测——生产 CORS 精确 allowlist 不为此放行新源），依赖 `livekit-client ^2.22.3`；R1 返工另含：检测卡片 rounded-lg、CI Web job 增 `npm run test --workspace apps/web` 门禁（job 更名 Web (test / typecheck / lint / build)）、首页文案改为准确状态（练习仍浏览器语音；连接检测已接入 LiveKit；完整 ASR/TTS 会话尚未接入）、验收脚本 console 分窗硬断言；新增 `infra/verify_web_livekit_client.py` 可重复真实浏览器验收工具（复用生产 API/LiveKit/DB 零重启，本分支构建经同源 rewrite 在空闲端口自起自收，Playwright Chromium + fake 麦克风 + 授权，DOM 级断言 + JWT 扫描，fail-closed 退出码 0/1/2；R2：Chromium 显式加 `--allow-loopback-in-peer-connection` 受控验收条件——非生产用户默认，docstring 记录根因与受控条件）。真实浏览器验收（R2 修订判定）：R1 返工后重跑 13/13 为历史记录；R1 首跑 connect 失败并非「环境抖动」——根因为默认拓扑间歇性 ICE 失败（当前 LiveKit 以 --node-ip 127.0.0.1 通告媒体且 UDP 仅绑 loopback，Chromium/WebRTC 默认不收集 loopback ICE candidate；Codex 独立默认拓扑首跑 connect 失败 `could not establish pc connection`、同构建复跑 12/12——拓扑级不确定）。R2 受控验收：脚本 Chromium 显式加 `--allow-loopback-in-peer-connection`（受控条件，生产用户浏览器默认不具备），gitignored `.verify/m14-35-web-livekit-client-r2/` 连跑 run1/run2 通过、run3 connect 步失败（受控 flag 未完全消除间歇性）、run4/run5/run6 复跑连续三次通过——**末段连续 3/3 构成受控拓扑验收通过，非默认浏览器直连稳定性**；受控通过段五步 token/connect/data/mic/cleanup 全 passed（fake 麦克风音轨真实发布到生产 LiveKit，房间为运行时唯一随机名）、`ws_url=ws://127.0.0.1:7880`、DOM 不含 JWT 形态 token（token 只经组件局部变量喂 SDK）、检测窗口 `console_error_count=0` 硬断言（登录窗口预期 401 单独记录不计入）。默认拓扑稳定化（LAN/TURN/显式绑定设计）为独立生产阻塞项。本地验证：单测 29/29 passed（22+7）、`npm run typecheck` 0 error、`npm run lint` 0 errors（11 warnings 全为既有文件，无一来自本任务文件）、`npm run build` 成功（9 路由；默认 env 下 rewrites 为空）、`npm audit --omit=dev --registry=https://registry.npmjs.org` 0 vulnerabilities、API 合约未改 pytest 不适用。诚实边界：**连接诊断切片——只验证「浏览器到 LiveKit 的连接与麦克风发布」，不代表完整语音会话（ASR/TTS）已接入**；且仅在 loopback 受控条件下取得末段连续 3/3 通过——默认拓扑存在间歇性 ICE 失败（受控 flag 下 run3 仍失败），默认拓扑稳定化（LAN/TURN/显式绑定设计）为独立生产阻塞项；单次单浏览器（唯一随机房间）不覆盖远端订阅/多人房间/重连恢复/长稳/并发；不构成 `production_ready=true` 依据，全局 `production_ready=false` 不变。新增证据 `docs/evidence/m14-35-web-livekit-client/README.md`，同步更新 ROADMAP（M14-35 完成条目 + 下一生产阻塞点：浏览器/LiveKit 连接第一切片受控条件闭环、新增默认拓扑 LAN/TURN/显式绑定设计生产阻塞项，完整会话/移动端/长稳/并发仍开放）与 PROJECT_STATUS 顶部任务结构（M14-34 降级为前一任务并收口 PR #111 合并事实：merge commit `a109c973`）；原始证据 gitignored `.verify/m14-35-web-livekit-client/`（R1）与 `.verify/m14-35-web-livekit-client-r2/`（R2 受控连跑）不入库。

- M14-33 真实本地语音 ASR/TTS 端到端冒烟验收回填（docs-only，零代码/零生产触碰；分支 `docs/m14-34-voice-e2e-backfill` 基于 `origin/main@7eb7404`（PR #110 merge））：M14-33 于 2026-09-15 23:51:34–23:52:05 +08:00 在 main@`7eb7404`（任务前后同一 HEAD、porcelain=0、tracked 全程 clean）上**一次性执行**仓库既有 `tools/voice/smoke_local_voice.py`（主 API 同款 adapter 链路——`LocalFunAsrAsrProvider`/`LocalCosyVoiceTtsProvider`、真实 HTTP、既有样例未下载）并 **PASS（5/5，本任务验收口径；Codex 独立验收亦 PASS）**——rc=0、stderr 空、stdout 末行 `ALL LOCAL VOICE SMOKE CHECKS PASSED`；ASR provider=`local-funasr` latency 2900ms 非空真实中文转写 `'希望你以后能够做的比我还好哟'`（confidence=0.000 如实记录——本地 bridge 不返回置信度，非判据）；TTS provider=`local-cosyvoice` latency 27066ms（CPU 本轮观测值）241964 bytes RIFF/WAV yes；样例 `artifacts/voice/smoke/asr_sample_zh.wav`（334138 bytes，SHA256 `c7b31d6d…c8ff6`）前后一致未下载；FunASR PID 3445/CosyVoice PID 3851/sidecar PID 701 前后不变且 started_at 不变；direct 8010/8011 与 sidecar 实际 bind `172.25.7.64` 的 18010/18011 `/health`、18011 `/health/live` 前后均 200（5/5）；前置自然监控连胜 4 轮 ok（23:05/23:15/23:30/23:45 均 34/0/0——非 M14-33 触发、未执行 monitoring_pipeline/未修改监控工件）。诚实边界：单次、单样例、单文本真实 adapter 冒烟——不覆盖真实客户端/LiveKit 会话、移动端录音播放、长稳（soak）、并发负载、真实客户端负载形态；全局 `production_ready=false` 不变。新增证据 `docs/evidence/m14-33-real-voice-e2e-smoke/README.md`，同步更新 ROADMAP（M14-33 完成条目 + 下一生产阻塞点：真实 adapter 冒烟闭环，真实客户端/LiveKit、移动端、长稳/并发、真实负载仍开放）与 PROJECT_STATUS 顶部任务结构（M14-32 降级为前一任务并收口 PR #110 合并事实：feature head `bf597a5`、merge commit `7eb7404`、PR CI run `34989193259` 与合并后 main CI run `34989807482` 均 5/5 job SUCCESS——supervisor 验收事实）；原始证据 gitignored `.verify/m14-33-real-voice-e2e-smoke/` 不入库。

- M14-31 语音引擎受控恢复验收回填（docs-only，零代码/零生产触碰；分支 `docs/m14-32-voice-recovery-backfill` 基于 `origin/main@4d6e5c8`（PR #109 merge））：M14-31 于 2026-09-15 22:56–23:08 +08:00 在 main@`4d6e5c8`（全程 tracked clean）上完成 FunASR/CosyVoice 受控恢复并经 **Codex 独立验收 PASS（10/10，本任务验收口径）**——仅经允许的 `tools/voice/voice_service_control.py start` 受控启动：FunASR PID 3445/8010 `/health` 200（sensevoice，模型自本地 modelscope-cache 加载无重下载）78s 达健康、CosyVoice PID 3851/8011 `/health` 200（Fun-CosyVoice3-0.5B-2512）279s 达健康（中间 `managed-running` + health 503 为工具文档明示的启动期相位，终态 200）；sidecar PID 701 未重启/未停止，18010/18011 `/health` 200、18011 `/health/live` 200（alive+ready），state `running-degraded` → `running-healthy`；单次只读 pipeline 23:05:22–24 rc=0 三步 ok，34 ok/0 warn/0 critical、alerts=[]、partial=false、funasr-health 200（13.4ms）、cosyvoice-health 200（3.2ms）；工件哈希链 monitor→history→insights 互相引用一致（monitor `81d6c283…`/history `9c1f2ee4…`/insights `3412dfe9…`）；secret 扫描 9 类 0 命中（Codex 独立复核亦 0）。如实披露：restart_evaluation 基线引用的 23:00:01 monitor 轮疑为周期性监控、非本任务动作。诚实边界：单轮恢复 + 单轮监控全绿不证明长期稳定性、不构成全局 `production_ready=true` 依据；本轮 monitor 报告 `monitoring_ready=true` 为单轮逐轮字段值（≠ production ready）；全局 `production_ready=false` 不变。新增证据 `docs/evidence/m14-31-voice-engine-recovery/README.md`，同步更新 ROADMAP（M14-31 完成条目 + 下一生产阻塞点方向：恢复后持续稳定性观察与真实端到端语音链路（ASR/TTS）复验——锚定既有未决项）与 PROJECT_STATUS 顶部任务结构（M14-30 降级为前一任务）；原始证据 gitignored `.verify/m14-31-voice-engine-recovery/` 不入库。

- M14-28 语音健康 sidecar 生产切换验收回填（docs-only，零代码/零生产
  触碰；分支 `docs/m14-30-voice-cutover-backfill` 基于 `origin/main@1b6d862`
  （PR #108 merge））：M14-28 受控生产验收于 2026-09-15 在 merge commit
  `1b6d862` 上整体复跑 **9/9 PASS**（sidecar 切换/生命周期/监控集成范围）——
  受控 start argv 恒传精确 status 文件（M14-29 修复经 `/proc/<pid>/cmdline`
  live 验证，PID 703/复启 701）、manifest/status/bind 172.25.7.64/端口
  18010/18011 单属主一致、引擎停机期 502 如实透传（1–11ms，绝不伪造 200）、
  start 幂等不重复 spawn、受控 stop 仅回收 sidecar PID、伪造 manifest
  pid=867 fail-closed 拒绝（rc=3、零信号、伪造件字节不变）、真实只读监控
  管道以 `voice_health_source=sidecar` 从 canonical manifest 派生语音端点
  完成完整采集（monitor 30 ok/2 critical → exit 2 → history/insights 按序
  skipped、lock acquired/released）——M14-26→28 链（sidecar→manifest→
  monitor）端到端 live 验证。**诚实边界：不宣称语音链路全绿**（验收窗口内
  FunASR/CosyVoice 停机，两 voice 端点 502 如实入档，pipeline overall
  failed 为环境真实状态与 fail-closed 语义，非 M14-28/M14-29 失败）；
  `production_ready=false`/`monitoring_ready=false` 不变。新增证据
  `docs/evidence/m14-28-voice-health-production-cutover/README.md`，同步
  收口 m14-29 证据 README/ROADMAP/PROJECT_STATUS 与本文件 Fixed 段的
  过期 fixed-pending-review/halted 表述；原始证据 gitignored
  `.verify/m14-28-voice-health-production-cutover-rerun-1b6d862/` 不入库。

- M14-27 监控语音健康来源切换契约（代码/测试切换，不触生产）：production_monitor 新增 --voice-health-source，默认 loopback 保持 8010/8011 直采兼容；sidecar 仅读取 canonical M14-26 manifest 常量，严格校验 schema/service/PID/exact ports/RFC1918 bind，64KiB stat 预检 + 读后长度复核，语音 URL 仅派生 18010/18011 /health，Web/API 仍固定字面 loopback；清单缺失/非法在报告与 Runner/Transport 前失败且绝不回退。monitoring_pipeline monitor argv 固定追加 --voice-health-source sidecar，loopback/缺值形态拒绝。验证：M14-27 35 passed、聚焦 332 passed、监控家族六套件最终回归 635 passed；supervisor 复跑 ruff/py_compile/diff-check 全过。真实 sidecar 启动与生产监控切换观察留 M14-28，production_ready=false 不变。

- M14-26 WSL 语音健康 sidecar（实现 M14-25 §8.6 建议②健康路径旁路——
  监控健康探测不经 wslrelay 直达 WSL eth0；分支
  `feat/m14-26-voice-health-sidecar` 基于 `070646f`（M14-25 回填 R1，
  谱系 d54ad5b（PR #104 merge）→ 3e96d66 → 070646f，本地 git 可验证），
  独占 worktree 按任务书仅一个本地 commit，不 push/不建 PR（该约束为
  开发时点状态——remote 发布现已由 PR #106 合并收口，merge commit
  `ec60a093`；真实生产验收仍留 M14-28）；开发期零
  生产触碰（零生产变更/启停/重启）——不启动 sidecar、不访问任何真实
  网络或健康端点、不 inspect/stop/restart 任何生产进程；本切片实现
  与 105 项专属测试零真实 WSL，开发回合内唯一例外是既有邻近回归套件
  test_cli_status_stopped_subprocess 自身设计内的 wsl.exe bash 只读
  端口探测（一次环境性超时、复跑通过，非本切片引入））。新增
  `tools/voice/voice_health_sidecar.py`：WSL 内纯标准库双端口只读窄代理
  （18010=FunASR→恒 `http://127.0.0.1:8010/health`、18011=CosyVoice→恒
  `:8011/health` 与 `/health/live`；上游 URL 恒为模块常量绝不取自请求；
  绑定地址 fail-closed 链——/proc/net/route 默认路由接口 + UDP 零发包
  探测 + RFC1918 + 接口网段归属，任何一环失败即退出绝不退回 0.0.0.0；
  精确 GET allowlist：查询串 400/未知路径 404/非 GET 405；有界代理：
  上游超时 5s、响应体 64KiB、非 2xx 原状态码字节级透传（503 如实
  可见）、超时 504/连接失败 502 固定脱敏文案；零代理 opener；status
  文件原子写 + schema 版本化 + 仓库根 containment + 符号链接/穿越
  拒绝）与 `tools/voice/voice_health_sidecar_control.py`（Windows 侧
  start/status/stop：固定 allowlist wsl.exe argv 零 shell（AST 测试
  锁定）+ CREATE_NO_WINDOW；start 幂等（stale/reused/损坏只清理文件
  零信号）；stop TERM→10s 宽限→单次 KILL；生产保护硬边界
  PROTECTED_PIDS {867,26008} + PROTECTED_MARKERS 先于一切探测零信号；
  仅对身份核验全过的本次落档 PID 补发单次 SIGTERM；WSL 管理面失败
  统一 `wsl-management-unavailable` rc 3 单次尝试；ControlLock 串行
  化；status 恒只读）。105 项契约/生命周期测试（全 fake Runner/
  Transport/Health/Popen + importlib + 临时文件）105 passed；ruff
  （10 项行为等价窄修正后 All checks passed）/py_compile/
  `git diff --check` 全过；邻近套件 78 passed（1 例既有测试环境
  flake 新会话复跑通过，非本切片引入）。真实部署与监控端点切换需
  supervisor 合并后受控复验，`production_ready=false` 不变。详见
  PROJECT_STATUS M14-26 条目与
  `docs/evidence/m14-26-voice-health-sidecar/README.md`。

- M14-25 生产验收回填（docs-only，零代码/零生产触碰；分支
  `docs/m14-25-production-acceptance` 基于 `main@d54ad5b`（PR #104 merge），
  独占 worktree 按任务书做且仅做一个本地 commit，不 push/不建 PR）。把
  supervisor 2026-09-14 获准窗口的受控生产验收事实回填入库，收口 M14-25
  开发切片遗留边界「真实离线生产重启需 supervisor 合并后受控复验」：
  ①PR #104（M14-25 修复）已合并 main——merge commit
  `d54ad5b51ec7653c592786399f37a778f8c07e5d`（parents `1f58800` + feature
  head `98a2d773c8e098e4a2ebf4328f82ccdcc25023b6`，本地 git 可验证）；
  PR CI 5/5 job success；合并后 main CI run `34803270943` 首败为 Docker
  Hub redis 镜像拉取连接重置（外部基础设施侧）、rerun 后 5/5 job
  success（CI 两项为 supervisor 验收事实）；②受控重启时间线：重启前
  基线 CosyVoice PID 19132/8011 与 FunASR PID 867/8010 双健康、六容器
  healthy；第一次 stop 尝试因 WSL `/proc` 探测瞬时不可用 **fail-closed
  拒绝且未动任何进程**（rc=3、未启动新实例）；默认环境重试后 PID 19132
  优雅退出（TERM）、新 PID 26008 于 2026-09-14T11:52:45+08:00 启动；
  ③offline fast path 生产实证（重启偏移后 service.log 硬证据）：命中
  日志明示 pip check + CUDA closure 一致性 + import cosyvoice.cli.cosyvoice
  + torchaudio WAV 探针全过——跳过 pip upgrade/install 与 CUDA 闭包网络
  恢复（零网络安装命令）；模型载荷已就位跳过下载；wetext 离线缓存就绪
  + bridge 侧 local-only 绑定（zero ModelScope traffic）；禁用模式 grep
  `FORBIDDEN_INSTALL_DOWNLOAD_PATTERN_MATCHES=0`（无 pip install/upgrade、
  无 `download.pytorch.org`、无 `Cloning into`、无
  `FunAudioLLM/CosyVoice.git`、无模型下载）；④稳态健康：`/health` 200
  ready（model `Fun-CosyVoice3-0.5B-2512`）、`/health/live` 200
  alive/ready（loading 期 503 契约不变）；⑤主 API 同款 provider TTS
  成功：`provider=local-cosyvoice latency_ms=21896 bytes=220844`、RIFF/WAV、
  SHA-256
  `E78CC2FE0AB4D894160033F1B6975F9B802275CCD0ADE990EDAE34C688AA4ABD`
  （原始 WAV 仅 gitignored `.verify/` 不入库）；此前 smoke provider TTS
  亦成功（241964 bytes、`riff_wav=yes`），但 ASR health 因 WSL 转发层超时
  未通过（`asr=FAIL tts=PASS`）；⑥自然监控如实（R1 追加后完整口径）：
  11:45 基线轮 pipeline/monitor overall ok（五端点全 200，funasr
  3.281ms、cosyvoice 4.615ms）；**12:00–13:30 共 7 轮生产监控同构失败**
  ——每轮六容器 compose 状态 ok、Web root/login 与 API health 均 200
  （延迟约 1.3–11.9ms），仅 Windows 侧 funasr/cosyvoice 两 loopback 端点
  5s（`request_timeout_seconds=5.0`）TimeoutError；monitor
  `overall_status=incomplete`/`partial=true`、pipeline
  `overall_status=failed`、history/insights 因失败 skipped（13:45 轮工件
  仍同签名）；supervisor 现场取证：**不是引擎本体死亡**——WSL 内部直连
  FunASR `/health` 曾 200、CosyVoice PID 26008 持续存活、Windows 侧曾短暂
  恢复 200；Windows 侧 8010/8011 listener 由 **wslrelay.exe PID 17936**
  持有（listener 启动 2026-09-12 20:44:46），`wsl.exe` 管理面间歇
  `WSL/Service/0x8007274c`/`TimeoutExpired`。**边界（诚实
  口径，R1 追加后）：M14-25 验收只覆盖 offline fast path 重启幂等——不
  宣称 WSL localhost 转发长期稳定、不掩盖 12:00–13:30 七轮 pipeline
  failed、不得宣称全绿；Windows→WSL loopback/relay 稳定性是新生产阻塞
  （wslrelay.exe PID 17936 持有 listener、管理面间歇
  `0x8007274c`/`TimeoutExpired`；建议下一片 M14-26 聚焦 relay 稳定性、
  FunASR health facade/sidecar、避免监控 history/insights 因 relay 层失败
  长期 skipped，不能写成 M14-25 回归）；`production_ready=false` 不变**。
  证据 `docs/evidence/m14-25-cosyvoice-offline-restart/README.md` §8（回填
  回合对 git 谱系、29 项证据工件字节数/SHA-256、前后 manifest、偏移
  日志硬证据与监控工件独立只读复核；R1 追加 14 份轮次工件逐份解析复核）

- M14-25 CosyVoice bootstrap 离线重启幂等修复（offline fast path；分支
  `fix/m14-25-cosyvoice-offline-restart` 基于 `origin/main@1f58800`（PR #103
  merge），本 Claude 开发回合独占 worktree 仅一个本地 commit，零生产触碰）。
  动机：M14-24 受控生产重启实证 PID 18447 在模型/venv/wetext 缓存完整时仍
  因脚本无条件 pip upgrade/install 与 CUDA closure 恢复联网而中止（PyPI
  不可达）——最终 PID 19132 只是网络恢复后成功，不是幂等修复。修法：
  四道门禁（pip check / CUDA closure 运行期一致性 / import
  cosyvoice.cli.cosyvoice / torchaudio WAV 探针）函数化为单一事实源
  （`gate_pip_check`/`gate_consistency_probe`/`gate_import_probe`/
  `gate_wav_probe`，CUDA closure 契约表 18 项不动）；venv 就绪后先以四道
  门禁判定——当且仅当全部通过即跳过整个依赖安装段（pip upgrade / cu128
  初始安装 / 最小与完整清单 / 终局 CUDA 闭包恢复五类联网命令零执行）直达
  模型检查/启动，日志明示 `offline fast path 命中`；任一失败点名缺失项
  （venv 缺失/pip check/一致性/import/WAV 五类原因）进既有安装路径、安装
  后门禁照常 fail-closed；判定只依据本地解释器/本地 metadata/本地探针，
  绝不先联网探测可用性；判定与门禁调用同一组函数（无双事实源）；模型
  payload 判定、ModelScope 缓存回落、`COSYVOICE_SKIP_DOWNLOAD=1` 缺模型
  fail-closed、wetext payload/local-only、bridge 启动段原样不动。测试：
  TDD RED→GREEN 扩展 `test_voice_local_scripts.py` 5 项（文本契约 + fake
  venv 行为实证 hit/miss——hit 全程零 install/upgrade/index-url、miss 走
  安装路径 + 门禁 fail-closed exit 1；含 R1 返工教训：初版 fake 布局少拼
  `cosyvoice/` 层曾误触真实 clone，本轮对齐脚本拼接路径并加运行前防错位
  assert）。验证：`test_voice_local_scripts.py` 43 passed/1 skipped +
  `test_voice_service_control.py` 64 passed（voice_service_control.py 零
  改动）+ bash -n + py_compile + ruff + `git diff --check` 全过。
  **边界：开发验证是本地/契约验证（fake venv/payload/git），真实离线生产
  重启需 supervisor 合并后受控复验；`production_ready=false` 不变**。
  证据 `docs/evidence/m14-25-cosyvoice-offline-restart/README.md`。

- M14-24 生产验收回填（docs-only，零代码/零生产触碰；分支
  `docs/m14-24-production-acceptance` 基于 `main@165ca5c`（PR #102 merge），
  按任务书不做本地 commit/push——变更以未提交工作树交付 supervisor 审查）。
  把 Codex supervisor 2026-09-13 获准窗口的受控生产验收事实回填入库，收口
  M14-24 开发切片遗留边界「生效时机：本回合零重启、随获准窗口下一次重启
  生效」：①PR #102（M14-24 修复）已合并 main——merge commit
  `165ca5caff9b48e8514a741e77fe3420b12b5fee`（parents `c4bda69` +
  `91da769`，本地 git 可验证）；PR CI 5/5 job success、合并后 main CI run
  `34766519950` 5/5 job success（supervisor 验收事实）；②受控重启时间线：
  旧 CosyVoice PID 7481 优雅退出 → 第一次新启动 PID 18447 bootstrap 中止
  （PyPI 网络不可达、模型缓存完整仍强制 pip 联网——暴露 M14-25 离线重启
  不幂等阻塞点）→ 最终 PID 19132 于 2026-09-13T23:51:10+08:00 启动成功
  （端口 8011、manifest managed-running、运行合并后新 bridge）；③
  readiness/lifecycle 实测：端口先监听、loading 期 `/health` 503、加载完成
  `/health` 200 ready、`/health/live` 恒 200（alive/ready）——liveness 与
  readiness 不混同；④真实 TTS 冒烟 18467ms、405164 bytes、SHA-256
  `C54E871D6FD12022085713D2A52DD3ED0483FF4C3A30A643D23958EC42E33DE3`、
  RIFF/WAVE（原始 WAV 仅 gitignored `.verify/` 不入库）；⑤合成期间并发
  健康探针 28 次（`/health/live` ×14 + `/health` ×14）非 200 = 0，live
  max 409.981ms/mean≈38.35ms、health max 9.708ms/mean≈4.61ms，并发合成
  输出 585644 bytes、SHA-256
  `92E2A5FADD4DCEF4ACE0D700123A8B3F5EF7373D0E05C2F0B74797865E8DD798`；
  ⑥新进程后自然监控 00:00 与 00:15 两轮均正常（00:15 轮为 supervisor R1
  修正补充）：00:00 轮 `pipeline-20260913-160025` overall ok、
  `monitor-20260913-160023` 34 检查 ok=34/warn=0/critical=0、六容器
  healthy、restart 增量全 0、FunASR `/health` 200 3.346ms、CosyVoice
  `/health` 200 12.417ms；00:15 轮 `pipeline-20260913-161559` overall ok、
  `monitor-20260913-161557` 34 检查 ok=34/warn=0/critical=0、六容器
  healthy、restart 增量全 0、web-root 7.731ms/web-login 12.052ms/
  api-health 12.362ms、FunASR 13.161ms、CosyVoice 13.243ms（全 200）。
  **边界（诚实口径）：单机、单轮真实冒烟 + 00:00/00:15 两轮自然观察——
  两轮自然观察仍不能证明长期稳定性，非高精度 benchmark；funasr 上游事件循环阻塞未修（ASR
  负载期 warn/incomplete 属预期非回归）；cosyvoice bootstrap 离线重启
  不幂等为下一功能切片（M14-25）；`production_ready=false` 不变**。证据
  `docs/evidence/m14-24-production-acceptance/README.md`（回填回合对 git
  谱系、两 WAV 字节/SHA-256/RIFF 头、探针统计、monitor/pipeline 工件独立
  只读复核）
- M14-24 语音健康端点延迟/劣化修复（cosyvoice bridge：liveness/readiness
  分离 + 合成路径有界分块转换；funasr 侧根因确认为上游包事件循环阻塞、记为
  残余风险）。分支 `fix/m14-24-voice-health-latency` 基于 `main@c4bda69`
  （PR #101 merge），本地 commit 待 supervisor 审查发布。生产触发：
  2026-09-13 自然监控 14:00/14:45 两轮 voice 端点 warn（funasr-health
  3619/4813ms、cosyvoice-health 1001ms），14:15/14:30 两轮 funasr+cosyvoice
  双双 5s 超时（incomplete）；容器面全程 healthy、restart 增量 0。根因：
  ①funasr（上游 `funasr==1.4.15`）：转写/健康同跑单 uvicorn 事件循环且推理
  内联阻塞——第三方包，本仓不修改，残余风险与后续建议见证据 README；
  ②cosyvoice bridge（本仓）：`/health` 本身零锁正确，但合成在推理锁内做
  整张量单次 `tolist`（非抢占 C 调用）长时独占 GIL 饿死健康线程，且缺轻量
  liveness。修复（`tools/voice/cosyvoice_openai_bridge.py`）：新增
  `GET /health/live`（恒 200、不取推理锁、不碰模型，readiness 如实透出）；
  `/health` 既有 503/200 契约逐字节不变（向后兼容回归锁）；
  `_tensor_to_samples` + `SAMPLE_CHUNK_SIZE=50_000` 分块转换、推理锁收窄到
  模型前向、转换移出锁外、块间显式 yield 释放 GIL。新增
  `services/api/tests/test_voice_health_bridge.py` 14 项（liveness 三态/
  冷启动/向后兼容逐字节/持锁并发探测计时/8 路并发/分块边界与 yield 次数/
  参数守卫/文本锚点；TDD RED 11 项实证后 GREEN）。验证：新套件 14 passed +
  `test_voice_local_scripts.py` 40 passed/1 skipped（合计 54）+ ruff +
  `py_compile` + `git diff --check`。零生产触碰（不停/启/重启任何服务/
  容器/voice 进程、零 Docker 变更、零计划任务变更、零监控管道执行、监控
  阈值零改动不遮蔽告警）；合成/fake 测试；`production_ready=false` 不变。
  证据 `docs/evidence/m14-24-voice-health-latency/README.md`
- M14-23 restart 增量语义生产验收回填（docs-only，零代码/零测试/零
  workflow 改动、零生产触碰；分支 `docs/m14-23-restart-delta-production-
  acceptance` 基于 `main@6efcdfd`（PR #100 merge），本地 commit 待
  supervisor 审查发布）。把 Codex supervisor 2026-09-13 真实计划任务验收
  事实回填入库，收口 M14-23 遗留边界「真实计划任务调度下的新语义验证尚未
  运行」：①PR #100（M14-23，feature head `5926172` 含 R1 加固）合并
  main——merge `6efcdfd`（parents `73d0443` + `5926172`，本地 git 可
  验证）；PR CI run `34750025828` 与合并后 main push CI run
  `34750207530` 均 completed/success **5/5 job**（supervisor 验收事实）；
  ②计划任务 `AIOS-Monitoring-Pipeline` 合并后**自然调度两轮**（18:00 与
  18:15（+08:00），均 Last Result=0，**零手动生产触发**）：两轮管道
  `overall_status=ok`、monitor → history → insights 三步全 ok exit 0
  （monitor 4.068/4.947s、history 0.364/0.31s、insights 0.282/0.201s，
  lock acquired/released=true）；③**语义翻面核心证据**：17:45 旧代码轮
  `monitor-20260913-094502.json` overall warn（api 累计 restart_count=1、
  无 restart_evaluation）；18:00 新代码轮 `monitor-20260913-100002.json`
  overall **ok**/monitoring_ready **true**——api 累计 1 不变、同容器实例
  （started_at 三轮逐字相同）、state ok/reason stable/**delta 0**、基线
  取自 17:45 **旧 v1 工件**（向后兼容基线路径生产实证）；18:15 轮
  `monitor-20260913-101502.json` 同构保持（滚动基线
  monitor-20260913-100002）；④18:15 刷新后 history **40 样本
  ok=3/warn=37**（最新行 ok，api 累计 1 与 restart_evaluation
  state ok/reason stable/delta 0 同档并存）、insights 40 样本 ok=3、
  当前 ok 连胜 ×2、最长 non-ok 连败 **37 恰终止于旧语义最后一轮
  （09:45:02Z）**、恢复转移恰 1 次 @ 10:00:02Z、api 增量/事件/恢复计数
  全 0（累计 restart_total=39 与增量口径明确区分）。证据
  `docs/evidence/m14-23-restart-delta-production-acceptance/`（安全摘要：
  文件名 + SHA-256 + 字节数 + 时长/状态，无绝对路径/容器 ID/密钥；
  回填回合对 git 谱系与全部留档哈希独立只读复核）。**边界（诚实口径）：
  两轮自然调度验证恢复语义正确性与短期（两轮窗口）稳定性——不构成外部
  告警、指标时序存储、长期稳定性、跨机监控的验证，更不构成 production
  readiness 宣称；重建/重置/baseline-missing 路径的真实生产触发尚未发生
  （仅契约测试覆盖）；`production_ready=false` 不变**。
- M14-23 监控 restart 增量语义修复（契约扩展，含 supervisor R1 加固 amend；
  分支 `feat/m14-23-restart-delta-monitoring` 基于 `main@73d0443`，本 Claude
  开发回合独占 worktree 仅一个本地 commit（R1 修正 amend 并入），supervisor
  审查与 remote 发布（push/PR/合并）在其后进行）。动机（M14-22 验收的
  17 连 warn 生产事实）：容器 RestartCount 是 Docker 的累计事实，旧阈值
  语义按累计值判定 → api restart_count=1 的健康栈（6/6 服务 healthy、
  5/5 端点 200）永久停留 warn。修法（TDD RED 先行）：①`tools/ops/
  production_monitor.py`——`container-restarts` 阈值改评**当轮新增增量**
  （当前累计 − 基线累计）；基线 = 本轮工件目录内最新合法 prior **完整**
  monitor 工件（R1 合法身份 = schema/tool/milestone/mode 四件套 + `partial`
  恒布尔 `False`（缺失/字符串/整数一律不可作基线）+ `started_at_utc` 为
  canonical 形态（`%Y-%m-%dT%H:%M:%SZ` 且日历合法——畸形/缺失值绝不复制进
  baseline.collected_at）+ 六容器事实齐全；纯本地只读 fail-safe——零
  shell=True/零网络/零写盘/零额外生产读取 + **R1 symlink 防御**（symlinked
  工件目录/祖先 → missing/artifact-dir-unreadable 绝不跟随，固定词汇无路径
  回显；symlinked 候选文件计 invalid_skipped 而不跟随）；非法候选显式
  `invalid_skipped_count`，绝不静默当作零基线）；同实例（started_at 一致）
  增量 0 → ok（存量 warn 下一稳定调度恢复），增量含边界 ≥1/≥5 →
  warn/critical；`started_at` 变化 = 容器重建、累计下降 = 计数重置 → 各发
  一轮可见 warn（负增量绝不静默映射为零），下一稳定轮恢复 ok；无基线时
  count 0 → ok、达阈值 → 一次性 baseline-missing 可见告警（下一轮以本轮
  工件为基线即恢复）；累计 restart_count 采集事实照实入档不变；报告加法
  字段 `threshold_results.restart_evaluation`（baseline 元数据 + 逐服务
  state/reason/delta，reason 固定词汇）；check_id 仍 `container-restarts`；
  schema 向后兼容（v1 旧工件照常作基线）。②`tools/ops/monitoring_history.py`
  ——可选加法字段在场即严格校验（缺省 = v1 旧工件合法，记录形态零变化；
  违规 `restart-evaluation` fail-closed 输出零写入；**R1 baseline 元数据
  一致性联动**——status ok 恒 reason=None + 白名单 stem + canonical
  collected_at；missing 恰固定词汇 reason 集 + 空元数据；unusable 恒
  no-usable-prior-artifacts + 空元数据）；记录规范化 `restart_evaluation`
  （重建/重置事件留痕、不可比增量恒 None）；摘要新增
  `restart_delta_totals`/`restart_event_totals`/`restart_delta_sample_count`
  （诚实区分「无增量数据」与「测得为零」），累计 `restart_totals` 口径
  不重定义。③`tools/ops/monitoring_insights.py`——行级可选字段（旧
  history 行照常可读；同一 R1 一致性校验，词汇单一事实源 = history）；
  `service_summary` 区分累计 `restart_total` 与当轮
  `restart_delta_total`/`restart_delta_samples`/`restart_event_count`/
  `restart_recovery_count`（恢复 = 事件→非事件转移，legacy 行后不计）；
  Markdown 服务表分列 + 口径注记。**诚实边界：开发回合零生产执行（未
  运行真实 monitor/history/insights/管道、未触碰计划任务）；真实计划任务
  调度下的新语义验证尚未运行——合并发布后首轮调度的预期行为（api
  restart_count=1 不变 → 当轮增量 0 → overall_status 恢复 ok）是预期而
  非已验证事实**；`production_ready=false` 不变。
- M14-22 生产监控三步管道验收回填（docs-only，零代码/零测试/零 workflow
  改动、零生产触碰；分支 `docs/m14-22-monitoring-pipeline-production-
  acceptance` 基于 `origin/main@1b89d91`（PR #98 merge），本地 commit 待
  supervisor 审查发布，含 supervisor R1 修正 amend——验收面由「首轮成功」
  增强为「两轮连续成功」）。把 Codex supervisor 2026-09-13 真实生产验收
  事实回填入库：①PR #98 已合并 main（merge commit `1b89d91`，parents
  `9977ece`+`1f131aa`，本地 git 可验证），合并后 main CI run
  `34737912550` 最终 completed/success、5/5 job（Web/Android/Release
  tools/Docker/API）；②计划任务 `AIOS-Monitoring-Pipeline` canonical 工具
  status=**installed**，Action/参数/cwd/Hidden/触发器/间隔/时限逐项匹配，
  **两轮连续真实调度成功**——Last Run 12:30:01 与 12:45:01（+08:00）均
  Last Result=0、下一轮 13:00；③canonical 管道报告两轮
  （`pipeline-20260913-043007` 12:30 + `pipeline-20260913-044503` 12:45，
  gitignored 不入库，仅安全摘要）：两轮 overall_status=ok，monitor →
  history → insights **三步全 ok、exit 0**（第一轮 duration
  1.756/3.055/0.432s、第二轮 1.27/0.13/0.143s），lock acquired/released=
  true；**insights 产物首次由真实计划任务产出（12:30）并在第二轮（12:45）
  刷新**（首轮 insights.json `d45c0bcc…b17b`/6112B、insights-summary.md
  `89e438d1…75b9`/2740B；第二轮 monitor-20260913-044501.json
  `6006ce2d…1d1d`/14346B、insights.json `8c82612d…1cea`/6277B、
  insights-summary.md `ac661d11…179c6`/2780B——回填回合两轮独立重算
  逐项一致）；④monitor 工件两轮结论同构（12:30/12:45）：
  overall_status=warn、partial=false——6/6 服务 healthy/running、5/5 端点
  200 且延迟全部正常，**唯一 warn = api 容器 restart_count=1 达
  restart_warn=1**（两轮同一静态累计值；静态累计阈值语义，**非服务故障**；
  monitoring_ready=false）；⑤insights-summary（12:45 刷新后）：18 个
  历史样本 ok=1/warn=17/critical=0、当前 warn 连续 17 条（最新样本服务
  全 healthy、端点全 200，唯一阈值告警来自 api restart_count=1）。
  **M14-21 遗留边界「管道真实三步运行与下一轮观察」闭环为已验收——两轮
  成功证明重复调度执行，不构成长期稳定性证明；未决项保留：外部告警
  接入、指标时序存储/查询、阈值随时间标定、真实客户端验收、>60s/
  真实负载/跨机长稳、AGC 发布；下一步建议 M14-23「监控告警语义修复/
  阈值演进」（区分静态累计 restart_count 与新增 restart、引入恢复态——
  依据 17 连 warn 生产事实）。`production_ready=false` 不变**。证据
  `docs/evidence/m14-22-monitoring-pipeline-production-acceptance/`。

- M14-21 监控洞察接入持续管道（`tools/ops/monitoring_pipeline.py` 三步化 +
  `tools/ops/monitoring_insights.py` 默认输入对齐 history canonical 输出 +
  契约测试 52→63/91→93/pipeline_task 预算 pin 三步化；**合并收口（回填
  2026-09-13）：已随 PR #98 合并 main（merge commit
  `1b89d9182566ba0ff4afe8f02893acefae94fb43`，feature head `1f131aa`，
  合并后 main CI run `34737912550` 5/5 job SUCCESS）；遗留观察边界
  「管道真实三步运行与下一轮观察」已由 M14-22 真实调度验收闭环——下文
  开发时点表述为快照**）。持续管道序列由 monitor → history 升级为
  **monitor → history → insights**：insights 仅在 history status=ok 后运行
  （history.jsonl 完整落盘才可洞察），任一前置失败/跳过以固定词汇原因
  （`monitor-status-<status>` / `history-status-<status>`）入档且前置步骤
  退出码/类别如实保留绝不遮蔽——insights 失败不改变 monitor/history 事实；
  修复**双路径事实源**：insights 默认 source 由 M14-15 切片遗留的
  `.verify/artifacts/m14-13-monitor-history-retention/`（无写入者）改为
  **直接引用同仓 `monitoring_history.DEFAULT_OUTPUT_DIR` 常量**
  （`.verify/artifacts/m14-13-monitoring-history/`——契约测试锁定
  pipeline/history/insights 三方 resolve 全等，canonical 输入自此恒有
  写入者）；固定命令白名单新增**唯一** insights 精确形态（`--execute` +
  既有精确确认短语 `EXECUTE READ-ONLY MONITORING INSIGHTS`，与工具自身
  门禁逐字一致，回归测试锁定；与管道/monitor 门禁短语互不通用）且
  **恒不带 `--source`**——canonical 输入经已修正默认值生效，`--source`/
  `--event-limit` 注入一律拒绝且内层零调用；insights 有界超时 5–50s
  （默认 15s），三步硬顶之和 540+120+50=**710s < PT12M=720s** 执行时限
  （monitor/history 界与默认不变——兼容面保留；task 注释与预算交叉 pin
  测试同步三步口径）；报告 config/stages/Markdown 加 insights stage
  （产物两固定名 + SHA-256 + 字节数；目录缺失如实 `artifact-dir-unreadable`），
  schema_version 仍 1（加法演进），安全面不变（无绝对本机路径/无子进程
  原文/无 env/token，写前 redact_secrets 终防线）。验证：监控家族三套件
  **238 passed**（TDD RED 先行后 GREEN）+ 监控全家族五套件 **510 passed**
  + 全量 services/api 套件 2496 passed / 34 failed（34 失败经主仓同
  commit 基线对照确认与 main@9977ece 完全一致——本切片零触碰的环境
  敏感域既有失败，非本切片引入）+ ruff +
  `py_compile` + `git diff --check` 全过 + 合成端到端 **40/40 PASS**
  （fake-runner 四场景退出码/固定词汇 skip 原因/报告卫生面 + 真实只读
  insights 于 canonical 目录形态 EXIT 0 + `--source` 注入拒绝——全程
  临时目录）。**开发回合零生产执行、零 scheduler mutation、零 canonical
  `.verify` 触碰；`production_ready=false` 不变**。证据
  `docs/evidence/m14-21-monitoring-insights-pipeline/`。

- M13-15 Harmony 治理运行时验收（docs-only，`docs/m13-15-closeout`，PR #96）——M13-14 结构性修正（固定 header 于 Scroll 之外）的**设备端运行时重新测量**（模拟器 `127.0.0.1:5555`，app PID 3826，隔离 mock `tools/harmony_mock` 端口 8765，host 8000 生产栈未触碰）：固定 header 在 load/reload/tab-return/error/recovery/deep-scroll **全状态可见**；加载中刷新按钮**可见但禁用**（防重复刷新），加载/错误/恢复后重新启用（`node scan-refresh-enabled.cjs .` 可复现逐态验证）；只读边界端到端成立（零写操作/零载荷渲染/零账号-token-密码持久化）；hilog 8538 行（`node scan-hilog-crash-markers.cjs hilog-full.txt` 退出码 0）**0 FATAL/0 AppCrash/0 AppFreeze/0 JS_ERR**；HAP 515871 bytes / SHA256 `a5829186e8c7f309f88d993db2b20ba5dfa02ce9db76e622183d849adff46095`；证据 `docs/evidence/m13-15-harmony-governance-runtime-acceptance/`。

**mock-only 边界**：未接真实 provider/生产后端/生产 DB，零 `apps/harmony/**` 改动，`production_ready=false` 不变。

- M14-15 监控历史洞察/告警摘要第 1 切片（`tools/ops/monitoring_insights.py` +
  `services/api/tests/test_monitoring_insights.py` 90 项契约测试；
  **合并收口（回填 2026-09-13）：已随 PR #90 合并 main（merged_at
  2026-09-12T18:10:07Z，merge commit `ce10060`，feature head `acdbbb5`；
  PR CI run `34710154644` 与合并后 main CI run `34710372281` 均 5/5 job
  SUCCESS）——合并 = 代码入库 + CI 绿**；
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
  `tools/ops/run_monitoring_pipeline_silent.vbs`；
  **合并收口（回填 2026-09-13）：已随 PR #89 合并 main（merged_at
  2026-09-12T17:28:55Z，merge commit `f6f0356`，feature head `23a0390`；
  PR CI run `34708110486` 与合并后 main CI run `34708350434` 均 5/5 job
  SUCCESS）——合并 = 代码入库 + CI 绿，持续运行仍为零**；
  **开发回合零真实管道
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
- M14-115 provider-smoke preflight search 预检超时误报修复 + 当前生产恢复证据固化（恢复证据固化/缺陷修复/文档切片，分支 `ops/m14-115-provider-smoke-current-recovery`（独立 worktree，基于 main `cf0a512`（PR #201 merge = M14-114 China Bing 直连引擎合入，精确基点），单 local commit，不 push）：**缺陷**——`provider-smoke-preflight`（M14-112）用单一 `PROBE_TIMEOUT_SECONDS = 10.0` 覆盖全部探测，而 search 预检（`/search?format=json`）是唯一触发真实上游聚合的探测：supervisor 生产恢复（searxng 单容器 recreate，其余容器 ID 未变；China Bing `https://cn.bing.com` 直连启用，PR #201 已合 main `cf0a512`，CI run 35905222519 5/5 success）后真实聚合延迟实测 10.3s（search-smoke pass 10342ms，results=5 query_len=21）/12.6s（手工「Python asyncio tutorial」40 条含 bing 10 条，12664ms）/18.5s（手工「清华大学」22 条，18502ms）——固定 10s 把慢聚合误报为 `endpoint_timeout`（voice ready、LLM ready/model resident、search 工具性误报 blocked）。**修复（最小改动，常量+测试，不新增配置面）**——新常量 `SEARCH_PROBE_TIMEOUT_SECONDS = 30.0`（保守覆盖观测到的最大聚合延迟仍有界）；`_probe_json` 增加 keyword-only `timeout_seconds` 参数（默认 `PROBE_TIMEOUT_SECONDS`，voice（/health）/LLM（/api/ps）调用点零改动、10s 语义不变）；`_check_search` 传 search 档并在报告 search 槽位新增 `probe_timeout_seconds` 字段透出实际探测界（归因可检视）；两档均固定常量不开放覆写（有界性是契约的一部分）；模块 docstring/CLI `--help`/DEVELOPMENT.md 超时描述同步分档。**证据固化**——新 `docs/evidence/m14-115-provider-smoke-current-recovery/README.md`：supervisor 恢复事实 + 真实三步冒烟（search/voice/llm 全 pass，`provider-smoke-aggregate` 三项 pass，2026-09-23T18:57Z）+ 如实保留的失败 attempt（search×3 = WSLENV 透传漏项 envmiss/querymiss/endpointmiss，非 provider 失败；llm×1 = 256 tokens thinking-only 空 content 脚本正确 fail，第 2 次 MAX_TOKENS=1024、timeout 仍 300s，正文 12 chars，rubric [True,True] conf=1.0；voice = ASR 2682ms/42 bytes + TTS 22490ms/241964 bytes RIFF WAV），8 个 gitignored JSON 以 SHA256+字节数锚定。**诚实边界：不声称 release_ready/production_ready——provider-smoke 当前证据闭合但 long-soak/审批链/发布材料缺位；本切片零生产触碰、零 secret 读取；preflight 修复后未对生产 searxng 重跑（30s 档正确性由契约测试+真实延迟样本论证）。**验证：`test_provider_smoke_preflight.py` 55 passed（基线 54+新增 `test_search_probe_timeout_30s_voice_llm_10s`）+ `test_searxng_local_provider.py` 17 passed + `test_provider_smoke_evidence.py` 128 passed + ruff（默认与 --select F,E9）+ py_compile + git diff --check 全净（canonical venv）。同步更新 ROADMAP（M14-115 状态更新）、DEVELOPMENT（预检超时分档）与 PROJECT_STATUS 顶部任务结构（M14-115 接替顶部、M14-112 移为次席）。
- M14-37 LiveKit 默认拓扑稳定性（默认/受控浏览器模式开关 + 媒体面独立绑定）——分支
  `fix/m14-37-livekit-default-topology` 基于 `main@54e8005`（PR #113 merge），本 Claude 开发
  回合独占 worktree 单 local commit 不 push。背景（M14-35 遗留生产阻塞）：当前栈 LiveKit
  以 `--node-ip 127.0.0.1` 通告媒体且 UDP 仅绑 loopback，Chromium/WebRTC 默认不收集
  loopback ICE candidate，默认浏览器直连存在间歇性 ICE 失败（拓扑级不确定）；M14-35 R2 的
  `--allow-loopback-in-peer-connection` 受控 flag 只是验收口径，生产用户浏览器默认不具备。
  修复双侧：① 验收口径诚实化（`infra/verify_web_livekit_client.py`）——新
  `AIOS_LIVEKIT_BROWSER_LOOPBACK` 严格开关：未设置/`0` = **default 模式绝不注入 loopback
  flag**（代表生产用户默认拓扑，失败即真实生产阻塞证据）；字面 `1` = controlled 受控模式
  （M14-35 R2 口径保留）；任何其他值（10 变体）**ENV-BLOCKED fail-closed** 退出码 2，绝不
  静默当默认；flag 字面量收敛为 `LOOPBACK_FLAG` 常量全源码唯一定义处（契约测试锁定恰好
  出现一次，杜绝旁路硬编码）；results.json 新增 browser 段（mode/flag present/argv 摘要，
  永不泄露 JWT/token）。② 媒体面独立绑定拓扑——`AIOS_LIVEKIT_BIND_IP` 单独把 livekit
  绑本机 LAN IP（端口回落链 LIVEKIT_BIND → BIND → 127.0.0.1，未设置存量行为零变化；
  node-ip 回落链 EXTERNAL → LIVEKIT_BIND → 127.0.0.1），浏览器拿到常规 LAN candidate 无需
  受控 flag，API/Web 仍走 `AIOS_BIND_IP` 不整体公开；`HOST_LIVEKIT_BIND_IP` 透传
  settings `host_livekit_bind_ip`；`validate_exposure` 新增 LiveKit-only 公开分支
  fail-closed：非 loopback 绑定要求强 `LIVEKIT_API_SECRET`（≥32 字节且非仓库公开默认
  占位值）+ 浏览器可达 `PUBLIC_LIVEKIT_URL`（不得缺失/容器内部/loopback），API/Web 面
  公开时 M9-06/M9-07 原四项校验语义零改动；rework（同 commit amend）：
  `livekit_bind_ip` 经 stdlib ipaddress 解析，wildcard（`0.0.0.0`/`::`）与非法
  字面量任何面状态下 fail-closed 拒绝（`--node-ip` 不能通告 wildcard，错误信息
  要求具体本机 LAN IP；空串保留 compose「未启用」哨兵；IPv4-mapped 先解包再
  分类），browser 段脱敏断言由占位恒真改为真实否定（token/JWT/secret 字样不得
  出现）；文档 `.env.example` + `docs/DEVELOPMENT.md`
  （一键命令、回落链、0.0.0.0 警告、TURN 后备衔接、验收口径）。测试新增 14 个函数（开关
  语义 4 含非法值参数化 10 变体、argv 纯净性、browser 段可审计与脱敏 3、源码文本契约 2、
  暴露校验纯函数 2、compose 渲染矩阵 1——`docker compose config` 渲染不启容器，锁定
  livekit 走 LAN 而 api/web 仍 loopback + node-ip 回落链；rework 另 +1 fail-closed
  用例）。本地验证：聚焦 77 passed（含 rework 用例）、
  ruff/py_compile/`git diff --check` 全绿、增行 secret 扫描 0 真实凭据命中；真实默认浏览器
  验收 **8/8 连跑 verdict=passed**（gitignored `.verify/m14-37-default-topology/run1..run8`：
  每轮 mode=default、loopback_flag_present=false、token/connect/data/mic/cleanup 五步全
  passed、ws_url=ws://127.0.0.1:7880、DOM 无 JWT、检测窗口 console 零错误、生产栈零重启
  零 env 变更）；受控回归 1/1 passed（mode=controlled、flag present——M14-35 R2 旧口径
  不破坏）；非法值探针 ENV-BLOCKED 退出码 2 无 results.json；8 连跑后本 worktree 孤儿
  进程扫描 FOUND 0（M14-36 不回归）。诚实边界：8/8 是间歇性失败未在本批复现的实证而非
  loopback 拓扑已稳定（生产栈绑定未动，间歇性 ICE 风险仍在）；拓扑修复以能力形态交付，
  实际切换（LAN 绑定 + 强 secret + 可达 URL 后 up -d）与切换后默认拓扑验收留运维显式
  执行；不覆盖远程设备/跨 NAT（M10-05 TURN 后备域）/多人/重连/长稳；不构成
  `production_ready=true` 依据。证据 `docs/evidence/m14-37-livekit-default-topology/README.md`。
- M14-36 Web 验收工具进程生命周期修复（Windows 孤儿进程树精确回收）——分支
  `fix/m14-36-acceptance-process-cleanup` 基于 `main@3a5c095`，本 Claude 开发
  回合独占 worktree 单 local commit 不 push。缺陷（监督者盘点实证）：
  `infra/verify_web_livekit_client.py` 旧版 start_web 以 npm 包装链
  （mise.exe → cmd.exe npm.cmd → node）为 Popen 对象、finally 只 terminate
  该包装器单 PID——Windows 进程终止不级联子进程，`next start` 的 node.exe
  与中间 shim 全部存活为孤儿，跨次验收累计 26 个 node.exe/mise.exe 孤儿
  进程锁死 worktree 文件句柄。修复只动验收工具、零生产服务触碰：
  ①`resolve_node_executable()` 以 `node -p process.execPath` 解析真实 node
  可执行文件，直接 Popen `[<真实node>, <next_bin()>, "start", "-p", <port>]`
  （Popen PID 即最终长命进程本身，无中间 shim）；`next_bin()` 惰性解析
  next CLI 真实入口（npm workspaces 提升 → 仓库根 node_modules 优先，
  兼容 apps/web 本地；全缺 ENV-BLOCKED fail-closed）；②`stop_process_tree()`
  有界精确树回收：Windows 恰 `taskkill /PID <Popen pid> /T /F`（只该 PID
  树，绝不按端口/进程名扫杀——无 /IM、pkill、killall、netstat、Get-Process、
  wmic）；POSIX `start_new_session=True` 自成进程组 → SIGTERM → 有界宽限
  → SIGKILL；已退出零动作；③main finally 换用 stop_process_tree。TDD
  新增 `test_verify_web_livekit_client.py` 20 项全 mock 契约测试（probe
  argv/平台分支/树杀语义/文本锚点与禁词，绝不真实执行 taskkill/killpg）。
  验证：聚焦 20 passed、全量 services/api **2826 passed / 0 failed /
  33 skipped**、ruff（services/api + infra 脚本）+ py_compile +
  `git diff --check` 全过；真实受控浏览器验收一次执行 **13/13 checks
  verdict=passed exit 0**（复用生产 API/LiveKit/DB 零重启；token/connect/
  data/mic/cleanup 五步全 passed、DOM 无 JWT、检测窗口 console 零错误；
  受控条件同 M14-35 R2 loopback flag 口径），验收前后孤儿扫描
  （Win32_Process：node/mise/cmd 命令行含本 worktree 路径）均 **FOUND 0**
  ——旧缺陷形态下退出后必然残留，修复后退出即零残留。边界：POSIX 组杀
  仅全 mock 契约验证（未在 POSIX 真机跑）；单次验收不穷尽外部强杀等
  异常路径（但该路径下孤儿面已最小化——无中间 shim 链）；默认拓扑 ICE
  间歇性失败未消除（独立生产阻塞项）；`production_ready=false` 不变。
  证据 `docs/evidence/m14-36-acceptance-process-cleanup/README.md`（原始
  证据 gitignored `.verify/m14-36-acceptance-process-cleanup/` 不入库）
- M14-29 sidecar status 路径契约修复（已随 **PR #108 合并 main**——feature
  head `527c454`、merge commit `1b6d862`，PR CI run `34933123493` 与合并后
  main CI run `34946366049` 均 5/5 job SUCCESS；分支
  `fix/m14-29-sidecar-status-path` 基于 `d19c296`，2 文件窄改）：M14-28
  受控生产验收步骤 4 暴露的确定性缺陷——控制器
  `status_file_relpath` 返回 artifacts **目录**而非 status 文件，
  `cmd_start` 将其作为 `--status-file` 传给 sidecar，sidecar fail-closed
  守卫正确拒绝目录目标（`unsafe-status-target`）于监听前退出，20s 落档
  等待超时、rc 1（原始证据：主仓库 gitignored
  `.verify/m14-28-voice-health-production-cutover/`，不入 git）。修复只改
  调用契约、绝不绕过守卫：拆分 `artifacts_dir_relpath`（目录，锚定
  manifest.log/日志提示）与 `status_file_relpath`（恒
  `<目录>/sidecar-status.json` 精确文件，仓库内 repo-relative/仓库外绝对
  posix）；start 的 spawn argv/落档等待/身份核验 probe 与 stop/status
  全部 probe 调用收文件路径；信号语义与保护边界零改动。TDD 回归：
  新增 8 项测试（修复前 8 failed/105 passed，修复后 **113 passed**，
  既有安全测试零削弱）；M14-27 套件 35 passed、ruff/py_compile/
  `git diff --check` 全过。M14-28 真实验收已在合并后整体重跑闭环
  （2026-09-15 于 merge commit `1b6d862` 复跑 **9/9 PASS**，sidecar
  切换/生命周期/监控集成范围；窗口内语音引擎停机、voice 端点 502 如实
  透传——不宣称语音链路全绿；pipeline overall failed 为环境真实状态而非
  任务失败，详见 `docs/evidence/m14-28-voice-health-production-cutover/README.md`），
  production_ready=false 不变。详见
  `docs/evidence/m14-29-sidecar-status-path/README.md`。

- M14-20 监控历史索引历史 incomplete 工件修复（本地 commit 待
  supervisor 审查发布）：M14-14 管道定时运行后默认源目录混有 2026-09-12
  栈修复期间 monitor 自产的历史 `partial=true + overall_status=incomplete`
  工件（时点 31/33），`tools/ops/monitoring_history.py` 旧「任何
  incomplete 一律整体拒绝」语义令其每轮 EXIT 2 零输出——监控历史永久
  零索引。修法 = 新增纯谓词 `is_recognized_incomplete`：识别窄类（完整
  monitor 身份 schema_version/tool/milestone==M14-12/mode==execute +
  incomplete/partial=true 共现对——monitor 契约中二者恒共现）→ 整件跳过
  不入档（`skipped_incomplete_count` 显式于 summary/摘要 MD/stdout；源
  文件绝不改动/删除；跳过件内容绝不进入输出——仅计数）；完整
  ok/warn/critical 样本照常严格校验入档；其余任何 incomplete/partial
  形态（身份不符/矛盾组合）仍 fail-closed；候选全为 incomplete → 新固定
  词汇 `no-complete-sources` 拒绝；`build_samples` 签名不变
  （monitoring_insights 委托面，跳过语义同样生效）；`history.jsonl` 记录
  schema 零改动。契约测试新增 9 + insights 委托回归 1（TDD RED 先行）；
  验证 = 聚焦 83 passed + 邻居四套件 411 passed + ruff/py_compile/
  `git diff --check` + 真实 canonical 源目录端到端实证（39 工件：exit 0、
  8 条完整记录入档、跳过 31 计数显式、源逐字节不变、两遍输出逐字节
  相同）。**不删除/不改写任何历史源工件（跳过而非清理是有意设计）**。
  详见 `docs/evidence/m14-20-monitoring-history-historical-incomplete/`。
- M14-19 CosyVoice bootstrap ModelScope 模型下载载荷过滤（**合并收口
  （回填 2026-09-13）：已随 PR #95 合并 main（merged_at
  2026-09-13T00:50:31Z，merge commit `4c9458f1f6d05857edb209f463887b48b7774b
  a4`，feature head `f63551b00be2458f2f52a223398ceb8f40583420`；PR CI run
  `34728812943` 与合并后 main push CI run `34729005137` 均全部 5 job
  SUCCESS）——下文「本地 commit 待 supervisor 审查发布」为开发时点快照**）：
  模型下载原为无过滤 `snapshot_download(model_id,
  local_dir=...)`——整仓下载 ≈9.85GB（2026-09-13 ModelScope API 实测
  `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` 共 19 文件），其中 ≈4.44GB 与所选
  运行时无关（`llm.rl.pt` 2.02GB 固定 commit 全仓零引用 /
  `flow.decoder.estimator.fp32.onnx` 1.33GB 仅 `load_trt=True` 路径 /
  `speech_tokenizer_v3.batch.onnx` 0.97GB 仅训练单例路径 / 宣传图与仓库
  元数据）。修法 = 新增 `tools/voice/cosyvoice_model_payload.py` 载荷契约
  模块（仅标准库；`RUNTIME_PAYLOADS` 按 model_id 注册 12 精确路径白名单 +
  11 项 strict 必需集），bootstrap 下载段经它 fail-closed 解析后以
  `snapshot_download(..., allow_patterns=[...])` 下发（modelscope 1.20
  受支持参数；断点续传/缓存语义不变），下载后逐一存在性校验必需文件；
  未知 model_id / 空·畸形白名单 / 必需件未被白名单覆盖一律拒绝
  （modelscope 空列表语义 = 不过滤 = 整仓）。**CosyVoice-BlankEN/\* 经源码
  + 生产 venv 探针实证属真正必需载荷**（cosyvoice.py:200 override →
  `Qwen2Encoder(Qwen2ForCausalLM.from_pretrained)` + `AutoTokenizer`，缺
  `model.safetensors` 即 OSError）——保留下载；白名单而非 ignore 列表：
  上游新增文件默认排除（fail-closed）。bash 侧四文件载荷判定/缓存回落/
  wetext 预热零改动；净效果 = 新冷机 ≈5.4GB（**-45%**）。契约测试 6 项
  新增（必需件放行/无关资产排除/BlankEN 显式选择语义/fail-closed/
  fnmatch 语义镜像/bootstrap 调用形态）。
- M14-18 CosyVoice 最小运行时 openai-whisper triton 元数据冲突修复（生产
  bootstrap 实证；**合并收口（回填 2026-09-13）：已随 PR #93 合并 main
  （merged_at 2026-09-12T21:53:44Z，merge commit `cc782b0`，feature head
  `2185bdc`；PR CI run `34721050144` 与合并后 main CI run `34721233549`
  均 5/5 job SUCCESS）；其后 PR #73 合并产生 main `42653b7`，merge-main
  CI run `34726676218` 亦 5/5 SUCCESS。合并 = 代码入库 + CI 绿；生产实际
  升级与冷启动复验仍为 supervisor 受控执行中，非完成宣称**）：M14-17 终局 CUDA
  闭包恢复已成功（2026-09-13 生产 service.log：torch `2.11.0+cu128` /
  triton `3.6.0` 及全部闭包成员就位），但 `cosyvoice-runtime-requirements.txt`
  的 `openai-whisper==20231117` METADATA 声明 `triton<3,>=2.0.0`（无环境
  标记）——与闭包 `triton==3.6.0` 冲突，`pip check` 附加门禁被「openai-
  whisper 20231117 has requirement triton<3,>=2.0.0, but you have triton
  3.6.0」卡死 FAIL；且清单分支装 20231117 时该约束触发 pip 回溯把 torch
  一路降级（实证 2.14.0→…→2.3.1）再连带降级 CUDA 闭包。修法 = **升级 pin
  而非绕过门禁**：`openai-whisper==20250625`（METADATA `triton>=2`，
  x86_64/linux 环境标记、无上界）与 cu128 闭包共存，运行时依赖集合与
  20231117 完全一致（more-itertools/numba/numpy/tiktoken/torch/tqdm，torch
  无版本约束，不替换 torch/CUDA 包；supervisor 只读 dry-run 确认 + PyPI
  sdist PKG-INFO 直读复核）；CosyVoice 固定 commit `074ca6d` 用到的两个
  API（`whisper.log_mel_spectrogram(audio, n_mels=128)` 与
  `whisper.tokenizer.Tokenizer(encoding=…, num_languages=…, language=…,
  task=…)`）经 20231117（生产 venv 已装源码）vs 20250625（sdist）逐字 diff
  实证**源码级不变**（tokenizer.py 无差异、log_mel_spectrogram 仅
  docstring 更新且明确支持 n_mels=128；生产 venv 只读探针 import + 签名
  实跑通过）。禁止以 `--no-deps` 装清单、强制降级 triton、改写已装
  dist-info 或弱化 pip check 绕过（M14-17 门禁/闭包契约/一致性探针一字
  不动）；官方 requirements.txt 在固定 commit 仍 pin 20231117——本清单
  **有意偏离**官方 pin。注意 20250625 在 PyPI 仅 sdist（无 wheel），pip
  从源码构建（纯 Python 包）。契约测试新增 3 项：
  `test_openai_whisper_pin_bump_contract`（唯一精确 pin + 升级依据证据
  锚点）、`test_openai_whisper_triton_no_bypass_contract`（两文件全生效行
  禁 `--no-deps`/`--force-reinstall`/`--ignore-installed`/triton 降级 pin/
  dist-info 改写 + pip check 门禁保持 `if !` fail-closed 形态）、
  `test_whisper_api_compat_contract_when_installed`（whisper 可导入环境的
  两 API 签名回归；canonical venv 无 whisper 显式 skip）。验证：聚焦
  `test_voice_local_scripts.py` **34 passed + 1 skipped** + `test_voice_
  service_control.py` 回归 **64 passed** + ruff + `bash -n` +
  `git diff --check` 全过（canonical venv 解释器仅执行，零 canonical 检出
  改动）。**本切片只修复可复现清单/bootstrap 契约与文档：未重跑 bootstrap、
  未向任何 venv 装依赖、未启停任何进程/容器（生产 venv 仅只读探针），
  生产实际升级与冷启动复验由 supervisor 合并后受控执行；
  `production_ready=false` 不变**。证据见
  `docs/evidence/m14-18-cosyvoice-whisper-triton/`。
- M14-17 CosyVoice bootstrap CUDA 闭包终局修复（真实生产 venv 只读取证；
  **合并收口（回填 2026-09-13）：已随 PR #92 合并 main（merged_at
  2026-09-12T19:12:05Z，merge commit `5fbeb22`，feature head `74d3988`；
  PR CI run `34713163316` 与合并后 main CI run `34713465954` 均 5/5 job
  SUCCESS）。合并 = 代码入库 + CI 绿；生产 venv 实际修复与冷启动复验为
  supervisor 合并后受控执行，非完成宣称**）：M14-16 的终局 `--no-deps` 回写只恢复三个
  主轮——生产 venv 实证（2026-09-13 `pip check`）torch/torchaudio/torchcodec
  均已 cu128，但 nvidia-cudnn-cu12 8.9.2.26（torch metadata 需
  `==9.19.0.56`）、nvidia-nccl-cu12 2.20.5（需 `==2.28.9`）、triton 2.3.1
  （需 `==3.6.0`），多数 CUDA runtime 仍是 12.1 系列（清单分支装 torch
  2.3.1 时连带降级的闭包），`import torch` 失败缺 `libcudnn.so.9`。根因：
  `--no-deps` 不恢复 torch metadata 声明的 Linux CUDA 依赖闭包。
  `bootstrap_cosyvoice_wsl.sh` 修法：① 终局改为**完整依赖解析**（同一
  cu128 index，无 `--no-deps`）——三件套精确 pin + torch `2.11.0+cu128`
  真实 METADATA（Linux 段，自生产 venv `torch-2.11.0+cu128.dist-info/
  METADATA` 导出）声明的闭包成员：`cuda-toolkit[cublas,cudart,cufft,
  cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==12.8.1`、
  `cuda-bindings>=12.9.4,<13`、`nvidia-cudnn-cu12==9.19.0.56`、
  `nvidia-nccl-cu12==2.28.9`、`nvidia-cusparselt-cu12==0.7.1`、
  `nvidia-nvshmem-cu12==3.4.5`、`triton==3.6.0`——精确 pin 使 PyPI 清单
  分支无从再降级，成员 pin 不满足即强制解析（三件套已满足也能修复被降级
  的闭包，自愈存量破损态），全部满足即 no-op；② 闭包恢复后新增
  `pip check` **附加门禁**（fail-closed，文案指向「CUDA closure 未恢复」；
  注释/测试明确它只是附加门禁——实证两盲区：混合 ABI 报「No broken
  requirements」、extras 门控的 12.8 系列 nvidia runtime 错配不报，
  **不能替代真实 import/运行探针**）；③ 一致性探针（M14-16 契约保留、
  顺序不变）扩展 **18 项 CUDA closure 契约表**（`==` pin 精确相等 /
  cuda-toolkit extras 通配前缀段边界匹配 / cuda-bindings 范围），任一不符
  即 FAIL 点名。契约测试：改写 `test_bootstrap_torch_reconciliation_order_
  contract`（无 `--no-deps` + 闭包成员 pin + pip check 门禁顺序锁定）、
  新增 `test_bootstrap_cuda_closure_contract`（18 项逐字锁定 + 生效代码无
  `--no-deps` + cu128 index 恰两处/清单分支恒 PyPI）、
  `test_bootstrap_torchcodec_pinned_on_cu128_install_line` 改以完整初始
  安装形态锁定、`test_runtime_requirements_contract` 扩展排除闭包成员前缀。
  验证（TDD RED 先行后 GREEN）：聚焦 + 回归合并 **96 passed**（32+64，
  exit 0）+ ruff + `bash -n` + `git diff --check` 全过（canonical venv 解释
  器仅执行，零 canonical 检出改动）；探针已在生产 venv **只读实跑**验证
  fail-closed 精确点名（`nvidia-cudnn-cu12 8.9.2.26 != 9.19.0.56`，先于
  torch import 触发），通配/范围辅助函数另以隔离单测验证。**本切片只修复
  可复现 bootstrap 契约：未重跑 bootstrap、未装任何依赖、未启停任何进程/
  容器/监控任务，不构成生产运行恢复宣称——生产 venv 实际修复与冷启动复验
  由 supervisor 合并后受控执行；`production_ready=false` 不变**。证据见
  `docs/evidence/m14-17-cosyvoice-cuda-closure/`。
- M14-16 CosyVoice bootstrap 依赖解析降级回归（真实生产冷启动实证；
  **合并收口（回填 2026-09-13）：已随 PR #91 合并 main（merged_at
  2026-09-12T18:28:09Z，merge commit `d6d0635`，feature head `f65251f`；
  PR CI run `34711072494` 与合并后 main CI run `34711281622` 均 5/5 job
  SUCCESS）**）：最小运行时清单的 PyPI 依赖解析
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
  `production_ready=false` 不变**。证据见
  `docs/evidence/m14-16-cosyvoice-runtime-consistency/`。
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


