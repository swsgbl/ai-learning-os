# M14-272d 证据：Voice live 会话路由（服务端管线 HTTP 面，fake-only）

- 分支 `api/m14-272d-voice-live-routes`（worktree
  `ai-learning-os-worktrees/m14-272d-voice-live-routes`），基于
  current main `7e6afb2f`（PR #361 merge，即 M14-272c）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-272a/c 管线已 lifespan 安装但无 HTTP 面——浏览器/
  客户端无法触发服务端订阅转写。本切片交付服务端权威 REST 面。

## 非声明（explicit non-claims）

- **HTTP 面就绪 ≠ 端到端已验收**：三端（Web/Android/Harmony）
  本切片零触碰（逐步对齐此契约属后续批）；真实 LiveKit+FunASR
  浏览器端到端属 M14-272 验收批。
- 后台 pump 循环未做（close 确定性冲刷已覆盖批式转写路径——
  实时流式 pump 调度属后续需要时切片）。
- 测试 fake runtime 工厂 + fake transcriber——零真实网络零真实
  连接零真实凭据；未启停任何生产/运行时服务；不触碰 compose/
  env/DB/迁移/recovery。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/api/routes/voice_live.py`（新增）**——
`/voice/live-sessions`（路由恒注册；数据面仅语音入口（管线
安装）可用，否则 503）：

- `POST /{session_id}/open` {room}：打开服务端 live 会话——
  identity **服务端生成** `server-sub-<hex8>`（绝不接受客户端
  指定）；room 走既有公开边界（422）；同会话幂等（已打开 200，
  新开 201）；打开失败（工厂/订阅校验）409 通用净化文案。
- `GET /{session_id}`：组合状态投影（session_id/identity/
  fsm_state/fsm_revision/asr_status/buffered_frames/
  buffered_bytes/closed）。
- `POST /{session_id}/events` {event, revision?}：FSM 事件
  （M14-271：revision 乐观并发——不匹配/非法迁移/未知事件 409；
  响应带 applied）。
- `POST /{session_id}/vad` {signal}：VAD 信号映射（speech_start
  播报期等价 barge_in；speech_end 零迁移审计）。
- `GET /{session_id}/transcript`：服务端权威 transcript（M14-270：
  seq 单调有界净化）。
- `DELETE /{session_id}`：确定性关停（M14-272a 四段序：停帧源→
  消费残余帧→ASR 冲刷→终态审计面），响应携带 final transcript；
  幂等（已终结再删返回同终态投影）。
- 错误面：503（管线未安装）/404（未知或已终结会话，除 DELETE）/
  409（迁移拒绝/revision 冲突/open 失败）/422（session_id/room
  非法）。全部响应无凭据/token（域层净化白名单透传）。

**domain 小扩展（零回归）**：`ServerVoiceSessionHandle` 记
identity（`identity` property；`state()` 投影含 identity/closed）；
`ServerVoicePipeline.submit_vad_session` 薄方法；`open_session`
入参 identity 复用工厂保守边界（`validate_identity_of`）。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_live_routes.py`（新增，11 项
fake-only 契约测试；fake runtime 工厂 monkeypatch + fake
transcriber 记录拼接音频）：

1. 503 门：6 端点参数化（无管线 app——create_app 缺省）。
2. 全流程：open(201, identity 形态) → get(SESSION_READY) →
   events(start_reading → READING_QUESTION) → vad(speech_start →
   WAITING_ANSWER) → transcript(空) → DELETE(closed、无帧零转写、
   fake 房间断开) → get 404。
3. 帧路径：服务端通道注帧（与订阅者 on_frame 同入口）→ DELETE
   冲刷 → transcriber 收到逐字拼接 → transcript 文本映射。
4. revision 并发：正确应用；旧 revision 409 且状态不变。
5. 错误面：非法事件 409；未知会话 404（get/events/transcript/
   delete 四端点）；标识非法 422（4 例参数化）。
6. 幂等 open：二次 200、订阅者恰一（manager 条目 1、房间 1）。
7. 响应零凭据：get/transcript 无 fake key/secret/JWT 形态。

## 验证（2026-10-16，worktree 内真实执行；零网络/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_live_routes.py -q` | **11 passed** |
| `-m pytest`（十九文件语音回归：live_routes + pipeline_entry + funasr_adapter + pipeline + session_control + asr_stream + frame_channel + session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **490 passed**（M14-272c 基线 479 零回归——含被扩展 main.py/pipeline 既有测试） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（触及五文件） | All checks passed |
| `-m compileall`（触及文件） | OK |
| `git diff --check` | 干净 |

TDD 顺序：先建测试确认 RED（路由 404），实现后转 GREEN；三轮
修正（①503 测试 POST 需带合法 body（Pydantic 校验先于 handler）；
②无帧时批式适配器空缓冲 finish 零转写——断言对齐真实语义；
③帧注入改为服务端通道直注（避免在请求循环外触发订阅者
ensure_future）+ 幂等 open 动态 200 + 路径参数去斜杠）。

## 后续空间（M14-272 验收批，不在本切片）

- 三端接入：Web 优先（浏览器麦克风 → LiveKit 发布 → live
  会话 open → 作答 → close → transcript）；Android 真机/Harmony
  模拟器分批；
- 后台 pump 调度（实时流式转写需要时）；
- owner 鉴权接 DB voice_sessions 链（当前管线会话为内存态——
  auth on 且 DB 可用时挂 `_require_owned_session` 属三端接入批）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-272d（M14-272c 转
  前一任务快照，含 PR #361 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-272d 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-272d 条目（含非声明）。
