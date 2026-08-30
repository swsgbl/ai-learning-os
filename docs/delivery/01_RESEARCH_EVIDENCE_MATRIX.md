# 调研证据矩阵

基线日期：2026-08-30  
目标：区分“可进入定版的事实”、“可作为设计启发的结论”和“必须再核验的二手线索”。

## 1. 证据分层规则

| 等级 | 定义 | 定版用途 |
|---|---|---|
| E1 官方已核验 | 当前官网、官方文档、官方 GitHub release/license/API docs 直接确认。 | 可进入架构、技术选型和事实表。 |
| E2 学术/机构来源 | 论文、大学实验室、课程官网、公开教育平台。 | 可作为方法与设计依据，性能数字需保留实验条件。 |
| E3 二手报告 | 用户上传的 AI 调研报告、搜索摘要、社区文章。 | 只能提供候选线索，不得直接当事实。 |
| E4 未验证/冲突 | 找不到官方来源，或与官方来源冲突。 | 不进入定版，只保留在待验证清单。 |

## 2. E1 核心开源项目

| 项目 | 官方状态 | 关键能力 | 对本产品的直接启发 | 风险/限制 |
|---|---|---|---|---|
| HKUDS DeepTutor | `v1.6.1`，2026-08-30；Apache-2.0；约 37.9k stars。 | lifelong personalized tutoring、Courses、Mastery Path、Quiz、Knowledge Center、LightRAG/GraphRAG、Memory、沙箱与 MCP。 | 学习工作台、长期记忆、多引擎 RAG、题目与掌握路径可作为产品能力参照。 | 不能直接替代本系统的考试事务、授权治理和语音考试状态机。 |
| THU-MAIC OpenMAIC | `v1.0.0`，2026-08-27；MIT；约 22.9k stars。 | Agent workbench、durable sessions、材料上传解析、slides/quizzes/interactives/PBL、20 个内置技能、PostgreSQL 持久化、provider-neutral 路由。 | 课程生成应是可恢复、可纠偏、可持久化的构建会话，而不是一次生成。 | 其默认浏览器持久化和示例鉴权不适合生产多人版；必须使用服务端身份与授权。 |
| LiveKit Agents | 官方 docs 核验，支持 Python/Node、STT-LLM-TTS 与 realtime pipeline。 | WebRTC、turn detection、interruption、tool use、多 Agent、云端或自托管。 | 作为实时语音传输与编排基础。 | 具体延迟取决于 ASR/TTS/LLM、网络和部署，不能承诺固定毫秒数。 |
| Pipecat | 开源语音 Agent 框架，E1 仓库核验。 | 语音管线编排、多 provider。 | LiveKit 的备选，可用于快速实验适配层。 | 不应同时把两套框架放进核心路径，MVP 只选一条。 |
| Qwen3-ASR | 官方仓库核验；Apache-2.0。 | ASR 系列；streaming 当前官方说明仅支持 vLLM backend。 | 本地/私有化 ASR 候选。 | 引入 vLLM 会提高部署复杂度，需要单独 benchmark。 |
| FunASR | 官方仓库核验；MIT。 | streaming ASR、VAD、标点、说话人分离、OpenAI-compatible/MCP serving。 | 更轻的本地 ASR 与语音服务候选。 | 具体模型效果需按中英文和噪声场景实测。 |
| CosyVoice | 官方仓库核验；Apache-2.0。 | 多语言 TTS、zero-shot 语音合成。 | 本地 TTS 候选。 | 声音克隆必须显式授权和防滥用。 |
| MinerU | Apache-2.0 + 项目附加条款；MAU 超 1 亿或月收入超 2000 万美元需单独商业授权；对第三方在线服务有显著署名义务。 | PDF、图片、DOCX、PPTX、XLSX 转 Markdown/JSON 等文档理解。 | 试卷、讲义、扫描资料解析候选。 | 个人版可先适配；商业版必须进入 License Registry 并自动检查阈值与署名。复杂版式需抽样质检。 |
| Docling | 官方仓库核验；MIT。 | 多格式文档转换、RAG 准备。 | 轻量解析和开发环境候选。 | 对特定 OCR 场景可能弱于专项模型。 |
| olmOCR | 官方仓库核验；Apache-2.0。 | PDF linearization、LLM 相关 OCR。 | 扫描试卷和学术文档候选。 | GPU 成本和版式还原需评测。 |
| Crawl4AI | 官方仓库核验；Apache-2.0。 | LLM-friendly 爬取。 | Search Service 的抓取执行器之一。 | 爬取必须先经过 robots、授权、频率与 SSRF 策略。 |
| EduStudio | 官方仓库核验；MIT。 | 认知诊断、Knowledge Tracing、练习推荐、学习路径推荐模型库。 | Student Model 实验与算法参照。 | MVP 不应直接引入复杂训练链路。 |
| pyKT | 官方仓库核验；MIT。 | 深度知识追踪 benchmark/library。 | 离线评测和模型对比。 | 需要数据规模与稳定标签。 |
| FSRS | 官方项目核验。 | 间隔复习调度与记忆模型。 | 复习计划基线。 | 需与课程目标和考试日期结合，不机械照搬。 |
| OATutor | 官方仓库核验；代码 MIT，内容库声明 CC BY 4.0。 | BKT 掌握度、自适应选题、提示/脚手架阶梯、OpenStax 内容、学习事件与 A/B 实验。 | 提示阶梯、题目-技能映射、掌握阈值和学习效果实验设计。 | 前端静态实现不能直接满足服务端考试事务、多课程授权和语音状态恢复。 |
| OLI Torus | 官方仓库核验；MIT。 | OLI 下一代课程创作、交付、数据驱动改进平台。 | Course Runtime 与学习对象编排参考。 | 引入完整平台过重；MVP 借鉴数据模型和反馈循环，不整体迁移。 |

## 3. E1/E2 教育资源与课程平台

| 来源 | 已确认状态 | 产品用法 | 授权边界 |
|---|---|---|---|
| MIT OCW | 免费讲义、作业、考试、视频，无需注册；默认 CC BY-NC-SA 4.0。 | S 级课程来源，适合目录、引用和学习链接。 | 非商业、署名、ShareAlike；AI training 有额外约束，不能当自由数据集。 |
| CMU OLI | 官方强调 learning by doing、即时反馈、学习目标、进度跟踪；页面声明 CC BY-NC-SA 4.0。 | Course Runtime 设计参照。 | 需按页面和课程条款决定 remix、商业和派生方式。 |
| Stanford CS224N | 官方课程站公开 schedule、assignments、materials。 | AI 课程资源源。 | 课程材料使用权按原站条款处理。 |
| Harvard CS50x | 2026 课程与 problem sets 公开。 | 编程学习和练习来源。 | 作业诚实性条款和再使用限制必须保留。 |
| Berkeley CS61A/CS61B/CS188 | 官方课程公开材料。 | CS 基础课与练习来源。 | 不同学期材料授权可能不同。 |
| Open Yale | 官方开放课程。 | 通识与讲座来源。 | 页面条款决定下载与再分发。 |
| OpenStax | 官方开放教材，CC BY-NC-SA 4.0。 | A 级 OER 教材源。 | 非商业和 ShareAlike 条款必须进入 License Registry。 |
| 国家高等教育智慧教育平台 | 官方平台提供课程目录和学习入口。 | 中国高校课程目录源。 | 不能推断课程内容可批量抓取或再分发。 |
| HKU Online Learning | 官方页面核验：40+ MOOC 和 5 个 Professional Certificate，分布在 edX/Coursera。 | 官方课程发现和链接入口。 | 平台条款决定内容缓存、派生和再分发。 |
| Tsinghua XuetangX | 清华官网核验：清华 2013 年发起的 MOOC 平台，国际版 2020-04-20 上线。 | 清华及中国高校课程目录源。 | MOOC 平台内容默认不进入再分发池。 |
| PKU Global Open Courses | 北大官方页面核验：2026 Fall 全球开放课程需注册申请、选课和实时上课。 | 北大官方课程线索与入口。 | 申请制和实时课程不等同公开语料。 |
| Stanford Online | 官方站点提供免费课程和内容入口。 | 西方名校课程源。 | 课程与内容授权分别确认。 |
| HKU ExamBase | HKU 图书馆提供部分历年试卷全文，存在访问限制。 | 授权入口与使用说明，不做绕权抓取。 | 受限资源不能镜像入库。 |
| PKU Undergraduate Course | 社区课程资料仓库，报告称 CC BY-NC-SA，需按仓库当前条款核验。 | B 级课程发现线索。 | 不代表北大官方授权批量商用。 |
| HKU-MATH-Notes | 社区维护课程资料仓库。 | B 级线索。 | 公开仓库不等于所有内嵌教材和 handout 可再授权。 |
| lib-pku | 社区维护“北京大学课程资料民间整理”。 | B/C 级发现线索。 | 仓库未建立整体上游授权前不进入公共内容池。 |
| REKCARC-TSC-UHT | 社区维护清华大学计算机系课程攻略。 | B/C 级发现线索。 | 不代表清华官方授权；内嵌资料需逐项核验。 |
| USTC-Course | 社区维护中国科学技术大学课程资源。 | B/C 级发现线索。 | 仓库版权说明不覆盖全部上游教材和试卷。 |

## 4. E1/E2 产品与方法参照

| 对象 | 已核验事实/官方描述 | 进入定版的设计结论 |
|---|---|---|
| OpenAI Study Mode | 官方帮助中心说明其通过提问、引导思考、分步帮助学习，而不是直接给答案。 | Tutor Policy 必须区分“练习模式”和“考试模式”；考试内禁止答案泄露。 |
| Google LearnLM | 官方页面和论文强调 learning science principles、个性化、分步解释。 | 教学策略要显式建模：认知负荷、主动学习、元认知、适应学习者。 |
| Khanmigo | 官方产品与 2026 博客公开学生数据使用和学习效果改进线索。 | Student Model 应成为导师上下文的一部分，不能只依赖当前聊天。 |
| CMU OLI | 官方页面强调即时反馈和数据驱动课程改进。 | 每道题、每个提示、每个学习事件都应进入闭环。 |
| DeepTutor | 当前 README/release 能力核验。 | 学习工作台需要记忆、RAG、Quiz、阅读、课程与工具权限分层。 |
| OpenMAIC | 当前 README/release 能力核验。 | 课程生成采用大纲 -> 场景 -> 可编辑对象 -> 测验 -> 材料溯源的流水线。 |

## 5. 上传报告吸收与剔除

| 上传材料 | 有效吸收 | 不进入定版的内容 |
|---|---|---|
| ChatGPT调研 | Personal Learning OS、Student Model、Tutor Policy、学习科学原则。 | 未经当前官方核验的效果提升百分比和项目归属。 |
| deepseek调研 | 语音、OCR、RAG、题库、状态机等候选技术清单。 | 大量项目名称、机构、许可证和性能数字存在未核验或明显冲突。 |
| Gemini AI调研 | 语音 FSM、双模语音、文档解析、错因分析框架。 | 固定延迟、硬件“完美运行”和部分项目能力描述不能作为事实。 |
| mimo AI调研 | 多智能体课堂、语音候选、随身助手、记忆分层思路。 | PocketClaw 等产品事实和商业数据未核验，不进入定版。 |
| 豆包调研 | 三合一功能拆解、模块化整合思路。 | DeepTutor 许可证、原生语音/考试计时/爬虫能力与官方仓库不一致，需以 E1 为准。 |
| 千问调研 | 双模语音、知识库、错题闭环。 | 未经核验的商业 SDK 指标和高校内部项目能力。 |
| 心流AI调研 | 倒计时、错因字典、学生画像、检索管线。 | 绕权爬虫、伪装、Cookie 维系等策略违反本产品授权原则，明确剔除。 |
| 元宝调研 | 受控学习中枢、状态机、证据与授权优先。 | 具体项目事实仍需本矩阵 E1 核验。 |
| Grok sandbox 文本 | 无有效内容。 | 不作为依据。 |

## 6. 关键纠错

1. DeepTutor 当前官方仓库为 Apache-2.0，不是部分上传报告写的 MIT。
2. OpenMAIC 当前官方仓库为 MIT，且 `v1.0.0` 已发布；不能继续引用旧版 AGPL 或 `v0.3.1` 作为当前状态。
3. “公开真题”必须区分：公开链接、允许学习使用、允许派生、允许商业使用、允许再分发、允许模型训练。
4. HKU ExamBase 存在访问控制，不能设计绕权抓取。
5. 语音延迟不能只写 ASR 毫秒数，必须拆成 capture、VAD、ASR first token、规范化、策略、LLM、TTS first audio 和网络 RTT。
6. 客观题判分不能交给 LLM；LLM 只做解释和主观题 rubric 审阅。
7. MinerU 不是无附加条件的 Apache-2.0；商业阈值和在线服务署名义务必须进入依赖审批。

## 7. 定版采用的先进技术模式

1. Deterministic First：事务、时间、权限、客观判分由代码决定。
2. Evidence First：课程事实和答案解析绑定 Evidence Object。
3. Learner Model First：学生状态进入所有教学决策。
4. Workflow over Free Agent：LangGraph 显式状态机约束 Agent 行为。
5. Provider-neutral capability routing：模型、语音、搜索、存储可插拔。
6. Course/Question/Student 共享 Concept Space。
7. Durable session：语音、课程生成、解析、评分均可恢复。
8. Learning by doing：课程运行时以练习、反馈和诊断为中心。
