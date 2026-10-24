# 学习能力证据状态（实现 vs 验证分离）

> 升级方案治理建议：**实现状态**（代码存在）与**验证状态**（运行证据存在）
> 分开记录；本文件只记「验证状态」与证据路径。能力全景与历史见
> `docs/audit/2026-10-baseline.md`（Phase 0 基线）。

状态语义：`pass`（真实执行且退出码 0）/ `failed`（真实执行失败）/
`not_executed`（未执行，不可折算为 pass）。PG 生产迁移验证在 CI migration
job；本机 SQLite 契约测试覆盖迁移 up/down 语义。

| 能力 | 实现里程碑 | 验证状态 | 证据 |
|---|---|---|---|
| Sprint 状态机域层（11 阶段单向链/幂等/暂停恢复/重计划边界） | M16-001 | pass（2026-10-22，13 域测试） | `services/api/tests/test_sprint_state.py`（穷举 121 组合全非法对拒绝） |
| 学习合同 API（创建校验/确认幂等/归属 404） | M16-001 | pass（SQLite 契约） | `services/api/tests/test_learning_contract_api.py::test_contract_crud_and_confirmation` |
| Sprint 生命周期 API（409 跳段/409 回退/422 未知阶段/暂停恢复） | M16-001 | pass（SQLite 契约） | `test_sprint_lifecycle_advance_pause_resume` |
| 学习事件幂等（idempotency_key 去重不重复落库） | M16-001 | pass（SQLite 契约） | `test_learning_event_idempotency` |
| 迁移 0033 三表（contracts/sprints/events） | M16-001 | pass（本地全量回归 6451 passed 含迁移语义；CI migration job=CI 门禁） | `services/api/alembic/versions/0033_learning_contracts.py` |
| 全量回归无破坏 | M16-001 | pass（6451 passed, 35 skipped；ruff clean） | 本机 2026-10-22 执行记录 |
| PG 隔离库迁移 up/down | M16-001 | not_executed（本机未设 AIOS_PG_TEST_URL 会话） | CI migration job |
| 生产部署 M16-001 | — | not_executed（按方案：RC 不自动部署） | — |
| 掌握度规则域层（状态链/时间序降级/高信心错误/确定性幂等） | M16-002 | pass（2026-10-22，11 测试） | `services/api/tests/test_mastery_rules.py` |
| 作答证据+掌握度 API（三态/全状态链/概览重算） | M16-002 | pass（SQLite 契约，6 测试） | `services/api/tests/test_learning_attempts_api.py` |
| 幂等键租户隔离（IDOR/预占压制修复回归） | M16-001b | pass（双用户同键隔离测试） | `test_learning_contract_api.py::test_event_idempotency_is_tenant_scoped_no_idor_no_suppression` |
| 掌握度阈值黄金集校准 | — | not_executed（Phase 7 黄金集建立后） | 常量在 `domain/mastery.py` 显式标注 |
| 微循环策略域层（骨架/六类错误路由/未判分保持/交错队列） | M16-003 | pass（2026-10-22，17 测试含完整微循环 E2E） | `services/api/tests/test_practice_loop.py` |
| 练习决策 API（next-step/interleave-plan） | M16-003 | pass（SQLite 契约） | `test_learning_attempts_api.py::test_practice_decision_endpoints` |
| 蓝图域层（等值不重叠/难度配平/不足如实 partial） | M16-004 | pass（2026-10-22，7 测试） | `services/api/tests/test_assessment_blueprint.py` |
| 前后测编排 API（答案隔离/阶段前置/三态判分落证据/重复提交 409/报告增益） | M16-004 | pass（SQLite 文件库契约，5 测试全链） | `services/api/tests/test_sprint_assessment_api.py` |
| 复习调度域层（间隔模板/表现调整/诚实状态机） | M16-005 | pass（2026-10-22，8 测试） | `services/api/tests/test_review_schedule.py` |
| 复习闭环 API（建队幂等/present/attempt 判分/滚动排期） | M16-005 | pass（SQLite 文件库契约） | `test_sprint_assessment_api.py::test_follow_up_review_lifecycle` |
| 判分题型解析差异修复（normalize_type 单一事实源回归） | M16-004b | pass（essay/unknown/alias 三态收录测试） | `test_assessment_blueprint.py::test_essay_and_unknown_types_excluded` |
| FSRS 真实算法接入（py-fsrs 6.3.2，MIT license 审查 2026-10-22 通过） | M17-003 | pass（缺省调度器切 fsrs：记忆状态 stability/difficulty 跨轮持久（memory_state 列 0038 + 滚动排期传递）；algorithm_version 随行记录可溯；生产 live：复习作答返回 fsrs-py-6.3，答错回最短档 1 天；模板调度器 intervals-mvp 保留可回退并有锁定回归） | `test_review_schedule.py`（FSRS 4 测试）+ `test_review_attempt_uses_fsrs_scheduler`（algorithm_version/memory_state 落库断言） |
| Web 学习工作台（合同/蓝图/前后测作答/报告/掌握度/复习面板） | M16-006 | pass（typecheck/lint/build + 334 测试） | `apps/web/src/app/learning/page.tsx` |
| 前后测提交原子化（并发双提交仅一方生效） | M16-006b | pass（重复提交 409 回归） | `test_sprint_assessment_api.py`（dup 提交路径） |
| 客观题生成域层（schema/去重/数量上限校验；混合批丢弃计数） | M16-007 | pass（2026-10-22，14 测试，stub 网关） | `services/api/tests/test_item_generator.py` |
| 生成→蓝图→前测 内容层闭环（答案隔离） | M16-007 | pass（契约测试：生成 8 题→complete=true→前测 4 题无答案泄露） | `test_sprint_assessment_api.py::test_generate_items_then_blueprint_usable` |
| LLM 出题 live 验证（本地 Ollama，ndtool.cn 生产） | M16-010 | pass（2026-10-22：201/16s 生成 6 题+二轮 8 题 0 丢弃；蓝图 complete=true→前测判分→推进全链） | 生产实测记录（本行即证据） |
| 来源证据 API（stub provider：入库/非法 url 过滤/无源 503） | M16-008 | pass（2026-10-22，4 测试） | `services/api/tests/test_source_refs_api.py` |
| LLM 调用线程池化（事件循环不阻塞） | M16-008b | pass（回归全绿；代码级 asyncio.to_thread 两处） | `learning.py`/`course_workflow.py` |
| SQL 黄金集离线评测（蓝图等值/判分一致/掌握度场景） | M16-009 | pass（2026-10-22，脚本 exit 0 + pytest 门禁） | `services/api/scripts/run_golden_eval.py`、`eval/golden_sql_foundation.json` |
| 黄金集扩充：迁移题 + 延迟变体 + 掌握度阈值校准 | M17-004 | pass（2026-10-22：transfer_items 4 道（JOIN+GROUP BY+HAVING 综合）+ delayed_variants 4 道（同概念换情境重述）全部判分一致；阈值校准 4 边界场景（恰好达标 provisional/差一条不 provisional/同题型重复不 provisional/迁移对 mastered）全过；离线评测 exit 0） | `scripts/run_golden_eval.py`（第 4 项校准）+ `test_golden_eval.py` CI 门禁 |
| 语音会话→学习事件闭环（close 落库统计摘要；隐私不存音频/全文） | M16-010 | pass（契约层；隐私边界断言） | `voice_live.py` close 路径、`test_learning_contract_api.py::test_top_level_learning_events_stream` |
| 语音弱网/NAT/设备切换/打断重连分层验收 | — | not_executed（需运维显式执行——方案 Prompt 8 边界） | 待验收清单 |
| Web 学习冲刺浏览器 E2E（生产真实 Chromium 全链） | M16-011 | pass（2026-10-22，ndtool.cn/aios：登录→建合同→确认→推进×5→后测 UI 作答 4 题+信心→提交 100 分→自动 report→报告增益 +100→复测队列 scheduled due +1d） | `artifacts/qa/e2e_01..13_*.png`（截图序列）；复测队列 API 复核 |
| 生产 Web 镜像 API base 回归修复 | M16-011 | pass（m16-production-v2 构建漏 `AIOS_PUBLIC_API_BASE_URL`，bundle 烘死 127.0.0.1:8000——登录态页面全灭；v4 以 `=/aios` 重建后浏览器复核全链） | `infra/env.production-recovery`（tag v4）；本行即证据 |
| 全新概念 Web 冷启动（合同→概念源头） | M17-001 | pass（概念源三级缺省：payload → 蓝图 → 合同目标 LLM 推导；推导失败 422 如实） | 本表上一行 + `domain/concept_derivation.py` |
| App 学习冲刺面板（入口/合同/阶段链/推进交互，真机验收） | M16-012 | pass（2026-10-22，华为真机 EYFBB22923201473 连生产：学习 tab 入口卡 → 面板渲染两合同（48.0 小时/目标数/通过标准）→ 阶段链 → 推进到间隔复测（服务端 follow_up 确认）；作答流 8 ViewModel 单测覆盖） | `artifacts/qa/app_02_sprint_panel.png`；sprints API 复核 |
| /papers 生成题 angles 解包 500 修复 | M16-012b | pass（红绿验证：回退读侧防御→测试 FAILED（TypeError 与生产栈一致）→恢复→pass；生产 Bearer 复核 500→200） | `test_sprint_assessment_api.py::test_generate_items_then_blueprint_usable`（尾部回归断言） |
| 全新概念冷启动（目标→概念推导，M17-001） | M17-001 | pass（2026-10-22 生产浏览器 E2E：全新概念合同（GROUP BY/HAVING，题池无此概念）→ 生成客观题（LLM 推导 12.6s + 出题 201）→ 蓝图 complete（4 概念等值）→ 冲刺 → 前测 UI 作答 8 题 → 提交 100 分 → 自动推进 concept_map_review） | `artifacts/qa/m17_01_coldstart_pretest.png`；域层 10 测试 + 契约冷启动全链 |
| 黄金集第二学科（Python 基础，扩科可复制性） | M17-007 | pass（2026-10-22：8 概念 ×4 题 + 迁移 4 + 延迟变体 4（人工标注）；双学科离线评测（80 题/16 概念）四项全 pass——蓝图等值/判分一致/掌握度场景/阈值校准对异构学科同样成立） | `eval/golden_python_foundation.json`；`scripts/run_golden_eval.py`（双学科）+ CI 门禁 |
| 语音链路延迟度量（单链路，Phase 8 自动化部分） | M17-008 |
| 课程资源目录（ChinaTextbook Mode A 元数据外链同步） | M18-001 |
| Web 找教材入口（学习库页目录检索） | M18-002 |
| 用户私有导入与分片组装（Mode B） | M18-003 |
| 教材解析与页面锚点（文本型 PDF） | M18-004 |
| 课程结构候选（章节树，outline 驱动） | M18-005 |
| 概念候选与 DAG 桥（多类型边/两态确认） | M18-006 |
| 题目发布门（练习/考试分离） | M18-007 |
| OCR 后备（扫描教材文本化） | M19-001 |
| 垂直切片全链闭环（审计 02 §8 八步） | M19-002 |
| 教材→冲刺一键向导（用户面封装） | M19-003 |
| Web 我的教材工作台（02 §7 课程页 UI） | M19-004 | pass（2026-10-24 生产浏览器：学习库页「我的教材」区——导入列表（标题/大小/解析态/概念计数）+ 分态操作（解析/OCR 分批/提取概念/一键开冲刺）；GET /library/imports 书架端点（UI 字段聚合）；三本真实教材渲染（离散数学已解析+概念 12/3、概率论扫描需 OCR）） | 生产 ndtool.cn/aios/library 实录 | pass（2026-10-24 生产 live：离散数学 3 概念一键 → 合同+确认+来源桥+生成 11 题+全发布+蓝图 complete+冲刺 created（单次调用全链）；幂等（同合同同冲刺复用不翻倍）；无确认概念 409 如实；LLM 不可用中止并透出原因） | `test_textbook_wizard.py`（2）+ 生产实录 | pass（2026-10-24 生产 live：Rosen 离散数学（英文）→ 目录→私有导入→解析（1071 页锚点）→章节树→概念确认（3）→**教材来源桥**（概念命中教材页锚点，含 slug 匹配中文概念名配英文教材）→生成题带教材引用（题干引用 P→Q/A∩B 教材语境）→发布→蓝图 complete→冲刺→前测 50 分→掌握度 practicing；契约测试覆盖全链 stub 版） | `test_vertical_slice.py` + 生产实录 | pass（2026-10-24 生产 live：体育七年级扫描书 12 页 OCR 置信 0.987、chunks 3→14、**真实教材中文逐句可读**（运动安全须知原文入库）；分批参数 ocr_max_pages（62 低文本页全量超 nginx 60s 窗——分批诚实语义：未完成页保持 needs_ocr）；页均置信低于 0.80 标 low_ocr_confidence（人工复核信号——不自动驱动题目生成）；中文黄金样本测试（判别式句逐字识别）） | `tests/test_ocr.py`（4）+ 解析 OCR 路径测试 + 生产实录 | pass（2026-10-24：生成题入池即 candidate——练习可用，蓝图只收 published；无行=legacy published（历史题与既有蓝图零破坏）；publish/retire 端点（owner 校验+撤题全路径排除——蓝图/练习/复习/迁移四场景）；契约 14 测试含三重门断言；生产 live：新题 candidate 缺席蓝图 + publish 200 + legacy 4 题照常） | `test_question_publish_gate_blocks_blueprint_until_published` + 冷启动链更新（生成→publish→蓝图） | pass（2026-10-24 生产 live：英文离散数学 LLM 提取 15 概念/9 边 0 丢弃（集合论/归纳法/命题逻辑/算法复杂度等核心概念+合理先修边）；确认 3 概念后 DAG 桥载荷 3 nodes（格式对齐既有 admin 发布端点；边只收两端 confirmed 的 prerequisite_of——语义正确）；重提取不覆盖已决候选） | `test_concept_candidates.py`（8 域测试）+ 闭环契约 + 生产实录 |
| native 网关 num_predict 修复 | M18-006b | pass（live 抓根因：native /api/chat 分支忽略 max_tokens→Ollama 默认截断 4177 字符长 JSON；options.num_predict 官方契约映射 + 回归断言） | `test_llm_gateway.py::test_native_payload_carries_num_predict` | pass（2026-10-23 生产 live：英文离散数学 outline 115 条 → 115 单元树（27 章+88 节，页锚点+parent 嵌套，1.1 Propositional Logic 等真实章节）；候选两态（candidate→人工 confirm）；重提取整树替换幂等；无 outline 书 409 如实） | `test_course_structure.py`（5 域测试）+ 结构闭环契约（2）+ 生产实录 | pass（2026-10-23 生产 live：英文离散数学 1071 页 → parsed/1071 页锚点 chunks/115 条 outline 透出（章节树候选）；扫描书如实 needs_ocr（体育 62/65 低文本、浙大概率论 424/424——**不虚构文本**，OCR 属后续基建）；重跑幂等整资源替换；跨用户隔离） | `test_pymupdf_parser.py`（5）+ 解析闭环契约（2）+ 生产实录 | pass（2026-10-23：权利声明必填 422/分片连续性校验（缺片重片拒绝）/SHA256 逐片+整体双算/二进制拼接 PDF 魔数校验/大小上限/幂等（同 owner+resource 返回既有）/跨用户 job 隔离 404——契约 4 + 域层 12 测试；生产下载链路留待部署实测） | `domain/ingest.py`；`test_library_import_api.py`（stub 下载器） | pass（2026-10-23 生产浏览器：学习库页「找教材」区块——默认「数学」命中 30 条（人教版各年级+许可徽标+来源外链）；主动搜「线性代数」命中同济第五版 2 条；许可状态如实展示 metadata_only） | 生产 ndtool.cn/aios/library 实录 | pass（2026-10-23 生产 live：pin commit 5a80345 → 2371 blob → 2087 条目（分片归组）→ 幂等重跑 unchanged=2087；搜索/过滤（学段/学科）实测；许可默认 metadata_only；差量语义 created/updated/unavailable 契约覆盖） | 域层 10 测试 + API 契约 8 测试（mock tree）+ 生产 sync-runs 实录 | pass（2026-10-22 生产实测：TTS p50 3171/p95 3897ms（含 frp 网路）、ASR p50 1087/p95 1309ms、识别相似度 0.737；可重复脚本）；**弱网/NAT/设备切换仍 not_executed（运维显式分层验收，边界不变）** | `scripts/measure_voice_latency.py`；`docs/evidence/m17-008-voice-latency/` |
| 定向练习作答闭环（targeted_practice 微循环落地） | M17-006 | pass（2026-10-22 生产浏览器 E2E：会话（最弱概念优先+域层策略决策透出）→ 单题作答流（答对推进步骤/答错同步骤重试+解析反馈）→ 概念 5 步（first→recall→faded→independent→exit_ticket）→ 全概念完成原子推进 transfer_check；契约测试含答错重试与完成推进断言） | `test_targeted_practice_answer_loop`；生产 E2E 实录 |
| 迁移检验作答闭环（transfer_check 阶段出题+判分+掌握度联动） | M17-005 | pass（2026-10-22 生产浏览器 E2E：transfer_check 阶段「开始迁移检验」→ 未见情境应用题（排除前后测题+优先未做过）→ UI 作答+信心 → 提交 100 分 → 原子推进 posttest → sql:select-basics 迁移对触发 mastered（证据 8 独立对 5）；契约测试含 mastered 联动断言） | `test_transfer_check_answer_flow`；生产 E2E 实录 |
| 延迟复测作答 UI 闭环（Beta 条件 6 收口） | M17-002 | pass（2026-10-22 生产浏览器 E2E：复习队列「开始复习」→ present → 取题（概念匹配/客观题/答案隔离）→ UI 作答+信心 → 服务端判分 →「复习答对 · 下一轮间隔 2 天」→ 滚动排期新 scheduled 出队） | `artifacts/qa/m17_03_review_item.png`；契约测试 test_review_queue_item_serves_isolated_question |
