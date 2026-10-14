# M14-267 证据：显式语音部署入口与服务端 URL 拆分（部署接线，fake-only）

- 分支 `api/m14-267-voice-entrypoint`（worktree
  `ai-learning-os-worktrees/m14-267-voice-entrypoint`），基于
  current main `bf46509ecaa859908375c0f6034968a5a267d01b`
  （PR #353 merge，即 M14-266）。执行前 porcelain 干净（基点
  本地 git 可验证）。
- 动因：M14-266 能从设置对象显式装配工厂，但部署入口仍导入
  `app.main:app`（故意无订阅者工厂）。本切片补上窄部署入口：
  服务端 LiveKit URL 设置拆分 + 入口模块 + 主 compose API 服务
  显式切换。**这是部署接线 only**——不使流式 ASR/VAD/barge-in
  可用、不声明生产语音就绪。

## 非声明（explicit non-claims）

- **注册 ≠ 启动 ≠ 能力**：入口只注册受控订阅者工厂——
  import/lifespan 全程零签发、零订阅者创建/启动、零房间/流
  构造、零 LiveKit SDK 调用（fake-only 测试锚定）；不加语音
  HTTP 路由/readiness。
- **零真实连接/凭据**：不连真实 LiveKit；测试全用显眼假凭据；
  compose 默认 `ws://livekit:7880` 为服务拓扑既有地址约定、
  API 凭据默认为既有 dev 占位——本切片零真实凭据入库。
- **本地不跑 Docker**：真实 compose 入口冒烟属 Docker CI
  （任务指定）；本切片未启停任何 API/Docker/LiveKit/ASR/TTS/
  emulator/代理/生产服务。
- **既有面零变化**：LIVEKIT_URL/PUBLIC_LIVEKIT_URL/API 凭据/
  绑定/端口/profiles/健康检查默认零改动；镜像默认 CMD 仍
  `app.main:app`；`app/main.py` 模块级 `app = create_app()`
  原样且不导入入口；Web/Android/Harmony/DB/迁移/recovery/
  edge/TURN 零触碰。
- **不声明缺口 3 / M4-09 关闭**：矩阵计数不变 implemented
  60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/core/config.py`（+4 行）**：Settings 新增恰
一字段 `livekit_server_url: str | None = None`（env
`LIVEKIT_SERVER_URL`，pydantic-settings 惯例映射）——服务端
容器地址；与浏览器面 `livekit_url`（token ws_url）/
`public_livekit_url`（显式公开 URL）语义分离、互不回退、互不
改写；无 validator/回落/强制/secret 行为；既有设置零改名零
改义。

**`services/api/app/voice_entrypoint.py`（新增，窄入口模块）**：

- `VoiceEntrypointSettingsLike`（runtime_checkable 最小设置面）：
  恰 `livekit_server_url`/`livekit_api_key`/`livekit_api_secret`
  三属性——浏览器面两字段故意不在面内。
- `_ServerUrlSettingsAdapter`（`__slots__` 锁死属性面）：恰读
  `livekit_server_url` 映射进 M14-266
  `VoiceAppSettingsLike.livekit_url`；属性读取抛错由 M14-266
  守卫统一净化；**浏览器面设置被结构性忽略**（适配器根本不
  转发它们）。
- `create_voice_entrypoint_app(database_url, *, settings)` 纯
  包装：复用 M14-266 `create_app_with_voice_subscriber`——
  零重复凭据/URL 校验/token 签发/runtime 工厂/净化逻辑；映射
  先于工厂装配；非法值经既有 M14-265/266 校验路径在任何
  FastAPI app 创建之前失败——绝不返回半装配 app。
- 模块级 `app = create_voice_entrypoint_app(None,
  settings=get_settings())`：`get_settings` 恰一次且仅此一处
  （AST 锚定）；**fail-closed**——`LIVEKIT_SERVER_URL` 缺失/
  非法时导入即失败（uvicorn 拒绝启动），绝不静默回退浏览器
  URL、绝不返回无工厂 app。

**`infra/docker-compose.yml`（主 API 服务，+9 行）**：显式
`command: ["uvicorn", "app.voice_entrypoint:app", "--host",
"0.0.0.0", "--port", "8000"]` + 环境注入
`LIVEKIT_SERVER_URL: ${AIOS_LIVEKIT_SERVER_URL:-ws://livekit:7880}`
（注释注明与浏览器面语义分离/缺失 fail-closed）。既有
LIVEKIT_URL/PUBLIC_LIVEKIT_URL/API 凭据/绑定/端口/profiles/
健康检查零变化（yaml 渲染断言锚定）。

**`.env.example`（+10 行）**：新增 `AIOS_LIVEKIT_SERVER_URL`
compose 插值键 + 注释块（服务端容器地址、非浏览器 URL、
compose 已注入默认、缺失 fail-closed）。

**零改动面（测试锚定）**：`services/api/Dockerfile`（CMD 仍
`app.main:app`）与 `app/main.py`（裸 `app = create_app()`、
无 voice_entrypoint 引用）。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_entrypoint.py`（新增，18 项
fake-only 契约测试）：签发器/runtime 工厂对 monkeypatch 记录；
模块级 app 经「受控 env + `get_settings.cache_clear()` +
`importlib.reload`」驱动（monkeypatch 退出自动还原 env、缓存
用后即清，杜绝假设置泄漏）。

1. **服务端 URL 映射**：三个 URL 显式不同的设置对象——恰
   `livekit_server_url` 进入 M14-266 映射面
   （`mapped.livekit_url == SERVER`，≠浏览器 ≠public）；
   key/secret 恰映射；`VoiceAppSettingsLike` 结构兼容。
2. **纯包装恰一次**：`create_app_with_voice_subscriber` 恰被调
   一次、database_url 位置原样透传、设置走 keyword；入口不
   直接导入/调用 `create_app`（文本锚定——只经 M14-266 包装）。
3. **非法 server URL 先于 app 创建失败**（6 例：None/""/空白/
   `http://bad`/`ws://`/`ws://u@h`）：经既有 M14-265/266 路径
   抛 `VoiceRuntimeFactoryError("LiveKit URL 非法")`——净化
   （无凭据/URL/JWT）、`__cause__`/`__context__` 均 None、
   runtime 工厂与 create_app（spy 哨兵）零调用、零签发。
4. **`get_settings` 恰一次且仅模块级**：AST 恰一调用节点 +
   正则锚定模块级 app 行；纯函数双守卫（源模块与入口模块内
   绑定同时替换为 raise-if-called）证明运行期不触全局设置。
5. **fake-only lifespan ×2**（纯函数构建与模块级默认各一）：
   恰一工厂同实例上 `app.state.voice_subscriber_factory`、
   管理器 open、订阅者计数 0、`/health` 200、零签发/零房间/
   零流构造、零启动；关停后管理器 closed、计数仍 0、仍零
   签发。
6. **源码卫生（AST+文本）**：运行时导入白名单
   {__future__, typing, fastapi, app.core.config, app.voice_app}；
   禁止调用清单（open/system/popen/run/sleep/getenv/
   ensure_future/create_task/start_new_thread/
   **build_voice_token/validate_livekit_url**——不重复凭据/URL
   校验 + 属性面 connect）；无 asyncio 调度；文本级无 getenv/
   os.environ/dotenv/动态逃逸/`.create(`/`.start(` 形态。
7. **默认面不变**：main.py 裸 `app = create_app()` 存在且无
   voice_entrypoint 引用；Dockerfile CMD 正则锚定
   `app.main:app`。
8. **compose 渲染**（yaml.safe_load）：API command 恰为
   `["uvicorn", "app.voice_entrypoint:app", "--host",
   "0.0.0.0", "--port", "8000"]`；environment 含
   `LIVEKIT_SERVER_URL: ${AIOS_LIVEKIT_SERVER_URL:-ws://livekit:7880}`；
   LIVEKIT_URL/PUBLIC_LIVEKIT_URL/LIVEKIT_API_KEY 原样；
   `.env.example` 含键且 ±10 行注释块覆盖服务端/浏览器区分。

## 验证（2026-10-14，worktree 内真实执行；全部零网络/零运行时服务/零真实凭据/本地零 Docker）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_entrypoint.py services/api/tests/test_voice_app_wiring.py services/api/tests/test_voice_subscriber_lifespan.py -q` | **44 passed**（18+14+12） |
| `-m pytest services/api/tests/test_voice_entrypoint.py services/api/tests/test_voice_app_wiring.py services/api/tests/test_voice_app_factory.py services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_runtime_factory.py services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py services/api/tests/test_voice_subscriber_lifespan.py -q` | **315 passed**（M14-266 基线 297 零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py -q` | **34 passed, 1 skipped**（compose 改动不破坏既有契约） |
| `-m pytest services/api/tests/test_rc_smoke_rehearsal.py -q` | **59 passed**（生产 pin 与 rc-smoke parity 同步后，含 docker compose config 只读渲染 parity） |
| `-m pytest services/api/tests/test_compose_profiles.py services/api/tests/test_compose_restart_policy.py services/api/tests/test_m905_ownership_audit.py -q` | **44 passed, 2 skipped** |
| `-m ruff check services/api/app/core/config.py services/api/app/voice_entrypoint.py services/api/tests/test_voice_entrypoint.py` | All checks passed |
| `-m compileall services/api/app/core/config.py services/api/app/voice_entrypoint.py services/api/tests/test_voice_entrypoint.py` | OK（无输出即成功） |
| `git diff --check` | 干净（无 whitespace 错误） |

真实 compose 入口冒烟属 **Docker CI**（任务指定本地不跑
Docker）。TDD 顺序：先建测试文件确认 RED（入口模块不存在，
FileNotFoundError 收集失败），实现后转 GREEN；期间三轮测试
自身修正（①from-import 绑定——reload 后 patch 源模块属性不
影响入口模块已绑定名字，改为直接 patch 入口模块属性；
②模块级 app 构建消耗 runtime 记录——失败路径断言前清基线；
③.env.example 注释块超出 ±8 行窗口——加宽到 ±10）。

### CI 同步（首轮 PR 检查后的显式对齐）

首轮 PR CI 中 API job 暴露 rc-smoke 契约漂移（本切片任务书
验证清单未含该文件，属部署面联动同步）：

- `test_production_files_byte_equivalent_to_base`：生产
  compose pin 击穿——本切片有意修改 `infra/docker-compose.yml`
  （api command + LIVEKIT_SERVER_URL 槽位），按 M14-100/148/
  159/160 先例**第五次显式更新 pin**（LF 口径新 sha
  `8fb2aafc…fedfe8e`）并在 pin 注释登记变更语义。
- `test_smoke_compose_api_env_parities_with_base_except_cors` /
  `test_smoke_compose_render_env_and_health_parity_with_base`：
  rc-smoke compose api env 键集必须与生产零增零删——同步追加
  逐字一致的 `LIVEKIT_SERVER_URL: ${AIOS_LIVEKIT_SERVER_URL:-ws://livekit:7880}`
  行（位于 LIVEKIT_URL 之后，与生产同位）。rc-smoke 镜像默认
  CMD 不变（`app.main:app`），smoke 演练入口不受本切片影响。

新增行卫生：真实 secret 形态 0 命中（compose 默认值为既有
dev 占位约定与拓扑地址；测试全为显眼假值）；JWT `eyJ` 命中
均为合成假值；私有绝对路径 0；U+FFFD 0；readiness 置 true 0。

## 后续空间（不在本切片）

- Docker CI 上的 compose 入口冒烟结果回填（supervisor 侧）；
  受控订阅者启动切片（真实连接/签发的运行时验收）；HTTP
  端点接线；帧消费者管线；VAD/barge-in/pause-resume。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-267（M14-266 转
  前一任务快照），里程碑段追加 M14-267 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-267 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-267 条目（含非声明）。
