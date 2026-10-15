# M14-272b 证据：FunASR 批式转写适配器（纯域层，fake-only）

- 分支 `api/m14-272b-voice-funasr-wiring`（worktree
  `ai-learning-os-worktrees/m14-272b-voice-funasr-wiring`），基于
  current main `4dbecfa5`（PR #359 merge，即 M14-272a）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-270 流式 ASR 协议（`VoiceAsrAdapterLike`）就绪但无
  真实 provider 适配。本切片把本地 FunASR 的**批式**转写
  （M4-02 `LocalFunAsrAsrProvider.transcribe` 形态：整段音频 →
  文本）适配为流式协议。

## 非声明（explicit non-claims）

- **适配器协议就绪 ≠ FunASR 已接线**：transcriber 为同步
  callable 注入——真实 HTTP 客户端包装（本地 FunASR loopback
  端点）与线程池调度属接线层切片；本切片零网络零真实 ASR
  调用。
- 不加 HTTP 路由、不进 lifespan、不连真实 LiveKit；本机端到端
  验收属 M14-272c。
- 零真实凭据（测试全为显眼假值）；未启停任何生产/运行时服务；
  不触碰 compose/env/Web/Android/Harmony/DB/迁移/recovery。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/domain/voice_funasr_adapter.py`（新增，纯域）**：

- `FunAsrBatchAdapter(transcriber=, max_buffer_bytes=8 MiB)`：
  - `feed(chunk)`：批式累积（零转写——返回空列表；transcriber
    零调用）；无效 chunk（非 bytes/bytearray/memoryview、不可读）
    跳过计数绝不抛出；
  - `finish()`：拼接缓冲音频（顺序逐字一致）→ 恰一次调用
    transcriber → 返回单一 final 段（`.text` 映射产物、
    `is_final=True`）；幂等（二次 finish 空列表且 transcriber
    仍恰一次；空缓冲 finish 同样零调用）；
  - **内存有界**：`max_buffer_bytes` 上界（默认 8 MiB）——超界
    丢最旧保最新（拼接音频恒 ≤ 上界）；单 chunk 超总预算直接
    丢弃计审计；
  - **失败净化**：transcriber 抛错或产物 text 非 str → 通用净化
    错误（守卫后 `raise ... from None`——链彻底抑制），缓冲清空
    （不重试）；
  - `state()`：计数投影（buffered/received/invalid/dropped/
    transcribe_calls/failed/finished——无音频内容无文本）。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_funasr_adapter.py`（新增，14 项
fake-only 契约测试；`RecordingTranscriber`/`ExplodingTranscriber`
fake 同步转写器）：

1. 构造校验（transcriber 非 callable / max_buffer_bytes 非正）。
2. 结构协议（满足 `VoiceAsrAdapterLike`）。
3. feed 批式语义：三类有效字节形态累积、零转写调用、空列表
   返回、计数。
4. 无效 chunk 两类 + data 属性抛错——跳过计数绝不抛。
5. finish：恰一次、拼接顺序逐字一致、单一 final 段、交付后
   缓冲清空。
6. finish 幂等（二次空列表、transcriber 恰一次）。
7. 缓冲上界：丢最旧保最新（拼接可证）、单 chunk 超预算拒绝。
8. 压力参数化（40 chunk 两组）上界恒成立。
9. 失败净化：异常文本/端点词零泄漏、`__cause__`/`__context__`
   均 None、failed 计数、缓冲清空。
10. 与 M14-270 消费链全链集成：通道帧 → 会话 pump（批式零段）→
    finish 冲刷单一 final 段进服务端 transcript。
11. 源码卫生（AST+文本）：白名单导入；禁网络/HTTP client/文件/
    线程/asyncio。

## 验证（2026-10-15，worktree 内真实执行；零网络/零真实 ASR/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_funasr_adapter.py -q` | **14 passed** |
| `-m pytest`（十七文件语音回归：funasr_adapter + pipeline + session_control + asr_stream + frame_channel + session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **462 passed**（M14-272a 基线 448 零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（新两文件） | All checks passed |
| `-m compileall`（同两文件） | OK |
| `git diff --check` | 干净 |

TDD 顺序：先建测试确认 RED（模块不存在），实现后转 GREEN；
一轮修正（模块 docstring 含断言敏感字样改写）。

## 后续空间（不在本切片）

- 最终接线切片：同步 transcriber 的真实 HTTP 包装（本地 FunASR
  loopback）+ ServerVoicePipeline 的 lifespan/路由安装（复用
  M14-266 manager 单一管理面）；
- M14-272c：本机端到端验收（compose 栈 + 真实 LiveKit +
  FunASR + 浏览器录音；Web/Android/Harmony 分批）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-272b（M14-272a 转
  前一任务快照，含 PR #359 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-272b 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-272b 条目（含非声明）。
