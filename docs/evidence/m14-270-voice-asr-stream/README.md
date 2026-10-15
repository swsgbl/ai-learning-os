# M14-270 证据：流式 ASR 消费链（纯域层、可替换 adapter，fake-only）

- 分支 `api/m14-270-voice-asr-stream`（worktree
  `ai-learning-os-worktrees/m14-270-voice-asr-stream`），基于
  current main `72c0a800`（PR #356 merge，即 M14-269）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-269 帧管道可 `drain` 但无消费者。本切片补上流式
  ASR 消费链：音频流交给**可替换 ASR adapter**（结构协议——
  本地 FunASR 是未来的一个 adapter 实现，本切片全部行为用
  fake adapter 确定性验证）。

## 非声明（explicit non-claims）

- **消费链协议 ≠ 真实 ASR 可用**：本切片零真实 FunASR 调用、
  零网络；FunASR adapter 实现与真实接线属后续切片（M14-272
  端到端）。
- **不回退浏览器语音识别**：模块无任何 Web Speech API 路径
  （源码卫生文本断言禁 SpeechRecognition/webkitSpeechRecognition）
  ——服务端消费链是唯一 transcript 来源。
- 不做 VAD/barge-in/pause-resume（M14-271）；不加 HTTP 路由/
  readiness、不进 lifespan、不连真实 LiveKit/ASR、不落盘。
- 零真实凭据（测试全为显眼假值）；未启停任何生产/运行时服务；
  不触碰 compose/env/Web/Android/Harmony/DB/迁移/recovery/edge/
  TURN。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/domain/voice_asr_stream.py`（新增，纯域层）**：

- `VoiceAsrAdapterLike`（`feed(chunk) -> list[AsrSegmentLike]` 增量
  + `finish() -> list[AsrSegmentLike]` 冲刷）与 `AsrSegmentLike`
  （`text`/`is_final`）——runtime_checkable 结构协议，可替换
  （本地 FunASR / 云端 ASR 均为未来实现）。
- `VoiceAsrStreamSession`：
  - `pump(limit)`：`channel.drain(limit)` → 逐 chunk **顺序
    一致**送 `adapter.feed` → 返回段进服务端 transcript；
    `AsrPumpResult` 投影（delivered/new_segment/failed/error/
    expired——纯计数）；
  - **transcript 权威来源 = 服务端会话**：`seq` 由会话单调分配
    （adapter 不参与编号）；有界环形（默认 64 段，超上限淘汰
    最旧 + 全量计数含被淘汰者）；快照全新拷贝；`text` 先 JWT
    形态净化再截断（默认 512 字符）；
  - **会话绑定**：`session_id` 复用 M14-268 保守边界
    （`validate_session_id`，异常转本层净化错误——不另立宽松
    边界）；
  - **失败恢复**：单 chunk `feed` 抛错只计
    `failed_chunk_count`/`error_count`（有界环形错误事件，通用
    净化文案，adapter 异常文本绝不进入投影）——其余 chunk 照常
    消费、已交付段保留、会话保持 active；
  - **超时边界**：可选注入 `clock`（monotonic callable）+
    `idle_timeout_seconds`——pump 开始检查距上次进度（收到
    chunk 或产出段）是否超窗，超窗则会话 `expired`（终结不再
    feed；`finish` 仍幂等收尾）；未注入 clock 跳过检查；
  - 状态机：active → finished（finish 冲刷 adapter 尾段）/
    expired（idle 超时）；终结后 pump 确定性拒绝（API 误用）、
    finish 幂等 no-op。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_asr_stream.py`（新增，20 项
fake-only 契约测试）：`ScriptedAdapter`（按 chunk 序号脚本化
产出段/抛错，含诱饵异常文本）+ `FakeClock` + 真实
`BoundedVoiceFrameChannel`。

1. 构造校验：session_id 非法拒绝、adapter/channel 缺失拒绝、
   正数参数（4 例）、fake 结构满足协议。
2. 消费链：三 chunk 顺序逐字一致到达 feed；段进 transcript
   （text/is_final 保真）。
3. pump 分批（limit 生效）与可重复、空转零交付。
4. seq 单调无重复、快照全新拷贝（篡改不入内账）。
5. 环形有界（max=3 下 10 段只留最新 3、全量计数 10）。
6. text 净化（JWT 形态替换）+ 截断（512 有界）。
7. 失败恢复：单 chunk 抛错隔离（前后段保留、会话 active、
   异常文本不泄漏）；全失败后新会话正常。
8. finish：冲刷尾段计数、终结 finished、后续 pump API 误用、
   finish 幂等（adapter.finish 恰一次）。
9. 超时：超窗 expired（零 feed）、进度刷新重置窗口、无 clock
   兼容跳过检查。
10. 快照序列化无凭据/token。
11. 源码卫生（AST+文本）：导入白名单 {__future__, typing,
    collections, dataclasses, re} + 域内运行时引用
    {voice_session_subscriptions}；禁网络/文件/线程/asyncio/
    HTTP client；文本禁 SpeechRecognition/webkitSpeechRecognition。

## 验证（2026-10-15，worktree 内真实执行；全部零网络/零真实 ASR/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_asr_stream.py -q` | **20 passed** |
| `-m pytest`（十四文件语音回归：asr_stream + frame_channel + session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **398 passed**（M14-269 基线 378 零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（新两文件） | All checks passed |
| `-m compileall`（同两文件） | OK |
| `git diff --check` | 干净 |

TDD 顺序：先建测试确认 RED（模块不存在，collection error），
实现后转 GREEN；两轮修正（①session_id 校验异常面统一转本层
净化错误；②模块 docstring 含断言敏感字样改写为中文表述）。

## 后续空间（不在本切片）

- 本地 FunASR adapter 实现（真实 HTTP/WS 调用与重试边界）；
- VAD/barge-in/pause-resume（M14-271，VoiceSession FSM 接线）；
- 真实 LiveKit + FunASR 端到端验收（M14-272）；HTTP 路由/
  lifespan 接线；transcript 与 voice session FSM 的会话级编排。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-270（M14-269 转
  前一任务快照，含 PR #356 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-270 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-270 条目（含非声明）。
