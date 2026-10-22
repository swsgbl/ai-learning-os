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
| FSRS 真实算法接入（py-fsrs 依赖与 license 审查） | — | not_executed（MVP 调度器已留 adapter 协议） | `domain/review_schedule.py` 注释 |
| Web 学习工作台（合同/蓝图/前后测作答/报告/掌握度/复习面板） | M16-006 | pass（typecheck/lint/build + 334 测试） | `apps/web/src/app/learning/page.tsx` |
| 前后测提交原子化（并发双提交仅一方生效） | M16-006b | pass（重复提交 409 回归） | `test_sprint_assessment_api.py`（dup 提交路径） |
| 客观题生成域层（schema/去重/数量上限校验；混合批丢弃计数） | M16-007 | pass（2026-10-22，14 测试，stub 网关） | `services/api/tests/test_item_generator.py` |
| 生成→蓝图→前测 内容层闭环（答案隔离） | M16-007 | pass（契约测试：生成 8 题→complete=true→前测 4 题无答案泄露） | `test_sprint_assessment_api.py::test_generate_items_then_blueprint_usable` |
| LLM 出题 live 供应商验证 | — | not_executed（契约层 stub；运维 smoke 待显式执行） | 生产 /generate-items 手动验证 |
| 来源证据 API（stub provider：入库/非法 url 过滤/无源 503） | M16-008 | pass（2026-10-22，4 测试） | `services/api/tests/test_source_refs_api.py` |
| LLM 调用线程池化（事件循环不阻塞） | M16-008b | pass（回归全绿；代码级 asyncio.to_thread 两处） | `learning.py`/`course_workflow.py` |
| SQL 黄金集离线评测（蓝图等值/判分一致/掌握度场景） | M16-009 | pass（2026-10-22，脚本 exit 0 + pytest 门禁） | `services/api/scripts/run_golden_eval.py`、`eval/golden_sql_foundation.json` |
| 黄金集扩充（延迟/迁移题、第二学科）与阈值校准 | — | not_executed（试点集已立；扩科属持续迭代） | 方案 Phase 7 持续项 |
| 语音会话→学习事件闭环（close 落库统计摘要；隐私不存音频/全文） | M16-010 | pass（契约层；隐私边界断言） | `voice_live.py` close 路径、`test_learning_contract_api.py::test_top_level_learning_events_stream` |
| 语音弱网/NAT/设备切换/打断重连分层验收 | — | not_executed（需运维显式执行——方案 Prompt 8 边界） | 待验收清单 |
