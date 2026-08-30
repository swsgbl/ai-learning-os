# AI Learning OS 2026 v2 执行摘要

版本：v2.2  
定版日期：2026-08-30  
文档状态：已替代根目录 `AI_Learning_OS_2026_FINAL_DESIGN.md` 的 v1.0 结论；根文件保留为历史草案。  
产品定位：Personal Learning OS / Adaptive AI Learning Agent OS

## 1. 定版结论

AI Learning OS 不应实现为聊天机器人、题库网站或单次课程生成器，而应实现为持续运行的学习运行时。它维护四个长期对象：课程结构、题目能力要求、学生学习状态、证据与授权状态；再通过语音、考试、检索三条入口驱动同一个学习闭环。

用户只看到三个一级动作：

1. 今日学习：系统根据目标、进度、错题和复习计划直接给出当日任务。
2. 开始语音：系统读题、读选项、接收语音作答、判分并复盘。
3. 开始考试：系统提供正式考试体验、服务端权威计时、自动保存、自动提交和审阅报告。

课程发现、试卷检索、文档解析、授权判断、知识点映射、难度选择、模型路由、错因诊断、复习排程全部由后台完成。

## 2. 三条核心闭环

### 2.1 语音做题闭环

选择课程或目标 -> 系统选题 -> TTS 读题和选项 -> 用户语音作答 -> ASR 转写 -> 意图与答案规范化 -> 确定性判分 -> 记录事件 -> 下一题 -> 全卷总分 -> 错题多角度讲解 -> 生成变式练习 -> 更新 Student Model。

语音层只负责交互，不拥有成绩和状态。语音会话断线后必须能从服务端状态恢复。

### 2.2 在线考试闭环

导入或生成试卷 -> 服务端创建 Exam Session -> 服务器时间权威计时 -> 客户端自动保存 -> 断线恢复 -> 超时自动提交 -> 幂等判分 -> 客观题确定性评分 -> 主观题 rubric + LLM 审阅 -> 生成报告 -> 错因诊断 -> 补救与再测。

客户端只能展示剩余时间，不能决定剩余时间。重复提交、并发提交、刷新页面、断网重连和本地时钟修改都不能破坏考试状态。

### 2.3 全网课程与真题闭环

理解学习目标 -> 生成搜索计划 -> 多源检索 -> 来源分级 -> 访问与授权判断 -> 文档抓取 -> OCR/解析 -> 题目和课程对象标准化 -> 入库 -> 选题/构课/复习 -> 记录 Evidence 和 License。

公开访问不等于可复制、镜像、训练或再分发。受限资源只提供入口和使用说明，不进入可再分发内容池。

## 3. 调研后的技术定版

| 层 | 定版选择 | 说明 |
|---|---|---|
| 前端 | Next.js + React + TypeScript | 响应式 Web 优先，考试 UI、报告、课程工作台同源。 |
| 后端 | Python + FastAPI | 适合学习引擎、评分、解析和 AI 服务编排。 |
| 编排 | LangGraph | 用显式状态机管理考试、语音、检索和学习流程，避免自由 Agent 跳转。 |
| 实时语音 | LiveKit Agents，Pipecat 备选 | 支持 WebRTC、STT-LLM-TTS、turn detection、打断和自托管。 |
| 数据库 | PostgreSQL + pgvector | MVP 同时承载关系数据、概念图和向量检索，降低部署复杂度。 |
| 缓存与队列 | Redis + 任务队列 | 用于会话锁、自动提交、解析任务和评分任务。 |
| 对象存储 | MinIO/S3 | 存放原始文档、音频、生成材料和派生文件。 |
| 文档解析 | MinerU、Docling、olmOCR、Marker 按 adapter 路由 | 不同文档选择不同解析器，保留页码和公式。 |
| 搜索 | Search provider abstraction + Crawl4AI/Playwright | 先来源分级和授权判断，再解析入库。 |
| 学习模型 | 可解释 heuristic/BKT-like + FSRS | MVP 不直接上复杂深度知识追踪模型。 |
| 部署 | Docker Compose -> Kubernetes | 单机个人版先行，多用户后再扩展。 |

## 4. 最重要参考项目

| 项目 | 已核验状态 | 吸收点 |
|---|---|---|
| HKUDS DeepTutor | v1.6.1，Apache-2.0，GitHub 约 37.9k stars | lifelong tutoring、学习工作台、Quiz、Mastery Path、多引擎 RAG、记忆与证据意识。 |
| THU-MAIC OpenMAIC | v1.0.0，MIT，GitHub 约 22.9k stars | agent workbench、可恢复课程构建会话、材料解析、slides/quizzes/interactives/PBL、provider-neutral 能力路由。 |
| CMU OLI | 官方公开页面核验 | learning by doing、清晰学习目标、即时反馈、持续改进循环。 |
| OpenAI Study Mode | 官方页面核验 | 分步引导而不是直接给答案。 |
| Google LearnLM | 官方页面与论文线索 | 将学习科学原则作为模型和系统设计约束。 |
| Khanmigo | 官方产品与博客线索 | 学生历史和技能缺口进入导师上下文，个性化不应只看当前问题。 |
| LiveKit Agents | 官方文档核验 | 实时语音管线、turn detection、打断、工具调用、自托管。 |
| EduStudio / pyKT / FSRS | 官方仓库或官网核验 | 学生认知建模、知识追踪评测、间隔复习调度。 |
| OATutor / OLI Torus | 官方仓库或官网核验 | OATutor 提供 BKT、提示阶梯、自适应选题和 CC BY 4.0 内容库；OLI Torus 提供数据驱动课程创作与运行时。 |

## 5. 自研与复用边界

### 必须自研

- Exam Session 状态机、权威计时、幂等提交。
- Question/Paper/Course/Concept 统一模型。
- Student Model、掌握度、误解候选、复习优先级。
- 错因分类和补救路径。
- Source Registry、License State Machine、Evidence Object。
- 语音做题 FSM 和答案规范化策略。
- 教学效果评测集和评分一致性评测集。

### 优先复用

- WebRTC 与实时语音框架。
- 基础 ASR/TTS 模型与服务。
- 通用 LLM 与 embedding。
- PDF/OCR/DOCX 解析。
- PostgreSQL、Redis、对象存储、向量检索。
- 间隔复习和学习追踪算法库。

## 6. 最高风险与定版控制

1. 版权风险：所有外部内容必须先过 Source Registry 和 License State Machine。
2. 考试一致性风险：计时、提交、判分和事件存储必须由服务端事务保证。
3. 主观题评分风险：rubric 强制结构化，低置信度二次审阅，建立人工标注 golden set。
4. 语音体验风险：单独优化端到端感知延迟、打断、命令识别、噪声和状态恢复。
5. 教学效果风险：不用“回答流畅”代替学习效果，必须建立前测、后测、保持率和迁移题评测。

## 7. v2 交付物

| 文件 | 作用 |
|---|---|
| `01_RESEARCH_EVIDENCE_MATRIX.md` | 事实分层、已核验来源、误报剔除和设计启发。 |
| `02_PRD_FINAL.md` | 产品需求、用户旅程、三大功能和非目标。 |
| `03_SYSTEM_ARCHITECTURE.md` | 架构、服务边界、状态机、部署与技术选型。 |
| `04_DATA_MODEL_AND_API.md` | 数据模型、API、事件契约和错误契约。 |
| `05_VOICE_EXAM_SEARCH_DESIGN.md` | 语音、考试、检索三条主链路的详细设计。 |
| `06_DEVELOPMENT_PLAN.md` | 10 周开发计划、里程碑、验收门槛和测试策略。 |
| `07_AI_AGENT_PROMPT_PACK.md` | 可直接复制给 coding agent 的提示词。 |
| `08_SECURITY_LICENSE_EVALUATION.md` | 安全、隐私、版权、许可证和内容治理。 |
| `09_SOURCE_REGISTER.md` | 公开来源与验证登记。 |
| `10_COURSE_SOURCE_SEEDS.md` | 大学、OER、MOOC、开源课程的初始种子源与导入边界。 |
| `11_IMPLEMENTATION_BACKLOG.md` | 可分配任务、依赖、提示词映射和验收标准。 |
| `12_DEPLOYMENT_OPERATIONS_RUNBOOK.md` | 本地/混合/云端部署、语音配置、备份和故障处理。 |

## 8. v2.2 补充定版

1. 课程源按“官方入口优先”扩展：HKU Online/edX/Coursera、HKU ExamBase、清华 XuetangX、北大 Global Open Courses、MIT OCW、CMU OLI、Stanford Online、Harvard CS50x、Berkeley 课程站、Open Yale、OpenStax、OER Commons。
2. lib-pku、清华课程攻略、USTC-Course 等社区仓库只作为发现线索；仓库公开不等于学校授权，也不代表内嵌教材、讲义、试卷可复制、派生或训练。
3. MinerU 采用 Apache-2.0 加附加条款：高 MAU/高收入门槛需商业授权，基于其提供第三方在线服务时必须显著标注 MinerU。个人版可用，商业化前需复核阈值和署名。

## 9. MVP Gate

MVP 不能只完成界面，必须同时通过：

1. 上传资料 -> 解析 -> 题库 -> 考试 -> 评分 -> 错题 -> 复习闭环。
2. Student Model 能影响下一轮选题。
3. 语音能完整完成一套题并生成报告。
4. 检索结果带来源、授权状态和可解释排序。
5. 客户端无法通过改时间、刷新、重复提交或并发请求破坏考试。
6. 核心测试、类型检查、构建和 E2E smoke 全部通过。
