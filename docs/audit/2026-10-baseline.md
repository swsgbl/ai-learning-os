# 2026-10 学习引擎升级基线审计（Phase 0）

审计日期：2026-10-22 · 审计者：开发智能体（本机代码级审计）

## 1. 范围与版本固定

- GitHub 主仓：`https://github.com/swsgbl/ai-learning-os.git`（origin，**单一主源**）
- AtomGit 镜像：`https://atomgit.com/hongfu/ai-learning-os.git`（fast-forward 同步）
- 基线 commit：`6d6111089866d5cd75d0d071116c0ad94137c80b`（= tag v0.3.1，PR #428 合并提交）
- 工作树：clean（仅未跟踪的本地 .claude/ 与 NUL 占位，不入库）
- 双仓对齐：M15-025 发版时已 `push atomgit origin/main:main` + tag v0.3.1，两仓 SHA 一致

## 2. 架构与数据流（现状）

- **Web**：Next.js（basePath /aios 公共部署）——只渲染状态与提交意图
- **API**：FastAPI + SQLAlchemy（PG/Alembic 迁移至 0032；未配 DATABASE_URL 回退内存仓储供契约调试）
- **App**：Android Compose（v0.3.1，versionCode 14，六 tab：首页/学习/考场/工作台/语音/设置）
- **考试引擎**（服务端权威）：papers/questions/exams——计时、答案隔离、交卷幂等、事件顺序、审阅报告、考试历史
- **课程生成**：course_generation_drafts（概念 DAG 匹配优先 → LLM 动态生成兜底）→ materialize 固化练习卷 → 学习视图（章节目标/解释/逐章练习）
- **语音**：LiveKit + 本地 FunASR/CosyVoice（WSL systemd）+ 云端 provider 槽位
- **检索/存储**：SearXNG 本地搜索 + MinIO 对象存储 + 资料获取链路
- **LLM**：多厂商 BYOK（user_llm_profiles，每用户 8 条恰一激活）+ 服务端全局（Ollama）；本机端点直连、云端经 VPS 隧道代理

## 3. 学习能力功能矩阵（本次升级的核心差距清单）

| 学习科学能力 | 状态 | 证据 |
|---|---|---|
| LearningContract（可观察能力目标/范围/排除项/成功标准） | **planned** | 全仓无 `learning_contract` 表/模型/API |
| Sprint 状态机（暂停/恢复/失败恢复/计划版本） | **planned** | 无 Sprint 领域对象；course_generation_drafts 是单次管线非状态机 |
| LearningEvent（幂等键/审计/trace） | **partial** | exams.py 有考试事件顺序；`domain/student_state.py` 消费「答题事件」做投影，但无正式 LearningEvent 表与幂等键 |
| Attempt 证据（信心/耗时/提示数/独立/延迟/迁移标记/错误类型） | **partial** | exam events 记录答案与得分；**信心、提示使用、独立/延迟/迁移标记、错误类型标签均未记录** |
| 概念掌握度 ConceptMastery（多状态可重算） | **partial** | `student_state.py` 有 mastery 标量（指数逼近 α/β 权重 + 遗忘风险 τ），事件可重放重算——是标量非 `unknown→emerging→practicing→provisional→mastered→at_risk` 证据状态机 |
| MasteryEvidence（证据→Attempt→概念可追溯） | **planned** | 无独立证据对象；mastery 由事件直接推导无中间证据层 |
| 前测/等值后测蓝图（题目-能力-概念映射、平行题） | **partial** | 题目有 knowledge 概念关联（LLM 课程 concept_id=llm:N）；**无蓝图对象、无等值题生成、后测复用原题** |
| 主动回忆/渐隐支架/交错练习/迁移题教学协议 | **planned** | 学习视图（M15-024）有「章节目标→解释→练习」线性结构；无先尝试后解释、无 worked example 渐隐、无交错调度、无迁移题 |
| 错误类型分类重教（知识缺失/混淆/步骤/推理/粗心） | **partial** | misconceptions 路由有误概念入口；错误标签体系与按类型再教学策略未实现 |
| 间隔复习调度 | **partial** | `review_scheduler.py`：last_seen+间隔推导 next_review_at（1/3/7 天固定模板级）；**非 FSRS、复习完成与真实作答未分离验证** |
| 延迟复测（+1/3/7/14/30 真实作答） | **planned** | 无延迟复测创建与作答记录 |
| 学习效果评测（黄金集/增益/迁移/校准误差） | **planned** | 无 evaluation_harness |
| 答案/评分规则服务端隔离 | **implemented** | 提交前 answer/rubric 不出服务端（既有门禁+回归测试覆盖） |
| 交卷幂等/断线恢复 | **implemented** | next_sequence 服务端权威；ExamResumeStore 恢复链路（真机验证过） |
| 语音学习事件闭环 | **partial** | 语音陪练有会话与评分；ASR→教学状态机→事件持久化的分层验收未系统化 |

## 4. 测试门禁（本分支真实执行）

| 命令 | 结果 |
|---|---|
| `ruff check services/api/app services/api/tests` | **pass**（All checks passed） |
| `pytest services/api/tests -q` | **pass**（6438 passed, 35 skipped，含 SQLite 契约/迁移/安全/状态机回归） |
| `npm run typecheck/lint/build`（Web） | pass（PR #428 CI 全绿，2026-10-21） |
| Android unit/lint/assemble | pass（PR #428 CI，v0.3.1 tag 构建签名通过） |
| PG 隔离库集成测试 | not_executed（本机未设 AIOS_PG_TEST_URL 指向隔离库的会话；CI 有迁移 job 绿） |
| live 语音/云端 provider smoke | not_executed（需显式运维执行；本地语音链 2026-10-21 实测 0.9s roundtrip 通过） |

## 5. 风险与优先级

- **P0**：无（本轮无生产数据/权限/答案泄露问题；ndtool.cn 子路径迁移已在新版引导中说明）
- **P1**（主闭环缺口，即本升级主线）：
  1. 学习合同与 Sprint 状态机缺失 → Phase 1
  2. Attempt 证据字段（信心/提示/独立/迁移）缺失 → Phase 2
  3. 掌握度是标量非证据状态机，mastered 无延迟/迁移证据支撑 → Phase 2
  4. 后测复用原题（练熟≠学会） → Phase 2/4 等值题
  5. 复习调度无 FSRS、复习完成未与真实作答绑定 → Phase 6
- **P2**：交错练习与迁移题协议、错误类型重教策略、效果评测黄金集（Phase 3/7）
- **P3**：语音学习事件分层验收系统化（Phase 8）

## 6. 升级主线决策（对齐方案文档）

1. **不重写**：保留考试引擎/发布门禁/语音/检索/BYOK 全部资产，新领域模块以 Alembic 迁移增量接入。
2. **顺序**：Phase 1（合同+状态机）→ Phase 2（Attempt 证据+掌握度 MVP）→ Phase 3（微循环教学）→ Phase 4（48h Sprint）→ Phase 5/6/7/8。
3. **每阶段一个 PR**，先测试后实现，pass/failed/not_executed 语义贯穿。
4. 掌握度 MVP 用可解释证据规则（方案 §5），不上未验证 KT 模型。

## 7. 未确认项

- FSRS 库（fsrs4anki/py-fsrs）当前版本兼容性——Phase 6 前做 license/依赖审查
- 黄金集试点学科选择（SQL 数据分析为现有 DAG 唯一主题，是自然候选）——Phase 7 决策
