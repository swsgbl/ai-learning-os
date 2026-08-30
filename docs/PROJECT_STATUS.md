# 项目进度台账

唯一进度真相源。每完成一个任务立即更新本文件，再 commit + push。
验收依据：docs/delivery/ 定版文档与 docs/delivery/11_IMPLEMENTATION_BACKLOG.md。

## 当前里程碑

M0 Foundation（已完成）→ M1 Content 基础层（进行中）

## 当前任务

M1-07 解析任务队列

## 已完成任务

| 任务 | 提交 | 测试证据 | 日期 |
|---|---|---|---|
| M0-01 生产 monorepo | 036cd6e | typecheck/lint/build/pytest/ruff 全绿 | 2026-08-30 |
| M0-02 Docker Compose 基础设施 | 036cd6e | compose 服务与 healthcheck、持久化卷就位 | 2026-08-30 |
| M0-03 FastAPI skeleton | 036cd6e | pytest 3 passed | 2026-08-30 |
| M0-04 Next.js skeleton | 036cd6e | build 成功，7 路由可渲染 | 2026-08-30 |
| 环境恢复（依赖安装） | 无代码变更 | baseline 全绿 | 2026-08-31 |
| 进度台账建立 | 129f638 | - | 2026-08-31 |
| M0-05 + M0-持久化（PostgreSQL repository + Alembic） | a6bd316, merge 2fa1a8c | pytest 12 passed（含真实 PG）；migration up/down/dry-run；全部门禁绿 | 2026-08-31 |
| M0-06 CI 门禁（GitHub Actions） | 1eb3436, merge 83bf7ea | Actions run 33329866289：Web 1m10s 绿 / API 34s 绿（真实 PG service container + migration roundtrip） | 2026-08-31 |
| M0-07 密钥与隐私配置 + 运维验证 | 622246e | Settings 隐私路由 4 测试、.env.example 按 runbook §3、MinIO healthcheck、compose 全栈 healthy + 重启数据保持 | 2026-08-31 |
| M1-01 Source Registry | 68599ef, merge 24e0193 | pytest 21 passed（含真实 PG）：seed 6 来源分层正确；CRUD+verify；重复 409；非法 id 422；无 DB 503；migration 0002 up/down roundtrip；ruff+前端门禁绿 | 2026-08-31 |
| M1-02 License State Machine | 5c3abb6, merge 6740b4c | pytest 33 passed（含真实 PG）：迁移矩阵 5 测（UNKNOWN 任意/PROHIBITED 吸收/回审/封禁/改判）；准入+存储策略 4 测（UNKNOWN 不进池、ACCESS_CONTROLLED 不存正文）；API 3 测（认定入池、吸收态 409、404）；前端门禁绿 | 2026-08-31 |
| M1-03 文件上传与 hash 去重 | ccbd320, merge 945fb9c | pytest 40 passed（含真实 PG）：同内容重传同 id + deduplicated；license 守卫（UNKNOWN 403、OPEN_LICENSE 放行）；类型白名单 422、空文件 422；404/503；真实 MinIO boto3 读写 + uvicorn 全链路冒烟（201→重传 dedup true→mc cat 内容一致）；migration 0003 roundtrip | 2026-08-31 |
| M1-04 Parser adapter 框架 + 安全修复 | 58abe1a, merge ef7009f | pytest 50 passed（含真实 PG）：registry 降级/指定重跑 4 测；API 解析成功/失败持久化/换 parser 重跑/fake 注入 5 测；migration 0004 动态 head；安全审查 3 项修复（license 快照、分块上传、dedup 补绑 source）+2 测；前端门禁绿 | 2026-08-31 |
| M1-05 Layout normalize | 2be8187, merge 6d8555c | pytest 55 passed（含真实 PG）：to_latex 符号+定界符 2 测；normalize_blocks 类型归一/页码继承/公式 LaTeX/表格结构化/slide 传递 3 测；parse 端点 normalize 集成 1 测；前端门禁绿 | 2026-08-31 |
| M1-06 Chunk 与 Evidence | 本分支 | pytest 58 passed（含真实 PG）：chunk locator/evidence 字段（parser/hash/locator/license）2 测；重解析幂等替换 1 测；长文切分页码保留 1 测；migration 0005 roundtrip；前端门禁绿 | 2026-08-31 |

## 待办任务（按 backlog 顺序）

- [ ] M1-07 解析任务队列（进行中）
- [ ] M1-08 解析质量报告
- [ ] M2-04 Redis timeout worker（Redis 延迟队列 + DB 事务兜底）
- [ ] M1-03~08 Content Ingestion（上传、parser adapter、chunk/Evidence、队列、质量报告）
- [ ] M2-01~11 题库 schema、试卷导入、grader、报告
- [ ] M3-01~07 Student Model
- [ ] M4-01~09 Voice
- [ ] M5-01~08 Search + 治理
- [ ] M6-01~07 评测/安全/备份
- [ ] M7-01~06 Release

## 阻塞与风险

| 类型 | 内容 | 状态 |
|---|---|---|
| 已修复 | asyncpg naive-datetime 时区 bug（曾致考试误判过期）；写库统一 aware UTC | 回归测试锁定 |
| 已修复 | sequence 跳号契约漏洞；两实现同步收紧为连续递增 | 契约测试锁定 |
| 环境 | postgres 容器运行中；push 偶发代理抖动，带 http_proxy=127.0.0.1:7892 重试即可 | 无阻塞 |
| 观测 | Actions 提示 actions/checkout 等目标 Node20 弃用（cosmetic） | 后续 hardening 升级 |

## 架构决策记录

| # | 决策 | 依据 | 日期 |
|---|---|---|---|
| 1 | Repository Protocol 先行，PG 等价替换且不改 API route | AGENTS.md 规则 3 | 2026-08-30 |
| 2 | 考试时间/客观判分/幂等提交服务端权威；active 响应不含答案解析 | docs/delivery/04 | 2026-08-30 |
| 3 | 答案事件 append-only，(exam_session_id, sequence) 唯一且连续递增；同载荷重放幂等、异载荷 409 | docs/delivery/04 §2.9/§6 | 2026-08-30 |
| 4 | 单元测试用 SQLite 替身；真实 PG 集成经 AIOS_PG_TEST_URL 门控 | AGENT_PROMPTS M0-06 | 2026-08-31 |
| 5 | 时间列统一存 aware UTC；读回统一 astimezone(UTC) | 实测修复 asyncpg 行为 | 2026-08-31 |
| 6 | SQLite 替身直接 create_all；PG 启动自动 alembic upgrade head | 开发者零迁移心智负担 | 2026-08-31 |
| 7 | CI api job 挂真实 PG service container，migration check 显式三步 | M0-06 验收 | 2026-08-31 |
| 8 | Source 元数据与内容抓取分离：registry 只存元数据（robots 快照、rate limit、license/trust 分层），不抓正文 | docs/delivery/03 §3.1；抓正文属 M1-03+ | 2026-08-31 |
| 9 | 种子来源全部 license_state=UNKNOWN，进入公共复用池前必须显式 verify | backlog M1-01/M1-02 验收语义 | 2026-08-31 |
| 10 | License 状态机：UNKNOWN 可认定任意状态；PROHIBITED 吸收态不可迁出；已认定可回 UNKNOWN重审或改判；准入仅 PUBLIC_ACCESS/OPEN_LICENSE/RESTRICTED_NON_COMMERCIAL；ACCESS_CONTROLLED/UNKNOWN/R 级只存元数据 | 10 号文档 §6.3/§6.4 + 08 号 License Registry | 2026-08-31 |
| 11 | 上传去重键为 SHA-256 content_hash（唯一约束 + 撞约束回查），对象以 uploads/{hash[:2]}/{hash} 内容寻址；带 source 上传受 allows_full_text_storage 守卫（UNKNOWN 亦拒正文） | 04 号文档 §2.4/§6.6 + M1-02 状态机联动 | 2026-08-31 |
| 12 | 上传时把来源 license_state 快照到资源行；无 source = 用户私有文档（access_state=unknown）；dedup 命中补绑来源并快照；上传分块读入增量限额 | push 安全审查 3 项（authorization-bypass/logic-data-integrity/resource-bound-placement） | 2026-08-31 |
| 13 | Parser 框架：Protocol + registry 惰性实例化（ParserUnavailable 自动跳过）+ prefer 指定重跑；Docling 延迟导入按部署安装，json-dataset 为零依赖真实实现 | prompt pack C 要求真实+fake 双实现 | 2026-08-31 |
| 14 | normalize 管线为 parse 内置步骤（parser 输出必过 normalize_blocks）；表格块保留换行结构先于空白折叠；符号表存裸 LaTeX 名运行时拼反斜杠（规避源码转义坑） | M1-05 实测；backlog M1-05 验收 | 2026-08-31 |
| 15 | Chunk/Evidence 采用整资源替换语义（重解析 delete+insert 同事务），chunk 哈希可回溯页码/slide；evidence 携带 parser 名与上传时点 license 快照 | backlog M1-06 + prompt pack C 幂等要求 | 2026-08-31 |

## 下一任务

M0-07 完成后：compose 全栈运维验证（M0 收尾），随后进入 M1-01 Source Registry。

## 追加：M0 收尾验证（compose 全栈）

- infra/docker-compose.yml：MinIO 增加 healthcheck（mc ready local）
- 实测：postgres / redis / minio 三服务全部 healthy；API 镜像 docker build 成功
- 持久化验收：创建考试与提交 -> docker compose restart postgres -> 数据仍在（exams=8 / submissions=2 可读）
- M0 至此全部完成，进入 M1 Content Ingestion
