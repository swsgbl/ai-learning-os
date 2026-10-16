# M14-277 证据：鸿蒙端语音作答 MVP（hmharness 实现 + supervisor R1 + 模拟器端到端验收）

- 实现分支 `harmony/m14-277-voice-exam`（worktree
  `ai-learning-os-worktrees/m14-277-harmony-voice`），基于 current
  main `ded869b3`。**多智能体协作**：hmharness 智能体实现（commit
  作者 hmharness-agent）+ Claude Code supervisor 审核/R1 契约修正/
  模拟器验收/发布。PR #369 CI 5/5 合并（merge `50c6781d`）。

## 交付面（9 files，+1283）

- **`pages/VoiceExamPage.ets`**（400 行）：状态机页面（选卷→读题→
  录音→转写草稿→提交→下一题循环→报告）；emitter/disposed/
  requestGeneration 竞态防护（仓库既有模式）。
- **`voiceexam/WavRecorder.ets`**（239 行）：
  - `ohos.permission.MICROPHONE` 运行时申请（拒绝=如实不可用）；
  - **`@ohos.multimedia.audio` AudioCapturer 原始 S16LE 16 kHz/mono
    采集 + 手写 44 字节 RIFF 头**——纯内存组装、零落盘（AVRecorder
    无 PCM 编码（仅 AAC/MP3/G711MU/AMR）产不出服务端 FunASR 要求
    的 16bit PCM WAV——hmharness 的正确技术决策）。
- **`voiceexam/VoiceExamApi.ets`**（347 行）：五端点
  （`POST /voice/sessions`、`GET …/resume`、`POST …/commands`、
  `POST /voice/transcribe`（multipart 手工 boundary，field=audio、
  filename=answer.wav、MIME=audio/wav、60s 超时）、`GET …/report`）；
  Result 型 + 错误文案收敛（AiosApi 同款风格）。
- **`voiceexam/VoiceExamViews.ets`**（245 行）+ `VoiceExamModels.ets`：
  选卷/题目/操作/报告四个纯展示组件 + Phase/TurnState 枚举。
- Index 语音 Tab 入口按钮 + module.json5（MICROPHONE 权限 + reason）
  + main_pages.json 路由 + string.json 文案。

## R1 契约修正（supervisor 审核发现）

hmharness 自标注疑点属实：`answer_proposed` 请求体原为
`{type, answer_text}`——服务端 `VoiceCommandRequest` 契约是
**`answer` + `question_id`**（缺一 422）。修正：
`AnswerProposedRequest` 字段改名 + `postAnswerProposed` 加
`questionId` 参数 + 页面加 `@State questionId` 并在 alignResume
保存 `d.question.id`。已 amend 进交付 commit。

## CI 白名单显式更新

`tests/harmony_release/test_download_pane_scan.py` 的
`test_no_new_permissions_in_module_json` 原锁定权限集恰为
`{ohos.permission.INTERNET}`——M14-277 的 MICROPHONE 是语音作答
合法运行时权限（任务书授权范围内），显式更新白名单为
`{INTERNET, MICROPHONE}`（其余新权限仍拒绝——防线保留）。

## 模拟器端到端验收（supervisor，2026-10-17）

环境：DevEco 模拟器（`127.0.0.1:5555`）+ 隔离验收栈
（uvicorn 0.0.0.0:18000 于最新 main worktree——隔离 PG 测试库 +
`VOICE_ASR_ENDPOINT=http://127.0.0.1:8010/v1`）+ FunASR（8010）。
未签名 hap 经 `hdc install` 直装模拟器（developer mode 接受）。

| 步骤 | 结果 |
| --- | --- |
| `hvigorw assembleHap`（DevEco toolchains） | 零 ERROR（7 条 audio syscap 设备能力警告，带麦设备支持） |
| baseUrl 配置（`uitest uiInput inputText` 输入）→ 保存 | API 状态「服务（/health）正常」 |
| 语音 Tab →「开始语音作答」→ 选卷（函数与导数基础） | VoiceExamPage：第 1/3 题 + 等待作答 + A/B/C/D |
| 麦克风权限弹窗 →「允许」 | ● |
| **录音（「正在录音」+ 宿主扬声器回环播放真实中文样本）** | ● |
| 停止 → `POST /voice/transcribe` | **UI 回显真实中文转写「希望你以后能够做的比还好哟」** |
| 「提交答案」→ `POST …/commands`（R1 契约） | **ANSWER_COMMITTED**（FSM 正常流转；提示可覆盖或下一题） |

UI 操作工具：`hdc shell uinput`（点击/滑动/返回键）+
`uitest uiInput inputText`（URL 注入）+ `snapshot_display` 截图验收。

## 非声明

- 三 ready 标志均 false；Web/Android/服务端零改动；无 LiveKit/
  live-sessions 调用（原生录音上传模式——与 Web 的 live 订阅链
  互补，非同一路径）。
- 构建产物不入库（.gitignore 覆盖）；无真实凭据；验收环境用后
  清理（模拟器 app 卸载/验收 API 停止/一次性资源删除）。
- 鸿蒙 CI 无独立 job（本地 DevEco 构建 + Release tools 权限白名单
  契约为闸）。

## 文档同步

- CHANGELOG / PROJECT_STATUS / ROADMAP 同步（M14-276 转前一任务
  快照）。
