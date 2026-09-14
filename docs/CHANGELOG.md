# Changelog

All notable changes to the AI Learning OS project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/), versions follow semver.

## [Unreleased]

### Added

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
