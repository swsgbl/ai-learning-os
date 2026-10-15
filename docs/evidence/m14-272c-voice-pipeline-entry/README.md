# M14-272c 证据：语音管线 lifespan 接线 + 同步 FunASR transcriber（fake-only）

- 分支 `api/m14-272c-voice-wiring`（worktree
  `ai-learning-os-worktrees/m14-272c-voice-wiring`），基于
  current main `32d629ac`（PR #360 merge，即 M14-272b）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-272a/b 交付了组装器与 FunASR 适配器，但管线仍未
  接进应用生命周期。本切片完成最终接线。

## 非声明（explicit non-claims）

- **接线就绪 ≠ 语音能力可用**：不做 HTTP 路由（voice session
  控制端点属三端 API 契约对齐后的独立切片）；不切换部署入口
  （`voice_entrypoint` 仍为 M14-267 语义——compose 默认 CMD/
  command 零变化）；本机真实端到端（LiveKit+FunASR+浏览器）
  验收待路由后进行。
- 测试 httpx MockTransport 拦截——零真实网络、零真实 ASR 调用、
  零真实凭据（全为显眼假值）。
- 未启停任何生产/运行时服务；不触碰 compose/env/Web/Android/
  Harmony/DB/迁移/recovery。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/main.py`（扩展，缺省零回归）**：

- `create_app` 新增 `voice_pipeline_factory: Callable[
  [VoiceRuntimeManager], object] | None = None`——非 None 必须
  callable（返回前确定性拒绝）；
- lifespan：manager 建立后调用安装 `app.state.voice_pipeline`
  （共享同一 manager——单一管理面；**注册 ≠ 启动**：安装不打
  开任何会话）；关停顺序：先关管线会话（逐会话确定性四段
  关停）→ 再关 manager（M14-258/259 契约零变化）→ 停 worker/
  引擎；缺省不安装该属性（既有测试零回归实证）。

**`services/api/app/voice_pipeline_entry.py`（新增）**：

- `VoicePipelineSettingsLike`：恰五属性（服务端 LiveKit 三件 +
  `asr_local_endpoint`/`asr_local_model`）；ASR endpoint 缺失/
  空白 fail-closed（服务端订阅转写链必须有 ASR——绝不静默
  空转）；
- `sync_funasr_transcriber(endpoint, *, model, api_key, timeout,
  transport)`：本地 FunASR OpenAI 兼容同步调用件——multipart
  表单 → `/audio/transcriptions`；api_key 空不发 Authorization
  头；loopback `trust_env=False`（M14-106：系统/环境代理不得
  劫持本机请求）；非 200/网络失败/坏 JSON/缺 text 统一净化
  文案（链彻底抑制——端点/响应体/状态文本绝不外泄）；
  transport 注入面供测试（MockTransport）；
- `build_voice_pipeline_factory(settings, *, transcriber=None)`：
  M14-266 校验先行（非法设置在任何管线/app 构造之前失败）+
  每会话 `event_sink_factory` 绑定 split + `FunAsrBatchAdapter`
  装配（transcriber 显式注入优先——测试/可替换面）；
- `create_app_with_voice_pipeline(database_url, *, settings,
  transcriber=None)`：受控工厂注册 + 管线注入（绝不半装配）。

**`services/api/app/domain/voice_runtime_factory.py`（扩展，零
回归）**：`build_room_aware_controlled_voice_subscriber_factory`
新增可选 `event_sink_factory` 透传（缺省 None 行为零变化）。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_pipeline_entry.py`（新增，17 项
fake-only 契约测试；fake runtime 工厂 monkeypatch 同 M14-267
入口测试口径）：

1. 设置协议结构满足。
2. 非法 LiveKit 设置（4 例）先于 app 创建失败（净化——无
   凭据泄漏）。
3. ASR endpoint 缺失/空白（3 例）fail-closed 拒绝。
4. lifespan 安装：`app.state.voice_pipeline` 在册、订阅者 0、
   零房间构造（零 LiveKit 连接）、`/health` 200；退出后
   manager closed。
5. 经 `app.state.voice_pipeline` 打开/关闭会话：订阅者经
   manager 在册 → 释放干净（房间断开）。
6. transcriber：请求形状（URL/method/multipart/音频/model）、
   无 key 无 Authorization 头、有 key Bearer、HTTP 500 净化
   （无 "secret"/状态文本）、坏 JSON 净化、text 映射。
7. main.py 缺省零回归：不传 factory 时无 `voice_pipeline`
   属性（M14-262 语义不变）。
8. 源码卫生（AST+文本）：导入白名单（domain 五模块 + fastapi/
   httpx + app.main/voice_app）；无 LiveKit SDK 直连/os.environ/
   subprocess/threading。

## 验证（2026-10-15，worktree 内真实执行；零真实网络/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_pipeline_entry.py -q` | **17 passed** |
| `-m pytest`（十八文件语音回归：pipeline_entry + funasr_adapter + pipeline + session_control + asr_stream + frame_channel + session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **479 passed**（M14-272b 基线 462 零回归——含被扩展的 main.py/runtime_factory 既有测试） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（触及四文件） | All checks passed |
| `-m compileall`（同四文件） | OK |
| `git diff --check` | 干净 |

TDD 顺序：先建测试确认 RED（模块不存在），实现后转 GREEN；
三轮修正（①`voice_app` 模块路径在 `app/` 而非 `app/domain/`；
②卫生白名单遗漏 collections 根；③测试异常断言精确化 +
未用变量清理）。

## 后续空间（不在本切片）

- HTTP 路由：voice session 控制端点（open/pump/close/状态/
  transcript + revision 409 语义）与三端对齐同一 API 契约；
- 部署入口切换评估（voice_entrypoint 改用
  `create_app_with_voice_pipeline`——需 compose ASR endpoint
  既有槽位核对）；
- M14-272 验收：本机真实端到端（LiveKit+FunASR+浏览器录音；
  Web/Android/Harmony 分批）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-272c（M14-272b 转
  前一任务快照，含 PR #360 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-272c 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-272c 条目（含非声明）。
