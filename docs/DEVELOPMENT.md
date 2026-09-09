# 开发指南

## 里程碑状态

全部里程碑进度、任务台账与架构决策见 `docs/PROJECT_STATUS.md`（单一真相源，
本文件不再维护里程碑快照——历史快照曾停留在 M0，已删除以免误导）。

当前版本 `0.1.0`（真相源：仓库根 `VERSION`）。M0-M7 已交付；M8-00 为发布
真实性修复（CI 环境隔离、镜像版本打包、MinIO 持久化、Docker CI 门禁）。

## API 契约

```text
GET    /api/v1/papers
POST   /api/v1/papers/{paper_id}/exams
GET    /api/v1/exams/{exam_id}
PUT    /api/v1/exams/{exam_id}/answers
POST   /api/v1/exams/{exam_id}/submit
GET    /api/v1/exams/{exam_id}/submission
```

规则：

1. `server_started_at` 和 `server_end_at` 只由 API 写入。
2. `questions` 不包含 `answer`、`explanation`、`angles`。
3. `sequence` 必须从 1 开始并递增（不允许跳号）。
4. 重复提交返回同一 submission。
5. 交卷后答案事件被拒绝。

## 数据库

```powershell
docker compose -f infra/docker-compose.yml up -d postgres
# API 启动时自动 alembic upgrade head；手动执行：
alembic -c services/api/alembic.ini upgrade head
```

- PostgreSQL 仓储与内存仓储共用同一 `Repository` 协议，API route 不感知实现差异。
- 单元测试用 SQLite aiosqlite 内存替身；真实 PG 集成测试设 `AIOS_PG_TEST_URL` 后运行，
  **且必须指向隔离测试库（`ai_learning_os_test` 等）**——安全门控
  （`services/api/app/db/test_gate.py`，M10-04 返工）只放行隔离测试库名，
  主/共享库名（`ai_learning_os`）、维护库 `postgres`、缺库名、非 PG 驱动
  一律自动跳过并给出原因，被拒绝的测试**不建立任何连接**。
- 环境变量按用途隔离（M8-00 教训）：pytest 只认 `AIOS_PG_TEST_URL`；
  `DATABASE_URL` 属于 alembic / API 运行时，混入 pytest 会把「无 DB 503」
  测试翻成 DB 路径导致断言失败。

## 认证与角色（M9-01 / M9-04）

- `AUTH_SECRET` 未配置 = 认证关闭（本地单用户模式），`GET /api/v1/auth/status` 如实透出；
- 配置后全业务路径要求 Bearer token 或浏览器 HttpOnly cookie；**治理动作（source 登记/verify/license 改判、
  概念图发布、课程生成/导入、变式、试卷抽取草稿的 approve/reject、审计读取）仅 admin**：
  learner 返回 403（门禁先于 404，不暴露存在性）；
- Web 登录态：`POST /auth/login` 设置 `aios_auth` HttpOnly cookie（默认
  `SameSite=Lax`；HTTPS 反代部署设置 `AIOS_AUTH_COOKIE_SECURE=true`）。页面
  JavaScript 不保存 JWT；CLI/API 仍使用 Bearer token。
- 个人数据（私有资源、考试、语音会话、搜索记录）严格 owner-scoped（他人 404）；
- 角色：`role` 默认 learner，无默认管理员账号——首个 admin 由持有数据库访问权的
  运维经 CLI 提升：`python -m app.ops.cli admin promote|demote|list <username> --db-url ...`；
  每次变更写审计（actor/before/after/request_id）；
- 生产 AUTH_SECRET fail-closed：APP_ENV=production 时未配置 / <32 字节 / 公开默认占位值
  一律拒绝启动；其他环境配置了但 <32 字节也明确报错；
- CORS 与 Web 端口联动：`AIOS_WEB_PORT` 自定义时默认跟随，`AIOS_CORS_ORIGINS` 可完整覆盖
  （冒烟验证 preflight 一致性）。

## 审计（M9-04 / M10-04）

治理动作（license 变更、DAG 发布、四类草稿 approve/reject、角色提升/降级）写
`audit_log`：actor、action、target、before/after、时间、request id（响应头
X-Request-ID 可关联）。读取 `GET /api/v1/audit` 仅 admin（auth off 本地模式可读）。

### 防篡改哈希链（M10-04）

- 算法：SHA-256。每条审计在写入事务内追加 `audit_chain_entries`
  （`audit_id` PK + FK ON DELETE RESTRICT、`sequence` 全局唯一从 1 连续、
  `previous_hash`、`entry_hash`、`algorithm=sha256`），entry_hash =
  sha256(canonical JSON of `previous_hash + sequence + audit 稳定字段`)；
  canonical JSON 为 sort-keys + 紧凑分隔符 + UTF-8，datetime 统一 UTC
  恒定微秒位——同值恒同哈希，跨 SQLite/PG 读回一致。
- 唯一写入入口：`app.domain.audit_chain.append_audit(session, payload, clock)`
  ——同事务内 FOR UPDATE 锁 `audit_chain_state` 单行（缺失时原子初始化
  genesis）分配 sequence、插入 audit 行并 flush、算 entry_hash、写 entry、
  推进 state；任一步失败随调用方事务整体回滚（fail-closed，并发不双初始化
  不断链）。生产代码禁止直接构造 `AuditLogRow` 绕链（静态守卫测试）。
  payload 的 before/after 递归扫描敏感键（password/token/secret/api_key 等
  变体），命中即拒绝写入——secret 不入日志也不入哈希。
- 验证（只读，零写入）：`python -m app.ops.cli audit-chain-verify --db-url ...`
  （`--json` 出完整报告）。全量重算比对：entries 与 audit_log 一一对应、
  sequence 连续、genesis previous_hash=64 个 0、previous/entry hash 链接、
  state head 与 algorithm。退出码：valid=0、invalid=1、缺 --db-url/连接
  失败=2。输出只含 audit_id/sequence 与原因，不含 before/after 正文。
- 边界（如实声明）：哈希链可检测篡改与漏记（改行内容、删 entry、跳号、
  head 漂移），**不等于数字签名，也不是存储级 WORM**——持有数据库写权限
  的攻击者理论上可整链重算；抵御整链重算靠**库外锚定**（M10-06，见下节：
  把 head_hash 定期记录到库外 append-only 锚文件并交叉核对，锚文件须
  另行归档到 WORM/对象锁/离线介质）。
- 迁移 `0027_audit_chain`：对既有 `audit_log` 按 id 升序一次性建链
  （与应用层同源算法），downgrade 只删两张新表不动审计数据。
  **生产主库（5433/ai_learning_os）尚未执行 0027**——生产库的链表与
  state 需运维在发布窗口显式 `alembic upgrade head` 后建立；执行前的
  生产审计仍为 append-only 无链形态，`audit-chain-verify` 会如实报告
  表缺失（exit 1）而非误报有效。

#### `0027_audit_chain` 生产迁移 runbook

前提：`alembic current` 必须显示 `0026_paper_owner`（不是该版本先停下
排查，不要盲跑 upgrade）。生产 URL/凭据只来自安全运维环境（部署 secret /
运维会话环境变量），**不入命令文档示例、不入 git、不回显日志**——下文
`<生产URL>` 一律是占位符。

1. **备份**：迁移前对数据库做完整备份（至少覆盖 `audit_log`；可用
   `python -m app.ops.cli backup --db-url <生产URL> --out ...` 或既有
   数据库备份通道）。
2. **静默审计写入方（quiesce）**：先停止/静默旧版 API 等一切直接写
   `audit_log` 的进程与 CLI（治理动作、admin 角色变更、数据治理迁移都
   会写审计），**再**执行 `alembic -c services/api/alembic.ini upgrade
   head`。禁止迁移过程中旧代码继续直接写 audit_log——迁移只对存量行
   建链，窗口期写入的 audit 行不会进链表，恢复后即为永久漏链行，
   verifier 持续 INVALID。
3. **迁移后验证（放行门禁）**：`python -m app.ops.cli audit-chain-verify
   --db-url <生产URL>` 必须 **valid 且 exit 0** 才恢复服务；INVALID
   保持停机排查原因，不带病恢复。
4. **初始锚定（M10-06，恢复服务前）**：verifier valid 后立即对空/
   存量链创建 initial anchor（命令与归档要求见下「库外锚定」节的
   锚定 runbook 第 1 步），锚文件归档到 WORM/对象锁/离线介质后再
   恢复服务——否则存量链头仍无库外见证。
5. **恢复后观察**：确认新审计写入正常（治理动作落 audit_log 且链
   sequence 前进），并复跑一次 verifier 确认仍 valid。
6. **downgrade 仅作应急方案**：必须先停写入、先备份、获得明确审批后
   才执行；它使生产回到无链形态，事后需重新走本 runbook 建链。

### 库外锚定（M10-06）

哈希链 + verifier 是库内自证：持数据库写权限者可整链重算且重算后自洽。
锚定把每个时刻的链头（sequence + head_hash）写到**数据库之外**的
append-only JSONL 锚文件，锚文件自身成链；既有锚点与重算后 DB 链在同
sequence 上的 entry_hash 必然对不上，交叉核对即可发现重算/回滚。

- **锚文件格式**：每行一个 JSON 对象，字段固定且不含任何敏感信息——
  schema_version、algorithm、sequence、head_hash、anchored_at、
  previous_anchor_hash、anchor_hash。anchor_hash = sha256(canonical
  JSON of 其余六字段)（与 DB 链同源 canonical 规则），首锚
  previous_anchor_hash = 64 个 0，逐锚链接。新建文件权限 0600（POSIX
  语义；**Windows 无 POSIX 权限位**，等效默认 ACL——锚内容本就无敏感
  值，机密性不依赖文件权限，完整性依赖 WORM 副本）。
- **CLI**：`python -m app.ops.cli audit-chain-anchor --db-url ...
  --anchor-file <path> [--yes | --verify-only] [--json]`
  （`app/ops/audit_chain_anchor.py`）。默认 dry-run 只打印将追加的锚行；
  `--yes` 才落盘（O_APPEND 单行写入 + fsync；写入/文件 fsync/新建后
  父目录 fsync 任一失败都尽力回截原大小并尽力 fsync 持久化回截——
  回截成功时报失败即锚行不在盘上，重跑不会误判 up-to-date；回截或
  回截后的 fsync 也失败时状态未知，由下次完整校验或人工排查处理；
  打开/创建区分与持久化能力边界见下条）；
  `--verify-only` 只做「DB 链 + 锚文件链 + 两者 head 交叉一致」校验。
  退出码与 verifier 对齐：valid/up-to-date/anchored/dry-run=0、
  invalid=1、缺 `--db-url`/锚文件路径问题（symlink、目录、父目录缺失，
  不自动创建）/连接失败=2。锚定对数据库零写入。
- **追加前防线**（顺序执行，任一失败拒绝且不落盘）：① verifier 全量
  重算当前库必须 valid；② 既有锚文件完整解析校验（UTF-8、JSONL、字段
  集合、类型、anchor_hash 重算、锚链链接、sequence 严格递增；partial
  line、空行、非 JSON、残缺 UTF-8 均 invalid **不自动修复**）；③ 每个
  历史锚点 (sequence, head_hash) 与当前 DB 同 sequence 的 entry_hash
  交叉核对（sequence=0 对 genesis 常量）——DB 整链重算（同 sequence
  不同 hash）或回退（锚点 sequence 不在当前链中）一律拒绝；④ DB head
  与最后锚点相同则 up-to-date，不重复追加。
- **并发边界**：锚定读取在**单一连接、单一事务快照**内完成 verify +
  交叉核对 + head 提取（PG 连接提升 REPEATABLE READ；SQLite 走显式
  事务的库级快照；verifier 相应拆出 `load_chain_snapshot` /
  `verify_chain_snapshot` 供同一快照复用），不存在「verifier 一条
  连接、锚定义一条连接」的竞态（测试锁定全流程只建一个引擎）。M10-08
  真实 PG 实测发现此处 `AsyncConnection.execution_options` 漏 `await`
  导致隔离级别静默不生效（协程被丢弃、事务实际 read committed），已
  与 preflight 同型缺陷一并修复；M10-09 补上该路径的**独立门控实证
  测试**（`tests/test_audit_chain_anchor.py` 第 7 节）：安全
  `AIOS_PG_TEST_URL`（`app.db.test_gate` 白名单）指向隔离测试库时，
  在 `load_verified_snapshot` 同一快照事务内 `SHOW transaction_isolation`
  = repeatable read，且第一次链读取确立快照后独立连接并发提交一条新
  审计，同一事务内第二次链读取仍返回第一次读取前的同一 PG 快照
  （head 不漂移）——漏 `await` 旧形态下两条断言实测均失败（read
  committed + head 漂移），暴露力锁定；测试不建辅助表、按基线彻底
  恢复共享隔离库（零残留），未设安全 env 时 skip。不引入
  后台服务与文件锁：两名操作员同时向同一锚文件追加会立刻造成
  previous_anchor_hash 断链，被下一次校验 fail-closed 发现（可检测；
  锚定操作按 runbook 串行执行）。
- **文件创建与持久化边界**（Codex 审核返工）：`--yes` 落盘区分续写与
  新建，不盲开 O_CREAT——既有锚文件以 `O_WRONLY|O_APPEND` 打开（不带
  O_CREAT；POSIX 附带 O_NOFOLLOW，路径是 symlink 时内核在 open 处即
  ELOOP 拒绝），文件不存在才以 `O_CREAT|O_EXCL` 排他新建（0600）；
  并发窗口内路径被另一操作员抢先创建/替换则 FileExistsError 失败退出
  （exit 2），**不猜测、不覆盖**（不引入文件锁，双操作员并发仍按
  上条「可检测断链 + runbook 串行」边界处理）。Windows CRT 的 O_EXCL
  会跟随 dangling symlink，新建后另复核路径本身不是链接。新建文件
  写入并 fsync 成功后，在支持目录 fsync 的 POSIX 平台 fsync 父目录
  （使目录项在 crash 后尽量持久；个别文件系统返回 EINVAL 视为能力
  不支持而跳过，其余 IO 错误不虚报——上抛前先回截已写锚行：新建
  文件回到 0 字节空文件=合法初始状态，不 unlink，删除目录项又需
  目录 fsync 而它正在失败；回截或回截后的 fsync 也失败时仍上抛
  **原始** OSError 不虚构成功，该残余灾难路径由下次完整校验按
  partial line fail-closed 或人工排查处理）；**Windows 无法打开目录 fd，
  跳过父目录同步**——如实声明，不虚报已持久。Windows 亦无 O_NOFOLLOW：
  前置 symlink 拒绝与打开后 fstat 常规文件复核之间仍存在 symlink
  swap 残余竞态窗口（POSIX 已由 O_NOFOLLOW 消除）；锚行本就无敏感值
  且权威见证在 WORM 副本，该窗口不扩大敏感暴露面。
- **锚文件必须另行归档到 WORM/对象锁/离线介质**：本机锚文件是可变
  文件系统对象，持主机写权限者可连锚文件一起重写——它只是「操作
  见证」，单独不构成对持库写权限者的防御。归档介质（对象锁桶、S3
  版本化、一次性写入介质、离线保管）与复制动作由运维负责，工具不
  代管、也不虚报「已锚定到 WORM」。

#### 锚定 runbook（生产首次与定期）

1. **首次（0027 迁移后立即）**：上方迁移 runbook 第 4 步——verifier
   valid 后、恢复服务前，先 dry-run 复核将写的锚行，再执行
   `python -m app.ops.cli audit-chain-anchor --db-url <生产URL>
   --anchor-file <运维保管路径>/audit-anchor.jsonl --yes`；随后立即
   把锚文件复制到 WORM/对象锁/离线介质并登记介质位置与最后锚点
   anchor_hash。空库锚 sequence=0（genesis）同样有效。
2. **定期锚定**：建议每周一次、每次重大治理动作后、或审计增量超过
   阈值时执行：先 `--verify-only`（三链一致 exit 0），再 dry-run、
   `--yes` 追加，然后更新归档副本。锚定不锁库不阻塞业务——锚的是
   读取时刻的链头，锚定窗口内新增审计只会让下次锚定继续前进。
3. **恢复/审查流程**：怀疑审计被篡改时，取**WORM/离线副本**（只读）
   作为锚文件运行 `--verify-only`：exit 0 = DB 链与全部历史锚点交叉
   一致；exit 1 按 problems 定位——「锚点 sequence=N 在当前 DB 链中
   不存在」= DB 回退；「head_hash 与当前 DB entry_hash 不匹配」=
   整链重算或该位置被重写；锚链「不链接/重算不匹配」= 锚文件本身
   被动过。发现不一致按事故处理：保全 DB 与锚文件证据、比对历史
   备份与归档锚点、追溯时间窗，**不在可疑状态下继续追加锚点**。
4. **当前生产状态（如实声明）**：生产主库未执行 0027、未创建任何
   锚点；本切片只交付工具与流程文档，不执行生产锚定。

### 安全边界（M10-03 后更新）

- 已交付：认证基座、三域归属隔离、Web 登录 UI、角色授权+治理审计、
  私有语料与四类草稿归属、试卷 owner 可见性、Web HttpOnly cookie、治理工作台、
  审计防篡改哈希链与库外锚定工具（M10-04/M10-06；生产主库迁移与首次
  锚定待执行，见上）、生产切换只读 preflight（M10-07；真实生产执行仍需
  用户/运维审批，见「生产切换 preflight」节）；
- 已知边界：744 张历史试卷与 generation/variant 历史无归属草稿仍待人工归属决策
  （M10-04 已交付 `legacy-paper-report`/`legacy-paper-migrate` 与
  `draft-owner-report`/`draft-owner-migrate` 只读报告 + 默认 dry-run 迁移 CLI，
  生产迁移待人工决策后显式 `--yes` 执行）；
  哈希链整链重算的抵御依赖库外锚定 + WORM/离线归档（M10-06 工具已交付，
  生产未锚定；非数字签名，见上）；
  TURN 未内置；云语音/LLM 真实 key 冒烟未执行（云语音 ASR/TTS 冒烟脚本已交付：
  M10-13 `infra/smoke_voice_cloud.sh`；LLM：M10-01 `infra/smoke_llm.sh`——真实
  端点/密钥冒烟待运维显式执行）；检索 cloud-web 已交付真实
  SearXNG-compatible 实现与冒烟脚本（M10-12——真实端点冒烟待运维显式执行，key 按需）。
- 部署绑定：所有端口默认 127.0.0.1；LAN/外网需 `AIOS_BIND_IP=0.0.0.0` 且必须同时
  设强 AUTH_SECRET + APP_ENV=production（启动 fail-closed），否则不要对外暴露。


- `AUTH_SECRET` 未配置 = 认证关闭，`GET /api/v1/auth/status` 如实透出 `auth_enabled=false`；
- 配置后（compose 已注入 dev 值）CLI/API 使用 `Authorization: Bearer <token>`，
  浏览器使用 `aios_auth` HttpOnly cookie；`register/login/status/logout` 与
  `/health`、`/api/v1/version`、`/docs` 豁免；
- 端点：`POST /api/v1/auth/register`（重复 409）、`POST /api/v1/auth/login`（失败统一
  「用户名或密码错误」防枚举）、`GET /api/v1/auth/me`；JWT HS256，默认 24h 过期。
- 生产部署用部署 secret 覆盖 `AIOS_AUTH_SECRET`；密码只存 bcrypt 哈希（72 字节上限）。
- HTTPS 反代部署同时设置 `AIOS_AUTH_COOKIE_SECURE=true`；`AIOS_AUTH_COOKIE_SAMESITE=none`
  仅用于跨站部署且会强制 Secure；`CORS_ORIGINS` 拒绝通配符 `*`（credentials 模式下
  `*` 会被反射成任意 Origin+凭据放行，启动即报错）。
- 数据归属（M9-02 资源/考试/语音 + M9-05 四类草稿与私有语料边界）已落地：个人数据严格 owner-scoped；当前真实边界见上方「安全边界」。

## 生产数据治理（M10-03 / M10-04）

- 只读盘点与验收清理：`python -m app.ops.cli data-inventory` / `acceptance-clean`
  （见 M10-03；清理默认 dry-run、精确行 ID 单事务、append-only 审计保留）。
- 历史无归属试卷（`owner_id IS NULL` 非 seed）：`legacy-paper-report` 只读分类
  （缺 DB fail-closed；默认摘要不含生产 paper ID；`--output` 强制 gitignore 的
  artifacts/temp 目录）；`legacy-paper-migrate {keep-public,assign-owner,export-delete}`
  默认 dry-run、`--yes` 才执行，只收精确 paper ID（未知 ID 整体拒绝），事务内
  复核行数/行状态、before/after 同事务写审计；export-delete 先导出校验 JSONL
  才删库，被历史考试引用的卷一律拒绝（人工处理，不级联删考试）；`--output`
  批次报告原生落盘（M11-13，护栏见「治理证据推导 governance-evidence」节）。
- 边界：CLI 不提供按标题/来源模糊批量操作；报告/导出文件含生产 ID 只写 gitignore
  目录；生产执行必须运维逐批显式 `--yes`。
- 历史无归属草稿（generation/variant，`owner_id IS NULL`，无资源外键锚）：
  `draft-owner-report [--kind course-generation|variant-question]`（缺省两类都报）
  只读报告——引用从业务 JSON 重构（`plan.lessons[].resources[].resource_id` /
  `variants[].evidence.resource_id`），仅当全部引用资源存在且同属唯一非 NULL
  owner 时建议 assign_owner（并给出该 owner），无引用/NULL owner/多 owner/资源
  缺失一律 manual_review，不自动猜测；默认摘要不含生产 draft_id，`--output`
  同 artifacts/temp 护栏。`draft-owner-migrate {assign-owner,keep-unowned}
  --kind <必填>` 默认 dry-run、`--yes` 才执行，只收精确 draft ID（未知或属于
  另一 kind 的 ID 整体拒绝），只改 owner_id（keep-unowned 零修改、只写审计
  决策），事务内 FOR UPDATE 复核目标用户与行状态，审计与更新同事务；
  生产未执行任何迁移（M10-04 第三切片）；`--output` 批次报告原生落盘
  （M11-13，护栏见「治理证据推导 governance-evidence」节）。
- 治理步证据导出（M11-12）：报告 + 成功批次 -> rehearsal/readiness 可
  消费的 `pending_count` 脱敏证据，见「治理证据推导 governance-evidence」
  节——计数由报告明细与批次 eligible 集合确定性推导，非人工转抄。

## 治理证据推导 governance-evidence（M11-12）

- **CLI**：`python -m app.ops.cli governance-evidence --report <报告JSON>
  --batch <批次JSON> [--batch ...] --output <artifacts>/legacy-papers.json
  |draft-ownership.json [--json]`，实现文件
  `services/api/app/ops/governance_evidence.py`。
- **定位**：cutover-rehearsal（M10-15）/ release-readiness（M10-11）的
  `legacy-papers` / `draft-ownership` 治理步需要 `pending_count` + `batches`
  证据；此前只能人工从报告抄录计数拼装（转抄没有任何交叉校验，抄错即
  证据失真）。本工具把「一份完整治理报告 + 一或多个成功 migrate 批次」
  确定性推导为同契约脱敏证据——与 backup-restore-evidence（M11-04）
  同一动机：人工拼装改为机器可复现导出。操作链：`legacy-paper-report
  --output` / `draft-owner-report --output` 出报告（artifacts/temp）→
  逐批 `legacy-paper-migrate --yes --output <artifacts路径>` /
  `draft-owner-migrate --yes --output <artifacts路径>`（批次报告原生
  落盘，M11-13 起支持，不再建议 stdout 重定向——见下条边界）→ 本命令
  推导出证据文件。
- **批次报告原生落盘（M11-13）**：两个 migrate 命令支持可选
  `--output <artifacts>/batch.json`，把返回的批次 report 原子落盘供本命令
  `--batch` 直接消费；不传 `--output` 时行为与此前完全一致（stdout 打印
  报告 JSON + 提示）。边界：输出必须位于 gitignore 的 artifacts/temp 内，
  symlink（含中间目录组件）/越界/目录形态在数据库访问前拒绝（exit 2、
  不连库、无输出文件）；输出不得与 `--ids-file`（及 export-delete 的
  `--export`）指向同一文件（`resolve` + `os.path.normcase` 归一比较，
  Windows 大小写/`..` 折叠等价书写不构成绕过；冲突时输入字节不变）；
  写入复用 CLI 共享原子写（同目录临时文件 + fsync + os.replace——失败
  旧输出字节原样、无 `.tmp` 残留、exit 2 并如实说明数据库执行与证据
  落盘状态）；dry-run 与 failure report 如实落盘且保留原退出码（本命令
  照旧拒绝其作为成功批次）；带 `--output` 时 stdout 只输出报告 JSON
  （可解析），`[dry-run]`/`[失败]`/「报告已写入」提示走 stderr；
  run_* 抛 RuntimeError（事务已回滚、无 report）时不写新输出、旧输出
  字节不变。
- **确定性推导（非转抄）**：`pending_count = 报告明细 ID 集合 - 全部成功
  批次 eligible ID 并集`（legacy 按 paper_id；draft 按 (kind, draft_id)
  二元组，两类 kind 独立不串）。批次处理过的 ID 不必仍在报告中——
  assign-owner/export-delete 执行后行已移出治理范围、重跑报告自然不含，
  keep-public/keep-unowned 不改行、仍在；两类时序形态同一公式覆盖。
- **失败批次 fail-closed（exit 2、不写输出）**：每个批次必须是成功执行
  形态（`executed=true` 且 `dry_run` **显式为 false** 且 `exit_code=0`
  且无 `failure`/invalid ID 且 eligible ⊆ 请求集合且 requested 与 ID
  条数一致，且 `audit_action` 与迁移路径/草稿 kind **精确匹配**——
  keep-public => `ops.legacy_paper.keep_public`、assign-owner =>
  `ops.legacy_paper.assign_owner`、export-delete =>
  `ops.legacy_paper.export_delete`、draft assign-owner =>
  `ops.draft_owner.assign_owner`、draft keep-unowned =>
  `ops.draft_owner.keep_unowned`）——dry-run（含 `dry_run=true` 即便
  `executed=true` 的拼改畸形形态）、审计动作错配（跨动作/跨 kind）、
  未知 ID 拒绝、目标用户不存在、导出校验失败等任何非成功形态一律拒绝，
  绝不从完整性存疑的执行历史推导 pending_count（会低估剩余待处理数）。
  批次类型必须与报告类型匹配；报告结构必须自洽（`summary.total` ==
  明细条数、ID 唯一、papers/drafts 恰有其一切判类型）。
- **路径护栏与输入保护**：报告/每个批次/输出都必须位于 gitignore 的
  artifacts/temp（复用 `is_safe_artifact_path`，含生产 ID 的文件不入
  仓库）；任何已存在路径组件是 symlink 即拒绝；**输出不得覆盖任何输入**
  ——输出 resolved 路径等于报告或任何批次的 resolved 路径即 exit 2
  （`resolve` + `os.path.normcase` 归一比较，Windows 大小写/`..` 折叠等
  等价书写形态不构成绕过；冲突时输入字节保持不变）；**同一批次文件不得
  重复传入**（含等价路径规范化后的重复——重复会虚增成功批次数）；输出
  文件名必须恰为 `legacy-papers.json` / `draft-ownership.json` 且与报告
  类型对应（manifest 工具只认精确证据文件名，防笔误产出不可消费文件）；
  输出原子落盘（同目录临时文件 + fsync + os.replace，失败旧文件字节
  原样、无 `.tmp` 残留、不打印推导结论）。
- **输出零业务 ID/零敏感值且契约最小化**：证据只含白名单标量
  （step/pending_count/`batches[].executed`——每项仅此一键——与聚合计数
  `batches_executed`/报告聚合计数/源报告 sha256/时间戳），**不输出任何
  逐批迁移路径、逐批解决计数、逐批文件哈希或其它批次细节**（人类摘要
  同样只打印聚合计数）；报告与批次正文一律不透传；输出可过下游装载层
  的敏感键与内嵌凭据扫描。
- **退出码**：`pending_count=0` => 0；`pending_count>0` => 1（证据照常
  原子落盘，如实记录治理未归零，不伪装 pass）；输入/路径/结构/失败
  批次/写入失败 => 2。`--json` 时 stdout 纯 JSON、提示走 stderr。
- **隔离声明**：纯本地文件推导器——不连数据库、不读环境变量、不访问
  网络、不执行任何迁移/治理/锚定/部署，无 `--yes` 执行形态；对报告与
  批次文件零写入（字节保持不变）。

## provider 冒烟证据导出与聚合 provider-smoke-evidence（M11-16）

- **CLI**：`python -m app.ops.cli provider-smoke-export <search|cloud-voice|llm>
  --output <artifacts>/search-smoke.json|cloud-voice-smoke.json|llm-smoke.json
  [--json]`（单步导出）与 `python -m app.ops.cli provider-smoke-aggregate
  --search <PATH> --cloud-voice <PATH> --llm PATH --output
  <artifacts>/provider-smoke.json [--json]`（三份聚合），实现文件
  `services/api/app/ops/provider_smoke_evidence.py`。
- **定位**：cutover-rehearsal（M10-15）的 `search-smoke` /
  `cloud-voice-smoke` / `llm-smoke` 三步与 release-readiness（M10-11）的
  `provider-smoke` 门此前的证据只能人工抄录冒烟结论拼装（转抄没有交叉
  校验，抄错即证据失真）。本工具把既有三个冒烟脚本的真实执行结论自动
  导出为脱敏、原子、机器可读证据——与 backup-restore-evidence（M11-04）
  / governance-evidence（M11-12）同一动机：人工拼装改为机器可复现导出。
  操作链：运维按各冒烟节执行 `bash infra/smoke_search.sh` /
  `infra/smoke_voice_cloud.sh` / `infra/smoke_llm.sh`（所需 key/端点由
  运维在调用前显式注入环境）→ 本工具逐 provider 导出单步证据 → 三份
  齐备后聚合为门证据。
- **单步导出**：以 bash 运行既有冒烟脚本（cwd=仓库根 + 相对 POSIX 路径
  ——Windows 绝对路径在 WSL bash 下不可解析），子进程整体继承当前环境
  与终端（脚本自身的脱敏摘要直通运维终端，本工具不捕获不保存）；runner
  退出码 0 => `result=pass`（CLI exit 0），非零 => `result=fail`（失败
  证据照常原子落盘、CLI exit 1——如实记录，不伪装 pass）。**本命令不
  改变三个冒烟脚本的判定逻辑、不自动补跑任何冒烟**（只编排运维已决定
  执行的冒烟并导出结论）。
- **聚合**：三份输入必须**确为本工具导出的单步证据**（exact schema）：
  顶层键集合恰为 `tool`/`schema_version`/`step`/`executed`/`result`/
  `exit_code`/`started_at`/`completed_at`/`duration_ms` 九键（缺字段/多字段
  均拒绝——缺 metadata 或携带额外字段的 JSON 无法确为本工具导出）；
  `tool` / `schema_version` 精确匹配、`step` 与 provider 槽位精确匹配、
  `executed=true`、`result` 只能 pass/fail 且与 `exit_code` 结论一致
  （pass => 0、fail => 非 0）、`exit_code`/`duration_ms` 为非 bool int
  （后者非负）、起止时间为 timezone-aware ISO 字符串且
  `completed_at >= started_at`（等时零耗时形态放行）——手工拼装、槽位
  错位、not_executed 形态、metadata 漂移一律 fail-closed 拒绝；确定性拼装
  `provider-smoke.json`（`providers.voice/search/llm` 每项仅 `executed`
  与 `result`，不透传单步 exit_code/时间/脚本细节）；任一 fail =>
  聚合证据照常落盘、CLI exit 1。纯本地文件推导：不运行冒烟、不连
  数据库、不读取任何敏感环境变量、不访问网络。
- **路径护栏与输入保护**：输出/输入必须位于 gitignore 的 artifacts/temp
  （复用 `is_safe_artifact_path`）；任何已存在路径组件（含自身）是
  symlink 即拒绝（先于 `resolve`——链接目标落在护栏内不放行）；已存在
  且不是常规文件拒绝；输出文件名必须恰为对应步/门的精确证据文件名
  （manifest 工具只认精确文件名）；聚合输出不得等于任何输入、同一输入
  不得重复传入两个槽位（`resolve` + `os.path.normcase` 归一比较——
  Windows 大小写与 `..` 折叠等价书写不构成绕过）；护栏先于 runner
  （exit 2 不运行冒烟、不写证据、不创建输出/父目录）；输出原子落盘
  （同目录临时文件 + fsync + os.replace，失败旧文件字节原样、无
  `.tmp` 残留、不打印结论）。
- **编排 fail-closed（exit 2、不写证据）**：provider 名必须是合法枚举；
  bash（PATH 查找）或既有脚本文件不可用（编排环境问题不是冒烟结论）；
  runner 抛 OSError。退出码口径：结论 pass=0 / 结论 fail=1（证据照常
  落盘）/ 输入、路径、编排或写入失败=2。
- **脱敏边界**：模块不读取任何敏感环境变量（provider 端点/密钥/模型名/
  查询词/音频路径等槽位一概不检查——冒烟所需 provider 端点/凭据只由
  运维在调用前注入，子进程整体继承）；对环境的访问仅限 `shutil.which`
  经 PATH 解析 bash 路径（编排检查）与子进程对当前环境的整体继承。
  不捕获不保存子进程 stdout/stderr；单步证据只含白名单标量（`schema_version`/`step`/
  `executed`/`result`/`exit_code`/`started_at`/`completed_at`/
  `duration_ms` 与自声明 `tool`）——无命令行、无 endpoint、无模型名、
  无查询词、无音频路径、无转写正文、无任何摘要文本；输出可过下游装载
  层的敏感键与内嵌凭据扫描。
- **测试**：`services/api/tests/test_provider_smoke_evidence.py`
  （覆盖矩阵：CLI 注册/分发、映射稳定性、fake runner 通过/失败/异常、
  runner 调用形态、路径与 symlink fail-closed、原子写、聚合校验（含
  exact schema 参数化回归：缺字段/多字段/结论与退出码矛盾/无效 naive
  倒置时间/负 duration/bool 伪装 int）、readiness/rehearsal 消费、零敏感
  落盘、源码级守卫）——**全部用 fake
  runner/stub 与本地文件，不执行任何真实外网冒烟**（唯一真实子进程是
  `python -c` 探针，仅验证 cwd=仓库根与环境继承，零网络）。

## 生产证据缺口清单 production-evidence-gap（M11-18）

- **CLI**：`python -m app.ops.cli production-evidence-gap --evidence-dir <path>
  [--output <artifacts/temp路径>] [--json]`，实现文件
  `services/api/app/ops/production_evidence_gap.py`。
- **定位**：cutover-rehearsal（M10-15）回答「13 步演练时间线证据齐不齐」，
  本工具回答「离实际生产切换还差哪几块证据」——把台账「下一任务」定义的
  四类生产前置证据缺口从 rehearsal 的**只读评估结果**中聚合为逐类缺口清单
  （不是 rehearsal 的改名复制：不重新解析证据文件、不重复实现证据 schema
  校验，`build` 恰调用一次 `run_cutover_rehearsal`，步骤状态与
  reason/next_action 全部复用其白名单提取）。四类之外的 5 步（CI、
  release-check、preflight×2、备份恢复）不在本清单范围，`source.`
  `steps_covered`/`steps_total` 如实透出覆盖面，完整时间线仍以
  cutover-rehearsal manifest 为准——**四类全 pass 不代表 13 步全 pass**。
- **类别矩阵**（输出顺序固定；steps 必须是 rehearsal step id，测试与
  `STEP_IDS` 交叉锁定防漂移）：`governance`（历史治理批次执行与计数归零：
  legacy-papers、draft-ownership）、`audit-chain`（0027 建链、校验与库外
  锚定：audit-chain-verify、audit-chain-anchor）、`provider-smoke`（真实
  provider 冒烟证据：search/cloud-voice/llm 三冒烟步）、`cutover-approval`
  （切换审批与发布窗口）。
- **输出语义（每类固定白名单字段）**：`category`/`title`/`basis`/`status`/
  `gap`/`covered_steps`（每项仅 `step`/`status`/`reason`/`next_action`）/
  `existing_tools`（证据从哪来的既有工具与 runbook）/`missing_evidence`
  （非 pass 步清单，每项仅 `step`/`status`）/`operator_actions`（非 pass 步
  的运维动作，来自 rehearsal 的 next_action——真实执行全归运维）/
  `agent_safe_actions`（agent 可安全执行的只读/本地动作，如纯本地文件
  推导与 DRAFT 底稿生成）/`authorization_required`（必须运维显式授权的
  边界：真实 key、生产连接、`--yes` 执行、审批签署）。
- **状态聚合（诚实优先）**：类别内全部步骤 pass 才 pass；有 blocked 优先
  blocked（敏感键/结构不符/结论为否/哈希失配——rehearsal 的 malformed/
  tampered/fail 已统一映射为 blocked，透传即可）；否则 pending；否则
  not_executed（missing/冒烟 not run/缺审批）。绝不把部分通过伪装成
  pass；跨类组合同理（一类 blocked + 一类 pending => overall=blocked）。
- **`production_ready` 恒为 `false`**：本输出是缺口清单，不构成生产放行、
  不构成 production readiness，也不授权任何生产操作（全 pass 时 exit 0
  只说明四类无缺口，该字段仍为 false 并附 `production_ready_note`）。
- **安全边界**：只读本地证据——不连接数据库、不调用 API、不访问网络、
  不读取任何环境变量（`os.environ` 零引用）；不执行任何迁移/治理/锚定/
  备份/部署/启停/发布/回滚、不运行任何 provider 冒烟——命令没有 `--yes`
  执行形态，是纯汇总器；对证据目录零写入。输出零敏感、零生产业务 ID：
  只透传 rehearsal 白名单文本 + 模块静态指引常量；来自 rehearsal 的
  reason/next_action 逐字段过 `scrub_sensitive` 纵深防御（静态常量是代码
  内字面量零敏感、不经运行时 scrub——通用 scrub 会按敏感**键名**模式把
  `authorization_required` 这类白名单字段误抹成占位符）；证据目录内的
  敏感键证据已由 rehearsal 按 blocked（malformed）语义处理，值从不回显。
- **路径护栏与 IO（exit 2）**：`--evidence-dir` 护栏复用 rehearsal
  （不存在/普通文件/symlink/目录内 symlink 拒绝）；`--output` 必须位于
  gitignore 的 artifacts/temp（复用 `is_safe_artifact_path`）、任何已存在
  路径组件是 symlink 即拒绝、已存在且不是常规文件拒绝、**不得位于证据
  目录内或等于证据目录**（拒绝覆盖证据输入，`resolve` +
  `os.path.normcase` 归一比较，Windows 大小写/`..` 折叠不构成绕过）——
  输出护栏先于任何证据内容读取（冲突形态下 rehearsal 零调用，测试锁定）；
  落盘复用 CLI 共享原子写（同目录临时文件 + fsync + os.replace，失败旧
  文件字节原样、无 `.tmp` 残留、不打印缺口结论）。退出码：四类全
  pass=0 / 任一类非 pass=1 / 目录或路径与 IO 问题=2。
- **后续真实运维动作（本工具只列不做）**：① 治理批次——运维以
  legacy-paper-report / draft-owner-report 复核后逐批精确 ID +
  `--yes` 执行（批次报告原生落盘，M11-13），再以 governance-evidence
  推导导出；② 审计锚定——按「审计」节 `0027_audit_chain` 生产迁移
  runbook 对主库建链（备份 -> quiesce -> upgrade -> verify -> 锚定 -> WORM
  归档，head_hash 库外存证）；③ 真实 provider 冒烟——运维显式注入
  key/端点并执行三个冒烟脚本，以 provider-smoke-export 导出单步证据、
  provider-smoke-aggregate 聚合；④ 审批与发布窗口——审批人从
  approval-draft 底稿从零组装 cutover-approval.json 并确认回滚预案与
  发布窗口（外部 coturn 部署模板同步纳入评估）。硬边界：真实 key、生产
  连接与执行批准必须由运维显式提供与授予，agent 不得虚拟生产就绪或代行
  任何生产操作。
- **测试**：`services/api/tests/test_production_evidence_gap.py`
  （覆盖矩阵：CLI 注册/分发、类别矩阵与 rehearsal `STEP_IDS` 交叉锁定、
  状态聚合参数化矩阵、四类映射形态（全 pass/空目录/pending/敏感键 blocked/
  链 invalid/冒烟 fail/缺审批/跨类组合优先级）、exact allowlist schema、
  `production_ready` 恒 false、路径/原子写/symlink/覆盖输入护栏（护栏先于
  证据读取）、零敏感三面 marker 扫描、只读性（输入字节不变）、源码级守卫
  （零 env/DB/网络引用、import 面恰为三个共享层、不含证据校验原语——
  复用而非重实现）、`--json` 纯 JSON 与落盘一致、build 恰调用一次
  rehearsal、covered 状态与独立 rehearsal 运行逐步一致）——全部用临时
  目录与本地文件，不连数据库、不发网络请求。

## 生产证据目录只读索引器 evidence-inventory（M11-20）

- **CLI**：`python -m app.ops.cli evidence-inventory --evidence-dir <path>
  [--evidence-dir <path> ...] [--output <artifacts/temp路径>] [--json]`，
  实现文件 `services/api/app/ops/evidence_inventory.py`。
- **定位**：production-evidence-gap（M11-18）要求一个完整 cutover evidence
  目录才能给缺口结论，而本机现状是证据分散——`artifacts/m11-02/cutover-evidence`
  是明确隔离 fixture/模板脚手架、`artifacts/m11-11` 只有 preflight 与 backup
  碎片、`docs/evidence` 还有历史汇总。本工具回答「这些目录里到底有什么、
  能否适用生产、缺什么」：对一或多个调用方显式提供的本地目录做只读
  **inventory**——逐文件列出 name/相对路径/size/SHA-256/mtime/分类/适用性，
  按目录与总体汇总 13 步覆盖/缺失、重复 sha256、可解析计数。**不做**
  cutover-rehearsal 的 13 步放行判定（`cutover_steps` 覆盖只看 root 顶层
  是否存在**精确证据文件名**——与 rehearsal 只消费 `root/<evidence_file>`
  的口径一致，嵌套同名文件列入 inventory 但不推进覆盖；与
  pass/pending/blocked 结论无关，完整时间线判定仍以 cutover-rehearsal
  manifest 为准——13 步清单直接复用 `cutover_rehearsal.STEPS`，不重新
  定义，测试交叉锁定）。
- **背景结论（Codex 已实测，2026-09-06，只读口径）**：对
  `artifacts/m11-02/cutover-evidence` 只读运行 production-evidence-gap 的
  结论是 `overall=blocked`（governance=pending、audit-chain=blocked、
  provider-smoke=not_executed、approval=not_executed），且该目录 README 自
  声明隔离 fixture——**不代表真实生产通过**，本工具不改变该结论。
- **文件分类（白名单枚举，按序判定）**：`cutover_template`
  （`*.template.json` / `*.jsonl.template`——cutover-evidence-pack 模板命名
  约定）、`audit_anchor_copy`（`audit-anchor.jsonl` supporting 副本）、
  `cutover_step_evidence`（13 步精确证据文件名）、`production_preflight`
  （`production-preflight*.json`）、`backup_manifest`（`manifest.json`）、
  `other`（其余全部——含 `database.json` 与 `docs/evidence` 历史汇总）。
- **适用性（诚实口径，枚举只有三值——本工具绝无 `production_verified`）**：
  模板一律 `not_applicable`（模板不是证据）；目录顶层 `README.md` 明确包含
  「隔离 fixture」声明（或英文 `isolation fixture` 变体，≤1MB UTF-8）时，
  该目录**非模板**条目标 `isolation_fixture` 并在目录 summary 记录依据
  （`fixture_declaration_source=README.md`）；无声明/超限/无 README 则
  `scope=unknown`、条目一律 `unverified`。生产适用性必须由运维按 runbook
  人工判定，工具绝不自动标生产。
- **白名单元数据（零内容回显 + 显示名脱敏）**：只按文件字节计算
  SHA-256/size/mtime（分块哈希，内存占用恒定），不回显文件正文；输出不
  使用绝对路径（目录以调用顺序 `#N` 编号）；**文件显示名脱敏**——文件名/
  相对路径本身可能携带业务 ID、key/token/password 等敏感值，只有 root
  顶层的「已知精确安全文件名」白名单（`SAFE_DISPLAY_FILENAMES`：13 步
  证据文件 + 锚副本 + README.md）原样显示，未知文件名与任何嵌套路径
  （父目录名未证明安全）一律 `[redacted]`，`duplicate_sha256_groups` 的
  paths 用同一安全显示口径；内部排序、hash、coverage 判断可用真实相对
  路径，进入 manifest/summary 的显示字段必须是安全值；已知分类的
  `.json` 才尝试解析为
  object 并提取极小白名单 `declared`（`step`/`phase`/`result` 仅当值命中
  受控枚举、`schema_version` 仅当命中安全标量模式、布尔字段仅当真是布尔、
  时间字段仅当 ≤64 字符且合法 ISO——任何不匹配/缺失一律 null），不输出解析
  错误文本、业务 ID、标题、正文、URL、endpoint、key/token；敏感键只报
  `sensitive_key_detected` 布尔（键名与值从不回显）；未知/非 JSON/模板/
  锚副本不解析内容；超过 1MB 的已知类别文件只做哈希元数据
  （`parse_status=skipped_size`——database.json 这类大文件）。最终 manifest
  整体过 `scrub_sensitive` 纵深防御。
- **输出语义**：顶层固定 `production_ready=false` 与
  isolation/no_execution/scope notes + `exit_code=0`；每目录一节
  （root_index/fixture 声明与依据/scope/文件与子目录计数/分类、适用性、
  解析状态计数/13 步覆盖与缺失/目录内重复 sha256 文件数/文件条目）；
  总体 summary（roots/files/dirs 计数、三组分类计数、13 步并集覆盖/缺失、
  跨目录重复 sha256 组——组内 `#N/相对路径` 标识）。
- **安全边界**：纯本地只读——不连接数据库、不调用 API、不访问网络、不
  读取任何环境变量（`os.environ` 零引用）、不运行任何 provider、不执行
  任何迁移/治理/锚定/备份/部署/启停/发布/回滚——命令没有 `--yes` 执行
  形态；对输入目录零写入（文件字节与 mtime 保持不变）。
- **路径护栏与 IO（exit 2）**：每个 `--evidence-dir` 必须已存在、为真目录、
  非 symlink，**递归枚举遇到任何 symlink 文件/目录（含 dangling）一律
  fail-closed 拒绝、绝不跟随**，**目录枚举本身失败（不可读/访问被拒绝的
  子目录）同样 fail-closed**——`os.walk` 显式提供 `onerror`，枚举错误转
  `EvidenceInventoryInputError`（exit 2），绝不静默跳过、绝不输出不完整
  清单或部分报告文件；同一目录不得重复传入、目录间不得互相嵌套
  （`resolve` + `os.path.normcase` 归一）；`--output` 必须位于 gitignore 的
  artifacts/temp（复用 `is_safe_artifact_path`）、任何已存在路径组件是
  symlink 即拒绝、已存在且不是常规文件拒绝、不得位于任一证据目录内或
  等于任一目录根——输出护栏先于任何目录枚举（测试锁定冲突形态下零枚举）；
  落盘复用 CLI 共享原子写（失败旧文件字节原样、无 `.tmp` 残留、不打印
  盘点结论）。`--json` 时 stdout 纯 JSON、提示走 stderr。退出码：成功盘点
  =0 / 输入或路径与 IO 问题=2（inventory 没有失败语义——缺什么是清单
  内容，不是命令失败）。
- **fixture / production-like / 真实生产的区别（防误判）**：fixture 目录
  （README 声明隔离，如 `artifacts/m11-02/cutover-evidence`）即使含已填
  step 文件也只是 `isolation_fixture`；production-like 目录（无声明、含
  preflight/backup 碎片，如 `artifacts/m11-11`）只能 `unverified`——工具
  无法也不试图自证「这些证据来自真实生产」；真实生产证据的适用性判定
  属于运维按 runbook 的人工职责。本清单是盘点事实记录，不是放行依据。
- **测试**：`services/api/tests/test_evidence_inventory.py`
  （覆盖矩阵：CLI 注册/分发/无 --yes、m11-02 与 m11-11 真实形态、分类与
  适用性矩阵（README 声明/无声明/无 marker/超限）、declared 白名单正反
  用例（毒化值全 null、安全标量透出）、大文件只哈希、元数据与独立计算
  一致、多目录汇总与 13 步交叉锁定（嵌套同名 step 文件列入 inventory 但
  不推进覆盖）、重复 sha256 组、exact allowlist
  schema（顶层/summary/roots/files/declared 七层键集合）、
  production_ready 恒 false、symlink/重复与嵌套 root/枚举错误 onerror
  fail-closed（build 层 + CLI 层零部分输出）/输出冲突（含 `..`
  折叠与 Windows 大小写）/原子写护栏（先于枚举）、敏感 marker 三面零泄漏
  （内容 marker 与文件名/父目录名 marker 两种形态；显示名脱敏白名单
  可读性保留）、零绝对路径、只读性（字节与 mtime 不变）、源码守卫（零
  env/DB/网络/subprocess、import 面恰为三个共享层 + 标准库、parser 块
  无 --yes））——全部用临时目录与本地文件，不连数据库、不发网络请求。

## 生产切换 preflight（M10-07）

`python -m app.ops.cli production-preflight --db-url <生产URL> --phase pre-migration|post-migration
[--anchor-file <path>] [--json] [--output <path>]`（`app/ops/production_preflight.py`）。

定位：生产切换 runbook 的**防呆汇总**，不是 release-check 替代品。全部检查只读——
不执行迁移、不写数据库、不写锚文件、不清理数据、不启动/停止服务；命令**没有任何
执行形态（无 --yes）**。全部数据库读取在**单一连接的显式只读事务**内完成：PG 为
**数据库层 READ ONLY + REPEATABLE READ**（SQLAlchemy execution option
`postgresql_readonly=True`——事务以 `BEGIN READ ONLY` 开始，任何写入语句在数据库
处即被拒绝，只读不依赖「本模块只发 SELECT」的语句面自律；隔离级别保证全部读取同一
事务快照——该 PG 行为已由 M10-08 门控集成测试在**隔离测试库**实证：`AIOS_PG_TEST_URL`
过白名单门控时在 run_preflight 同一快照事务内 `SHOW transaction_isolation` =
repeatable read、probe `CREATE TABLE` 被 PostgreSQL 以 read-only transaction 拒绝、
异常回滚后独立连接复核 probe 表不存在（实测同时发现并修复漏 `await`
`AsyncConnection.execution_options`、执行选项静默不生效的缺陷，见
audit-chain-anchor 节同型修复记录；锚定路径的 REPEATABLE READ 已由 M10-09
独立门控实证，见审计节并发边界；未设安全 env 时该测试跳过；**未在生产库
执行** preflight，仅验证事务语义本身）；SQLite 无等价的 READ ONLY 事务语法，走显式事务的**语句面只读**快照
（本模块只发 SELECT / inspector，不伪造数据库层能力——与 audit-chain-anchor 的
快照口径一致，方言差异如实声明）。db-connect / alembic / audit-chain /
legacy-governance 与锚定交叉核对消费**同一份快照**（`run_anchor` 可接收已加载的
snapshot——与 `--yes`、与 db_url 均互斥，verify 结论始终从 snapshot 纯重算、不接收
外部 verify_report，杜绝数据来源歧义；锚定校验不再开第二个数据库连接）。五项检查：
① 连通与当前库名（PG `current_database()`，不输出完整 URL/凭据）；② alembic
current/head 只读对账（直接查 `alembic_version` + 只读解析脚本目录，绝不
upgrade/downgrade）；③ 审计链（复用 audit-chain-verify 的 load/verify snapshot
语义）；④ 库外锚定（提供锚文件且存在时只跑 anchor verify-only 交叉校验，消费主
流程快照；绝不追加锚行）；⑤ 历史治理聚合计数（无归属非 seed 卷、generation/
variant NULL owner 草稿——只输出计数，不输出生产 ID）。

- **phase 语义（--phase 必选）**：迁移后忘带 phase 会误用 pre 的宽松语义
  （链表缺失算 pending），故强制显式选择。pre-migration：**唯一**允许的链表
  缺失形态是 `audit_chain_entries` 与 `audit_chain_state` **同时缺失且
  audit_log 存在**（0024 建 audit_log、0027 原子建两张链表——0026 -> 0027 的
  正常未迁移形态）= `pending`（pending_migration）；仅缺一张链表、audit_log
  缺失而链表存在、三表全缺（库早于 0024，不是本 runbook 的 preflight 起点——
  runbook 预期 current=0026）一律 `fail`——不对应任何迁移可达形态（schema 部分
  损坏或连错库）；current 落后 head = `pending`、未锚定 = `pending`
  （expected_pending）。post-migration：链表缺失（含部分缺失）/链 invalid/
  current != head/未知 revision 一律 `fail`；锚文件未提供或不存在 =
  `not_configured`（按 runbook 人工完成初始锚定 + WORM 归档，本工具不自动创建）。
- **状态与退出码**：`pass` / `pending` / `fail` / `not_configured`；无 fail=0、
  存在 fail=1、输入/锚文件路径/数据库连接/Alembic 脚本目录解析/报告写入失败=2
  （Alembic 解析失败捕 `alembic.util.exc.CommandError` 基类与迁移脚本
  `SyntaxError`；`--output` 写入失败不打印检查结论摘要，避免半途报告被误读为完整
  结论）。**pending / not_configured 不包装成 pass**——人类摘要明确写「生产切换
  仍需按 runbook 人工决策/执行」。报告与错误信息不含完整 URL/凭据（连接与
  Alembic 错误先抹 `://user:pass@` 再输出）、不含生产 paper/draft ID、不含
  audit before/after 正文。`--output` 复用 artifacts/temp 路径护栏，默认不落盘；
  落盘为**原子写**：同目录临时文件写满 + fsync 后 `os.replace` 到目标——磁盘满/
  IO 中途失败时既有旧报告字节原样保留、不留本次 partial 报告、无残留临时文件
  （exit 2）；目标是 symlink 时拒绝（POSIX `os.replace` 替换链接本身不跟随写穿，
  Windows 语义无保证——统一 fail-closed 拒绝；前置检查与 replace 前复核之间存在
  理论 swap 竞态窗口，与锚文件同口径如实声明）。

### 生产切换 preflight runbook

真实生产执行**必须先获得用户/运维明确审批**；生产 URL/凭据只来自安全运维环境，
不入命令文档示例、不入 git、不回显日志（`<生产URL>` 为占位符）。preflight 本身
只读可重复执行，但**不代替**下面任何人工步骤：

1. **切换前（pre-migration）**：备份完成后运行
   `production-preflight --db-url <生产URL> --phase pre-migration`。预期：audit-chain
   `pending_migration`（0027 未执行——两链表同时缺失且 audit_log 存在）、alembic
   `pending`（current=0026 -> head）、anchor `pending`、治理计数 `pending`（如仍有
   待归属卷/草稿）；任何 `fail`（alembic_version 异常/链表仅缺其一或 audit_log
   缺失/三表全缺——schema 部分损坏或库早于 0024/链表存在但 invalid/Schema 表
   缺失）先停下排查。本工具要求生产库 preflight 时点至少已在 0024 之后
   （audit_log 存在）；runbook 预期形态是 current=0026。
2. **人工执行切换**（preflight 绝不代劳）：按「审计」节 `0027_audit_chain`
   迁移 runbook——静默审计写入方后 `alembic upgrade head`，随后
   `audit-chain-verify` valid、人工 `audit-chain-anchor --yes` 建初始锚并归档
   WORM，再恢复服务。历史卷/草稿归属迁移按「生产数据治理」节逐批 `--yes`。
3. **切换后（post-migration）**：恢复服务前运行
   `production-preflight --db-url <生产URL> --phase post-migration
   --anchor-file <运维保管路径>/audit-anchor.jsonl`。放行门禁：db-connect /
   alembic / audit-chain / audit-anchor 全 `pass` 且退出码 0（anchor
   verify-only up-to-date）；anchor `not_configured` 或 `pending`（head 超前）
   说明初始锚定/归档未完成，按锚定 runbook 补齐后再复跑；任何 `fail`
   保持停机排查，不带病恢复。

## 发布门禁 release-check 汇总与 JSON 证据（M7-05 / M11-03）

- **CLI**：`python -m app.ops.cli release-check [--api-base URL] [--db-url URL]
  [--local-only] [--json] [--output PATH]`，实现文件
  `services/api/app/ops/release_check.py`。九字面门禁：本地七项命令
  （api-lint/web-lint/web-typecheck/web-build/api-test/migration/backup，
  命令与 CI 同构，900s 超时，`--db-url` 过 `app.db.test_gate` 门控后才注入
  api-test 的 `AIOS_PG_TEST_URL`）+ live 三项（e2e/voice/license，对
  `--api-base` 运行中服务执行；服务未运行如实 fail 不虚报）。
- **M11-03 JSON 证据导出**：`--json` 向 stdout 输出纯 JSON（可管道给 jq；
  `--output` 同用时「报告已写入」提示走 stderr），`--output` 原子写入 JSON
  文件，两者可同用互不替代。证据同时自声明 `gate`（release-readiness 消费）
  与 `step`（cutover-rehearsal 消费），白名单提取面
  `all_green`/`total`/`passed`/`failed_ids` 与两侧评估器一致，另有
  `not_executed_ids`/`execution_scope`（full|local-only）/`checks`（id/title/
  kind/status/detail）扩展字段，计数自洽（passed + failed + not_executed ==
  total；all_green=true 仅当全部项真实 pass）——可直接作为 readiness /
  rehearsal 的 `release-check.json` 证据文件。detail 过 `redact_secrets`
  抹 `://user:pass@` 形态凭据；字段名不含敏感键模式。
- **诚实语义（local-only 不冒充全绿）**：`--local-only` 下 e2e/voice/license
  三项如实 `not_executed`、`all_green=false`、`total` 覆盖本地 7 + live 3、
  `passed` 只统计真实 pass；`failed_ids` 只放真实 fail，未执行项进
  `not_executed_ids`。**local-only 证据只能作为本地过程证据，不能替代完整
  release-check / live 门禁；full 模式未执行不得记 pass**（cutover evidence
  手册的 release-check 步来源命令已同步该口径）。CLI 退出码与证据分工：
  退出码按已执行门禁判定（local-only 本地七项全过=exit 0，语义不倒退），
  导出的证据不因此伪装全绿——完整门禁 `all_green` 需要 full 模式 10 项
  全部真实 pass。
- **`--output` 路径与原子写**：只允许 gitignore 的 `artifacts/`、`temp/`
  目录（复用 `is_safe_artifact_path`；普通路径/越界/symlink 一律拒绝），
  拒绝时 exit 2 且**不执行任何门禁**（护栏先于执行）；落盘复用 CLI 共享的
  `_write_report_atomic`（同目录临时文件写满 + fsync + `os.replace`），
  写入失败稳定 exit 2、无 traceback、旧报告字节原样保留、无 `.tmp` 残留，
  且不再打印门禁结论摘要（防半途报告被误读为完整结论）。九项门禁的命令、
  超时、环境隔离与 live 检查逻辑零改动。

## 隔离本地 full release-check 一键编排（M11-10）

- **CLI**：`python -m app.ops.cli release-check-isolated [--workdir DIR]
  [--output PATH] [--health-timeout SECONDS] [--json]`（从 `services/api`
  目录、repo venv 执行），实现文件
  `services/api/app/ops/release_check_isolated.py`。示例：
  `python -m app.ops.cli release-check-isolated`（全自动唯一工作区）；
  `python -m app.ops.cli release-check-isolated --workdir
  ../../artifacts/m11-10/run1 --output ../../artifacts/m11-10/evidence.json
  --json`。
- **定位**：把 M11-09 的人工流程收敛为一条命令——一次性 gitignored SQLite
  （缺省 `artifacts/release-check-isolated/run-<UTC>-<pid>-<seq>/`，每次唯一）
  -> `alembic upgrade head` -> 127.0.0.1 回环临时 uvicorn -> 限时 `/health`
  就绪 -> full 模式 10 项门禁（零改动复用 `release-check`，含 voice/license/
  e2e live 三项）-> JSON 证据原子落盘（`execution_scope=full` 同一契约，可被
  readiness/rehearsal 消费）-> `finally` 关停临时 API。
- **退出码**：全绿=**0** / 门禁真实 fail=**1**（证据照常落盘、不虚报）/
  护栏或编排失败=**2**（迁移失败、临时 API 未就绪、证据写入失败等，均**不写
  证据**）。护栏先于一切副作用（exit 2 时不建目录/不迁移/不启服务）：工作区
  与证据输出必须位于 gitignore 的 artifacts/temp（复用 `is_safe_artifact_path`；
  已过护栏工作区内的嵌套证据路径同样放行），工作区 symlink 一律拒绝；一次性
  SQLite 已存在即拒绝（保护既有证据绝不改写）。
- **临时 API 保证关停**：无论成功、门禁失败还是编排异常，`finally` 路径都会
  terminate -> 限时等待 -> kill 兜底并复核进程已回收；关停失败即使门禁全绿也
  如实降级 exit 2 并提示人工核查，绝不留常驻子进程。单进程无 reload/workers
  即完整进程树；uvicorn 日志留档工作区 `uvicorn.log`。
- **环境隔离**：子进程环境显式构造——剥离继承的 `AUTH_SECRET`/
  `AIOS_PG_TEST_URL`/`DATABASE_URL`，显式注入 `APP_ENV=development`、
  `VOICE_MODE=local`、`HOST_BIND_IP=127.0.0.1`、
  `DATABASE_URL=<一次性 SQLite>`；不连接任何生产 DB / PG 面。所有对外消息过
  `redact_secrets`。
- **产物与边界**：一次性 SQLite、uvicorn 日志与 JSON 证据全部留在 gitignored
  `artifacts/` 本地，不入 git。**`all_green=true` 只表示「隔离本地运行面 10 项
  门禁全部真实通过」（development 配置、auth-off、本地语音），不是 production
  readiness、不授权生产发布；`production_ready=false` 保持不变**。本工具不部署、
  不打 tag、不发 Release、不推镜像。

## 备份恢复演练证据导出（M11-04）

- **CLI**：`python -m app.ops.cli backup-restore-evidence --backup-dir <备份目录>
  --restore-db-url <隔离PG URL> --output <artifacts路径> [--json]`，实现文件
  `services/api/app/ops/backup_restore_evidence.py`。
- **定位**：release-readiness 的 `backup-restore` 门（M10-11）与
  cutover-rehearsal 的 `backup-restore` 步（M10-15）需要可复现、脱敏、原子
  落盘的演练证据（`gate`/`step`/`schema_version`/`manifest_sha256`/
  `created_at`/`restore_drill.verified`/`restore_drill.inserted_rows`）。本
  工具把「隔离库迁移 + 完整恢复 + 只读导出比对」编排成一条命令，复用 M6-06
  `run_restore`/manifest 校验语义（零重构），产出同契约 JSON，可直接作为
  readiness/rehearsal 的 `backup-restore.json` 证据文件。
- **前置条件（操作者须知）**：备份已生成（`cli backup`，备份目录内
  `manifest.json`/`database.json` 齐备）；restore 目标是**已存在的隔离库**
  ——工具会对它执行 `alembic upgrade head` 并**覆盖恢复**（全量替换），因此
  它必须是专用演练库（`ai_learning_os_drill` 或 `ai_learning_os_test` 前缀
  变体），不是任何业务/源库。
- **护栏（全部先于执行；违例 exit 2、不迁移/不恢复/不写输出）**：备份目录
  必须位于 gitignore 的 artifacts/temp（复用 `is_safe_artifact_path`）、为真
  目录（symlink 拒绝）且结构合法（schema_version=aios-backup-v1、tables 为
  非负整数计数表）；恢复目标 URL 必须过 `app.db.test_gate`（`evaluate_pg_test_url`）
  隔离白名单——主库 `ai_learning_os`、维护库 `postgres`、缺库名、非 PG 在
  建立任何连接前拒绝；输出必须位于 artifacts/temp 且**不得位于备份目录内
  或等于备份目录**（备份目录的精确文件集是 manifest 完整性校验面，添加文件
  会破坏未来对该备份的校验）；输出 symlink 拒绝（共享原子写 `_write_report_
  atomic`：同目录临时文件 + fsync + `os.replace`，失败保留旧文件、无 `.tmp`
  残留）。
- **诚实语义**：`verified=true` 仅当（a）恢复后隔离库只读全量导出
  （`dump_database`）与备份 `database.json` 逻辑数据全等（表集合一致 + 逐表
  行多重集合全等；行序无关——关系表行序不属于逻辑数据）且（b）
  `inserted_rows` == manifest `tables` 计数总和。不一致 => `verified=false`
  证据照常落盘、exit 1（如实记录失败，绝不伪装 pass）；迁移/恢复/导出失败 =>
  错误抹 `://user:pass@` 凭据后输出、exit 2、不产证据（旧 evidence 字节
  原样保留）。`--json` 时 stdout 纯 JSON、「报告已写入」提示走 stderr。
- **安全边界**：只写隔离目标库（alembic 子进程显式
  `DATABASE_URL=<隔离URL>`）；不读取/不修改备份源库（备份目录之外零数据库
  读访问）、不写生产、备份目录字节保持不变；证据零敏感（无 DB URL/凭据/
  备份内容/表名/业务 ID；`manifest.tables` 只取计数总和），
  `manifest_sha256` 是备份 `manifest.json` 文件字节 SHA-256，`created_at` 是
  演练执行时刻。
- **真实 PG 测试覆盖（AIOS_PG_TEST_URL 门控）**：`tests/test_backup_restore_
  evidence.py` 第 9 节复用 `test_backup_drill` 同一安全门控基础
  （`pg_test_gate_from_env` 白名单），但额外要求源库（业务造数 + `run_backup`
  的对象）固定为隔离库 `ai_learning_os_test`；满足时自动在独立
  `ai_learning_os_drill` 库（DROP+CREATE、用后即删、不碰源库数据）真实执行
  alembic 迁移 + 恢复 + 只读导出全等对账 + 证据导出并断言零敏感。env 未
  设置，或指向 `ai_learning_os_drill` 等其他白名单隔离库名（含前缀变体）时，
  本测试在建立任何连接前 skip——避免把正在使用的源库当 drill 目标
  DROP+CREATE；CI 的 api job（postgres service，`AIOS_PG_TEST_URL` 指向
  `ai_learning_os_test`）自动跑通该路径。注意：这验证的是演练工具链在
  隔离库上的端到端正确性，**不等于生产备份已演练**。

## 发布准备 readiness manifest（M10-11）

- **CLI**：`python -m app.ops.cli release-readiness --evidence-dir <path>
  [--json] [--output <artifacts路径>]`，实现文件
  `services/api/app/ops/release_readiness.py`。
- **定位**：只读汇总调用方显式提供的 10 个 gate 证据 JSON/JSONL，计算
  SHA-256；不连 DB/网络/API，不读环境变量，不执行迁移、锚定、清理、WORM、
  发布、回滚，无 `--yes` 执行形态。
- **gate 矩阵**：required——`ci-main`、`release-check`、`production-preflight`、
  `backup-restore`、`audit-chain-anchor`、`legacy-papers`、`draft-ownership`、
  `provider-smoke`、`release-approval`；optional——`turn-tls`。
  `provider-smoke` 门的证据来源口径（M11-16）：来源命令是
  `provider-smoke-aggregate --search <PATH> --cloud-voice <PATH> --llm <PATH>
  --output <artifacts>/provider-smoke.json`——三份输入必须是
  `provider-smoke-export` 导出的单步证据（形态校验 fail-closed，不接受
  手工拼装），`providers.voice/search/llm` 每项仅 `executed`/`result`
  （见「provider 冒烟证据导出与聚合 provider-smoke-evidence」节）。
- **状态语义**：`missing` / `malformed` / `tampered` / `blocked` / `pending` /
  `pass`，不得把 pending 包装成 pass。`release_ready=true` 仅表示全部 required
  gates `pass` 且 `release-approval` 的 gate id + evidence sha256 集合与当前证据
  完整匹配；`approved_by` 只做记录，不做身份认证。
- **turn-tls 语义**：本机/LAN 发布形态可 `pending`/缺省，不阻断
  `release_ready`；公网语音发布必须另行要求 `turn-tls=pass`。manifest 固定输出
  `not_pass_optional` 与 `optional_scope_note`，`release_ready` 不得解释为
  公网语音就绪。
- **输出脱敏/路径边界**：不回显证据正文、完整 DB URL、密码/token/key、生产
  paper/draft/用户 ID；敏感键或内嵌凭据证据判 `malformed`。`evidence_dir` 是
  唯一 caller-supplied 路径字段，可能含本机路径，供人工复核定位；`--output`
  仅允许 artifacts/temp 护栏并原子写入，symlink fail-closed。

## 生产切换演练编排器（M10-15）

- **CLI**：`python -m app.ops.cli cutover-rehearsal --evidence-dir <path>
  [--json] [--output <artifacts路径>]`，实现文件
  `services/api/app/ops/cutover_rehearsal.py`。
- **定位与分工**：与 release-readiness（M10-11 发布审批门矩阵）互补——本工具按
  **生产切换时间线**组织 13 个 required steps（pre-window 7 步 → pre-migration
  2 步 → post-migration 3 步 → cutover 1 步），演练「一次完整切换需要什么证据」
  的编排视角：切换窗口前 CI/门禁/三类 provider 冒烟/历史治理计数归零；窗口内
  pre 预检 + 备份恢复演练；迁移后 post 预检 + 审计链校验 + 锚定；最后人工审批。
  **隔离 rehearsal**：不代表生产验收，也不授权生产写入/发布——`rehearsal_ready=true`
  仅说明演练时间线 13 步证据齐备且审批绑定完整，生产放行仍须按各 runbook 人工
  执行。
- **step 矩阵**（全 required；对 release-readiness 证据面的拆分——preflight 拆
  pre/post、audit-chain 拆 verify/anchor、provider-smoke 拆 search/cloud-voice/
  llm，拆分理由：同一文件混合多类结论会互相掩盖，时间线视角要求每类证据单独
  可审计、单独给出下一步动作）：`ci-main`、`release-check`、`search-smoke`、
  `cloud-voice-smoke`、`llm-smoke`、`legacy-papers`、`draft-ownership`、
  `preflight-pre-migration`、`backup-restore`、`preflight-post-migration`、
  `audit-chain-verify`、`audit-chain-anchor`、`cutover-approval`。
  `release-check` 步的证据来源口径（M11-03 修正）：来源命令是
  `release-check --output <artifacts路径>`（`--api-base` 提供运行中服务，live
  三项真实执行）；`--local-only` 导出（`execution_scope=local-only`、live 项
  `not_executed`、`all_green=false`）**只能作为本地过程证据，不能替代完整
  release-check / live 门禁，full 模式未执行不得记 pass**。
  `legacy-papers` / `draft-ownership` 步的证据来源口径（M11-12）：来源命令
  是 `governance-evidence --report <治理报告> --batch <成功批次>
  [--batch ...] --output <artifacts>/legacy-papers.json|draft-ownership.json`
  ——`pending_count` 由报告明细与成功批次确定性推导（见「治理证据推导
  governance-evidence」节），不接受人工转抄计数；成功批次自 M11-13 起由
  `legacy-paper-migrate --yes --output` / `draft-owner-migrate --yes
  --output` 原生落盘（护栏见该节）。
  三 provider 冒烟步（`search-smoke` / `cloud-voice-smoke` / `llm-smoke`）
  的证据来源口径（M11-16）：来源命令是 `provider-smoke-export
  <search|cloud-voice|llm> --output <artifacts>/search-smoke.json|
  cloud-voice-smoke.json|llm-smoke.json`——结论由真实冒烟脚本退出码机器
  导出（见「provider 冒烟证据导出与聚合 provider-smoke-evidence」节），
  不接受人工抄录拼装。
- **四态语义**：`pass` / `pending`（待人工决策或执行：治理计数 >0、锚定落后、
  WORM 未归档、恢复演练未 verified、post 预检有 pending）/ `blocked`（fail、
  malformed——结构不符/自声明 step 错位/计数自相矛盾/敏感键、tampered——审批
  哈希失配/锚文件副本校验失败，统一映射，不给「待补」的宽松读法）/
  `not_executed`（missing、冒烟 not run、缺审批或审批覆盖缺口）。分步要点：
  preflight-pre 无 fail 即 pass（迁移前 pending/not_configured 属预期不阻断，
  放行仍需 post 证据）；preflight-post 要求放行形态；audit-chain-verify 与
  anchor 独立评估（链 invalid 先 blocked，锚定另行校验）；三类 provider 冒烟
  独立证据互不掩盖。
- **顶层判定**：优先级 blocked > pending > not_executed > ready；manifest 输出
  `blockers`（全部 blocked 步及原因）与 `next_actions`（全部非 pass 步的逐步
  动作建议）。
- **审批绑定**：cutover-approval.json 必须绑定 step id + 主 step 证据
  sha256 集合 + `supporting_evidence`（文件名 -> sha256 mapping；当前
  supporting 文件仅 `audit-anchor.jsonl` 锚副本）+ 时间 + 说明。缺审批、
  未覆盖其余 12 步、或未覆盖当前实际 supporting 文件 => `not_executed`
  （审批动作尚未完整发生，`coverage_gaps` / `supporting_coverage_gaps`
  透出）；主 step 或 supporting 哈希与当前证据失配、或引用当前不存在的
  supporting 文件 => `blocked`（证据在审批后被改动/移除，`hash_mismatches` /
  `supporting_hash_mismatches` 透出）；未知 supporting 文件名 / 非 64 位
  小写 hex / 缺 `supporting_evidence` 字段 => malformed => blocked；目录无
  supporting 文件时该 mapping 必须为空 mapping。审批记录携带任一 DRAFT
  底稿保留元数据字段（`draft`/`manual_fields_required`/`confirmation_required`/
  `step_evidence_missing`/`load_problems`/`approval_file_present`/
  `generated_at`/`notice`/`tool`/`evidence_dir`，即
  `APPROVAL_DRAFT_RESERVED_FIELDS`）=> malformed => blocked——即使补齐全部
  人工字段、哈希精确匹配也不放行（M10-16 返工：DRAFT 不可审批边界）。
  supporting 绑定的意义：
  锚文件副本不属于任何主 step JSON，单靠主 step 哈希发现不了「审批后把
  副本换成另一份仍自洽、锚点数相同的副本」——supporting 绑定补上这个
  完整性缺口。`approved_by` 只做记录，不做身份认证。
- **输出脱敏/路径边界**：与 release-readiness 同口径——不连 DB/网络/API，不读
  环境变量，不执行迁移/锚定/清理/WORM/部署/启停/发布/回滚，无 `--yes` 执行
  形态；不回显证据正文与生产业务 ID；敏感键或内嵌凭据证据判 blocked；
  `--output` 仅允许 artifacts/temp 护栏并原子写入，symlink fail-closed。
- **共享证据层**：路径护栏/JSON loader/敏感键与内嵌凭据扫描/scrub/schema
  校验原语提取为 `services/api/app/ops/evidence_kit.py`（M10-15 从
  release_readiness 原样提取，零行为变更，既有测试全绿），两个只读 manifest
  工具的装载与校验底层保持同一实现，安全语义不漂移。

## 生产切换证据包脚手架（M10-16）

- **CLI**：`python -m app.ops.cli cutover-evidence-pack scaffold
  --target-dir <artifacts/temp内新目录>`（生成模板+手册）与
  `python -m app.ops.cli cutover-evidence-pack approval-draft
  --evidence-dir <dir> [--output <artifacts路径>]`（审批 DRAFT 哈希底稿），
  实现文件 `services/api/app/ops/cutover_evidence_pack.py`。
- **定位**：M10-15 cutover-rehearsal 回答「证据齐不齐、绑没绑」，本工具回答
  「每份证据从哪来、怎么脱敏、怎么算通过、审批哈希怎么算」——在运维显式
  指定的 artifacts/temp 新目录内生成 13 步证据模板（`.template.json` 命名，
  预填值全部 REPLACE-ME 形态）+ 锚文件副本模板（`audit-anchor.jsonl.template`）
  + 逐项操作手册 README（证据文件名/来源命令或 runbook/脱敏要求/通过失败
  语义/人工授权/隔离 fixture 形态六要素，数据登记在 `MANUAL_ENTRIES` /
  `FIXTURE_FORMS`，README 由数据渲染生成，测试守卫与 `STEPS` 一一对应无漏项）。
  **真实生产操作必须人工逐项授权**：手册内出现的生产命令（production-preflight /
  backup / audit-chain-* / *-migrate 等）全部由运维显式授权后手动执行，本工具
  与 rehearsal 均不代为执行。
- **模板防误用（三层）**：`.template.json` 命名——rehearsal 只认精确证据
  文件名（`KNOWN_EVIDENCE_FILES`），模板只会进 `unrecognized_files`，不可能被
  当作证据评估；预填值全部 REPLACE-ME 字符串（类型/枚举故意不符），即使手工
  改名直用也只会在 `req_int/req_bool/req_choice` 处 malformed => blocked；
  每个模板带 DRAFT/REPLACE-ME 标注与 `_template_notice` 说明字段。测试锁定
  「13 个模板改名直用全部 blocked、绝不 ready」。
- **scaffold 路径护栏（fail-closed，exit 2）**：只写入用户显式指定且通过
  `is_safe_artifact_path`（直接父目录名 artifacts/temp，或 git check-ignore
  判定忽略；仓库外路径一律拒绝）的新目录；路径任何已存在组件是 symlink 即
  拒绝、`..` 越界归一后落在护栏外即拒绝、目标是 artifacts/temp 目录本身即
  拒绝；已存在且非空则必须与本工具脚手架**逐字节一致**（幂等重放零改写，
  产物无时间戳保证确定性）否则拒绝覆盖——多余文件、被填写的模板、子目录
  占用模板名一律拒绝且既有字节不变。
- **approval-draft**：只读计算当前证据目录**其余 12 步**主证据与实际存在
  supporting 文件（锚副本）的 SHA-256 底稿（cutover-approval.json 自身不绑定
  自己，已存在时 `approval_file_present` 如实标注）；输出固定 `draft: true`、
  `manual_fields_required` 人工必填字段清单（REPLACE-ME 提示）与「直接改名
  只会 blocked」声明；装载层问题（非法 JSON/敏感键/内嵌凭据）如实列
  `load_problems`（只报字段路径，值不回显）。底稿缺 `step`/必填审批字段，
  误用即 malformed => blocked。
- **DRAFT 不可审批边界（fail-closed，返工补上）**：底稿输出的全部元数据
  字段（`draft` / `tool` / `generated_at` / `evidence_dir` / `notice` /
  `approval_file_present` / `step_evidence_missing` / `load_problems` /
  `manual_fields_required` / `confirmation_required`）登记在
  `cutover_rehearsal.APPROVAL_DRAFT_RESERVED_FIELDS`——cutover-rehearsal 的
  审批评估器对携带**任一**上述字段的审批记录一律 malformed => blocked：
  即使在底稿上补齐 `step` 与全部人工审批字段、step/supporting 哈希精确
  匹配，只要任一底稿元数据字段还在就不得通过（此前评估器忽略未知字段，
  「补齐后改名保留 draft 元数据」存在 pass 读法，削弱 DRAFT 边界）。合法
  人工审批不得携带这些字段，审批人应从底稿哈希出发**从零组装**
  cutover-approval.json（只带 step/schema_version/approved_at/note/window/
  rollback_plan/observation/approved_by/step_evidence/supporting_evidence），
  不要在底稿文件上补字段改名。测试锁定「底稿字节改名直用 blocked 不
  ready」「底稿补齐全部合法字段但保留 draft 元数据 => blocked 且整体 not
  ready / 移除全部 draft 专用元数据后 => pass-ready」「十字段逐一混入合法
  审批均 blocked」「拒绝名单与底稿输出字段集合同步（底稿新增元数据漏登记
  即红）」与「证据变更后旧底稿哈希失配 => rehearsal blocked、新底稿重新
  绑定恢复」。approval-draft `--output` 复用 artifacts/temp 护栏并原子
  落盘，写入失败 exit 2。
- **审批 SHA-256 计算**：`step_evidence` = 其余 12 步每份证据**文件字节**的
  SHA-256（64 位小写 hex）；`supporting_evidence` = 当前实际存在 supporting
  文件的 sha256 mapping（目录无锚副本时必须为空对象）。辅助命令 approval-draft
  或手工 `python -c "import hashlib,sys;print(hashlib.sha256(
  open(sys.argv[1],'rb').read()).hexdigest())" <证据文件>`。
- **隔离 fixture 全链路**（README 手册同步记载）：`scaffold --target-dir
  temp/cutover-pack-dryrun` → 复制到 temp/ 演练目录，按各步「隔离 fixture
  形态」（`FIXTURE_FORMS`，如 ci-main 用 `{"run_id":1,"merge_commit":
  "aa11bb22cc33","conclusion":"success"}`；anchor 步最简形态不提供锚副本、
  supporting_evidence 用空对象）填写 12 步并改名 → approval-draft 取哈希底稿
  → 审批人模拟填写人工字段从零组装 cutover-approval.json（只带合法审批
  字段，不带入任何底稿元数据字段）→ `cutover-rehearsal
  --evidence-dir` 得 `rehearsal_ready=true`。**声明：fixture 值不代表任何真实
  执行结果，ready 不代表生产验收、不授权生产写入/发布**——生产证据必须来自
  人工逐项授权后的真实执行与导出（测试用同一份 FIXTURE_FORMS 真实复现该链路）。
- **安全边界**：不连 DB/网络/API、不读环境变量（AST 级测试守卫零 `os.environ`/
  零引擎/零 HTTP 引用）、不执行任何生产命令、无 `--yes` 执行形态；模板与手册
  零敏感键/零凭据字样（find_sensitive_key 与凭据 URL 形态扫描测试锁定）；
  scaffold/approval-draft 退出码：成功（含幂等重放）=0 / 目标目录或路径与 IO
  问题=2。

## 本地 Release Candidate 包（M10-17）

- **入口与分工**：编排入口是 `bash infra/build_release_candidate.sh --tag vX.Y.Z
  --output-dir artifacts/rc-vX.Y.Z [--web-build-arg http://127.0.0.1:8000]`
  （Docker/compose/git 编排）；可独立测试的纯文件逻辑（tag/VERSION 一致性、输出
  目录护栏、manifest/SHA256SUMS 原子落盘、独立 verify）在
  `services/api/app/ops/release_candidate.py`，经
  `python -m app.ops.cli release-candidate manifest|verify` 子命令暴露。
- **构建序列（fail-closed）**：① tag 严格 `vX.Y.Z` 且与 VERSION 文件**逐字**
  一致；② git worktree 必须干净并记录完整 commit SHA（40/64 hex）；③ 输出目录
  必须在 gitignore 的 artifacts/temp 内的新目录（symlink 组件、`..` 越界、非空
  已存在目录、artifacts/temp 目录本身一律拒绝）；④ 同一源码树构建
  `aios/api:<tag>` 与 `aios/web:<tag>`（web 带
  `NEXT_PUBLIC_API_BASE_URL` build arg）；⑤ `AIOS_IMAGE_TAG=<tag>` +
  `up -d --no-build` 起 compose local profile（隔离项目名 `aios-rc-<tag>`）跑
  冒烟，`trap cleanup EXIT` 保证结束/失败都 `down --remove-orphans`——down 永远
  不带 `-v`，绝不删除任何卷；⑥ `docker save` 两个独立归档写入选定目录；
  ⑦ manifest 助手原子落盘后立即独立 verify，失败即整体失败并清理未完成包目录。
- **manifest 契约**：必填顶层字段 `schema_version`/`package_kind`/`version`/
  `tag`/`git_commit`/`build_context`/`web_build_arg`/`compose`（文件 +
  sha256）/`images`（api+web 各含 repository/tag/image_id/
  `local_only: true`）/`archives`（两归档 component+filename+sha256+
  size_bytes）/`checksums_file`/`smoke`（脚本 + passed）/`scope`=
  `local-release-candidate`/`scope_note`/`boundary_note`（固定边界声明并被
  verify 校验）。SHA256SUMS 用 sha256sum 兼容格式按文件名排序，**恰覆盖**两个
  归档 + manifest 自身；原子写（同目录临时文件 + `os.replace`）失败清理全部
  最终文件与临时文件——绝不留看似有效的半成品 manifest。
- **独立 verify（不加载 Docker 镜像）**：`cd services/api && python -m
  app.ops.cli release-candidate verify --package-dir <dir> [--version-file
  VERSION]`——schema/必填字段与类型、version-tag（-VERSION 提供时三方）一致性、
  校验和覆盖面（缺/多/重复条目/非法行/包内多余文件）、逐档 SHA-256 重算 +
  size 复核；「自洽篡改」（改 manifest 后重算 SHA256SUMS 绕过字节哈希门）由
  schema/一致性门拦下。退出码：0=通过 / 1=校验失败 / 2=输入或 IO 问题。
- **GitHub Actions 手动 workflow**：`.github/workflows/release-candidate.yml`
  仅 `workflow_dispatch` 触发（静态契约测试锁定：push/pull_request/schedule
  永不触发），`permissions: contents: read` 最小权限，GitHub 托管 Linux runner
  的本地 Docker 构建与冒烟，产物以 `actions/upload-artifact@v4` 上传（保留
  14 天）——**不是** GitHub Release。checkout/setup-node/setup-python 等
  runtime action 统一钉 v7 主版本（node24 runtime，M11-14——v4/v5 为 node20
  旧 runtime，GitHub 托管 runner 已打 Node.js 20 deprecation 警告；
  `services/api/tests/test_workflow_actions_runtime.py` 静态锁定不回退，
  upload-artifact 不在此列、保持 v4）。
- **验证证据要求**：验收口径为聚焦 `pytest services/api/tests/
  test_release_candidate.py`（含 build 脚本 `bash -n` 语法、源码级零发布动词
  守卫（无 git 标签/push/login/gh release/kubectl/ssh 等）、stub 行为面
  （脏 worktree/tag 失配/护栏外/冒烟失败全部早退且零残留）、workflow 契约）
  + 清除 `AIOS_PG_TEST_URL`/`DATABASE_URL` 后的全量 `pytest services/api -q` +
  `ruff check services/api` + `bash -n infra/build_release_candidate.sh` +
  `git diff --check` 全绿；真实 Docker 构建冒烟仅在 `AIOS_RELEASE_SMOKE=1`
  时执行（默认 skip）。手动 workflow 触发证据（run URL + conclusion）在触发后
  回填台账。
- **安全边界**：本地 RC **不是 production readiness 声明，不授权部署**；不打
  git 标签、不发 GitHub Release、不推镜像仓库、不碰生产 DB/服务/主机；
  manifest 助手不读环境变量、不连 DB/网络、不执行任何命令（AST 级测试守卫零
  `os.environ`/零 subprocess/零网络 DB 引用）；产物与 CLI 输出无密码/token/key
  字样、无凭据 URL 形态（测试锁定）。

## LLM 接入（M10-01）

- OpenAI 兼容 gateway（`app/llm/gateway.py`）：`LLM_ENDPOINT/LLM_API_KEY/LLM_MODEL`
  三槽位齐备才装配；key 只放本机 .env 或部署 secret，不入库不入码不入日志。
- 接入点：主观题 rubric LLM judge（`app/llm/rubric_judge.py`，实现 M2-10 预留的
  `RubricJudge` 协议）。`RUBRIC_JUDGE=llm` 启用；默认 `keyword` 保持确定性判分。
- fail-closed：LLM 不可用 / 响应非法 / 模型编造 evidence_id → judge 返回 None →
  essay 进复核三态。模型缺席绝不产生分数；`judge_model`/`prompt_hash` 服务端覆写留痕。
- 真实端点冒烟（需要部署 key，无 key 明确失败不虚报）：`bash infra/smoke_llm.sh`。
- 单测全部 fake transport（协议格式 / 失败语义 / 装配两态），不发起网络调用。
- 已知取舍（ADR 65）：判分管线是同步域函数，gateway 用同步 httpx——单用户本地版
  可接受；判分并发化是后续演进。

## 搜索 cloud-web provider 与冒烟（M10-12）

- CloudWebProvider（`app/search/providers.py`）为 SearXNG-compatible JSON API 真实
  实现：`GET {SEARCH_CLOUD_ENDPOINT}/search?q=<查询词>&format=json`（endpoint 是
  base URL）；`SEARCH_CLOUD_API_KEY` 可选——设置时经 Authorization 鉴权头出示，
  无鉴权 SearXNG 留空即可；key 只放部署 secret 或本机 .env，不入库不入码不入日志。
- 启用判定（`cloud_web_availability`，三门全部放行才 enabled）：
  1. `PRIVACY_SEND_CONTEXT_TO_CLOUD=false`（隐私总闸）→ 禁用，云检索不出站；
  2. `SEARCH_MODE != cloud`（local/hybrid——检索暂无混合形态）→ 禁用，
     **即使 endpoint/key 配齐也不出站**（本地路由）；
  3. `SEARCH_CLOUD_ENDPOINT` 缺失或非法（非 http/https base URL）→ 禁用。
  key 不参与启用判定（可选）；三种禁用形态都在 `/api/v1/search/providers` 视图与
  执行记录 skipped 里给出**不含敏感值**的原因（不虚报可用）。
- 结果归一化：`results[].title/url/content` → title / url / snippet（snippet 取
  content，200 字符截断）、provider/source=cloud-web、authority 默认 community
  （上游显式给 official/oer/platform/community 白名单标注时透传，未知标注兜底
  community——不猜测）；缺失/非法 URL（非 http/https、无 host）的结果过滤；
  返回条数不超过 limit（客户端截断）。local-corpus 行为不变。
- fail-closed：HTTP 非 2xx / 响应非合法 JSON / results 非列表 / 网络错误 / 超时
  一律 `ProviderUnavailable`（进 skipped）；错误信息为固定脱敏文案——不含 endpoint、
  API key、鉴权头或其它敏感值。
- 单测全部 httpx.MockTransport 伪造端点（协议格式 / 失败语义 / 可用性矩阵 /
  Authorization 仅 key 存在时添加 / 零敏感泄漏），不发起网络调用。
- 真实端点冒烟（需要真实 SearXNG-compatible 端点，**按需 key**）：`bash
  infra/smoke_search.sh`——`SEARCH_CLOUD_ENDPOINT` 未设置时明确 FAIL 不虚报；
  `SEARCH_SMOKE_QUERY` 覆盖默认查询词（"AI Learning OS GitHub"）；至少 1 条合法
  结果才 PASS；输出只含 provider/结果数等脱敏摘要。release readiness 的
  `provider-smoke` gate 以真实端点冒烟执行记录为准（**检索冒烟不需要 key**、
  LLM 冒烟需要 key——见上节 smoke_llm.sh；pass 语义不放宽——仍是 required
  gate）；门证据自 M11-16 起由 `provider-smoke-export` /
  `provider-smoke-aggregate` 从冒烟脚本真实退出码机器导出（见「provider
  冒烟证据导出与聚合 provider-smoke-evidence」节），不接受人工抄录拼装。
- 冒烟脚本契约由 `services/api/tests/test_smoke_search_script.py` 锁定（不触网：
  env 缺失 FAIL、探针失败传播、文本契约与零敏感回显）。

## 云语音 provider 失败语义与真实端点冒烟（M10-13）

- CloudOpenAiAsrProvider / CloudOpenAiTtsProvider（`app/voice/providers.py`）失败
  语义与 CloudWebProvider（M10-12）同口径 fail-closed：网络/超时 / HTTP 非 2xx /
  非法 JSON 一律 `ProviderUnavailable` **固定脱敏文案**——不嵌 httpx 异常文本
  （其含请求 URL/endpoint），不回显 endpoint、key、鉴权头或响应正文（HTTP 失败
  只透状态码）。
- 载荷校验：ASR 响应顶层必须是 JSON 对象、`text` 字段必须是字符串、`confidence`
  非数值即拒绝（不猜测转写结果）；TTS 请求固定 `response_format=wav`——响应音频
  为空或不带 RIFF/WAV 头一律拒绝（不把非 WAV 字节冒充 wav 结果）。成功路径的
  provider / latency / confidence 字段保持不变。
- 真实端点冒烟（需要真实 OpenAI 兼容端点 + 部署 key + 一段真实短语音 WAV，
  **仅运维显式执行**）：`bash infra/smoke_voice_cloud.sh`——必填
  `ASR_CLOUD_ENDPOINT` / `ASR_CLOUD_API_KEY` / `ASR_CLOUD_MODEL` /
  `TTS_CLOUD_ENDPOINT` / `TTS_CLOUD_API_KEY` / `TTS_CLOUD_MODEL` /
  `ASR_SMOKE_AUDIO`（真实短语音 WAV 路径），任一缺失明确 FAIL 不虚报；可选覆盖
  仅 `VOICE_SMOKE_TEXT`（TTS 合成文本，默认 "AI Learning OS cloud voice smoke"）
  与 `ASR_SMOKE_EXPECTED_TEXT`（设置时要求其 casefold 文本出现在 casefold 转写
  中）；PASS 门槛：ASR 转写非空（设置了期望文本则 casefold 包含）且 TTS 响应
  非空并带 RIFF/WAV 头；输出只含 provider / 转写长度 / TTS 字节数等脱敏摘要。
- **边界**：真实云端点/密钥**本仓库不执行**——冒烟是显式运维动作（key 只经部署
  secret/env 注入，不入库不入码）；单测全部 httpx.MockTransport 伪造端点（失败
  脱敏 / 非法载荷 / 成功字段保持），不发起网络调用。
- 冒烟脚本契约由 `services/api/tests/test_smoke_voice_cloud_script.py` 锁定
  （不触网、不读真实 secret：必填 env 缺失 FAIL、音频文件缺失 FAIL、探针失败
  传播、文本契约与零敏感回显）。

## 本地真实语音引擎（M14-01 第一切片）

- **架构**：主 API 不嵌模型 SDK，只经 OpenAI 兼容 HTTP 调本机服务（全部只绑
  `127.0.0.1`）——ASR 用 `local-funasr`（funasr-server，SenseVoiceSmall，
  CPU，`/v1/audio/transcriptions`，无鉴权）；TTS 用 `local-cosyvoice`
  （CosyVoice 官方仓库 + `tools/voice/cosyvoice_openai_bridge.py`，
  `/v1/audio/speech` 恒返回 WAV）。首发不做三引擎同卡常驻：ASR 走 CPU、
  TTS 用 GPU（cu128），LLM 另行安排。
- **配置**：`ASR_LOCAL_ENDPOINT` / `ASR_LOCAL_MODEL` / `ASR_LOCAL_API_KEY` /
  `TTS_LOCAL_ENDPOINT` / `TTS_LOCAL_MODEL` / `TTS_LOCAL_API_KEY`（key 可选，
  本地服务默认无鉴权；真实 key 只放本机 .env）。endpoint 配好 → local 模式
  选中真实引擎；未配置 → 降级 `fake`/`tone` 并在 `GET /api/v1/voice/providers`
  与 `X-Voice-Provider` / `X-Voice-Fallback` 响应头透出 fallback（transcribe
  与 synthesize 同口径），不虚报已接真实引擎。hybrid 语义：ASR 本地（同上），
  TTS 仍按云端语义。未知 provider 名 422；引擎失败 ProviderUnavailable → 502
  固定脱敏文案（与云端 M10-13 同口径，本地/云端文案明确区分，不互相冒充）。
- **部署与冒烟**：可复现脚本在 `tools/voice/`——`bootstrap_funasr_wsl.sh`
  （Python 3.11 venv + CPU torch + funasr 1.4.15，127.0.0.1:8010）、
  `bootstrap_cosyvoice_wsl.sh`（Python 3.10 venv + 官方仓库固定 commit
  `074ca6d` + cu128 torch + Fun-CosyVoice3-0.5B-2512，bridge 127.0.0.1:8011）、
  `smoke_local_voice.py`（真实 HTTP 探测 ASR/TTS，输出 latency/bytes/RIFF/
  文本与 PASS/FAIL）。模型与 venv 全部落 gitignored `artifacts/voice/`；
  Windows 从 PowerShell 调 WSL 的命令与引号规则见 `tools/voice/README.md`。
- **测试**：`services/api/tests/test_voice_local_providers.py`（路由选择/成功/
  未配置降级/HTTP 失败脱敏/非法 JSON/空与非 WAV/provider header/未知 provider
  422，全部 MockTransport 或注入替身，不触网）与
  `services/api/tests/test_voice_local_scripts.py`（脚本语法与文本契约 +
  bridge `/health`、404/400/503/401 与 WAV 序列化行为）。
- **边界**：真实模型**未在本切片部署**（脚本未执行、模型未下载）——实际部署
  验证是另立的验收任务，完成前 `production_ready=false`；流式 ASR/TTS 未实现
  未宣称；compose 容器内 API 访问不到宿主 WSL loopback，本地引擎部署形态是
  宿主直跑 API（compose 接入属后续工作）；bridge 单 worker 串行推理，并发
  容量未测。

## 运行观测快照（M10-14）

- **用途**：runbook（`docs/delivery/12_DEPLOYMENT_OPERATIONS_RUNBOOK.md` 第 7 节
  观测与告警）的最小可维护落地——运维用一个只读端点回答「解析队列堵不堵、
  治理草稿积压多少、语音会话在什么状态、用户角色分布、搜索/审计量级」。
  **不是 Prometheus / Alertmanager / Grafana 的替代品**：没有时序、没有抓取
  协议、没有告警规则；只是「运维此刻打开一眼」的聚合快照。
- **调用方式**：`GET /api/v1/system/ops-snapshot`。门禁与治理端点同口径——
  auth on 时未认证 401、learner 403、admin 200（`require_admin`）；auth off
  本地单用户放行。仅持久 DB 模式可用：MemoryRepository / 无 sessionmaker 返回
  503（`Ops snapshot requires a database`）；数据库异常同样 503，detail 固定
  脱敏（`Ops snapshot temporarily unavailable`）——不透出异常文本、连接串、
  host 或 db name。
- **实现**（`services/api/app/ops/snapshot.py` + `app/api/routes/system.py` 挂载）：
  单个会话内只发 SELECT / COUNT / GROUP BY（零写入、零状态变更；SQLite 与
  PostgreSQL 双方言可用）；Pydantic response model `OpsSnapshotOut` 锁定字段
  命名与类型。`worker_running` 来自 `ParseWorker.is_running()`——SQLite 测试
  替身按既有口径不启动消费循环，快照如实返回 false。
- **字段语义**（全部为聚合值/状态，无任何业务正文）：
  - `generated_at`：服务器 UTC ISO-8601（快照生成时刻）；
  - `database_backend`：`sqlite` / `postgresql` 类别 only——不输出 URL/host/db name；
  - `users_by_role`：各角色用户数（learner / admin）；
  - `papers_total`：试卷总数（含 seed 公共卷）；
  - `resources_by_parse_status`：上传资源按 parse_status 的分布；
  - `parse_jobs_by_status`：解析任务队列按 status 的分布（pending 积压/running/
    succeeded/failed 观测面）；
  - `pending_review_drafts`：四类待审草稿计数（course_import / course_generation /
    paper_question / variant_question，治理积压观测面；approved/rejected 不计入）；
  - `voice_sessions_by_status`：语音会话按 FSM status 的分布；
  - `search_queries_total` / `audit_entries_total`：搜索执行与审计留痕总量级；
  - `worker_running`：解析消费循环是否存活。
  空表不伪造非零：分布为空 dict、计数为 0，结构恒稳定。**不输出** username、
  query 文本、title、题目正文、request_id、业务 ID、endpoint、模型 key。
- **安全边界**：admin-only；只读（测试锁定调用前后 SQLite 文件字节与全部表
  行集合完全不变）；marker 注入验证敏感正文零回显；PRIVACY.md 第一节如实披露
  （无新增出站请求、无学习内容正文）。
- **后续告警接入边界**：如需真告警（阈值/持续异常判定），应在运维侧外挂
  Prometheus 抓取器或定时脚本对该端点采样并自行持久化——本仓库不引入监控
  后端服务/重型依赖；该端点语义只保证「读时点聚合」，不保证连续时序。

## 对象存储

- compose 的 api 服务注入 `S3_ENDPOINT/S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY`
  连接 minio；`MinioObjectStore` 启动时幂等确保 bucket 存在。
- 本地裸跑 API（无 S3_* 环境变量）回退内存实现——上传数据不持久，仅供契约调试。

## 部署模式（M9-07）

**本机模式（默认）**——所有端口只绑 127.0.0.1，数据服务（postgres/redis/minio）永不公开：

```bash
docker compose -f infra/docker-compose.yml --profile local up -d --build
```

无需额外变量（AUTH_SECRET/LiveKit 凭据使用仓库内开发占位值，仅本机使用）。

**局域网模式**——其他设备访问 Web/语音（AIOS_BIND_IP 只影响 api/web/livekit，
postgres/redis/minio 始终固定 127.0.0.1 不公开）。必须同时设置：

```bash
AIOS_BIND_IP=0.0.0.0 AIOS_APP_ENV=production AIOS_AUTH_SECRET='<至少 32 字节随机串>' AIOS_LIVEKIT_API_KEY='<生产 key>' AIOS_LIVEKIT_API_SECRET='<至少 32 字节随机串>' AIOS_CORS_ORIGINS='http://<本机局域网IP或域名>:3000' AIOS_PUBLIC_API_BASE_URL='http://<本机局域网IP或域名>:8000' docker compose -f infra/docker-compose.yml --profile local up -d --build
```

fail-closed 规则：绑定非 loopback 时任一条件缺失（非 production / 弱 secret /
localhost-only CORS / **缺 PUBLIC_LIVEKIT_URL 或其仍是容器内部地址**）API 拒绝启动。
`AIOS_PUBLIC_API_BASE_URL` 是**构建期**注入 Web 的 API 地址（NEXT_PUBLIC_API_BASE_URL），
改值后必须 rebuild Web；`AIOS_CORS_ORIGINS` 必须包含实际 Web origin。

**语音（LiveKit）局域网/公开拓扑（M9-08）**：

- `PUBLIC_LIVEKIT_URL`（API env）：token 返回给浏览器的 ws 地址——局域网必配
  `ws://<LAN_IP>:7880`；本机模式留空，token 回退 `ws://127.0.0.1:7880`；
- 媒体面通告：`AIOS_LIVEKIT_EXTERNAL_IP=<LAN_IP>` → livekit `--node-ip`（局域网必配）；
  公网机器用 `AIOS_LIVEKIT_CONFIG=/etc/livekit/livekit-public.yaml`
  （`use_external_ip: true` 自动探测）；
- 防火墙：放行 TCP 7880(signal)/7881(rtc-tcp) + **UDP 7882-7892(媒体)**；
  对称 NAT/严格防火墙需 TURN——M10-05 提供外部 coturn 部署模板
  （`infra/coturn/`，独立 compose 文件不并入主栈、主栈默认渲染不含 coturn；
  fail-closed：必填 secret/external-ip 缺失直接拒绝启动，entrypoint 再挡端口
  越界/自冲突/与 LiveKit 7880-7892 同机冲突、配置注入字符、无效布尔值；默认镜像
  digest pin），部署/防火墙/
  与 LiveKit 生产联动/验证见 docs/COTURN_DEPLOYMENT.md；
- 语音连通性验证（真实 livekit.rtc 客户端：token 鉴权 → Room.connect → CONN_CONNECTED
  → 数据通道）：
  `AIOS_MODE=local bash infra/smoke_voice.sh` /
  `AIOS_MODE=public AIOS_PUBLIC_HOST=<LAN_IP> bash infra/smoke_voice.sh`。

## 本地验证

```powershell
npm run typecheck
npm run lint
npm run build
ruff check services/api
# 真实 PG 集成测试：先创建隔离测试库（只查存在 + CREATE，不碰任何既有库），再指向它
Push-Location services/api
.venv\Scripts\python.exe scripts\create_pg_test_db.py
Pop-Location
$env:AIOS_PG_TEST_URL='postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os_test'
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
python -m pytest services/api -q
```

> **禁止把 5433/`ai_learning_os`（主/共享库）设为 `AIOS_PG_TEST_URL`**（M10-04 事故：
> 该用法曾让全量 pytest 每轮向主库写入 8 张「PG 验证卷」，6 批共 48 张永久污染，
> 只读分类报告 744/1397 -> 792/1487）。安全门控现在会对主/共享库名自动跳过
> 并给出原因，但请直接使用隔离库 `ai_learning_os_test`，不要依赖跳过兜底。
> 不设 `AIOS_PG_TEST_URL` 时全量 pytest 只跑 SQLite 单元路径，同样全绿。

> **测试自建 async engine 必须在创建它的同一个 `asyncio.run` 事件循环内
> `finally: await engine.dispose()`**（M10-10）：连接池持 aiosqlite worker
> 线程，不 dispose 就退出循环会让 worker 携已关闭 loop 的 future 存活，
> GC 时机不定地在后续任意测试触发 `PytestUnhandledThreadExceptionWarning`
> （call_soon_threadsafe on closed loop），且警告归属的测试与泄漏源无关。
> `TestClient(create_app(...))` 的 engine 由应用 lifespan 关闭，无需处理。

Docker 生产本地版冒烟（全服务 healthy + 端点 + 上传重启读回）：

```bash
docker compose -f infra/docker-compose.yml --profile local up -d --build
bash infra/smoke_docker.sh
```

## Android 壳工程（M12-01）

原生 Android App Shell，位于 `apps/android`（模块 `:app`，包名
`com.ailearningos.app`）。第一切片只做壳与认证/API 基础：单 Activity +
Jetpack Compose Material 3 + Navigation，五个界面（首页 / 学习占位 / 语音占位 /
设置与 API 配置 / 登录），Retrofit + OkHttp + kotlinx.serialization 对接
`GET /health`、`GET/POST /api/v1/auth/*`、`GET /api/v1/system/privacy`。
语音 / 搜索 / 治理等业务后续里程碑再接入（考试 / 学习 / 审阅已由
M12-02 接入，见下节）。

### 本地环境

- JDK 17（`gradle.properties` 与模块编译均按 17）。
- Android SDK：通过仓库外的 `apps/android/local.properties`（gitignored）
  写 `sdk.dir=<本机 SDK 路径>`，或设 `ANDROID_HOME`。
  **不要提交 local.properties 或任何本机绝对路径。**
- Gradle 使用 wrapper（8.13），不要手改 `gradle/wrapper/gradle-wrapper.properties`；
  依赖版本集中在 `gradle/libs.versions.toml`（AGP 8.13.1 / Kotlin 2.0.21 /
  compose 1.7.0 / retrofit 2.11.0 / okhttp 4.12.0 等，全部 pin 死）。

### 构建与测试

```powershell
# PowerShell（Windows）
apps\android\gradlew.bat testDebugUnitTest lintDebug assembleDebug
# POSIX / CI
sh apps/android/gradlew testDebugUnitTest lintDebug assembleDebug
```

- 全绿标准：JVM 单测 0 失败（M12-04 后共 318 项）、lint 无 error（允许
  依赖版本提示类 warning）、产出 `apps/android/app/build/outputs/apk/debug/app-debug.apk`。
- `gradlew` 不带可执行位入库存放，统一经 `sh ./gradlew` 调用，CI 无需
  `chmod +x`。
- 测试不连真实 API、不连数据库：远端交互全部走 MockWebServer fixture（显式绑
  loopback），ViewModel 走 fake gateway；token 存储用内存 fake（真实 Keystore
  实现无法上 JVM）。

### 调试 API 地址

- debug 构建默认 `http://10.0.2.2:8000/`（模拟器回环访问宿主机 API）。
- 设置页可改 base URL（普通配置走 DataStore，非敏感）；保存后立即重新探测。
- 规则（`core/BaseUrlPolicy` + `BaseUrlPolicyTest` / `ApiConfigProviderTest` 锁定）：
  - 存储层不做静默修复：读到的原始值（含首尾/内部空白）直接交给策略
    fail-closed；首尾空白只允许发生在设置页输入层（保存前 trim）；
  - 拒绝空白 / query（`?`）/ fragment（`#`）/ 反斜杠 / userinfo（`@`）/
    控制字符，规范化 scheme、host、端口与恰好一个尾部斜杠；
  - debug 允许 http 仅限 loopback（`localhost` / `127.0.0.0/8` / `10.0.2.2` /
    `10.0.3.2` / `::1`）或局域网（RFC1918 / `.local` / 单标签主机名），
    公网 http 一律拒绝；
  - release（`ALLOW_INSECURE_HTTP=false`）强制 https；错误态可在设置页修正后恢复，
    不做静默改写。

### 安全边界

- Bearer token：`data/local/KeystoreTokenStore` 用 AndroidKeyStore AES-256/GCM
  加密后，密文（Base64(iv‖ct)）存 DataStore；密钥不出系统密钥库，解密失败
  fail-closed（当作未登录并清残留密文）。**token 不允许明文落盘**
  （`TokenStorageGuardTest` 源码级守卫锁定）。
- token 全链路 fail-closed、零静默修整：登录响应的 accessToken 不 trim——空值或
  含任何空白（首尾/内部）一律 `BAD_RESPONSE` 且不落任何存储；OkHttp 拦截器
  同样不 trim，读到空白/异常 token 时不拼 `Authorization` 头
  （`AuthRepositoryTest` 锁定）。
- 用户名全链路不静默 trim：`LoginViewModel.validateCredentials` 对含首尾空白的
  用户名直接判非法；`AuthRepository.login` 原样提交，不做双重修复
  （`LoginViewModelTest` / `AuthRepositoryTest` 锁定 `" alice "` 形态）。
- `/me` 探测的 401/403 语义：401 = 凭据失效，清本地 token 回落未登录；
  403 = 权限不足，凭据仍有效，抛 `FORBIDDEN` 并保留 token，绝不把无权限
  静默降级成已登出。登录后 `/me` 失败（含被取消）不留半个会话：
  `NonCancellable` 清理 token，取消按 `CancellationException` 原样透传。
- 内存 token 缓存（`SessionTokenCache`）经 Mutex 串行化 ensureLoaded/save/clear，
  并发的 clear 与在途 load 不可能交错（杜绝「clear 后被旧 load 复活」）；
  配置变更广播（`ConfigurationBus`）replay=1，事件先于订阅发生也不丢。
- 会话探测并发契约（`SessionViewModel`）：`refresh()` 最新请求胜出——新探测/登出
  先取消在途探测 Job，再以代际号保护状态写入（旧探测即便已过挂起点、晚完成也
  不得覆盖新结果）；取消按 `CancellationException` 收场，绝不把「被取消」误写成
  `Unavailable`；登出同样作废在途探测，晚放行的旧探测不得把已登出状态复活成
  `Authenticated`（`SessionViewModelTest` 闸门式确定性并发测试锁定）。
- 密码只在登录请求内存中存在，不保存；logout 只清本地 token 与用户状态，
  不请求服务端。
- `data/`、`ui/` 层零日志（无 println / Log / printStackTrace，守卫测试锁定），
  错误文案统一脱敏映射（`core/AppError`），不暴露 token、完整 URL 查询串或
  底层异常细节。
- CI：`.github/workflows/ci.yml` 的 `android` job（temurin 17 +
  `gradle/actions/wrapper-validation@v4` +
  `sh ./gradlew testDebugUnitTest lintDebug assembleDebug`；契约测试
  `services/api/tests/test_workflow_actions_runtime.py` 锁定 action 版本、
  Java 17、wrapper 校验先于构建、无 lint baseline）。

## 考试/学习业务闭环（M12-02）

在 M12-01 壳上接入考试域三个真实界面与完整业务闭环：学习（试卷列表 +
恢复提示）→ 考场（开考/恢复/作答/倒计时/交卷）→ 审阅（报告优先，404
诚实降级）。全部走服务端权威语义，本地不缓存答案、不本地判分。

### 数据层

- `data/remote/ExamDtos.kt`：`GET /api/v1/papers`、`POST /papers/{id}/exams`、
  `GET/PUT /api/v1/exams/*`、submit / submission / report 的 DTO，
  snake_case 与服务端 Pydantic 契约一一对应；`ignoreUnknownKeys` 容忍
  服务端新增字段，不猜语义（`ExamDtoParsingTest` 锁定解析矩阵）。
- `data/ExamRepository.kt`：`ExamGateway` 接口的 Retrofit 实现（经
  M12-01 的 `ApiProvider`/token 拦截器复用认证链路）；错误经
  `RemoteErrors` 归类，新增 409 → `AppErrorKind.CONFLICT`（`core/AppError`
  文案「已按服务端状态对齐」）。`ExamRepositoryTest`（MockWebServer）锁定
  端点契约与错误分类。
- `data/model/ExamModels.kt`：领域视图。`nextSequence` 只来自服务端响应，
  状态字符串保留服务端原词（created/active/submitted/expired/
  report_ready），未知值不猜测、按非进行中处理。
- `data/local/ExamResumeStore.kt`：断线/进程重启恢复槽。**本地只允许保存
  exam_id 一个值且只作「恢复提示」**——恢复时必须 GET /exams/{id} 对齐
  answers / next_sequence / 剩余时间，答案绝不本地缓存当真相源
  （`ExamResumeStoreGuardTest` 源码级锁定只写 exam_id 键）。

### 考场状态机（`ui/exam/ExamViewModel`）

- **服务端权威序号**：`next_sequence` 只从服务端响应推进，PUT 失败绝不
  消耗序号；重试沿用同序号（服务端同 (sequence, question_id, answer)
  幂等）。同卷作答串行 drain，序号严格递增无重复。
- **同步失败对齐重放**：PUT 失败（网络/409）先 GET 对齐服务端
  answers/next_sequence/剩余时间，再以对齐后的序号重放未生效答案；
  断网时保留待同步答案停在可重试态；服务端持续拒绝同一答案时不无限
  对齐-重放（连续失败上限后停在可重试态，交卷按服务端已有答案结算）。
- **倒计时**：由 `server_end_at` + 可注入时钟推算（`server_remaining_seconds`
  为初值），归零自动交卷一次；**交卷幂等 + 防抖**（submitted 标志 + 在途
  Job 检查），交卷前补交待同步答案。
- 开考入口语义：有恢复槽先尝试续考（同卷 active 才续）；恢复槽 404 清槽
  按点击意图开新考；服务端状态未知（网络失败）**不盲目开新考**；已提交/
  已过期考试直接收口进审阅。

### 审阅（`ui/review/ReviewViewModel`）

优先 GET report（总分/题分/概念掌握/错题/解析/补救任务）；report 404
诚实降级 GET submission（概要 + 明确标注「详细报告暂不可用」）；两者
皆 404 → 「审阅报告尚未生成」。**绝不本地判分**：correct/expected/score
全部来自服务端响应（`ReviewViewModelTest` 锁定 report-first 流程）。

### UI

`ui/study`（试卷列表四态 + exam_id-only 恢复卡，替换 M12-01 占位页）、
`ui/exam`（题卡 / 单选 / 多选 / 简答输入、题目导航、倒计时、同步状态、
交卷确认）、`ui/review`（报告分区呈现）；`ui/common/QuestionTypes.kt`
题型显示名与 Web 端对齐，未知题型如实显示「作答」。语音页仍为占位
（`ui/common/PlaceholderPage.kt` 通用化，不虚构功能）。导航与 VM 工厂
经 route 参数注入（`AiosApp` / `AiosViewModelFactory` + CreationExtras）。

### 测试与调度约定（防挂死）

`ExamViewModelTest`（25 项）覆盖开考/恢复矩阵、append-only 序号、同步
失败不耗序号、409 对齐重放、倒计时归零自动交卷、交卷防抖与补交。考试
VM 持有 `while + delay` 常驻 ticker，测试约定（文件头注释锁定）：
`runTest` 与 `MainDispatcherRule` 共享同一 `TestCoroutineScheduler`
（`runTest(mainDispatcherRule.testDispatcher.scheduler)`），且每个用例经
`runExamTest` 包装在测试体内 `finally` 先 `cancel` 全部 `viewModelScope`
再让 runTest 收尾——否则 runTest 的 idle 检测因常驻 ticker 挂死。

### M12-02 验证状态与边界

- **PR #45 已合并**（2026-09-06，Codex 实测口径，merged_at `15:16:55Z`）：
  base `main`，最终 head 分支 `feature/m12-02-android-exam-flow`、head
  `4713aa8ef6dd46325a151e0d6bf07d9a097129a3`（`feat(m12-02): add Android
  exam workflow`），merge commit `47c553a7d3b0b0bcd31cf35f10270cbd0eff72fc`
  （parents `5954862` + `4713aa8`），远端 main merge tree `7f569a0` 与
  head tree 完全一致；PR CI run `34041549579` 全绿（API 4m41s /
  Android 2m03s / Docker 2m23s / Web 1m24s），merge 后 main CI run
  `34041811291` success（headSha `47c553a7`，15:16:57Z 创建、15:20:55Z
  完成）；远端功能分支已删除。「未 push、未开 PR」为实现时点状态，已由
  PR #45 收口。
- 验证命令（2026-09-06，Windows 11 + JDK 17 本地实测）：
  `apps/android/gradlew.bat testDebugUnitTest lintDebug assembleDebug --rerun-tasks`
  → BUILD SUCCESSFUL；test-results XML 汇总 **170 tests / 0 failures /
  0 errors / 0 skipped**（17 个测试类）；lint 0 error（20 warning，
  同 M12-01 依赖版本提示类）；产出 `app/build/outputs/apk/debug/app-debug.apk`。
- **未跑模拟器/真机**：全部验证为 JVM 单测（MockWebServer / fake
  gateway / fake 存储），Compose UI 与 Keystore/DataStore 行为未经
  真机验证。
- 测试不触网、不连数据库；不读取/不输出任何 key/token/password；
  `production_ready=false` 语义不变。

## 语音陪练业务闭环（M12-03）

在 M12-02 壳与考试域之上接入语音陪练第一切片（分支
`feature/m12-03-android-voice-flow`，基于 `main@5065585`；已随
PR #47 合并收口）。目标：用户从学习页选试卷即可开始/继续/结束语音陪练，
界面呈现服务端状态、当前题、选项、已提交答案、恢复提示与错误降级——
复杂留给系统，简单留给用户。

### 服务端契约（只消费不重造）

全部端点以 `services/api/app/api/routes/voice.py` 及其测试为准
（`test_voice_session_fsm.py` / `test_voice_resume.py` /
`test_voice_report.py` / `test_voice_trace.py` / `test_voice_providers.py`）：

- `GET /api/v1/voice/providers`（链路概要，Picker 态展示）
- `POST /api/v1/voice/sessions`（为进行中考试创建语音会话）
- `GET /api/v1/voice/sessions?exam_id=`、`GET /sessions/{id}`、
  `GET /sessions/{id}/resume`（断线恢复视图：状态 + 当前题公开字段 +
  服务端权威已提交答案）
- `POST /sessions/{id}/commands`（15 种 FSM 命令：start_reading /
  question_read / options_read / answer_proposed / answer_clarify /
  commit_confirmed / skip / repeat_* / slow_down / end / pause / resume /
  barge_in / report_ready）
- `POST /sessions/{id}/intents`（transcript → 服务端解析 → FSM 应用，
  作答主路径）、`POST /sessions/{id}/answers`（event_id 幂等规范化提交，
  仓储面已接入并测试，UI 主路径走 intents）
- `GET /sessions/{id}/report`（REPORT_READY 后的语音播报投影）
- `POST /api/v1/voice/transcribe`（multipart WAV → 文本）、
  `POST /api/v1/voice/synthesize`（文本 → WAV 字节）、
  `POST /api/v1/voice/trace`（客户端实测耗时 span，尽力而为）

状态枚举保留服务端原词（SESSION_READY / READING_QUESTION /
READING_OPTIONS / WAITING_ANSWER / CLARIFYING / ANSWER_COMMITTED /
NEXT_QUESTION / REPORT_READY）；错误码经 M12-02 的 `AppError` 语义映射
（409 → CONFLICT 先 GET resume 对齐再提示，404 清恢复槽，503/5xx → SERVER）。

### 实现结构（apps/android 主源码）

- `data/remote/VoiceDtos.kt`：语音 DTO（snake_case 对齐服务端 Pydantic）。
- `data/model/VoiceModels.kt`：领域视图（状态原词、未知值不猜）。
- `data/VoiceRepository.kt`：`VoiceGateway` 的 Retrofit 实现，复用 M12-01
  认证链路（`ApiProvider` / token 拦截器 / `withRemoteError`），不复制
  第二套 HTTP 基础设施。
- `data/local/VoiceResumeStore.kt`：恢复槽（DataStore）——只保存
  session_id / exam_id 两个最小元数据键；语音音频、transcript、答案、
  判分绝不落本地（`VoiceResumeStoreGuardTest` 源码级锁定）。
- `voice/WavCodec.kt`：WAV RIFF 封装/解析纯逻辑——`PcmSpec` 暴露
  dataOffset/dataBytes 边界（LIST/fact 等合法前置 chunk 使 data 不在 44，
  播放从 dataOffset 消费；声称长度超出文件实际即拒绝）；播放参数以
  header 实际采样率为准（服务端本地 TTS 为 8kHz）。
- `voice/AudioEngines.kt`：`AudioCaptureEngine` / `AudioPlaybackEngine`
  可注入接口 + Android 实现（AudioRecord 16kHz/mono/PCM16 采集）。
  播放走 `WavPlaybackSession`（JVM 可测编排）+ `WavPlaybackDriver`
  （可注入驱动；生产=AudioTrack，marker=播放头到达终点帧才回调完成）：
  **写完 ≠ 播完**——数据全部入队后挂起等待驱动侧完成信号才返回，
  question_read/options_read 不会过早推进服务端 FSM；**取消状态属于
  单次播放尝试**（每次 play 一个 `Attempt`：独立取消标志 + 完成信号 +
  自己的轨道快照，新播放顶掉旧播放时取消旧尝试自己的状态并立即
  pause 旧轨道——旧音频不与新播报重叠——绝不重置），停止立即唤醒
  挂起播放（取消语义，不当作已播完）、取消与 pause 配对在同一尝试、
  各次播放释放自己的轨道、不忙等。
  调度边界：引擎注入 `CoroutineDispatcher`（默认 Dispatchers.IO）——
  AudioTrack 创建、PCM 写入、等待完成与 release 全部在 IO 执行，
  ViewModel 的 Main 调用 play 不阻塞主线程；stop 的立即部分（标志 +
  唤醒）同步执行，停止时刻轨道快照交 IO pause/flush
  （`WavPlaybackSessionTest` 以单线程 executor 断言阻塞驱动操作绝不
  落在调用方 dispatcher，并以真实线程门控测试锁定并发顶替语义）。
  失败抛固定脱敏文案的 `AudioEngineException`，绝不静默假装成功。
- `ui/voice/VoiceViewModel.kt`：服务端权威状态机——读题→读选项→倾听由
  「播报完成」驱动链式推进（question_read/options_read 在 TTS 播完后发）；
  播报失败停在当前状态显示可重试错误；409 以 resume 对齐；恢复槽 404/409
  清理；trace 上报（asr/tts 客户端实测）失败静默吞掉不扰动业务。
- `ui/voice/VoiceScreen.kt`：Picker（链路概要 + 恢复卡 + 选卷引导）/
  进行中（状态卡、题面与已提交答案高亮、澄清提示、播放控制、录音 +
  文字双作答入口、推进主按钮、结束）/ 报告（spoken_text + 错题摘要 +
  补救建议）三态。录音按钮先请求 RECORD_AUDIO 运行时授权，拒绝则保留
  文字作答（诚实降级，不冒充已录音）；录音中有可见状态提示。
- 导航：学习页试卷卡新增「语音陪练」入口（`voice-paper/{paperId}`）、
  恢复卡（`voice-session/{sessionId}`）、语音 tab（Picker）。RECORD_AUDIO
  权限加入 main manifest。

### M12-03 验证状态与边界

- **PR #47 已合并**（2026-09-07 回填，Codex 核验口径，merged_at
  `2026-09-06T17:52:22Z`）：base `main@506558566ed5`，最终 head 分支
  `feature/m12-03-android-voice-flow`、head
  `85fc01e6dc55edf16217c429debb2a21d0bed415`（`feat(m12-03): add
  Android voice workflow`），merge commit
  `2bbeb072cde1e33660f60fbc742c22951d2704ff`（parents `5065585` +
  `85fc01e6`，本地 git 可验证），功能 head tree 与远端 merge tree 均为
  `a59b122d`（内容等价）；PR CI run `34049713374` 四项 SUCCESS
  （API / Android / Docker / Web），merge 后 main CI run `34049946924`
  四项 SUCCESS；远端功能分支已删除。「本地已提交、未 push、未开 PR」
  为实现时点状态，已由 PR #47 收口。远端事实为 Codex 核验提供，
  本回填切片不触网。
- 验证命令（2026-09-07，Windows 11 + JDK 17 本地实测，PowerShell）：
  `apps/android/gradlew.bat testDebugUnitTest lintDebug assembleDebug --rerun-tasks`
  → **BUILD SUCCESSFUL**（44s，55 任务全量执行）；test-results XML 汇总
  **256 tests / 0 failures / 0 errors / 0 skipped**（23 个测试类，其中
  M12-03 新增 6 类 86 项：VoiceViewModelTest 29 / VoiceRepositoryTest 23 /
  VoiceDtoParsingTest 12 / VoiceResumeStoreGuardTest 3 / WavCodecTest 9 /
  WavPlaybackSessionTest 10）；
  lint **0 error / 19 warning**（依赖版本提示类，同 M12-01/02 基线构成）；
  产出 `app/build/outputs/apk/debug/app-debug.apk`。
- 测试期间修复的真实缺陷（如实记录）：① `VoiceRepositoryTest` 两处对同一
  请求调用两次 `server.takeRequest()`（第二次无限等待下一请求导致
  worker 挂死），改为一次取请求后复用变量断言 path 与 body——Codex 已用
  PowerShell 独立复验挂死用例修复；② Codex 验收提出的两个阻塞项——
  播放引擎原实现 write 后立即 stop/release（write 返回只代表入队，会截断
  播报并让 question_read/options_read 过早推进服务端 FSM），重写为
  `WavPlaybackSession` + `WavPlaybackDriver`（AudioTrack marker=播放头
  到达终点帧的完成回调；停止/取消立即唤醒并释放，不忙等），并以
  `WavPlaybackSessionTest` 锁定「写完 ≠ 播完」推进条件；`PcmSpec` 原缺失
  data chunk 偏移（播放硬编码 44 起），改为暴露并校验 dataOffset/dataBytes
  边界（前置 LIST/fact chunk、截断/谎报长度均覆盖测试）；③ Codex 验收
  第二轮阻塞项——播放重构后 AudioTrack 创建/PCM 写入/release 曾直接跑在
  调用方（Main）dispatcher，引擎注入 `CoroutineDispatcher`（默认
  Dispatchers.IO）把全部阻塞路径移出 Main（stop 的立即唤醒同步、停止时刻
  轨道快照交 IO pause/flush），并以单线程 executor 的调度边界测试锁定
  阻塞驱动操作绝不落在调用方 dispatcher；端点计数失真修正（文档/提交
  信息误写 13，实际契约 12 个端点）；④ Codex 验收第三轮阻塞项——全局
  `stopRequested` 布尔在「旧 play 阻塞在 write、新 play 顶掉后立即重置
  标志」的真实线程交错下会让旧会话失去取消语义，重构为每次播放尝试
  私有的 `Attempt`（独立取消标志 + 完成信号，`current`/`activeHandle`
  @Volatile），并以 wait/notify 条件等待 + latch 门控的真实线程测试锁定
  （旧会话取消收尾且不再写已暂停轨道、新会话由自己的 marker 完成）；
  `WavCodec.pcmSpec` chunk 遍历改 Long 累加防 Int 溢出（恶意/损坏 WAV
  的巨大 chunkSize fail-closed 返回 null，不异常不死循环，含溢出测试）；
  ⑤ Codex 验收第四轮两个阻塞项——新播放顶替旧播放时只取消旧尝试
  不 pause 旧轨道（旧音频在旧协程退出前继续播、与新播报重叠），改为
  顶替时对旧尝试自己的轨道快照立即 pause 再启动新驱动，并以真实线程
  测试锁定旧 handle 在顶替路径被 pause 恰好一次；`requestStop` 先读全局
  `activeHandle` 再读 `current` 的两次独立读存在错配竞态（可能取消新尝试
  却 pause 旧轨道），重构为每次尝试私有 `@Volatile` handle 快照并移除全局
  `activeHandle`，`requestStop` 只读一次 `current`、对同一尝试取消并返回
  其快照，以真实线程交错测试锁定外部停止的取消与 pause 配对在同一尝试上。
- **未跑模拟器/真机**：全部验证为 JVM 单测（MockWebServer 显式绑
  loopback / fake gateway / fake 引擎 / fake 存储）；Compose UI 渲染、
  AudioRecord/AudioTrack 实机行为（含 marker 回调时序）、运行时授权弹窗、
  DataStore 真机行为均未验证。
- **未接真实 provider**：transcribe/synthesize 在测试中全部走 fake /
  MockWebServer fixture；未读取任何真实 key/token/password；测试不触网。
- `answers` 端点已在仓储面接入并测试；UI 作答主路径走 `intents`
  （服务端解析绑定当前题，含糊进澄清不落库）。
- 「不 push、不建 PR、不打 tag」为实现时点约束，已由 PR #47 合并收口
  （不打 tag、不发 Release 仍然成立）；`production_ready=false` 语义不变，
  本切片不构成任何 production readiness。

## 搜索接入第一切片（M12-04）

在 M12-01/02/03 基座上接入搜索域第一切片（分支
`feature/m12-04-android-search-flow`，基于 `main@144bbf7`（PR #48 merge
commit）；**PR #49 已合并，最终 head `dba0a6c3`，merge commit
`64e26ca`，来源 URL 安全行返工与模拟器冒烟证据均随 PR 入库，PR 与
merge 后 main CI 四项全绿（详见后文合并状态回填）**）。目标：Android 搜索页从无到有
——复杂留给系统，简单留给用户：进入页面即见搜索源可用性与禁用原因，输入
查询词即可预览计划或执行搜索，结果/排序理由/弃用原因全部如实呈现，可按
query_id 回查服务端记录；本地不持久化任何搜索历史。

### 服务端契约（只消费不重造）

全部端点以 `services/api/app/api/routes/search.py` 及其测试为准
（`tests/test_search.py` / `tests/test_query_planner.py`）：

- `GET /api/v1/search/providers` → `{ items: [ { name, kind, enabled,
  unavailable_reason } ] }`——注册表视图，enabled=false 必带原因
  （如 `SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站`）。
- `POST /api/v1/search/plan`，body `{ query, providers? }` → `{ query,
  slots: { subject, school, year, course, question_type, publicity },
  plan: [ { provider, query, enabled, unavailable_reason } ] }`——六槽位
  识别（未识别为 null，不虚报）+ 逐源计划，只预览不执行；**指定未知
  provider 直接 422**。
- `POST /api/v1/search/queries`，body `{ query, providers?, limit
  （默认 10，1..50）}` → `{ query_id, query, providers_requested, results:
  [ { title, url, snippet, source, provider, authority?, rank_reason } ],
  skipped: [ { provider, reason } ], result_count, duration_ms }`——多源
  执行；**指定未知 provider 不报错，进 skipped 记原因**（与 plan 的 422
  语义区分）；结果经服务端 rank_and_dedup 排序去重后截断 limit。
- `GET /api/v1/search/queries/{query_id}` → `{ id, query,
  providers_requested, providers_skipped, result_count, duration_ms,
  results, created_at }`——执行记录回查（服务端 queries 表是唯一可回查
  来源）。

语义边界（Android 侧不得削弱）：无 DB 时四端点 503；计划未知 provider 422
（映射 `AppError` BAD_RESPONSE）、执行未知 provider 进 skipped、均如实展示
服务端 reason 不虚报可用；结果与排序由服务端返回，Android 不去重、不重排、
不改写 rank_reason、不隐藏 skipped；云搜索是否出站由服务端配置决定，Android
不直接访问 cloud-web/provider URL；`query_id` 为服务端 int，Android 侧以
Long 承接。

### 实现结构（apps/android 主源码）

- `data/remote/SearchDtos.kt`：搜索 DTO（snake_case 对齐服务端 Pydantic；
  unavailable_reason / 六槽位 / authority 可空字段保留 null 不猜语义；
  `AiosJson` 的 `explicitNulls=false` 使 `providers=null` 不进请求体——
  缺省即全部已注册源，`encodeDefaults=true` 使默认 `limit=10` 随体显式
  编码，两者与服务端缺省语义一致）。
- `data/model/SearchModels.kt`：领域视图（SearchProviderEntry /
  SearchPlanSlots / SearchPlanItem / SearchPlan / SearchSkipped /
  SearchResultItem / SearchOutcome / SearchRecord）。
- `data/SearchRepository.kt`：`SearchGateway` 接口（providers / plan /
  search / record 四方法，测试用 fake 实现）+ Retrofit 实现，复用 M12-01
  认证链路（`ApiProvider` / token 拦截器 / `withRemoteError` → `AppError`
  映射：401 UNAUTHORIZED / 404 NOT_FOUND / 422 BAD_RESPONSE / 5xx SERVER /
  IOException NETWORK），零新增 HTTP 基础设施；limit 值域由调用方保证，
  客户端不静默截断改写用户意图。
- `ui/search/SearchViewModel.kt`：扁平 `SearchUiState`（providers /
  查询输入 / 计划 / 结果 / 回查五区，进程内 state 不落盘）；每操作请求
  序号守卫 + Job 取消双防护——迟到的旧响应（或取消通知晚于结果到达的
  响应）不得覆盖新请求结果；空白查询词与非法 query_id 本地拦截提示
  （与服务端「查询词不能为空白」422 同语义，省一次注定失败的请求），
  其余校验全部交给服务端；搜索/回查失败保留旧结果只显示错误，零结果
  如实呈现空态不是错误。
- `ui/search/SearchScreen.kt`：Compose M3 单页 LazyColumn——搜索源卡片
  （逐源 name/kind/可用性，禁用显示服务端 unavailable_reason 原文；加载/
  错误/重试三态）、查询卡片（输入 + 「预览计划」/「搜索」双操作）、计划
  卡片（识别到的槽位逐项展示，未识别不显示；逐源计划行含将执行的查询词
  或「不会执行」+原因）、结果区（query_id/result_count/duration_ms/
  providers_requested 概要 + 逐条 title/snippet/source·provider·authority/
  rank_reason + skipped 逐条原因 + 零结果空态）、按编号回查卡片
  （输入 query_id → 服务端记录全字段 + created_at）。沿用 SectionCard /
  StateViews 视觉体系，无营销页/占位页；结果与回查逐条共用 `SourceUrlRow`
  ——来源 URL 原文断行展示（可溯源：不截断、不改写、不隐藏），仅
  `ResultUrlPolicy` 校验通过的 https 提供「打开」（ACTION_VIEW 交给系统
  浏览器，无应用接管如实 Toast），未通过（非 https / malformed）禁用打开
  并附原因，不做「猜意图」的修复。
- `core/ResultUrlPolicy.kt`（返工新增）：结果来源 URL 安全校验与展示断行
  纯函数（JVM 单测 `ResultUrlPolicyTest` 15 项锁定）。与 `BaseUrlPolicy`
  的语义差异：结果 URL 是服务端返回的完整链接（path/query/fragment 属
  正常形态，予以保留），不是客户端配置的 API base URL；但交给 ACTION_VIEW
  前收紧为仅 https（大小写不敏感——不存在「debug 放行 http」的例外），
  拒绝空白/控制字符/反斜杠、authority 内 userinfo（`good.com@evil.com`
  视觉混淆面；query 里的 `@` 不误伤）与非 ASCII 原文（JDK 17 实测
  `java.net.URI` 对原文非 ASCII path 并不抛异常，必须显式拦截）；通过
  校验的 URL 原样返回不规范化。`wrapForDisplay` 长 URL 断行：`/ ? & =`
  优先断行（断点字符留行尾），窗口内无断点才硬切，每行 ≤40 字符
  （bodySmall 12sp 下 360dp 屏不横向溢出）。
- 导航：底部导航扩为 Home/Study/**Search**/Voice/Settings 五位
  （`AiosApp.kt` 的 `AiosDestination.Search`，Icons.Filled.Search），
  `AppContainer` 注册 `searchGateway`、`AiosViewModelFactory` 装配
  `SearchViewModel`。

### M12-04 验证状态与边界

- 验证命令（2026-09-07，Windows 11 + JDK 17 本地实测，PowerShell）：
  `apps/android/gradlew.bat testDebugUnitTest lintDebug assembleDebug
  --rerun-tasks --console=plain` → **BUILD SUCCESSFUL**（41s，55 任务全量
  执行）；test-results XML 汇总 **26 类 / 303 tests / 0 failures /
  0 errors / 0 skipped**（M12-03 后 256→303，新增 3 类 47 项：
  SearchDtoParsingTest 12 / SearchRepositoryTest 18 / SearchViewModelTest
  17）；lint **0 error / 19 warning**（依赖版本提示类 + 既有文件基线，
  无一条指向本切片新增文件）；产出
  `app/build/outputs/apk/debug/app-debug.apk`；`git diff --check` 干净。
- 返工（2026-09-07，改动未提交）：Codex 验收确认前版 `SourceUrlRow` 未写完
  （引用与导入就位但 composable 缺失，无法编译），并要求执行结果与回查
  结果可溯源展示来源 URL、仅合法 https 可经 ACTION_VIEW 打开、非 https /
  malformed 禁用打开且如实展示、小屏不横向溢出——补 `core/ResultUrlPolicy.kt`
  + SearchScreen 两侧 `SourceUrlRow` + `ResultUrlPolicyTest` 后同命令复跑 →
  **BUILD SUCCESSFUL**（55 任务全量执行）——test-results XML 汇总 **27 类 /
  318 tests / 0 failures / 0 errors / 0 skipped**（303→318，新增
  ResultUrlPolicyTest 15：校验矩阵 10 + 断行矩阵 5），lint **0 error /
  19 warning**（与基线同构成：GradleDependency/NewerVersionAvailable ×13、
  ObsoleteLintCustomCheck ×3、ModifierParameter/DataExtractionRules/
  ObsoleteSdkInt 各 1，无一条指向本切片文件），APK 重产出
  （11,405,252 bytes）。返工修复的真实缺陷（如实记录）：① 首版
  `wrapForDisplay` 硬切分支 off-by-one——每行超宽 1 字符（41/40），与其
  「每行不超过 maxLine」自述矛盾，被新增单测当场抓获后修复；② 首版实现
  与自身文档「非 ASCII 原文按 malformed 拒绝」矛盾——`java.net.URI`（JDK
  17 实测）对原文非 ASCII path 不抛异常而放行，补显式 ASCII 拦截
  （BAD_SCHEME）并以独立 JVM 探针核实解析行为；③ 首版 lint 多 1 条
  UseKtx 指向 `Uri.parse`，改 core-ktx `String.toUri()` 后回到 19 条基线。
- 测试覆盖：DTO nullable 字段与 snake_case（providers/plan/queries/
  record 四形态、未知键容忍、缺必填拒绝、全空槽位、authority null 与
  official、skipped 原因原样）；仓储契约（路径/方法/body——`providers=null`
  不编码、默认 `limit=10` 显式编码、providers 列表编码；鉴权头——seed
  token + `ensureLoaded` 后 `Authorization: Bearer` 注入、无 token 不拼头；
  错误映射——422 BAD_RESPONSE / 503 SERVER / 404 NOT_FOUND / 401
  UNAUTHORIZED / 网络断开 NETWORK）；ViewModel（providers 加载/失败重试/
  过期响应不覆盖、计划预览成功/422/503、搜索成功含 skipped 可见/零结果
  不虚报/503 保留旧结果/422、空白输入本地拦截不发请求、回查成功/404/
  非法输入本地拦截/失败保留旧记录、连续搜索迟到的第一次响应被丢弃
  （cancel 路径）与逃逸取消的迟到响应被序号守卫拦截（handler 吞取消异常
  模拟「响应先于取消通知到达」的真实网络层形态））。时序用例全部经
  FakeSearchGateway 的 handler 钩子 + CompletableDeferred 门控，不依赖
  真实时间睡眠。
- 开发期间修复的真实缺陷（如实记录）：① Compose 委托属性
  （collectAsStateWithLifecycle）不能 smart cast，LazyColumn builder 内
  先取局部 val 再判空；② 首版 `SearchRepositoryTest` 鉴权用例只
  `tokenStore.seed` 未 `ensureLoaded`——AuthInterceptor 只读内存缓存
  （`SessionTokenCache.peek`），token 未进缓存导致断言失败，改为 seed 后
  `ensureLoaded()`（与真实启动惰性加载路径一致）。
- **模拟器 mock 冒烟（2026-09-07，已通过，证据 `docs/evidence/m12-04-android-search/`
  ——README + summary.json + 9 组 uiautomator dump 截图/XML + Chrome 接管
  dumpsys 前后对比 + mock_requests.log + 9603 行 logcat-full.txt.gz + 归档
  mock_api_used.py。logcat 以无损 gzip 归档：**不做任何清洗**——.gz 解压
  SHA256 与原始捕获文件逐字节一致（`5d2c76c2…908c112`，1,283,682 字节；
  与此前 autocrlf 归一化纯文本暂存 blob 仅差行尾 CR、CR 增删往返核对
  一致），原始 CRLF 行尾与系统日志行自带尾随空白原样保留；gzip 使 git
  按二进制 blob 处理，避免 `git diff --check` 把 raw 文本当文本 diff 检查
  ——前版纯文本暂存时曾报 549 条尾随空白告警（全部位于该证据文件、属
  逐字证据原文，源码与文档零告警），换 .gz 后通过）**：
  Android 16 `emulator-5554`（AVD Medium_Phone，SDK 36，1080x2400@420dpi
  ≈ 411dp 宽）`adb install -r` + `pm clear` 后安装 08:32:46 构建的 debug
  APK（晚于全部工作区源码修改，返工 `ResultUrlPolicy` 已核实编入
  classes5/classes8.dex）；mock 当次运行于 `.verify/m12-04-android-smoke/
  mock_api.py`（gitignored，纯标准库、仅绑 127.0.0.1:8000、模拟器经
  10.0.2.2:8000 即 debug 默认 base URL 访问，自建自停、端口已复核释放），
  该无密钥 mock 契约已归档为证据目录内 `mock_api_used.py`（据此可复现），
  固定 JSON 覆盖
  /health、/auth/status、/system/privacy、/search/providers、POST /plan、
  POST /queries（query_id=9001）、GET /queries/9001。**已验证**：①五位
  底栏导航（首页/学习/搜索/语音/设置各恰 1 节点）；②搜索源注册表
  （local-corpus 可用 / cloud-web 不可用 + 禁用原因原文）；③计划预览
  （未执行标注 + 六槽位 + local-corpus 将搜索 / cloud-web 不会执行 +
  原因）；④执行搜索（#9001 / 2 条 / 12 ms / 请求的源 / skipped cloud-web
  原因原样透出；mock 请求日志逐端点核对，POST /queries body 54B 含干净
  查询词 `2024 tsinghua advanced math mcq` + limit:10）；⑤结果与回查来源
  URL **原文恰两行断行**（长 ASCII URL 首行恰 40 字符于 `?` 断开 + 第二行
  query 串，行宽 ≤40 无横向溢出、不截断不改写不隐藏）；⑥「打开」仅对
  通过 `ResultUrlPolicy` 校验的 https 启用，点按后 ACTION_VIEW 由 Chrome
  接管（dumpsys 前后对比：mCurrentFocus/ResumedActivity 从
  `com.ailearningos.app/.MainActivity` 变为 `com.android.chrome` 首运行页
  `FirstRunActivity`）；⑦返回 App 后按编号回查 9001，记录全字段（原查询
  词/结果数/耗时/记录时间/请求的源/结果列表含 URL 两行断行/skipped 原因）；
  ⑧全程 logcat 9603 行 FATAL EXCEPTION=0、ANR=0、AndroidRuntime+本包 0 行。
  未发现应用缺陷（summary.json 五例 observations 均为工具/环境口径说明，
  非缺陷）。
- **冒烟口径边界（不夸大）**：Chrome 接管只证明 VIEW intent 焦点转移——
  模拟器 Chrome 从未启动、ACTION_VIEW 落在首运行页 `FirstRunActivity`
  （未接受 ToS，不触发 Chrome 侧页面加载/联网行为——整机层面的 OS 自动
  探测与此无关，见下条），**不声称页面加载成功**；mock 结果
  URL 固定 `https://localhost/m12-04/smoke-source?trace=local&suite=android-360`，
  host 是 localhost（不出设备），**不代表外网真实 provider 可用**；查询词为
  ASCII（adb input text 无法注入 CJK——输入注入工具限制，mock 固定中文
  槽位/计划/结果文案仍覆盖中文渲染）；首页对 mock 非枚举路由字符串如实
  渲染「未知」（呈现边界，不虚报）。
- **未接真实 provider**：search 冒烟与测试全部走自建 loopback mock /
  MockWebServer fixture（显式绑 loopback）/ FakeSearchGateway，App 与
  测试不触外网、不访问任何真实 search/cloud-web 端点；未读取任何真实
  key/token/password。「不触外网」为 App/mock 口径而非整机口径：冒烟
  logcat 含模拟器系统层自动网络行为——NetworkMonitor（system_server）
  对 connectivitycheck.gstatic.com（HTTP 204 实际出网成功）/
  www.google.com / play.googleapis.com 的联网探测，及 Gboard 在输入框
  获焦时自 gstatic 请求词典/模型清单；均为 OS/系统输入法组件行为、非
  本 App 流量、不涉及任何 AI provider/生产服务（详见证据 README 结论）。
- **仍未验证**：真机（Compose 渲染/导航/ACTION_VIEW 实机行为）；**非
  https / malformed URL 禁用态的模拟器交互**——冒烟中「打开」仅出现在
  通过校验的 https 结果旁，禁用态 UI（附原因文案、不可点击）未在模拟器
  上实际验证，该禁用逻辑由 `ResultUrlPolicyTest` 15 项 JVM 单测覆盖；
  无浏览器接管时的 Toast 降级实机形态；生产可用（mock 冒烟不构成任何
  production readiness）。
- UI 第一切片固定默认 limit=10（契约 1..50 值域由仓储层透传，未做 UI
  limit 选择器）、未做 providers 勾选过滤（默认全部已注册源执行，skipped
  语义自然呈现）——留给后续切片按真实使用反馈决定。
- **合并状态回填（2026-09-07）**：PR #49 已合并（base main@144bbf7，最终
  head dba0a6c3，merged_at 2026-09-07T01:46:44Z，merge commit 64e26ca）；
  PR CI run 34073874737 与 merge 后 main CI run 34074112566 均为
  API/Android/Docker/Web 四项 SUCCESS；远端功能分支已删除。未打 tag、
  未发 Release、未部署；真机、真实 provider、生产可用仍未验证，
  production_ready=false。
- PR #49 已合并收口（不打 tag、不发 GitHub Release、不部署仍然成立）；
  `production_ready=false` 语义不变，本切片不构成任何
  production readiness。

## 治理与发布只读第一切片（M12-05）

### 范围与契约

- 分支 `feature/m12-05-android-governance-release`，基于
  `main@b6475c02071c280ec4c154f91989c54db94a432c`；本地实现与模拟器 mock
  冒烟完成，**已随 PR #51 合并**（PR/merge CI 与收口细节见下文「合并状态」）。
- Android 只消费既有 `GET /api/v1/version`、`GET /api/v1/system/ops-snapshot`、
  `GET /api/v1/audit?limit=100`；不新增服务端端点、不做治理写操作。
- 首页入口仅在 AuthDisabled 或 admin 用户状态显示；入口可见性不放宽服务端
  admin-only 权限。
- 服务端审计响应包含 before/after 载荷；Android DTO 不建模、领域模型不保留、
  UI 不渲染。

### 实现结构

- `data/remote/GovernanceDtos.kt`：snake_case DTO；
  `AuditEntryResponse.actorId/actorUsername` 使用 `RequiredNullableString`，
  字段键必须存在、值可为显式 null；原因是全局 `AiosJson` `explicitNulls=false`
  会让普通 `String?` 缺键静默变 null；不改全局配置。
- `data/model/GovernanceModels.kt`：版本、运行快照、审计领域投影；审计投影
  不含 before/after。
- `data/GovernanceRepository.kt`：`GovernanceGateway` + Retrofit 实现，复用
  `ApiProvider`、认证拦截器、`AppError/RemoteErrors` 映射；审计固定
  limit=100。
- `ui/governance/GovernanceViewModel.kt`：三区并发加载，每区独立 Job 与请求
  序号守卫；迟到旧响应不得覆盖新响应；单区刷新失败保留旧数据并只显示该区
  错误。
- `ui/governance/GovernanceScreen.kt`：Compose M3 版本/运行快照/审计三区，
  服务端字段原词呈现，不虚构数据；actor null 如实显示。
- `AiosApi/AppContainer/AiosViewModelFactory/AiosApp/HomeScreen`：端点、装配、
  治理路由与按会话状态显示入口。

### 测试与验证

- 新增 `GovernanceDtoParsingTest`、`GovernanceRepositoryTest`、
  `GovernanceViewModelTest`；`Fakes.kt` 新增 `FakeGovernanceGateway`。
- 本地全量门禁：`apps/android/gradlew.bat testDebugUnitTest lintDebug
  assembleDebug --rerun-tasks --console=plain`，**BUILD SUCCESSFUL**，55 任务；
  347 tests / 0 failures / 0 errors / 0 skipped；lint 0 error / 19 warnings；
  APK 11,454,463 bytes，SHA256
  `56119e1a28199712b7ac0c7e9dbb54fee19c4bad52a23b3c85afd218a44c59a8`——
  该 SHA256 为**模拟器冒烟证据 APK**指纹（已归档
  `docs/evidence/m12-05-android-governance/summary.json`）。
- 文档/注释回填后 Codex 提交前最终复验（同命令
  `--rerun-tasks --console=plain`）：**BUILD SUCCESSFUL**，55 任务；
  347 tests / 0 failures / 0 errors / 0 skipped；lint 0 error / 19 warnings；
  APK 重产出同为 11,454,463 bytes，SHA256
  `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`——
  该 SHA256 为**提交前最终复验构建 APK**指纹，与证据 APK 指纹不同，
  两者如实区分、互不替换（复验指纹不得回写为证据指纹）。
- Android 16 `emulator-5554`（API 36，`sdk_gphone64_x86_64`，1080x2400@420dpi）
  loopback mock 冒烟通过：mock 仅绑 127.0.0.1:8000，App 经 10.0.2.2:8000 访问，
  auth disabled；入口/版本/运行快照/审计断言通过；App 仅 6 条预期 GET；
  logcat 4453 行，FATAL EXCEPTION=0、ANR=0、AndroidRuntime 崩溃行=0、本包
  崩溃行=0；AndroidRuntime 75 行为 uiautomator 工具生命周期日志，非崩溃。
  证据 `docs/evidence/m12-05-android-governance/`。

### 合并状态

- **PR #51 已合并**（2026-09-07T05:04:30Z）：base
  `main@b6475c02071c280ec4c154f91989c54db94a432c`，head
  `cd25805e877c9e34f6d31dec61c9df3301e48173`（分支
  `feature/m12-05-android-governance-release`），merge commit
  `8c6fe6b6354c504e8e61beb078bfe2675b7f558a`；功能 head tree 与 merge
  commit tree 均为 `79f0bacfcde7a135e83f6d889475a01a2d33a9b0`（本地原提交
  `7c929f168265a1a67cb5cf49979177f5fb2ec2a2` tree 相同——远端提交经 GitHub
  Git Data API 创建、提交消息尾部换行被去掉致 SHA 不同，非内容差异）。
- PR 变更 32 files changed / 1967 insertions / 6 deletions；PR CI run
  `34085123408` 与 merge 后 main CI run `34085414607` 均
  API/Android/Docker/Web 四项 SUCCESS；远端功能分支已删除；未打 tag、
  未发 GitHub Release、未部署。

### 边界

- 模拟器与固定 mock 数据，不代表真机、真实 provider、生产后端/生产 DB 或
  生产可用。
- 只读 GET，不覆盖治理写路径；未连接真实服务/数据库；不读取/不输出
  key/token/password。
- **PR #51 与 merge 后 main CI 两轮 run（`34085123408` / `34085414607`）
  四项 SUCCESS 已实证**；两轮 CI 不扩大验证范围，仍不代表真机、真实
  provider、生产后端/生产 DB、治理写操作或生产可用。
- 不打 tag、不发 Release、不部署；`production_ready=false` 不变。

## Android loopback 冒烟 harness（M12-06）

### 范围与结构

- 分支 `feature/m12-06-android-smoke-harness`，基于
  `main@4ef912f2e168caa654f74331826aac65afec08d7`；**已随 PR #53 合并**
  （merge commit `4cbdfd2512891b36443c2caa5d967769567fcfac`，合并状态见
  `docs/PROJECT_STATUS.md` M12-06 条目）。
- 仅新增 `tools/android_smoke/`（9 模块）与 `tests/android_smoke/`（9 个
  测试文件，18 files / +4566）；apps/android 主源码与服务端零改动。
- 模块分工：`adb`（设备编排封装）、`interaction`（uiautomator dump 解析 +
  坐标点击）、`server`（loopback mock，纯标准库、仅绑 127.0.0.1）、
  `mock_contract`（预期请求清单与 JSONL 断言）、`logcat_analysis`
  （FATAL/ANR/AndroidRuntime/本包 crash 行纯文本计数，`has_blocking_issue`
  仅由 FATAL/ANR 触发，AndroidRuntime 行仅诊断计数）、`sanitize`（脱敏）、
  `summary`（机器可读 summary.json）、`runner`（15 stage 编排 CLI）。
- mock 请求日志只记 method/path/query_keys/body_len，不记请求 header 与
  body（无凭据面）。

### 最小可复现命令

- 纯 Python 单测（不触网、不依赖真机/adb）：

  ```bash
  python -m pytest tests/android_smoke -q
  ```

  口径 **215 passed / 1 skipped**（skip 为 `test_runner.py:216` Windows
  符号链接需特权、仅显式开启时运行——平台限制非缺陷）。

- 真实模拟器冒烟（前置：adb 在 PATH、Android 模拟器已运行、APK 已构建）：

  ```bash
  python -m tools.android_smoke.runner \
    --serial emulator-5554 \
    --apk apps/android/app/build/outputs/apk/debug/app-debug.apk \
    --port 8000
  ```

  参数：`--serial` 必填；`--output` 显式输出目录（必须不存在，默认
  `.verify/` 下自动生成）；`--host` 仅允许 127.0.0.1；`--skip-install`
  跳过安装；`--keep-output` 保留输出目录。产物为
  `.verify/<output>/summary.json`（15 stage 状态、mock
  expected/unexpected/total、logcat 指标）与截图/dump/mock 请求日志。

- USB 物理设备冒烟（M13-04，前置：`adb devices` 已列出该物理 serial；
  mock 仍只绑定宿主机 `127.0.0.1`，不暴露 LAN）：

  ```bash
  python -m tools.android_smoke.runner \
    --serial <物理serial> \
    --device-type physical \
    --apk apps/android/app/build/outputs/apk/debug/app-debug.apk \
    --port 8000
  ```

  `--device-type`（默认 `emulator`）：`physical` 时在 App 启动前执行
  `adb reverse --no-rebind tcp:<port> tcp:<port>`（设备侧 `127.0.0.1:<port>`
  即宿主机 mock），首次启动后经真实 Settings UI 把 base URL 配成
  `http://127.0.0.1:<port>/`（点输入框 → 光标移末尾连发 DEL 清空 → 输入
  → 保存 → 等「已保存」提示与「当前生效」→ 回首页等 /health 显示「正常」），
  收尾无论成败在 finally 中 `adb reverse --remove` 清理映射。物理模式多出
  `setup-adb-reverse` / `configure-base-url` / `remove-adb-reverse` 三个
  stage；`emulator` 模式行为与 M12-06 完全一致（默认 base URL
  `http://10.0.2.2:8000/`，不触 Settings）。物理 serial 传
  `emulator-*` 会被配置校验拒绝。

- M13-04 物理首跑与两轮修正（证据见
  `.verify/m13-04-android-physical-smoke/physical-EYFBB22923201473{,-r2,-r3}/`，
  gitignored 本地证据）：

  - **首跑（r1）**：安装/reverse/Settings 配置/首页健康检查/tab 巡检/
    搜索执行全通过，但 720x1600 屏上「搜索结果概要」卡在计划卡下方
    视口外——搜索阶段等待超时；随后 USB 掉线，`dump_logcat` 抛错导致
    `_finalize` 失败、summary.json 缺席，已记录的 failed search 阶段与
    reverse 清理状态被掩盖。
  - **修正 1（屏幕相对滚动 + finalize 韧性）**：删除硬编码 swipe 坐标
    （旧值 y=1800 在 1600 高度屏幕上越界），改为 `adb shell wm size`
    实测尺寸（Override 优先）+ 百分比换算（x=50%、75%→25%、400ms；
    在 1080x2400 模拟器上逐值等价于旧坐标）；搜索流程改为执行后先经
    `dumpsys input_method` 按需收起 IME，再滚动结果卡进视口，然后才等
    「搜索结果概要」/「#9001」，回查与治理滚动共用同一相对滚动；
    `_finalize` 捕获 logcat 采集失败——dump-logcat 阶段记 failed
    （缺失的 logcat 不得隐性通过）、stats 置零并标记 `unavailable`，
    summary 照常写出。
  - **修正 2（真机 dump/logcat 加固，r2 暴露）**：真机 uiautomator dump
    含零尺寸 bounds 节点（EMUI 治理页 `pending：3` 的 `[0,0][0,0]`），
    `parse_ui_dump` 由整 dump 抛错改为跳过不可见节点；ANR 匹配由裸
    子串改为词边界（`fileCanRead:false` 的 "CanRead" 内嵌 "anr" 子串
    曾把 dump-logcat 误判成 blocking）。
  - **r3 实跑通过**：serial `EYFBB22923201473`（Huawei MGA-AL00，
    720x1600）18/18 stage 全 passed（8 个启动/配置：wait-device、
    setup-adb-reverse、install-apk、clear-app、clear-logcat、
    start-activity、wait-home、configure-base-url ＋ 5 个 tab 巡检 ＋
    search、governance ＋ 3 个收尾：remove-adb-reverse、mock-contract、
    dump-logcat）、mock expected 19 / unexpected 0、
    logcat 本包 FATAL/ANR/crash 0、`adb reverse --list` 收尾为空；
    模拟器回归（`--device-type emulator`）15 stage 仍全 passed。
    物理验证成立**不改变** `production_ready=false` 边界：mock 数据、
    无真实 provider、无生产后端/DB、无凭据。

- 根 `tests/__init__.py` 约定：仓库根**不放** `tests/__init__.py`——它会让
  根 `tests` 成为常规 Python 包并与 `services/api` 同名测试目录的
  pytest 收集/包导入冲突（首提交曾引入、`e505716` 已删除修复，PR #53
  净 diff 不含该文件）。

### 验证状态与边界

- r8 实跑（`.verify/m12-06-android-smoke-r8/`，gitignored 本地证据）：15
  stage 全 passed、mock expected 18 / unexpected 0 / total 18、logcat 本包
  FATAL/ANR/crash 均 0；AndroidRuntime 标签行 145 为 uiautomator 工具进程
  生命周期日志的仅诊断计数（87 D + 58 I、0 条 FATAL EXCEPTION），不夸大
  为「全局 AndroidRuntime 行为 0」。Gradle 门禁由 CI `Android (unit test /
  lint / assemble)` job 承载（PR run `34132047633` 与 main run
  `34132434939` 均 SUCCESS），本 harness 切片未在本地重跑 Gradle。
- loopback mock 口径：mock 仅绑 127.0.0.1、固定 mock 值，不代表真机、
  真实 provider、生产后端/生产 DB 或生产可用；harness 是测试工具不是
  产品功能；`.verify/` 证据不入库、不可由远端 CI 复核。

## HarmonyOS 只读壳与设置（M13-01）

### 范围与契约

- 分支 `feature/m13-01-harmony-shell-api-auth`，基于
  `main@4cbdfd2512891b36443c2caa5d967769567fcfac`；**已随 PR #54 合并**
  （merge commit `cedf862e4b940d7c637378eea40be2247ba4249d`，合并状态见
  `docs/PROJECT_STATUS.md` M13-01 条目）。
- `apps/harmony`（ArkTS）：bundle `com.ailearningos.app`，EntryAbility +
  `pages/Index` 五 Tab（首页/学习/搜索/语音/设置）；`AiosApi.ets` 只读
  GET 六端点（health / auth-status / privacy / version / ops-snapshot /
  audit?limit=100，`METHOD_GET` 硬编码拒绝其它 method，单次 GET 失败
  如实返回不重试）；`UrlPolicy.ets` base URL 白名单纯函数（仅 http/https、
  禁 userinfo/query/fragment/控制字符/反斜杠）；`SettingsStore.ets` 只存
  base URL（preferences 名 `aios_settings`、唯一键 `api_base_url`，零
  token/password/key）；Study/Search/Voice 只读占位，不虚构功能。
- `module.json5` 仅申请 `ohos.permission.INTERNET`；根 `.gitignore` 新增
  `apps/harmony/entry/build/`（构建产物不入库）。

### 构建/安装/验收（最小可复现，本地模拟器口径）

- 前置：DevEco Studio / hvigor 工具链 + 本地 HarmonyOS 模拟器（验收时
  `hdc -t 127.0.0.1:5557`）。
- clean 构建：在 `apps/harmony` 下 `assembleHap`（BUILD SUCCESSFUL，产物
  `entry/build/default/outputs/default/entry-default-unsigned.hap`，
  190,715 bytes）。
- 安装与启动：`hdc -t 127.0.0.1:5557 install
  entry-default-unsigned.hap`（未签名 HAP 直装）→ `aa start` 拉起
  `com.ailearningos.app`。
- 验收：依次点击五个 Tab，每 Tab 采集截图 + layout 转储；Home 页上滑
  验证审计日志区滚入可见。完整步骤、六张 1320x2856 截图清单与已知警告见
  `docs/evidence/m13-01-harmony-shell-api-auth/README.md`。

### 边界

- 未签名 HAP 直装是本地验收形态，不构成发布形态（签名配置留待 AGC/正式
  流程）；验收环境为本地模拟器，非真机。
- 仅 INTERNET 权限、只读 GET、只存 base URL；未接真实 provider、生产
  DB、生产后端与治理写链路；Home 区块「网络请求失败」为无后端时的如实
  失败（错误态 + 重试，非崩溃）。
- CI 无 HarmonyOS job（四项仍为 API/Android/Docker/Web），HarmonyOS
  构建与验收为本地口径，不构成远端 CI 结论。
- 不打 tag、不发 Release、不部署；不读取/不输出 key/token/password；
  `production_ready=false` 不变。

## HarmonyOS Home mock 数据与降级验收（M13-02）

### 范围与契约

- 分支 `feature/m13-02-harmony-mock-data`，基于 `main@a88ed80a20bbd2d6961d53f29407f9c59aca859f`；本切片只新增验收工具，不改 Harmony 生产源码。
- `tools/harmony_mock/server.py` 复用 `tools.android_smoke.mock_contract.ReadOnlyMockContract`，仅暴露 M13-01 Home 六个 GET 端点：`/health`、`/api/v1/auth/status`、`/api/v1/system/privacy`、`/api/v1/version`、`/api/v1/system/ops-snapshot`、`/api/v1/audit?limit=100`。
- 非 GET 或未支持 method（如 HEAD/OPTIONS/FOO）、未知路径、audit 缺少/非 `limit=100` 或含多余 query 均返回 404；纯 Python 标准库，不触网，不读取密钥，不记录请求 header/body。
- 默认绑定 `127.0.0.1`；仅本地 HarmonyOS 模拟器验收显式使用 `--host 0.0.0.0`，不得用于生产。
- 入库证据为 `docs/evidence/m13-02-harmony-mock-data/README.md` 与 `fixtures/mock_responses.json`；原始验收证据在 gitignored `.verify/m13-02-harmony-mock-data/`，不入库。

### 运行与复验

```powershell
python tools/harmony_mock/test_contract.py --host 127.0.0.1 --port 18765
python tools/harmony_mock/test_contract.py --host 127.0.0.1 --port 28765
python -m py_compile tools/harmony_mock/server.py tools/harmony_mock/test_contract.py

# 仅本地模拟器验收；不要在生产暴露该服务
python tools/harmony_mock/server.py --host 0.0.0.0 --port 8765
```

### 验收事实（2026-09-07）

- 契约测试实现时首验：端口 18765 与 28765 均 **15/15 passed**；Codex 审查发现未支持的 HEAD/OPTIONS/FOO 曾落入 501，补充三个未支持 method 负例并修复 fail-closed 501 缺口后，最终两端口复验均 **18/18 passed**；`python -m py_compile` 通过。
- 在单次 PowerShell 进程内设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk` 后，clean `assembleHap` 通过；未签名 HAP 194715 bytes，SHA256 `990906DA88FEC6F769705106BE156FDA33F2CD34576B300A3E566BEBF4A1CA30`。
- `hdc -t 127.0.0.1:5557 install` 安装成功，`aa start` 启动成功；重启后 App PID `22710`。
- HarmonyOS 模拟器网络中宿主地址使用 `http://192.168.8.3:8765/`；通过 Settings 真实 UI 保存并点击「测试连接」，layout 显示 `连接成功: aios-mock-android-smoke`。
- 重启后 Home 六区均渲染 mock 数据：服务健康 `status: ok` / `service: aios-mock-android-smoke`；认证为本地模式（认证未开启）；隐私模式 local/local/local/关/关；版本 `0.12.5-mock` / `m1205mockgit` / `m1205mockrev`；运维快照 `postgresql`、papers 34、search 456、audit total 1024、worker 是；审计显示 `#1001 paper.publish` 与 `#1002 worker.tick`，before/after 载荷不渲染。
- 停止 mock 监听并释放 8765 端口后点击「整体刷新」，六区最终显示 `网络请求失败: [object Object]` 并保留各自「重试」按钮；App PID 保持 `22710` 未崩溃，学习/搜索/语音/设置四 Tab 仍可切换。

### 已知生命周期边界

`HomePane` 仅在 `aboutToAppear` 读取一次 base URL；Settings 保存新地址后，已挂载的 Home 不会自动刷新该地址。M13-02 按当前设计重启 App 完成验证，不在本切片修改生产源码；该问题列为后续独立优化项。（注：该生命周期缺口已由 M13-03 修复，见下方 M13-03 节。）

### 边界

本验收是本地模拟器 + mock only 口径，不代表真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用。未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名。CI 无 HarmonyOS job，后续 PR 的 API/Android/Docker/Web 结果不能扩大为 HarmonyOS 远端验证；`production_ready=false` 语义不变。以上为提交前本地验收快照；远端 PR/CI/合并状态以后续 PROJECT_STATUS 回填为准。

## HarmonyOS Home 地址变更自动刷新（M13-03）

### 范围与契约

- 分支 `feature/m13-03-harmony-home-refresh`，基于 `origin/main@0b125319f8b2ef4f05428d75925e8293c3e8d4a6`（PR #56 merge commit，本地 git 可验证）；修复 M13-02 记录的已知生命周期缺口——Settings 保存新 base URL 后，已挂载的 Home 不会自动刷新该地址。
- 生产改动仅两个文件（`apps/harmony/entry/src/main/ets/components/`）：`SettingsStore.ets` 与 `HomePane.ets`；服务端 / Android / Web / infra / mock 工具零改动。
- 事件机制（官方 `@kit.BasicServicesKit` `emitter`，进程内）：
  - 事件 ID 集中由 SettingsStore 导出：`EVENT_ID_URL_CHANGED = 'aios://settings/url_changed'`；订阅方（HomePane）只导入，不自持字符串。
  - SettingsStore 仅在 UrlPolicy 校验、preferences `put`、`flush` 三步全部成功后才 `emitter.emit`；校验失败不写盘不发事件，写入/flush 失败如实报错同样不发事件。emit 附带的 URL payload 仅作观测用途，订阅方不信赖。
  - HomePane 持有一个稳定回调引用（类字段 `urlChangeListener`）；`aboutToAppear` 先 `off` 再 `on` 防止重复注册堆叠；`aboutToDisappear` 使用带回调的 `emitter.off` 精确退订，仅移除本组件订阅、不影响其他订阅者。
  - 回调不信任事件 payload：始终 `loadBaseUrl(context)` 重读持久化存储作为权威来源（防御其他写入者），更新地址显示后 `refreshAll()`。
  - `aboutToAppear` 仍只做一次初始 URL 加载（`loadBaseUrl`）与一次初始刷新（`refreshAll`）；事件路径不重复首刷语义。
- 入库证据为 `docs/evidence/m13-03-harmony-home-refresh/README.md`（唯一入库文件）；原始验收证据在 gitignored `.verify/m13-03-harmony-home-refresh/`，不入库。

### 构建与验收（最小可复现，本地模拟器口径）

- 前置：DevEco Studio / hvigor 工具链 + 本地 HarmonyOS 模拟器（验收时 `hdc -t 127.0.0.1:5557`）；在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`。
- clean 构建：`hvigorw.bat clean --no-daemon` → `hvigorw.bat assembleHap --no-daemon`，均 exit 0 / BUILD SUCCESSFUL；产物 `entry-default-unsigned.hap` 197,338 bytes，SHA256 `C06327CED5973DD5A634E8A974C2DF9C26D6991CBF815F2BE9416BC8C1938960`。
- 正向流（全程不重启 App）：全新安装首启默认 `http://127.0.0.1:8000`，六区真实网络错误态；supervisor 仅为本运行启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`，模拟器使用 `http://192.168.8.3:8766/`；真实 Settings UI 经系统文本菜单清除旧地址、输入 mock URL、点击「保存」（`已保存: http://192.168.8.3:8766/`）；返回已挂载 Home，`服务地址` 与六区刷新为 mock 数据（服务健康/认证/隐私/版本/运维/审计，before/after 载荷不渲染）。
- 负向流（同样不重启 App）：停止 mock（端口无残留监听）；Settings 清除有效地址输入 `javascript:alert` 被拒（`URL 校验失败 [4]: 缺少 scheme:// (仅允许 http/https)`）；已挂载 Home 保留先前数据、不刷新为网络错误态——证明被拒保存未写盘也未 emit 事件。
- Stage A 干净首屏：同一 M13-03 HAP 卸载重装后重采首屏与滚动视口，六区全覆盖真实错误态，无 mock 数据。
- 完整证据清单、断言明细与截图/布局文件对应关系见 `docs/evidence/m13-03-harmony-home-refresh/README.md`。

### 边界

本验收是本地模拟器 + mock only 口径，不代表真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用。未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名。CI 无 HarmonyOS job，后续 PR 的 API/Android/Docker/Web 结果不能扩大为 HarmonyOS 远端验证；不打 tag、不发 Release、不部署；`production_ready=false` 语义不变。以上为提交前本地验收快照；远端 PR/CI/合并状态以后续 PROJECT_STATUS 回填为准。

## HarmonyOS Study 论文只读列表（M13-05）

### 范围与契约

- 分支 `feature/m13-05-harmony-study-papers`，基于 `origin/main@7f344bf8251deb88c7ff917ddf8d9d2f2575c3ce`（PR #59 merge commit，本地 git 可验证）；实现时点分支零本地提交，代码/测试/文档改动全部位于工作区（未 commit、未 push、未开 PR）。
- 目标：把 M13-01 学习 Tab 的静态只读占位升级为论文只读列表——HarmonyOS 各业务域只读接入（评估项「学习」）的第一切片；服务端 / Android / Web / infra 零改动。
- 生产改动三个文件（`apps/harmony/entry/src/main/ets/`）：`AiosApi.ets` 新增第 7 个只读端点 `GET /api/v1/papers` 与 `PaperOut` DTO（可空字段对齐共享契约）、导出 `getPapers`，仍硬编码仅 GET；`StudyPane.ets` 升级为单一列表状态机 LOADING/EMPTY/SUCCESS/ERROR（EMPTY=HTTP 成功零条；ERROR 展示原始错误并带「重试」；SUCCESS 渲染存在字段带「刷新」，空可选字段省略渲染、存在时完整渲染），`aboutToAppear` 一次初始加载，订阅 SettingsStore 既有 `aios://settings/url_changed` 事件（稳定回调、先 off 再 on、带回调 `emitter.off` 精确退订、回调内以 `loadBaseUrl` 重读持久化为权威来源，显式 null/undefined 检查无非空断言；hmharness 稳定性修复：元数据行 Flex(Wrap) 窄屏自动换行、标题/副题/元数据 maxLines + TextOverflow.Ellipsis、`disposed` 守卫（组件销毁后异步回调立即返回）、请求代际守卫（`requestGeneration` 递增，过期响应丢弃））；`Index.ets` 仅注释与接线说明更新。全程只读：无考试/答题/评分/答案缓存/解释/路由/麦克风/存储/凭据逻辑。
- 共享 mock 契约：`tools/android_smoke/mock_contract.py` 新增确定性 3 篇论文 fixture（Attention Is All You Need / BERT / ImageNet Classification，paper-003 `origin_url: null`），`GET /api/v1/papers` 200 顶层数组、`POST /api/v1/papers` 404（原「GET /api/v1/papers 返回 404」断言移除）；`tools/harmony_mock/server.py` 允许路径加入该端点；两份测试文件相应扩为契约 20 项与 papers 正/负例。改动面合计 7 files，+449/−41（不含文档）。
- 入库证据为 `docs/evidence/m13-05-harmony-study-papers/README.md`（唯一入库文件）；原始验收证据在 gitignored `.verify/m13-05-harmony-study-papers/`，不入库。

### 构建与验收（最小可复现，本地模拟器口径）

- 本地测试：`python tools/harmony_mock/test_contract.py` **20/20 passed**（含 M13-05 papers 正例与 POST 404 负例）；`pytest tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py` **28 passed**。
- clean 构建（初始 Stage 3 时点，3A/3B/3C 证据对应）：`hvigorw.bat clean --no-daemon` → `hvigorw.bat assembleHap --no-daemon`，均 exit 0 / BUILD SUCCESSFUL；产物 `entry-default-unsigned.hap` 240231 bytes，SHA256 `E4255F488C06FAB755F0CA844F407E077533332485737E7EDE1D9DD50FD36B3A`；已知非阻塞警告与 M13-01/M13-02/M13-03 基线一致（无显式 `targetSdkVersion`、无签名配置、`SettingsStore` may-throw 静态提示）。
- 最终 supervisor 复验（hmharness 稳定性修复后，`final-*` 证据对应）：以全局 `hvigorw.bat`（在 `apps/harmony`，PowerShell 进程内 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`）重新 `clean` + `assembleHap`，均 BUILD SUCCESSFUL；最终 HAP 255674 bytes，SHA256 `3E3CF7CBB3160F15FE8A78240F24F4D1036AC6771E90C42E962DC532CB958E0D`；已知警告仍仅为同一基线三项。fresh install（App PID 15016）全流程重验通过：默认 URL 真实错误态 + 「重试」→ 真实 Settings UI 保存 `http://192.168.8.3:8766/` → **App 不重启**，学习 Tab 渲染全部 3 篇论文 → 停 mock 后「刷新」转错误态 + 「重试」且论文消失 → 重启 mock 后「重试」3 篇全部恢复。证据为 `final-*` 系列（`final-positive.json/.jpeg` 为弃用中间态——该 dump 仍为 Settings 页布局，不作正向证据；`final-positive2.json` 为采信正向证明），清单见 evidence README。
- Stage 3A（默认地址错误态）：模拟器 `127.0.0.1:5557` 全新安装首启，学习 Tab 在默认 `http://127.0.0.1:8000` 下为真实网络错误态（`网络请求失败: [object Object]` + 「重试」，无论文标题渲染）。
- Stage 3B（Settings→Study 正向流）：supervisor 仅为本次运行在 `0.0.0.0:8766` 启动 mock，模拟器侧使用 `http://192.168.8.3:8766/`；真实 Settings UI 保存成功（`已保存: http://192.168.8.3:8766/`）；**App 不重启**，返回进入学习 Tab 即显示新服务地址并刷新为三篇确定性论文（学科/难度/时长/标签全渲染；paper-001/002 显示 arxiv 原文链接，paper-003 `origin_url: null` 不渲染链接；无「重试」按钮）。
- Stage 3C（错误与恢复流）：精确停止 mock（进程与 8766 端口释放实证）后点「刷新」→ 错误态（`网络请求失败: [object Object]` + 「重试」，三篇论文消失）；重启 mock（监听 PID 与端口实证）后点「重试」→ 三篇论文恢复；收尾全部 mock 进程停止、端口释放实证。
- UI 自动化输入备注：坐标式 `uitest uiInput inputText x y <text>` 在 TextInput 上表现为追加/重复畸形文本，不可用；最终成功方法为系统文本菜单「全选/剪切」清空后聚焦输入框，用 `uitest uiInput text <url>` 整段输入并 dump 核对与目标完全一致。
- 完整证据清单、断言明细与截图/布局文件对应关系见 `docs/evidence/m13-05-harmony-study-papers/README.md`。

### 边界

本验收是本地模拟器 + mock only 口径，不代表真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用；无任何写路径。空态（EMPTY）在代码中存在但未做 UI 覆盖（mock 恒返 3 篇论文）；滚动行为未测（三篇均落首屏）。mock 逐请求日志刻意关闭（日志仅含启动监听单行消息），验收依据为确定性 UI 状态迁移而非请求日志。错误文案 `网络请求失败: [object Object]` 为 UX 跟进项。未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名。CI 无 HarmonyOS job，后续 PR 的 API/Android/Docker/Web 结果不能扩大为 HarmonyOS 远端验证。仓库状态（文档时点）：分支未 commit、未 push、未开 PR，无远端 CI run、未合并、未打 tag、未部署；`production_ready=false` 语义不变。以上为提交前本地验收快照；远端 PR/CI/合并状态以后续 PROJECT_STATUS 回填为准。

## HarmonyOS Search providers 只读列表（M13-06）

### 范围与契约

- 分支 `feature/m13-06-harmony-search-providers`，基于 `origin/main@34d30ec4d9afee15dda037713ab9867f3e33d1f8`（PR #60 merge commit（M13-05），其 merge 后 main CI 四项 job 全绿，本地 git 可验证）；已随 PR #61 合并——PR head 902fbda，PR CI run 34218137511 四项 success；merge commit 8d4445a（2026-09-08T14:29:11Z），merge 后 main CI run 34238561233 四项 success。
- 目标：把 M13-01 搜索 Tab 静态只读占位升级为 providers 只读列表——HarmonyOS 各业务域只读接入（评估项「搜索」）的第一切片。仅新增 `GET /api/v1/search/providers` 一个只读端点；M12-04 其余 search 端点（`plan`/`queries`/`queries/{id}`）对 Harmony 一律 404；无搜索发起/预览/回查、无认证、无真实 provider、无生产后端/生产 DB、无任何写路径；服务端 / Android / Web / infra 零改动。
- 生产改动三个文件（`apps/harmony/entry/src/main/ets/`）：`AiosApi.ets` 新增第 8 个只读端点 `AiosEndpoint.SEARCH_PROVIDERS = '/api/v1/search/providers'`（仍硬编码仅 GET）与 `SearchProviderOut`/`SearchProvidersData` DTO（`name`/`kind`/`enabled`/`unavailable_reason: string | null`，字段与共享 mock 契约精确对齐）、导出 `getSearchProviders`；`SearchPane.ets` 升级为单一列表状态机 LOADING/EMPTY/SUCCESS/ERROR（EMPTY=HTTP 成功零条；ERROR 展示原始错误并带「重试」；SUCCESS 渲染 provider 名称/kind/启用态并带「刷新」，`unavailable_reason` 仅非 null 时展示、`enabled=false` 且原因为空时「未启用」兜底），`aboutToAppear` 一次初始加载，订阅 SettingsStore 既有 `aios://settings/url_changed` 事件（稳定回调、先 off 再 on、带回调 `emitter.off` 精确退订、回调内以 `loadBaseUrl` 重读持久化为权威来源），`disposed` 守卫与请求代际守卫（过期响应丢弃），元数据 Flex(Wrap) 窄屏换行，显式 null/undefined 检查无非空断言；`Index.ets` 仅注释与接线说明更新。全程只读：无写请求、无凭据、不访问麦克风/存储。
- mock 契约与工具：`tools/harmony_mock/server.py` 允许路径加入 `/api/v1/search/providers`，并收紧 fail-closed——允许清单 GET 端点拒绝一切查询串，唯一例外 audit 整串须精确为 `?limit=100` 方才委托共享契约；`tools/harmony_mock/test_contract.py` 契约测试 20 → 30 项（providers 正例（按稳定 name/kind 定位禁用项校验 `enabled=false` + 非空 `unavailable_reason`）、providers POST 与 `?foo=bar`/空查询串 `?` 负例、其余 search 端点 POST 与 GET 形式负例、audit 精确查询串负例组，usage 补 `--host`）；`.gitignore` 新增 `/.hvigor/`。改动面合计 6 files，+400/−33（3 个 Harmony 生产文件 + 2 个 mock 工具 + 1 个 `.gitignore`，不含文档）。
- 入库证据为 `docs/evidence/m13-06-harmony-search-providers/README.md`（唯一入库文件）；原始验收证据在 gitignored `.verify/m13-06-harmony-search-providers/`，不入库。

### 构建与验收（最小可复现，本地模拟器口径）

- 本地测试：`python tools/harmony_mock/test_contract.py --host 127.0.0.1 --port 18765` **30/30 passed**（exit 0；8 正例 + 22 负例，全部 fail-closed）；Android 冒烟 Python 单测 **287 passed / 1 skipped**（skip 为既有 Windows symlink 特权测试；共享契约变更未破坏 Android 侧）。
- clean 构建：在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，`hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon` 均 exit 0 / BUILD SUCCESSFUL；产物 `entry-default-unsigned.hap` 298153 bytes，SHA256 `7D31E3F43F4A2547F2299184D64AA14ADC8735758963F362B1DA46643BF23297`；已知非阻塞警告与 M13-01/M13-02/M13-03/M13-05 基线一致（无显式 `targetSdkVersion`、无签名配置、`SettingsStore` may-throw 静态提示）。
- Stage A（默认地址错误态）：模拟器 `127.0.0.1:5557` 全新安装首启，搜索 Tab 在默认 `http://127.0.0.1:8000` 下为真实网络错误态（`网络请求失败: [object Object]` + 「重试」，无任何 provider 渲染）。
- Stage B（Settings→Search 正向流）：supervisor 仅为本次运行在 `0.0.0.0:8766` 启动 mock，模拟器侧使用 `http://192.168.8.3:8766/`；真实 Settings UI 输入并保存成功（`已保存` 实证）；**App 不重启**，返回搜索 Tab 即显示 `local-corpus`（enabled）与 `cloud-web`（disabled，如实展示 mock 固定 `unavailable_reason`「未配置云端检索通道（mock 固定禁用，验证不可用原因如实展示）」）。
- Stage C（错误与恢复流）：停止 mock（8766 端口释放实证）后点「刷新」→ 错误态（原始错误 + 「重试」），两个 provider 消失；重启 mock（监听实证）后点「重试」→ 两个 provider 恢复；全流程 App PID 保持 28168 不变（无重启/崩溃）；收尾 8766 端口监听数 0（`port-proof-after-stop.txt` / `final-port-proof-after-stop.txt`）。
- 完整证据清单、断言明细与截图/布局文件对应关系见 `docs/evidence/m13-06-harmony-search-providers/README.md`。

### 边界

本验收是本地模拟器 + mock only 口径，不代表真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用；无任何写路径、无凭据。空态（EMPTY）在代码中存在但未做 UI 覆盖（mock 恒返 2 个 provider）。mock 逐请求日志刻意关闭（日志仅含启动监听单行消息），验收依据为确定性 UI 状态迁移而非请求日志。错误文案 `网络请求失败: [object Object]` 为 UX 跟进项。未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名。CI 无 HarmonyOS job，本地 HarmonyOS 验收不构成远端 CI 验证，后续 PR 的 API/Android/Docker/Web 结果不能扩大为 HarmonyOS 远端验证。仓库状态（回填时点）：PR #61 已合并，PR CI run 34218137511 与 merge 后 main CI run 34238561233 四项 success；未打 tag、未部署；`production_ready=false` 语义不变。本地验收事实保持时点化；PR/merge 与 main CI 状态已在本节回填。

## HarmonyOS Voice providers 只读面板（M13-07）

### 范围与契约

- 分支 `feature/m13-07-harmony-voice-providers`，基于 `origin/main@5ef95b29872bf5e50d839ef480f98093f8c195ec`（PR #62 merge commit（M13-06 状态回填），本地 git 可验证）；已随 **PR #63** 合并 main——merged_at `2026-09-08T16:45:25Z`，PR head `d201b062f86a2179fa5d8421053e4ece478971a7`、merge commit `6c72c75dc3f09e9aeb29f683554f00410102075d`（本地 git 可验证；本地 worktree 提交 `4bdab4c42393b10c19df1c3bec358324d1533aa4` 与 PR head 的 tree 逐字节一致，均为 `392e04d2c23450ddf4f8ba31c0c2c239e2a5e043`，commit SHA 不同）。
- 目标：把 M13-01 语音 Tab 静态只读占位升级为 providers 只读面板——HarmonyOS 各业务域只读接入（评估项「语音」）的第一切片。仅新增 `GET /api/v1/voice/providers` 一个只读端点；其余 voice 端点（`token`/`sessions`（含列表与详情 GET）/`transcribe`/`synthesize`/`trace`（含 GET 形式））对 Harmony 一律 404；无麦克风访问、无 ASR/TTS 执行、无语音会话、无认证、无真实 provider、无生产后端/生产 DB、无任何写路径——不构成任何真实语音能力；服务端 / Android / Web / infra 零改动。
- 生产改动两个文件（`apps/harmony/entry/src/main/ets/`）：`AiosApi.ets` 新增第 9 个只读端点 `AiosEndpoint.VOICE_PROVIDERS = '/api/v1/voice/providers'`（仍硬编码仅 GET）与 `VoiceProviderViewOut`（`requested: string | null`/`provider`/`fallback`）、`VoiceProvidersData { voice_mode, asr, tts, privacy_store_audio, privacy_send_context_to_cloud }` DTO（snake_case 与 Android `VoiceDtos.kt VoiceProvidersResponse` 精确对齐）、导出 `getVoiceProviders`；`VoicePane.ets` 升级为 LOADING/SUCCESS/ERROR 状态机（响应是单对象无有意义空态；ERROR 展示原始错误并带「重试」，成功视图带「刷新」；成功视图渲染 voice_mode、ASR/TTS 卡片（`fallback=true` 展示「已回退」徽标、`requested` 仅非 null 时展示）与隐私开关事实标签（无开关控件）；响应字段类型校验（voice_mode/asr/tts/provider/requested/fallback/privacy 布尔任一缺失或类型不符即错误态如实报错）），`aboutToAppear` 一次初始加载，订阅 SettingsStore 既有 `aios://settings/url_changed` 事件（稳定回调、先 off 再 on、带回调 `emitter.off` 精确退订、回调内以 `loadBaseUrl` 重读持久化为权威来源），`disposed` 守卫与请求代际守卫，元数据 Flex(Wrap) 窄屏换行。全程只读：无写请求、无凭据、不访问麦克风/存储。
- mock 契约与工具：`tools/android_smoke/mock_contract.py` 新增 `VOICE_PROVIDERS` 确定性快照（`voice_mode=hybrid`、ASR `fake` 无回退、TTS 请求 `cloud-openai-tts` 回退 `tone`、`privacy_store_audio=False`、`privacy_send_context_to_cloud=True`，与 Android `VoiceProvidersResponse` 逐字段一一对应）；`tools/harmony_mock/server.py` 允许路径加入该端点（其余 voice 路径一律 404）；`tools/harmony_mock/test_contract.py` 契约测试 30 → 42 项（voice providers 正例整包精确相等 + 11 项负例：仅 GET、`?foo=bar`/空查询串拒绝、其余 voice 端点写方法与 GET 形式全 404）；`tests/android_smoke/test_mock_contract.py` +3、`tests/android_smoke/test_server.py` +1。改动面合计 7 files，+525/−31（2 个 Harmony 生产文件 + 2 个 mock 工具 + 3 个测试文件，不含文档）。
- 入库证据为 `docs/evidence/m13-07-harmony-voice-providers/README.md`（唯一入库文件）；原始验收证据在 gitignored `.verify/m13-07-harmony-voice-providers/`，不入库。

### 构建与验收（最小可复现，本地模拟器口径）

- 本地测试：`python tools/harmony_mock/test_contract.py` **42/42 passed**（exit 0；9 正例 + 33 负例，全部 fail-closed）；`pytest tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py` **32 passed**（21+11 个测试函数，共享契约变更未破坏 Android 侧）。
- clean 构建与产物演进：`apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，`hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon` 均 BUILD SUCCESSFUL，初始产物 347823 bytes、SHA256 `1BF9B0BC1DED9B855F083AA66EB2228B15BFCBF0ED874B6204F7EEBF66F6AF45`（2026-09-08 23:42，Stage A/B/C 验收对象）；构建后一次源码编辑在 `VoicePane.ets loadProviders` 留下同作用域重复的 `const d` 声明（未进入任何已验证产物），该行删除后 hmharness 与 Codex 各自独立重跑 `assembleHap` 均 exit 0 / BUILD SUCCESSFUL / 0 compiler errors，最终产物 `entry-default-unsigned.hap` **348946 bytes、SHA256 `970241F92ECE4736D90D8A7ACE9025FD9D2548FA7991911172353DB54CC5C283`**（字节数与 SHA256 经 finisher 会话 stat/sha256sum 独立复核一致）；已知非阻塞警告与 M13-01/02/03/05/06 基线一致（无显式 `targetSdkVersion`、无签名配置、`SettingsStore` may-throw 静态提示）。
- Stage A（默认地址错误态）：模拟器 `127.0.0.1:5557` 全新卸载重装首启（App PID 19960），语音 Tab 在默认 `http://127.0.0.1:8000` 下为真实网络错误态（`网络请求失败: [object Object]` + 「重试」，无任何 provider/voice_mode 渲染）。
- Stage B（Settings→Voice 正向流）：supervisor 仅为本次运行在 `0.0.0.0:8766` 启动 mock；真实 Settings UI 输入先出现污染串 `http://127.0.0.1:8000http://192.168.8.3:8766/`，以 HarmonyOS `KEYCODE_DEL=2055` 逐字清除后整段输入并核对为精确 `http://192.168.8.3:8766/`，保存成功（「已保存」实证）、收起键盘；**App 不重启（PID 保持 19960）**，切到语音 Tab 即显示 `语音模式 (voice_mode) hybrid`、ASR `Provider: fake`（requested 为 null 不展示、无回退徽标）、TTS「已回退」+ `Provider: tone` + `Requested: cloud-openai-tts`、`存储音频 (privacy_store_audio): 关`、`发送上下文到云端 (privacy_send_context_to_cloud): 开`（采信 `stage-b-voice-positive2.json/.png`，早期 stage-b 文件为过程证据）。
- Stage C（错误与恢复流）：仅停止已核验的 M13-07 mock 进程树（8766 监听数 0 实证）后点「刷新」：短暂加载态后**约 33 秒**显示 `网络请求失败: [object Object]` + 「重试」，provider 面板全部消失（采信 `stage-c-voice-error-33s.json/.png`；紧邻的 `stage-c-voice-error.*` 为过程证据）；重启同一 mock 后点「重试」→ 完整 provider 面板恢复（`stage-c-voice-recovered.json/.png`）；App PID 保持 19960；收尾再次全停 mock，8766 监听数 0（finisher 会话另以 netstat 独立复核）。
- 当前产物复验：348946 bytes 最终 HAP 由 Codex 在 `127.0.0.1:5557` 卸载重装并启动（App PID 8444），同一 UI 路径 Settings 精确设置 `http://192.168.8.3:8766/` 后语音 Tab 渲染全部预期字段且与 Stage B 采信证据逐项一致（`rebuild-url.json`/`rebuild-positive.json/.png`，JSON 布局转储经独立复核）；复验后 mock 清理 8766 listeners=0。
- 完整证据清单、断言明细与截图/布局文件对应关系见 `docs/evidence/m13-07-harmony-voice-providers/README.md`。

### 边界

本验收是本地模拟器 + mock only 口径，不代表真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用；无任何写路径、无凭据、不访问麦克风/音频/存储，不构成任何真实语音能力（ASR/TTS 执行）。mock 逐请求日志刻意关闭（日志仅含启动监听单行消息），验收依据为确定性 UI 状态迁移而非请求日志。**停服后约 33 秒才转入错误态的时延与 `[object Object]` 原始错误文案合并记为非阻塞 UX 加固跟进项——本验收仅确认「最终能如实进入错误态并可恢复」，不计为已通过的 UX 质量项。**非阻塞代码加固跟进项另行跟踪：①隐私/嵌套字段类型校验可更完整；②避免把缺失布尔渲染为「关」（渲染层三元形式对未来未守卫布尔字段有此风险）；③`AiosApi.ets` 的 `VoiceProvidersData` mock 示例注释过时（写 `privacy_send_context_to_cloud: false`/`voice_mode: "local"`，实际 fixture 为 true/hybrid）。未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名。CI 无 HarmonyOS job，本地 HarmonyOS 验收不构成远端 CI 验证，后续 PR 的 API/Android/Docker/Web 结果不能扩大为 HarmonyOS 远端验证。仓库状态（已回填 2026-09-09）：**PR #63 已合并 main**——merged_at `2026-09-08T16:45:25Z`，PR head `d201b062f86a2179fa5d8421053e4ece478971a7`、merge commit `6c72c75dc3f09e9aeb29f683554f00410102075d`（本地 git 可验证），PR CI run `34252437713` 与 merge 后 main CI run `34253004195` 四项 job（API/Android/Docker/Web）全部 success；未打 tag、未部署；`production_ready=false` 语义不变。本地验收事实保持时点化；远端 PR/merge 与 main CI 状态已在本节回填。

## HarmonyOS 考试会话只读快照（M13-08）

### 范围与契约

- 分支 `feature/m13-08-harmony-exam-readonly`，基于 PR #63 merge commit `6c72c75dc3f09e9aeb29f683554f00410102075d` 之上的本地 M13-07 状态回填提交 `be97a72`（本地 git 可验证）；仓库状态（回填 2026-09-09）：**已随 PR #65 合并 main**——PR head `9e1e64fe0c3bd89dbd3fc03f85a221d0a48fa9b9`，PR CI run `34284891184` 四项 job（API/Android/Docker/Web）全部 success；merge commit `31b626926450618468dbe5c10064b6a6eb6f4506`，merge 后 main CI run `34285331863` 四项 job 全部 success；远端功能分支已删除；本地两个提交（`47ad110` mock 契约先行、`0d295fe` 生产 UI/API/docs）的远端等价提交为 `c0806267854fcddfc5a6084bae0342cb461b4a75` 与 `9e1e64fe0c3bd89dbd3fc03f85a221d0a48fa9b9`，commit SHA 不同、tree 逐字节一致（`da7dc8af2743a3beebd9fd64d8ba12aa64fa0b8a` 与 `d777ff02e206b3b1e359cfed43c99d12a7227190`，本地 git 可验证）。
- 目标：把考试只读能力接入 HarmonyOS——HarmonyOS 各业务域只读接入（评估项「考试」）的第一切片。仅新增 `GET /api/v1/exams/{exam_id}` 一个只读**动态路径**端点；其余 exam 端点（`POST /api/v1/papers/{paper_id}/exams`、PUT 作答、`POST` 提交、`GET` submission/report）对 Harmony 一律 404；无作答/改答控件、无提交、无倒计时或计时器、无正确答案/解析展示（公共投影本就不含）、无认证、无真实考试会话、无生产后端/生产 DB、无麦克风/存储访问、无任何写路径——不构成任何考试作答能力；服务端 / Android / Web / infra 零改动。
- 生产改动两个文件（`apps/harmony/entry/src/main/ets/`）：`AiosApi.ets` 新增第 10 个只读端点——首个动态路径 `GET /api/v1/exams/{exam_id}`（`EXAM_PATH_PREFIX = '/api/v1/exams/'` + 校验编码拼接，刻意不进 `AiosEndpoint` 固定路径枚举，避免把「半个路径」当端点直接请求）与 `ExamSessionData`（`exam_id`/`paper_id`/`paper_title`/`mode`/`status`/`server_started_at`/`server_end_at`/`server_remaining_seconds`/`questions[]`/`answers: Record<string,string>`/`next_sequence`）、`PublicQuestionOut`（`id`/`type`/`stem`/`options[]`）、`QuestionOptionOut`（`key`/`text`）DTO（snake_case 与 mock `EXAM_SESSION` 及 Android `ExamDtos.kt` 逐字段对齐）、导出 `getExamSession(baseUrl, examId)`——examId 发起请求前白名单校验：trim → 非空 → ≤128 字符 → 拒绝 C0/DEL/C1 控制字符 → 拒绝 `/ ? #` → 仅允许 `[A-Za-z0-9._:-]`，任一不符直接返回 `ApiResult.ok=false`（中文错误，**不发起任何网络请求**），合法再 `encodeURIComponent` 编码拼入路径；仍硬编码仅 GET、单次请求、无重试、无写入、无凭据处理。`StudyPane.ets` 论文区（M13-05）**行为保持不变**（状态机/字段渲染/URL 订阅自动刷新），新增完全独立的考试只读区：独立状态机 `ExamSessionStatus` IDLE/LOADING/SUCCESS/ERROR（与论文区 `ListStatus` 无任何耦合）；独立 `examRequestGeneration` 代次守卫 + `disposed` 守卫（过期考试响应不得覆盖较新考试状态、也不得触碰论文区状态）；每次「查询/重试」恰好一次 `getExamSession`（无自动重试循环，LOADING 中按钮禁用）；成功先做契约形状校验（七个标量字符串字段、两个数字字段、questions 数组逐题逐选项、answers 对象逐值，任一不符一律 fail closed 转错误态并给中文错误）；成功视图渲染公共元数据（exam_id/paper_id/mode/status/server_remaining_seconds/next_sequence）与全部公共题目（id/type/stem/每选项 key/text）；`answers` 仅作「学员已保存作答」展示并**明确标注非正确答案**（未作答如实显示「暂无」，题目列表之外的键不静默丢弃）；不渲染正确答案/解析、无作答控件/提交按钮/倒计时（server_remaining_seconds 为服务端时间窗剩余秒数的静态只读快照，页面注明不做倒计时）；URL 变更（既有 `aios://settings/url_changed` 订阅）时论文区照旧自动刷新、考试区**立即失效**——`invalidateExamSession()` 使考试代次 +1 并清空数据/状态/错误，展示中文提示「服务地址已变更，当前考试会话已失效（旧地址数据已清除），请用新地址重新查询。」，不自动重查（由用户显式重新查询）；布局调整为整页单个纵向 Scroll（论文区在上、考试区在下，两区各自完整可用；论文区成功列表不再嵌套内层 Scroll），多行文本 maxLines + Ellipsis、元数据 Flex(Wrap) 窄屏换行。改动面 2 files，+699/−80（`AiosApi.ets` +128/−8、`StudyPane.ets` +571/−72，不含文档）。
- mock 契约与测试切片（本地提交 `47ad110` 先行入库）：`tools/android_smoke/mock_contract.py` 新增 `EXAM_SESSION` 确定性 fixture（exam-m13-08-001 / paper-001「Attention Is All You Need」/ mode=exam / status=active / 固定未来时间窗 45 分钟 / server_remaining_seconds=900 / 两道 mcq 各四选项 / answers 仅学员已保存作答投影 `q-m13-08-001→"A"`（非正确答案）/ next_sequence=2）与 GET-only 路由分支；`tools/harmony_mock/server.py` 允许清单新增该端点（所有 query strings 仍拒绝，其余 exam 路径/方法一律 404）；`tools/harmony_mock/test_contract.py` 42→54 项（fixture 整包精确相等正例 + 11 项 fail-closed 负例：learning-events、未知 exam id、集合路径、query strings、POST 建考/PUT 作答/POST 提交/GET submission/report）；`tests/android_smoke` 同步扩展（fixture 精确相等、公共投影不变量、fail-closed 负例、loopback readonly exam session）。请求日志脱敏不变。
- 入库证据为 `docs/evidence/m13-08-harmony-exam-readonly/README.md`（唯一入库文件）；原始验收证据在 gitignored `.verify/m13-08-harmony-exam-readonly/`，不入库。

### 构建与验收（最小可复现，本地模拟器口径）

- 本地测试：`python tools/harmony_mock/test_contract.py` **54/54 passed**（exit 0，全 fail-closed 负例含 M13-08 新增 11 项）；全量 `pytest tests/android_smoke` **295 passed / 1 skipped**（skip 为既有 Windows symlink 特权测试）；`git diff --check` 干净。
- clean 构建：`apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，`hvigorw.bat clean --no-daemon`（CLEAN_EXIT=0）与 `hvigorw.bat assembleHap --no-daemon`（ASSEMBLE_EXIT=0 / BUILD SUCCESSFUL）均 exit 0（复核脚本与日志 `.verify/m13-08-stageb-build.cmd`/`.log`，2026-09-09 02:03–02:04）；产物 `entry-default-unsigned.hap` **418261 bytes、SHA256 `E8E8E4B1C9D9DC85D8212386ACD4F2DF17373FCE47AD0B7FC702BB6047AB6A3A`**（本地 stat/sha256 独立复核，python hashlib 与 certutil 双工具一致）；已知非阻塞警告同基线仅三项：无显式 `targetSdkVersion`、`SettingsStore.ets:56` may-throw 静态提示、`No signingConfig found`（未签名）。
- 正向流（Settings→Study→考试默认查询）：模拟器 `127.0.0.1:5557` 全新安装启动，**App PID 27738** 全程稳定；supervisor 仅为本次运行启动 `tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`；真实 Settings UI 输入（污染串先清除）保存**精确** `http://192.168.8.3:8766/`（`settings-url.json`/`settings-saved.json`）；**App 不重启**，学习 Tab 论文区照旧渲染 M13-05 三篇论文（论文行为保持不变，`study-after-url2.json`）；考试区输入框缺省 `exam-m13-08-001`，点「查询」经加载态（`exam-loading.json`）后成功视图显示 exam_id=exam-m13-08-001、paper_id=paper-001、试卷「Attention Is All You Need」、mode=exam、status=active、server_remaining_seconds=900、next_sequence=2，两道 mcq 全部渲染（8 个选项 key/text 逐项可见），q1 已保存作答 A **明确标注「学员已保存，非正确答案」**、q2 如实显示未作答（`exam-positive.json`/`exam-positive.jpeg`/`exam-positive-final.jpeg`，滚动视图 `exam-scrolled.json`）。
- 错误与恢复流：停止 mock 并确认 8766 端口监听数 0（`exam-before-error.json` 为停服前对照）后，考试区点「重试」：如实进入网络错误态（原始错误 + 「重试」），考试数据清空、面板消失（`exam-error-12s.json`/`exam-error-scrolled.json`/`exam-error.jpeg`）；重启同一 mock（监听实证）后点「重试」→ 完整考试会话视图恢复，字段与正向流逐项一致（`exam-recovered.json`/`exam-recovered.jpeg`）；App PID 保持 27738。
- URL 变更失效流：真实 Settings UI 改存 `http://192.168.8.3:8767/`（输入/清除/核对过程证据 `url-8767-*.json`/`url-menu.json`/`url-cut.json`，精确值 `url-8767-exact.json`/`url-8767-saved-exact.json`）：已加载考试会话**立即失效**——旧地址数据清除、状态回 IDLE，展示中文提示「服务地址已变更，当前考试会话已失效（旧地址数据已清除），请用新地址重新查询。」，不自动重查（`url-change-invalidated.json`/`url-change-invalidated.jpeg`）；改回 `http://192.168.8.3:8766/`（`restore-*.json` 过程证据）：论文区照旧自动刷新恢复三篇，**考试区保持 IDLE**（无自动重查，语义正确）（`final-study.json`/`final-exam-idle.json`/`final-exam-idle.jpeg`）；收尾 mock 全停、8766 监听数 0。
- 完整证据清单、断言明细与截图/布局文件对应关系见 `docs/evidence/m13-08-harmony-exam-readonly/README.md`。

### 边界

本验收是本地模拟器 + mock only 口径，不代表真机、真实考试会话、生产后端、生产 DB、治理写链路或生产可用；无任何写路径、无凭据、不访问麦克风/存储，不渲染正确答案/解析、不提供作答/提交/倒计时——不构成任何考试作答能力。未签名 HAP（`No signingConfig found` 为基线已知警告）直装只是本地验收形态，**不构成发布形态，不声称已签名/可发布**；未使用 AGC key、签名配置或自动签名。CI 无 HarmonyOS job，本地 HarmonyOS 验收不构成远端 CI 验证，PR #65 的 API/Android/Docker/Web CI 结果不能扩大为 HarmonyOS 远端验证。仓库状态（回填 2026-09-09）：已随 PR #65 合并 main（merge commit `31b626926450618468dbe5c10064b6a6eb6f4506`，PR CI run `34284891184` 与 merge 后 main CI run `34285331863` 四项 job 全部 success，远端功能分支已删除）；未打 tag、未部署；`production_ready=false` 语义不变。

## HarmonyOS 生产加固（M13-09）

### 范围与实现

- 分支 `feature/m13-09-harmony-production-hardening`，基于 `main@de5f41a`（M13-08 状态回填提交，本地 git 可验证）；仓库状态（已回填 2026-09-09）：**PR #67 已合并 main**——PR head `998f701a6ccf8e4d2293807c892644266b8965b5`（tree `18bebe450952b9cf8598a260b7a31b4dcfb36abf`，本地 git 可验证），PR CI run `34293394598` 四项 job（API/Android/Docker/Web）全部 success；merge commit `4572551f8ce88b150c5cb9bf6a2d910af2ab18d6`（本地 git 可验证：parents 为 PR #66 merge commit `7f250e1` 与 PR head `998f701`，merge tree 与 PR head tree 同为 `18bebe4`），merge 后 main CI run `34293722342` 四项 job 全部 success（Web 1m15s、Docker 2m27s、API 3m49s、Android 3m26s）；远端功能分支 `feature/m13-09-harmony-production-hardening` 已删除。
- 目标：交付前生产加固——消除两类已知基线告警 + 消灭 `[object Object]` 错误文案 + 收敛信息泄露面，不新增任何业务能力。
- 生产改动共 4 个文件（均在 `apps/harmony/`，+62/−45，不含文档）：
  - `build-profile.json5`：default product 显式声明 `"targetSdkVersion": "6.1.1(24)"`（与 `compatibleSdkVersion` 一致），消除基线告警 `WARN: ArkTS:CHECK Missing targetSdkVersion`；**未添加任何 signingConfig/签名材料/密钥**。
  - `ets/ErrorText.ets`（新增，~100 行纯函数、无状态、无 IO）：`safeErrorText(e)` 白名单提取 `code`（number）/`message`（string），200 字符有界截断，裸对象/空串/`[object Object]` 落固定中文兜底「未知错误」；**绝不 `JSON.stringify` 整个异常对象**（不随对象字段把 URL/令牌/响应体带出到 UI）；自身任何步骤失败均有静态兜底、绝不抛出。
  - `ets/AiosApi.ets`：删除旧 `errToString`（`${e}` 对裸对象产生 `[object Object]` 的根因），网络层/JSON 解析失败文案经 `safeErrorText` 收敛；**非 2xx 响应体不再透出到 UI**——5xx 固定「服务内部错误 (HTTP 5xx):请稍后重试或联系管理员」（响应体可能含栈/内部路径）、400 固定「请求被服务拒绝 (HTTP 400):请检查请求参数」（阻断 FastAPI detail 对注入探针的回显）、其余仅「请求失败 (HTTP <status>)」；只读 GET、零重试、零持久化、零凭据边界不变。
  - `ets/components/SettingsStore.ets`：消除 `SettingsStore.ets` may-throw 静态告警——去掉「返回 Promise 的 openPrefs」间接层（间接层函数体内的 await 不在调用方 try/catch 词法范围内），`preferences.getPreferences/getSync/put/flush` 全部词法地位于调用方 try/catch 内；保存失败文案经 `safeErrorText` 收敛；`emitter.emit` 单独包 try/catch（通知失败不影响已成功的持久化结果）；preferences 名称/键名、默认值、URL 白名单校验、事件语义（put+flush 全部成功才 emit）全部不变。
- 权限基线不变：`module.json5` 仍仅 `ohos.permission.INTERNET`；无任何 token/key/password/签名材料引入（独立审计复核）。

### 构建与验收（最小可复现，本地模拟器口径）

- 契约与回归：`python tools/harmony_mock/test_contract.py` **54/54 passed**（hmharness 双轮）；全量 `pytest tests/android_smoke` **295 passed / 1 skipped**（与基线一致，本切片未触碰任何 mock fixture/测试）；`git diff --check` 干净。
- 构建（hmharness）：`apps/harmony` 下进程环境 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，`hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon` 均 exit 0 / BUILD SUCCESSFUL；**最终警告清单仅 1 项**——`WARN: No signingConfig found for product default`（刻意保留的诚实未签名边界；基线 3 项中的 Missing targetSdkVersion 与 SettingsStore may-throw 均已消除）。
- 产物：`entry-default-unsigned.hap` **422721 bytes、SHA256 `D382D5969EF79513220E2741196EEEAAF920260268F4ED9E12CC9A311979B919`**（运行时验收后未重建、独立复检一致）。
- 运行时冒烟（集成方 Claude，`hdc -t 127.0.0.1:5557`，全新卸载重装上述 HAP，App PID **25301** 全程稳定）：① 默认地址 `http://127.0.0.1:8000` 首启错误态文案为 `网络请求失败: 2300007 Failed to connect to the server`——M13-05 同场景的 `[object Object]` 已消灭（`05_home_initial.json/.jpeg`）。② 真实 Settings UI（污染输入经系统菜单全选/剪切清除）保存 `http://192.168.8.3:8766/` 成功（`已保存:` 实证）；**App 不重启**切学习 Tab：`服务地址: http://192.168.8.3:8766/` 且三篇确定性论文全部渲染（订阅者刷新链路完好，`15_study_after_url_change.json/.jpeg`）。③ 停 mock（8766 listeners=0 实证）后学习 Tab 点「刷新」：如实进入错误态 `网络请求失败: 2300028 Operation timeout`（中文可读、无 `[object Object]`），旧论文数据清空、仅错误文案+重试按钮、App 不崩溃（`18_study_error_resolved.json/.jpeg`）。④ 重启 mock 后点「重试」：三篇论文完整恢复、错误文案消失（`20_study_recovered.json/.jpeg`）；收尾 mock 全停、8766 监听数 0、无 harmony_mock 残留进程（`22_final_cleanup_proof.txt`）。

### 边界

本加固未改变任何业务能力边界：仍为本地模拟器 + mock only，未验真机、真实 provider、生产后端/生产 DB、任何写链路；未使用 AGC key/签名配置，未签名 HAP（`No signingConfig found` 为唯一保留告警，诚实未签名边界）直装仅为本地验收形态，**不构成发布形态，不声称已签名/可发布**；CI 无 HarmonyOS job，本地验收不构成远端 CI 验证；仓库状态（已回填 2026-09-09）：已随 PR #67 合并 main（merge commit `4572551f8ce88b150c5cb9bf6a2d910af2ab18d6`，PR CI run `34293394598` 与 merge 后 main CI run `34293722342` 四项 job 全部 success，CI 结果不扩大为 HarmonyOS 远端验证；远端功能分支已删除）；未打 tag、未部署；`production_ready=false` 语义不变。剩余生产阻塞：AGC 签名与发布流程、真机验证、真实 provider 冒烟、生产后端/生产 DB 接入、HarmonyOS CI 缺位——均待运维显式授权评估。

## HarmonyOS AGC 签名 readiness preflight（M13-10）

### 范围与实现

- 分支 `feature/m13-10-harmony-agc-signing-readiness`，基于 `main@4572551`（PR #67 merge commit，本地 git 可验证）；仓库状态（已回填 2026-09-09）：**PR #69 已合并 main**——feature commit `7342c519111893763d199c104aa3d22c403389d7`（chore: add harmony signing preflight，7 files +889、零删除），PR CI run `34296903848` 四项 job（API/Android/Docker/Web）全部 success（Web 1m25s、Docker 2m28s、API 3m49s、Android 3m36s）；merge commit `bc41d6cf083191958ca9710ae5b71ba31e056aa5`（本地 git 可验证：parents 为 PR #68 merge commit `2fded00` 与 feature commit `7342c519`，merge 与 feature 的差异仅为 PR #68 的四份 M13-09 状态回填文档），merge 后 main CI run `34297190550` 四项 job 全部 success（Web 1m26s、Docker 2m36s、API 3m45s、Android 3m25s）；远端功能分支 `feature/m13-10-harmony-agc-signing-readiness` 已删除。
- 目标：把 AGC 签名 readiness 的非 Harmony 工程面固化为 fail-closed preflight 门禁——仅工具/测试/防护，`apps/harmony/**` 零改动，不新增任何业务能力，不创建/修改/读取任何真实签名材料。
- 改动共 7 个文件（`tools/` 3 + `tests/` 2 + `.gitignore` + 证据 README，+889、零删除）：
  - `tools/harmony_release/preflight.py`（新增，标准库实现，300 行）：签名前置 fail-closed 门禁 CLI——① 校验 `apps/harmony/build-profile.json5` 的 `signingConfigs` 仍为空数组，并把 unsigned 边界写进结果（当前契约 = 仓库必须保持诚实未签名边界）；② 仓库内扫描 `.p12/.p7b/.cer/.csr/.jks`（排除 `.git`/`.verify`/`build`/`node_modules` 等目录），存在即失败并报告扩展名+相对路径（**只看文件名，绝不读取内容**）；③ 仅检查 `AIOS_HARMONY_CERT_PATH` / `AIOS_HARMONY_PROFILE_PATH` / `AIOS_HARMONY_KEYSTORE_PATH` 三个约定变量名的存在性，存在时校验路径存在、是常规文件、扩展名分别为 `.cer/.p7b/.p12`、resolved 路径在仓库外，任何违规 fail-closed 失败；**绝不打印环境变量值/路径值/文件内容**；④ `--hap` 只记录存在/字节数/SHA256/文件名是否含 unsigned，不推断已签名；⑤ `--strict` 把 warning 升级为 failure；缺材料不是 warning，而是明确的 `blocked_by_external_materials` 状态；输出 deterministic JSON（排序键、无时间戳、无绝对路径）。
  - `tools/harmony_release/json5lite.py`（新增，85 行）：preflight 的最小 JSON5 兼容加载器（纯 JSON 优先，失败后退到注释/尾逗号剥离重试、字符串感知；不支持非引号键；任何不可解析返回 None 由调用方 fail-closed 为 `build_profile_unparseable`）；同时支持 `python -m tools.harmony_release.preflight` 与直接脚本两种入口。
  - `tests/harmony_release/test_preflight.py`（新增，405 行）：35 项针对性测试，全部使用临时目录+占位字节（`placeholder-not-a-real-certificate`），不生成真实证书。
  - `.gitignore`（+9）：签名材料扩展名防护 `*.p12` `*.p7b` `*.cer` `*.csr` `*.jks`（仓库内本无此类 tracked 文件，纯防御未来误提交，`git ls-files` 本地可验证）。
- 退出码契约：`0` = ok 或（默认模式下）blocked；`1` = failure（fail-closed 违规，或 `--strict` 升级）；`2` = blocked 且给了 `--require-materials`。外部材料唯一约定：上述三个 `AIOS_HARMONY_*_PATH` 环境变量，材料文件必须放在仓库外；JSON 输出对每个变量只含变量名/present/valid/expected_extension/error 类别，不含任何路径值。

### 验证（最小可复现，本地口径）

- 本地测试：canonical venv `python -m pytest tests/harmony_release -q` → **35 passed**（覆盖干净仓库 blocked/exit 0、`--require-materials` 缺材料 exit 2、仓库内材料 fail、外部正确材料通过、各类 fail-closed 违规类别、JSON 不含环境变量值/绝对路径双向断言、真实仓库 signingConfigs 各分支、`--hap` 记录与 `--strict` 升级、CLI 子进程 exit 码）；全量 `pytest tests/android_smoke -q` → **295 passed / 1 skipped**（与 M13-09 基线一致）；`python -m compileall tools/harmony_release` 通过；`git diff --check` 干净。
- 真实仓库冒烟：preflight → `status=blocked_by_external_materials`、`repo_materials.count=0`、`build_profile.unsigned_boundary=true`、exit 0。
- hmharness 上游验证轮（基线 `origin/main@4572551`）：release 构建 `hvigorw.bat assembleHap --mode module -p product=default -p buildMode=release --no-daemon` exit 0 / BUILD SUCCESSFUL（6s347ms）；仅 2 WARN、0 ERROR（release 混淆开关提示 + `No signingConfig found for product default`）；`signingConfigs: []` 原样；未签名 HAP 198569 bytes、SHA256 `21CF87CA17BF2FBEAED9591BDE619B7627303DA0DDDF08216F0B65832C476D3B`。
- hmharness 复检（基于 feature commit `7342c519111893763d199c104aa3d22c403389d7`，clean/release 两次构建均完成于 PR #69 合并之前；时点 worktree 相对远程跟踪分支 ahead 1 / behind 2）：clean 与 release 构建均成功，0 ERROR、2 条预期 WARN（与上游验证轮清单一致）；未签名 HAP **198569 bytes、SHA256 `D67FDA0B46018B30CD5F28A6D64BB320D592CE90246053E5102ABABF777490FD`**（zip 重建产物，与上游验证轮留存产物同字节数、不同哈希——符合「HAP 为 zip 打包产物、同源重建哈希可能不同」的已知边界）；两次 preflight 调用均 exit 0 且 `blocked_by_external_materials`；tracked 文件未变。

### 边界

本切片不改变任何业务能力与签名边界：AGC 发布材料仍缺位（仓库与本机均无发布证书/Profile/密钥库，本机仅 DevEco 本地调试身份）→ **不声称已签名/可发布**——release 构建成功仅证明 `buildMode=release` 可执行，产物仍为 `entry-default-unsigned.hap`，不存在任何已签名 HAP；preflight 通过 ≠ 签名配置正确（材料与 bundleName `com.ailearningos.app` 的匹配、signingConfigs 接入方式属 hmharness 后续切片，均未验证）；未做真机验证与任何运行时验证（release 产物未在任何设备安装）；未启用/验证混淆（release 混淆提示 WARN 为既有 `ruleOptions.enable=false` 配置）；CI 无 HarmonyOS job，PR #69 的 PR CI run `34296903848` 与 merge 后 main CI run `34297190550` 四项 job 全部 success 均不扩大为 HarmonyOS 远端验证（`tests/harmony_release` 未纳入 CI，与 `tests/android_smoke` 同为本地/canonical venv 口径）；仓库状态（已回填 2026-09-09）：已随 PR #69 合并 main（远端功能分支已删除）；未打 tag、未部署；`production_ready=false` 语义不变。剩余生产阻塞：AGC 签名与发布流程（材料创建与 signingConfigs 接入）、真机验证、真实 provider 冒烟、生产后端/生产 DB 接入、HarmonyOS CI 缺位——均待运维显式授权评估。
