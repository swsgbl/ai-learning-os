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
  才删库，被历史考试引用的卷一律拒绝（人工处理，不级联删考试）。
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
  生产未执行任何迁移（M10-04 第三切片）。

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
  14 天）——**不是** GitHub Release。
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
  LLM 冒烟需要 key——见上节 smoke_llm.sh；pass 语义不放宽——仍是 required gate）。
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
