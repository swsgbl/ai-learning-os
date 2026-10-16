# M14-274 证据：Web 端接入服务端 live 会话（浏览器端到端实证）

- 分支 `api/m14-272e-voice-entrypoint-pipeline`（worktree
  `ai-learning-os-worktrees/m14-272e-voice-entrypoint-pipeline`），
  基于 current main `f798f6a`（PR #364 merge，即 M14-272f）。
- 动因：M14-272d live HTTP 面就绪且服务端链路（M14-272f）实证，
  但 Web 端未接入——浏览器无法触发服务端订阅转写。本切片完成
  Web 对齐并做**浏览器端到端验收**。

## 非声明（explicit non-claims）

- **浏览器 fake 麦克风为正弦音**：链路实证（帧实时到达服务端 +
  close 冲刷 FunASR 产出段）不含真语音文本——真机麦克风文本
  转写属 Android 真机/Harmony 分批验收批（真语音转写已由
  M14-272f SDK 发布者实证）。
- live 是**增强面**：open/close 全部 fire-and-forget 容错——失败
  绝不阻塞学生作答主流程（M14-252 录音上传 /transcribe 不变）。
- UI `server-live-transcript` 展示在完卷跳转竞态下可能不渲染
  （服务端 API 已证终态；小瑕疵后续微调）。
- 验收环境一次性：dev LiveKit 容器/本地 API/Web dev 均验收后
  清理；生产栈与 FunASR/CosyVoice 零触碰。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

- **`apps/web/src/lib/api.ts`**：`voiceLiveOpen(sessionId, room)` /
  `voiceLiveClose(sessionId)` / `voiceLiveTranscript(sessionId)` +
  投影类型（`VoiceLiveState`/`VoiceLiveClosed`/`VoiceLiveTranscript`
  ——M14-272d 契约）。
- **`apps/web/src/lib/live-session-flow.ts`（新增）**：
  - `openLiveSession(sessionId, room)`：幂等 open；404（服务端
    旧版无 live 路由）/5xx/网络失败静默降噪（console.debug）；
  - `closeLiveSession(sessionId)`：四段关停 → final transcript
    文本数组（404/网络失败 → null；空段 → []）。
- **`apps/web/src/components/voice-studio.tsx` 接线**：
  - `listen()`：`getOrCreateVoiceInput` 连接成功后
    `openLiveSession(sessionId, voiceRoomName(sessionId))`
    （同房间——服务端订阅者接收同一发布轨）；
  - `releaseVoiceInput`（完卷/离场/unmount 共用）：先断本地输入
    （停帧源）再 `closeLiveSession`——服务端 FunASR 冲刷后的
    transcript 进 `liveTranscript` state →
    `data-testid=server-live-transcript` 展示。
- **`apps/web/src/lib/live-session-flow.test.ts`**（新增 8 项）：
  open/close 全容错路径 + URL/method/body 契约。

## 浏览器端到端验收（2026-10-16，Playwright + 隔离栈）

拓扑：dev LiveKit 容器（127.0.0.1:17880）+ 本地 API（uvicorn
18000：`DATABASE_URL=…ai_learning_os_test` **隔离 PG 测试库**
（create_pg_test_db.py 创建——绝不触碰主库 ai_learning_os）+
`VOICE_ASR_ENDPOINT=http://127.0.0.1:8010/v1` + devkey）+
FunASR（8010 running）+ Web（`next build && next start` 3100 +
`AIOS_ACCEPTANCE_API_PROXY=http://127.0.0.1:18000` 同源代理
——M14-35 验收拓扑）+ Playwright Chromium（`grantPermissions
(['microphone'])` + fake 音频设备）。

流程与结果：

| 步骤 | 结果 |
| --- | --- |
| `/voice` 试卷列表 | 渲染（隔离库种子：函数与导数基础/算法复杂度入门） |
| 进入 `/voice/functions-basics` | VoiceStudio：DB voice session（`vs_dec85fef…`）+ TTS 播题 + WAITING_ANSWER 1/3 |
| 授予麦克风 + 点「语音作答」 | getUserMedia ✓ → LiveKit 连接 ✓ → 音轨发布 ✓ |
| `POST /api/v1/voice/live-sessions/{sid}/open` | **201 Created**（identity `server-sub-5d27de99`） |
| 录音中服务端状态 | **`buffered_frames: 256 / buffered_bytes: 245760`——浏览器麦克风帧实时到达服务端**（与 M14-272f SDK 发布者同量级） |
| `DELETE /api/v1/voice/live-sessions/{sid}` | **200**：四段关停 → **FunASR 真实 HTTP 调用** → transcript `segment_count: 1`、text `""`（fake 正弦音源的正确空输出——SenseVoice 对无语音内容返回空文本） |

过程发现与处置（不入库的验收技巧，供复现参考）：
- Next dev（Turbopack）模式页面 client hydration 未接管（effect
  不跑）——改用 `next build && next start` 生产 bundle；
- `NEXT_PUBLIC_API_BASE_URL` 运行时注入不稳定——改用仓库既有
  `AIOS_ACCEPTANCE_API_PROXY` 同源 rewrite 拓扑（M14-35）；
- Playwright Chromium getUserMedia 默认挂起（权限弹窗未决）——
  `context.grantPermissions(['microphone'])` 解决。

## 验证（2026-10-16，worktree 内真实执行）

| 命令 | 结果 |
| --- | --- |
| `npx vitest run src/lib/live-session-flow.test.ts` | **8 passed** |
| `npm test`（Web 全量） | **334 passed**（326+8） |
| `npm run typecheck` / `npm run lint` / `npm run build` | 全过 |

## 后续空间（不在本切片）

- 真机麦克风文本转写验收：Android 真机 / Harmony 模拟器或真机
  （分批——浏览器 fake 音源无真语音）；
- UI transcript 完卷跳转竞态微调；实时流式 pump 调度（需要时）；
- 生产 Web 镜像重建（含本切片 Web 改动——随下一次发布批）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-274（M14-272f 转
  前一任务快照，含 PR #364 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-274 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-274 条目（含非声明）。
