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
