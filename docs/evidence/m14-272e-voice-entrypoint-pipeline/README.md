# M14-272e 证据：部署入口升级为管线装配 + VOICE_ASR_ENDPOINT 槽位（fake-only）

- 分支 `api/m14-272e-voice-entrypoint-pipeline`（worktree
  `ai-learning-os-worktrees/m14-272e-voice-entrypoint-pipeline`），
  基于 current main `de630427`（PR #362 merge，即 M14-272d）。执行
  前 porcelain 干净（基点本地 git 可验证）。
- 动因：M14-272a/c/d 交付了组装器、lifespan 接线与 live 路由，
  但部署入口（voice_entrypoint）仍是 M14-267 的「工厂注册」版
  ——生产入口无法装配管线（live 路由无数据面）。本切片完成
  入口升级；途中发现 ASR 槽位语义冲突（M14-01 契约：provider
  面 `ASR_LOCAL_ENDPOINT` 默认留空 = 降级替身不虚报）并以独立
  槽位解决（与 M14-267 服务端 URL 拆分同构的语义拆分先例）。

## 非声明（explicit non-claims）

- **入口装配管线 ≠ 端到端已验收**：真实 LiveKit+FunASR+浏览器
  端到端属 M14-272 验收批。
- M4-02 provider 面契约原样保持：`ASR_LOCAL_ENDPOINT` 默认留空
  = 降级替身（compose 默认零变化——`test_local_voice_env_
  passthrough_and_host_gateway` 原样通过）。
- transcriber 惰性：入口/管线装配与 lifespan 期间零 ASR HTTP
  （真实转写在 live 会话 close 冲刷时才发生）。
- 零真实凭据；未启停任何生产/运行时服务；Web/Android/Harmony/
  DB/迁移/recovery 零触碰；compose command/镜像 CMD 零变化。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

- **`services/api/app/voice_entrypoint.py`（升级）**：
  `VoiceEntrypointSettingsLike` 扩为恰五属性（服务端 LiveKit 三件
  + `voice_asr_endpoint`/`asr_local_model`）；`_ServerUrlSettings
  Adapter` 把 `voice_asr_endpoint` 映射进 M14-272c
  `asr_local_endpoint`（浏览器面两字段仍结构性忽略）；
  `create_voice_entrypoint_app` 改调 `create_app_with_voice_pipeline`
  （凭据/URL/ASR 校验全复用 M14-265/266/272c——fail-closed 链
  扩展至 ASR 端点，缺失/空白导入即失败）；模块级 app
  `get_settings` 恰一次。
- **`services/api/app/core/config.py`**：Settings 新增
  `voice_asr_endpoint: str | None = None`（env `VOICE_ASR_ENDPOINT`）。
- **`infra/docker-compose.yml`**：api env 新增
  `VOICE_ASR_ENDPOINT: ${AIOS_VOICE_ASR_ENDPOINT:-
  http://host.docker.internal:8010/v1}`（宿主 FunASR 拓扑默认）；
  `ASR_LOCAL_ENDPOINT` 默认留空零变化。
- **`infra/docker-compose.rc-smoke.yml`**：parity 同步（键集+值
  逐字一致契约保持）；生产 pin 第六次显式更新（新 sha
  `1740f8ca…71ed5`）。
- **`.env.example`**：`AIOS_VOICE_ASR_ENDPOINT` 键 + 服务端容器
  转写地址（与 provider 面语义分离）注释。

## 测试（TDD：先改写断言确认约束，再实现转绿）

`services/api/tests/test_voice_entrypoint.py`（20 项——M14-267
的 18 项适配升级语义 + 2 项新增）：

1. 五属性映射（`voice_asr_endpoint` → `asr_local_endpoint` 直通；
   浏览器面两字段忽略断言保持）。
2. 包装恰一次调 `create_app_with_voice_pipeline`（透传
   database_url + keyword 设置）。
3. 非法 server URL 六例先于 app 创建失败（M14-265/266 路径）。
4. **新增**：ASR 端点缺失/空白三例 fail-closed（M14-272c 路径）
   先于 create_app（零调用断言）。
5. `get_settings` 恰一次且仅模块级（AST+正则锚定）。
6. fake-only lifespan：工厂与**管线**双注册（app.state.
   voice_subscriber_factory + voice_pipeline）、订阅者计数 0、
   零签发/零房间/零流、关停 closed。
7. main.py/Dockerfile 默认不变锚定。
8. compose 渲染：voice 入口 command + LIVEKIT_SERVER_URL +
   **VOICE_ASR_ENDPOINT 拓扑默认** + **ASR_LOCAL_ENDPOINT 默认
   留空不变** + 浏览器面三变量原样。
9. .env.example 双键文档断言。

## 验证（2026-10-16，worktree 内真实执行；零网络/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_entrypoint.py -q` | **20 passed** |
| `-m pytest`（十九文件语音回归：entrypoint + live_routes + pipeline_entry + funasr_adapter + pipeline + session_control + asr_stream + frame_channel + session_subscriptions + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **492 passed**（M14-272d 基线 490 零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped**（含 M14-01 provider 默认留空契约） |
| `-m ruff check`（触及文件） | All checks passed |
| `-m compileall`（触及文件） | OK |
| `git diff --check` | 干净 |

实施中的修正：首版直接改 `ASR_LOCAL_ENDPOINT` compose 默认值被
`test_compose_profiles` 的 M14-01 契约（默认留空 = 降级替身不
虚报）正确拦截——改为独立 `VOICE_ASR_ENDPOINT` 槽位（与 M14-267
服务端 URL 拆分同构），provider 面零回归。

## 后续空间（M14-272 验收批，不在本切片）

- 本机真实端到端：起隔离验收栈（voice 入口 + LiveKit）+ 浏览器
  麦克风发布 → live 会话 open → 作答/打断 → close → transcript
  文本校验；Web 优先，Android 真机/Harmony 分批；
- 后台 pump 调度（实时流式转写需要时）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-272e（M14-272d 转
  前一任务快照，含 PR #362 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-272e 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-272e 条目（含非声明）。
