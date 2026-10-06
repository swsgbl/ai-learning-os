# M14-246 证据:API 可观测性(M0-03 缺口关闭)

## 0. 结论与边界

- 动因:M14-245 能力真相矩阵判定 M0-03(FastAPI skeleton)为 partial,
  缺口为独立 readiness 端点、结构化(JSON)日志、全局 exception handler
  注册(矩阵 §5 缺口 6 / §7 切片候选 6)。本切片实施候选 6,三缺项
  全部补齐并以 21 项契约测试锚定;supervisor 复审追加两处安全面
  (异常日志脱敏、/readyz reason 净化),终审再追加两项(空注册表
  fail-closed、不透明长串拒绝)并更正扫描计数口径,见 §1/§4。
- 审计基点:current main `0d96991`(PR #332 merge,即 M14-245)。
  worktree `ai-learning-os-worktrees/m14-246-api-observability`,分支
  `ops/m14-246-api-observability`,执行前 porcelain 为空。
- M14-245 矩阵 M0-03 行同步改判 ✅(现行口径 implemented 60 /
  partial 3;审计基点原判定以 2026-10-07 更新注记保留,不抹除历史)。
- **边界(不可越界解释)**:本切片一切结论仅为**代码+仓库内测试口径**,
  不等于、不可引用为 `production_ready` / `release_ready` /
  `public_ready`。未做任何生产/部署级验证;未启动/停止任何容器或服务;
  未触碰 DB/Redis/MinIO/voice/LiveKit/设备/模拟器/生产服务/凭据;
  未 push、未开 PR、未合并。生产权威口径不变(provider-smoke 门仍
  blocked,见 M14-245 证据 §4)。
- 后续切片空间(非本切片缺项):uvicorn 自身 logger(uvicorn/
  uvicorn.error/uvicorn.access)仍为 uvicorn 默认格式;部署级依赖
  连通检查(DB/Redis/voice/LiveKit)未内置为默认检查(架构上经
  `app.state.readiness_checks` 注入即可,单测不受影响)。

## 1. 实现摘要

### 1.1 `/readyz` readiness 端点(与 `/health` liveness 语义分离)

- `/health` = 进程存活(liveness):恒 200 静态体,行为不变。
- `/readyz` = 依赖就绪(readiness):注入的检查全部通过才 200
  `{"status":"ready","checks":{...}}`,任一失败 503
  `{"status":"not_ready","checks":{...}}`。
- 检查注册表挂 `app.state.readiness_checks`(依赖注入,无全局
  monkey-patch):`app/ops/readiness.py` 定义 `CheckOutcome` 与
  `evaluate_checks`;检查为同步或异步可调用,单测注入假检查即可覆盖
  成功/失败/异常路径,**绝不依赖真实 DB/Redis/voice/LiveKit**。
- 默认检查恰一项 `repository`:验证启动装配完成(repository 已接线,
  内存模式与 DB 模式皆可判)——即「app 完成引导可服务」的最低就绪
  语义,不引入外部依赖。
- 单项检查抛异常不炸端点:按 not_ready 记账,`reason` 只取异常类名
  ——不透出异常文本/连接串(与 ops-snapshot 固定脱敏文案同款语义)。
- **reason 安全面(supervisor 复审追加)**:`sanitize_reason` 在 readiness
  边界净化一切 reason——正常安全文案(如 "db unreachable")与异常类名
  原样保留;含控制字符、URL/连接串形态(`://` 或 user:pass@host)、
  敏感标记(token/secret/password/credential/authorization/bearer/
  api_key,注意裸 "key" 不是标记——KeyError 等类名须放行)、超长
  (>128)或**不透明长串**(无空白且 >48 字符——随机 base64/hex/API
  key 形态可不含敏感词或 URL 语法,保守拒绝;含空白的人类可读长文案
  保留)的 reason 一律替换为固定安全回退文案。部署检查误把凭据/
  连接串写进 reason 时,不会经未认证的 `/readyz` 泄出。
- **空注册表 fail-closed(终审追加)**:检查注册表为空(`{}` 或 None)
  = 无任何就绪证明 → 503 not_ready,响应带固定安全诊断条目
  `{"readiness": {"ready": false, "reason": "no readiness checks
  registered"}}`(不暴露内部结构)——绝不因 `all([])` 恒真虚报 ready。
- 认证开启(AUTH_SECRET 配置)时 `/readyz` 与 `/health` 同为豁免
  路径(基础设施探针无凭据;业务路径门禁不变)。

### 1.2 结构化 JSON 日志(bootstrap 路径)

- 新 `app/core/logging.py`:`JsonLogFormatter`(单行 JSON,固定字段
  ts/level/logger/message)+ `configure_logging`(幂等安装到 root
  logger)+ `request_id_var`(请求关联 ContextVar)。
- bootstrap 挂点:`create_app()` 首行调用
  `configure_logging(settings.log_level)`——生产入口
  `uvicorn app.main:app` 经模块导入 `app = create_app()` 即生效;
  幂等保证测试反复建 app 不叠加 handler。
- 级别语义保留:新 Settings 字段 `log_level`(env `LOG_LEVEL`,空白
  归一 None、大小写不敏感、非法值拒绝启动);**未配置 = 不改变既有
  logger 级别(零漂移)**;formatter 输出恒含 level 字段。
- 请求关联:request-id 中间件在既有 M9-04/M9-05 语义(X-Request-ID
  透传+格式校验+响应回填)之上,把 request id 写入 ContextVar(请求
  期间应用日志自动携带);异常处理器路径经 record 属性显式携带;
  formatter 取 record 属性优先、ContextVar 兜底、两者皆无省略键。
- 敏感值不落日志:formatter 不记录请求头/请求体/响应体;`extra_fields`
  显式附带字段中命中敏感键标记(authorization/cookie/token/secret/
  password/api_key/apikey/credential,小写子串匹配)的值脱敏 `***`。
- **异常不落原始形态(supervisor 复审追加)**:exc_info 只结构化为
  `{"type": 异常类名, "stack": [{func, file(basename), line}]}`(栈帧
  数上限 64)——原始异常消息(可能含凭据/连接串/请求派生文本)、完整
  traceback 文本、源码行文本与完整文件路径一律不进序列化输出;诊断
  价值经「类型 + 净化栈位置」保留。
- 测试中一切凭据形态字符串均为**自造无效哨兵**(AUTH_SECRET 测试值、
  假连接串/假敏感键值),仅用于断言「不进响应/不进日志」的脱敏契约,
  不对应任何真实凭据。

### 1.3 全局异常处理

- 新 `app/api/error_handlers.py`,`create_app` 注册,两类 handler:
  - **DomainError 族 HTTP 映射**(经 ExceptionMiddleware,响应流经
    内层 request-id 中间件):NotFoundError→404、ConflictError→409、
    其余 DomainError→400;响应体与 HTTPException 同形
    `{"detail": str(exc)}`(此前未注册时这类异常以 500 逃逸)。
  - **未捕获异常 500 兜底**(经最外层 ServerErrorMiddleware,响应
    不经内层中间件,故 handler 自行回填 X-Request-ID):固定脱敏
    detail `{"detail":"Internal server error"}`(不透出异常类/文本);
    服务端结构化日志(`logger.exception`,带 request_id 与
    exception_class)只落异常类型与净化栈位置——原始异常消息/
    traceback 由 JsonLogFormatter 统一净化,不进日志;处理后异常继续
    上抛(uvicorn 与测试客户端仍可见,语义与 Starlette 既有行为一致)。
- **不重新注册、不变弱**:HTTPException(401/404 等 JSON detail 形状)
  与 RequestValidationError(422 校验错误列表)保留 FastAPI 既有处理;
  request-id 中间件行为不变(成功路径仍回填 header,异常路径 finally
  复位上下文后上抛,不吞异常)。

## 2. source-to-test 映射

| 源 | 契约测试(`services/api/tests/test_api_observability.py`) |
|---|---|
| `app/ops/readiness.py` `evaluate_checks` + main.py `/readyz` 默认检查 | `test_readyz_ready_by_default_sqlite`、`test_readyz_ready_without_database`(内存模式无 DB 也 ready) |
| `/readyz` 503 not_ready + 注入性 | `test_readyz_not_ready_with_injected_failing_check`、`test_readyz_supports_sync_checks_and_partial_failure`(同步检查+部分失败) |
| 检查异常包含与脱敏 | `test_readyz_check_exception_is_contained_and_sanitized`(reason==异常类名,连接串不进响应) |
| `readiness.py` `sanitize_reason` 安全面 | `test_readyz_unsafe_reason_replaced_by_safe_fallback`(连接串/userinfo/敏感标记/控制字符/超长→固定回退,哨兵不进响应)、`test_sanitize_reason_boundary`(安全文案与异常类名保留,KeyError 放行,非 ASCII 与含空白长文案保留)、`test_sanitize_reason_rejects_long_opaque_strings`(自造无效不透明长串→固定回退,端到端不泄出) |
| `/readyz` 空注册表 fail-closed | `test_readyz_fails_closed_when_no_checks_registered`(`{}` 与 None → 503 + 固定安全诊断条目) |
| `/health` 与 `/readyz` 语义分离 | `test_readyz_semantics_distinct_from_health`(同 app 下 health 200 / readyz 503) |
| `auth.py` require_user 豁免 `/readyz` | `test_readyz_exempt_from_auth_gate`(auth on:readyz/health 200,业务路径 401) |
| `core/logging.py` `JsonLogFormatter` 固定字段 | `test_json_formatter_fixed_fields` |
| request_id 关联(record 属性/ContextVar/无则省略) | `test_json_formatter_correlates_request_id_from_record_and_context` |
| 敏感键脱敏 | `test_json_formatter_redacts_sensitive_extra_fields` |
| exc_info 结构化脱敏(类型+净化栈位置,无原始消息/traceback/全路径) | `test_json_formatter_sanitizes_exception_details`、`test_unhandled_exception_500_contract_and_log_correlation`(端到端:响应与序列化日志均无哨兵值,栈位置/类型/request_id 保留) |
| `configure_logging` 幂等 + 级别保留 | `test_configure_logging_idempotent_and_level_preserved` |
| `config.py` `log_level` 归一与校验 | `test_log_level_settings_normalization_and_validation` |
| `error_handlers.py` DomainError 映射 | `test_domain_error_http_mapping`(404/409/400+detail 形状+X-Request-ID) |
| 未捕获异常 500 契约 + 日志关联 | `test_unhandled_exception_500_contract_and_log_correlation`(固定 detail/脱敏/X-Request-ID 回填/结构化日志同 request_id) |
| HTTPException 契约不变 | `test_existing_http_exception_contract_unchanged`(404 `{"detail":"Not Found"}`) |
| RequestValidationError 契约不变 | `test_existing_validation_error_contract_unchanged`(422 detail 列表 loc/msg/type) |

既有回归面(非本切片新增,验证不变弱):`test_auth.py`(门禁两态与
豁免)、`test_config_privacy.py`(Settings)、`test_migrations.py`
(uvicorn logger 不被禁用)、`test_exam_flow.py`(app 引导全流程)。

## 3. 验证记录(命令与真实结果)

执行环境:上述 worktree 根目录。解释器说明(如实):worktree 无独立
`.venv`,验证使用**主 checkout(同仓库同级工作副本)的 canonical 仓库
虚拟环境** `\.venv\Scripts\python.exe`(Python 3.11.15;fastapi
0.118.3 / starlette 0.48.0 / pytest 9.1.1 / ruff,均仓库既有依赖,
零新增依赖)——venv 只提供依赖包,被测代码为本 worktree 工作树。
Codex 曾以同一解释器独立复跑修正前聚焦面(16 契约+38 回归=54
passed)与 ruff clean,结果一致。

1. 聚焦契约套件(本切片新增,先红后绿——实现前
   `ModuleNotFoundError: No module named 'app.core.logging'` 确认红;
   supervisor 复审与终审修正后复跑):
   `python -m pytest services/api/tests/test_api_observability.py -q`
   → **21 passed in 2.18s,exit 0**(含复审新增 reason 净化 2 项、异常
   脱敏 1 项,终审新增空注册表 fail-closed 1 项与不透明长串 1 项)。
2. 聚焦回归(auth 门禁/Settings/迁移 logger/考试全流程):
   `python -m pytest services/api/tests/test_auth.py services/api/tests/test_config_privacy.py services/api/tests/test_migrations.py services/api/tests/test_exam_flow.py -q`
   → **38 passed in 12.98s,exit 0**。
3. 全套 API 测试(main.py 为全局 bootstrap 改动,跑全套证无回归;
   各轮修正后均复跑):
   `python -m pytest services/api/tests -q`
   → 终审修正后 **见 §3 末「终审复跑结果」**(复审轮曾 5839 passed /
   36 skipped / 306.84s / exit 0;首终轮 5836/36/307.02s;skip 均为
   既有环境门控项——真 PG/AIOS_PG_TEST_URL 等,与本切片无关,未运行
   任何门控集成测试)。
4. API ruff(仓库 AGENTS.md 口径):
   `python -m ruff check services/api/app services/api/tests`
   → **All checks passed!,exit 0**。
5. `git diff --check` → **无输出,exit 0**(无空白错误)。
6. 分支级新增行卫生扫描(口径:**最终 committed 分支 diff**
   `git diff 0d96991..HEAD -U0` 的 `+` 行(含空行,与 `git show --stat`
   insertions 同口径),共 **1208** 行——早前记录的
   1173 行为中间工作树口径,已更正为最终提交口径;宽松正则覆盖
   secret/token/password/key/bearer/credential/authorization 的赋值与
   键值/传值形态),如实分类:
   - **凭据形态(真实或可生效):0 命中**;
   - `token = ...` 赋值形态:**2 处代码命中,均为良性 ContextVar 句柄**
     (`main.py` 与 `test_api_observability.py` 中
     `token = request_id_var.set(...)` 的 reset 令牌,非凭据);另有
     2 处命中为本文件描述扫描口径的说明文字行(非代码);
   - 键值/传值形态:**3 处测试代码命中,均为自造无效哨兵**(脱敏断言
     样本 `"authorization": "Bearer sample-value"`、`"api_key":
     "sample-value"`,以及 422 契约测试的无效载荷 `{"username": "x",
     "password": "y"}`)——这些是**有意使用的自造哨兵**(见 §1.2
     说明,仅用于断言脱敏/不泄出契约,不对应任何真实凭据,与既有
     `test_auth.py` 的 AUTH_SECRET 哨兵惯例一致);另有 2 处命中为
     本文件引用上述样本形状的说明文字行(非代码);
   - U+FFFD **0**(utf-8 解码全通过)、绝对本地路径(盘符形态)
     **0**、Windows 用户名 **0**。

**终审复跑结果(修正 1/2/3 落地后)**:全套 `python -m pytest
services/api/tests -q` → **5841 passed, 36 skipped in 293.68s
(0:04:53),exit 0**(较复审轮 +2 = 终审新增契约测试)。

前端套件(`npm run typecheck/lint/build`)未运行:本切片零
TypeScript/ArkTS 变更(变更面仅 services/api Python + docs),如实说明。

## 4. 变更清单

- **supervisor 复审修正(2026-10-07,amend 进同一 commit,不产生第二
  个提交)**:
  1. `JsonLogFormatter` 不再序列化原始 `formatException` 文本——exc_info
     只结构化为异常类型 + 净化栈位置(函数名/文件 basename/行号,栈帧
     上限 64);原始异常消息与完整 traceback 不进日志。对应测试改为
     断言「request_id/exception_class/栈位置保留,自造哨兵与原始异常
     文本不进任何序列化输出」。
  2. `/readyz` reason 安全面:新增 `sanitize_reason`(控制字符/URL/
     连接串形态/敏感标记/超长 → 固定安全回退),`evaluate_checks` 全量
     过净化面;新增 2 项契约测试(不安全 reason 端到端回退 + 净化面
     单元边界)。
  3. 证据精确性:卫生扫描如实分类(见 §3.6);自造哨兵使用如实声明;
     解释器来源如实记录(主 checkout canonical 仓库 venv);记录 Codex
     以同一解释器独立复跑修正前聚焦面 54 passed 与 ruff clean。
- **supervisor 终审修正(2026-10-07,再次 amend 进同一 commit)**:
  1. `/readyz` 空注册表 fail-closed:`{}`/None → 503 + 固定安全诊断
     条目(不再 `all([])` 恒真虚报 ready);新增 1 项契约测试。
  2. `sanitize_reason` 不透明长串规则:无空白且 >48 字符的 reason
     (随机凭据形态,可不含敏感词/URL 语法)保守拒绝为固定回退;
     含空白的人类可读长文案与非 ASCII 诊断保留;新增 1 项契约测试
     (自造无效不透明哨兵,端到端断言不泄出)。
  3. §3.6 卫生扫描口径更正:上轮文档误记 1173 行(中间工作树口径,
     commit+worktree 叠加),终审按 supervisor 核定的 committed 口径
     (`git diff 0d96991..HEAD -U0`)更正——上轮 committed 实为 1109
     行,本轮含终审修正后最终 committed 为 **1208** 行;良性分类
     (2 处 ContextVar 句柄、3 处自造哨兵键值)保留并补充文档说明
     行命中说明。
- 新增:`services/api/app/core/logging.py`、
  `services/api/app/ops/readiness.py`、
  `services/api/app/api/error_handlers.py`、
  `services/api/tests/test_api_observability.py`、本文件。
- 修改:`services/api/app/main.py`(bootstrap 安装日志/注册异常
  handler/`/readyz` 端点+默认检查/request-id 中间件加日志上下文)、
  `services/api/app/core/config.py`(新 `log_level` 字段+校验)、
  `services/api/app/api/routes/auth.py`(豁免 `/readyz`)。
- 文档台账:`docs/PROJECT_STATUS.md`、`docs/ROADMAP.md`、
  `docs/CHANGELOG.md`(按既有 ledger 惯例)、
  `docs/evidence/m14-245-capability-roadmap-truth/README.md`
  (M0-03 行改判 ✅+注记,§0/§5/§7 同步注记)。
- 无依赖变更(requirements 零改动)、无配置/CI/前端/迁移变更。
