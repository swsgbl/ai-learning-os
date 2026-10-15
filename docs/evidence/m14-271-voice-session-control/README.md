# M14-271 证据：VAD/barge-in/pause-resume 服务端权威会话控制（纯域，fake-only）

- 分支 `api/m14-271-voice-session-control`（worktree
  `ai-learning-os-worktrees/m14-271-voice-session-control`），基于
  current main `1c93d3b8`（PR #357 merge，即 M14-270）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M4-03 VoiceSession FSM 有权威迁移矩阵（barge_in/pause/
  resume 事件已在矩阵中）但无服务端会话控制面——无 revision
  并发控制、无审计、无 VAD 信号映射。本切片补上控制层。

## 非声明（explicit non-claims）

- **控制协议 ≠ 真实 VAD/语音能力**：VAD 检测器（能量阈值/模型）
  实现与 HTTP 接线属后续切片（M14-272 端到端）；本切片零真实
  检测、零网络、零音频。
- **客户端只消费状态**：本模块零客户端概念、零 HTTP 路由、
  不进 lifespan；Web/Android/Harmony 对齐同一 API 契约属后续
  HTTP 接线切片——三端零触碰。
- 零真实凭据；未启停任何生产/运行时服务；不触碰 compose/env/
  Web/Android/Harmony/DB/迁移/recovery/edge/TURN。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/domain/voice_session_control.py`（新增，纯域）**：

- `VoiceSessionController`：
  - `submit(event, *, revision=None)`：迁移判据完全复用 M4-03
    `transition`（零重复矩阵）；未知事件（EVENTS 白名单外）/终态
    （REPORT_READY 封闭）确定性拒绝——状态不变、零审计残留；
  - **revision 乐观并发**：提交可带期望 revision——不匹配拒绝
    （状态/revision 均不变、零审计残留；转 409 属未来路由层）；
    缺省直接应用；应用成功 revision +1；
  - `submit_vad(signal)`：
    - `speech_start`：播报期（READING_QUESTION/READING_OPTIONS）
      等价 `barge_in`（迁移到 WAITING_ANSWER，审计记录信号语义
      `vad_speech_start`）；倾听期幂等自环（仍记审计）；
    - `speech_end`：恒零状态迁移（只记审计——答案提交属 ASR/
      transcript 链，VAD 信号不携带答案语义）；
    - 未知信号/终态确定性拒绝；
  - **pause/resume**：任意非终态自环（FSM 既有矩阵）、重复幂等
    （状态稳定、每次记审计）、终态拒绝；
  - **审计有界净化**：环形保留最新 N（默认 64，全量计数含被
    淘汰者）；事件白名单恰 {seq, event, from_state, to_state,
    revision}；seq 单调；快照全新拷贝、序列化无凭据/token；
  - `snapshot()`：客户端消费投影（state/revision/is_terminal/
    审计计数/最新 N 事件）。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_session_control.py`（新增，35 项
fake-only 契约测试；`at()` 便捷构造器经合法迁移到达指定状态）：

1. 构造校验：未知初始状态/非法审计上限拒绝；未知事件拒绝。
2. 全链迁移冒烟（SESSION_READY→…→REPORT_READY，revision 递增）。
3. barge-in：两播报态 → WAITING_ANSWER（审计 from/to 保真）；
   倾听期幂等自环；SESSION_READY/ANSWER_COMMITTED/
   NEXT_QUESTION 拒绝（拒后状态不变 + 零审计残留）。
4. VAD：speech_start 两播报态触发迁移（审计信号语义）+ 倾听期
   幂等（applied=True）；speech_end 零迁移只审计（倾听期与
   播报期）；未知信号拒绝（零审计）；终态拒绝。
5. pause/resume：四非终态参数化自环 + 重复 pause 三次幂等 +
   终态拒绝。
6. 事件确定性（同状态同事件恒同输出）。
7. revision：正确应用 +1；不匹配拒绝零残留；缺省直接应用；
   非正数拒绝。
8. 审计：seq 单调 + 环形有界（max=4 下 6 事件只留最新 4、全量
   计数 6）+ 全新拷贝（篡改不入内账）+ 白名单五字段 + 序列化
   无凭据。
9. 终态封闭：四事件全拒且审计零增量。
10. 源码卫生（AST+文本）：导入白名单 {__future__, typing,
    collections} + 域内 voice_session_fsm；禁网络/文件/线程/
    asyncio/HTTP client。

## 验证（2026-10-15，worktree 内真实执行；全部零网络/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_session_control.py -q` | **35 passed** |
| `-m pytest`（十五文件语音回归：session_control + asr_stream + frame_channel + session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **433 passed**（M14-270 基线 398 零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（新两文件） | All checks passed |
| `-m compileall`（同两文件） | OK |
| `git diff --check` | 干净 |

TDD 顺序：先建测试确认 RED（模块不存在，collection error），
实现后转 GREEN；一轮修正（VAD speech_start 审计记录信号语义
而非映射后事件名——`_apply` 增加 audit_name 覆盖）。

## 后续空间（不在本切片）

- VAD 检测器实现（能量/模型 → speech_start/speech_end 信号）；
- HTTP 接线：voice session 控制端点（submit/snapshot/revision
  409 语义）与三端对齐同一 API 契约；
- transcript 链与会话控制的服务端级编排（M14-272 端到端）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-271（M14-270 转
  前一任务快照，含 PR #357 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-271 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-271 条目（含非声明）。
