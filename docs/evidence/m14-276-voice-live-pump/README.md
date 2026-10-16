# M14-276 证据：live 会话后台 pump 调度 + Android 真机真语音端到端验收

- 分支 `api/m14-276-voice-live-pump`（代码，PR #367 已合并
  `ded869b3`）+ 本 docs 回填分支 `docs/m14-276-evidence`，基于
  current main `ded869b3`。

## 交付一：live 会话后台 pump 调度（代码已合并）

**问题**：帧通道 256 帧 ≈ 25.6 秒（10ms/帧）——长语音作答触发
drop_oldest 丢最旧音频，close 冲刷转写不完整（M14-274 验收恰在
窗口内未暴露；理论推导 + 契约测试实证）。

**修复**（`services/api/app/api/routes/voice_live.py`）：
- open 成功后启动 per-session 后台 pump 任务：`PUMP_INTERVAL_SECONDS
  = 2.0` 周期 `pipeline.pump_session(sid, 256)`（drain 通道 →
  `FunAsrBatchAdapter` 8 MiB 缓冲——真实音频节奏 10ms/帧 × 2s =
  200 帧 < 256 单周期安全）；
- close 路由先 `_stop_pump`（cancel + gather）再 `close_session`
  （无搬运竞态）；
- 会话终结（`VoicePipelineError`）循环自退出（不抛出——close/终结
  竞态下安静退场；lifespan 直接关停路径最长 2s 延迟自清理）；
- pump 注册表 `app.state.voice_pump_tasks` 惰性建（零模块级可变
  全局）；open 幂等不双开；周期可经 `app.state.voice_pump_interval`
  覆盖（测试面）。

**测试**（`test_voice_live_pump.py`，3 项）：
1. 长会话真实节奏：两批 150 帧跨 pump 周期 → 全部 300 帧进
   transcript（无 pump 时累计 300 > 256 会丢 44 帧）；
2. close 取消 pump（注册表无残留任务）；
3. 短会话 pump 与 close 冲刷共存帧恰一次（80 帧 = pump 搬 50 +
   close 冲 30，transcriber 恰一次调用）。

二十文件合计 **497 passed** 零回归；审计 93 passed 1 skipped；
ruff/compileall/diff-check 全过。

**生产**：`aios/api:m14-276-production`（`ccf6e3151b63`）已切换
上线，7/7 healthy（Web 保持 `m14-275-production`；基础设施未重建）。

## 交付二：Android 真机真语音端到端验收 PASS

**设备**：华为 MGA-AL00（HarmonyOS 兼容 Android app），USB 连接
（serial EYFBB22923201473）。

**拓扑**：本机验收 API（uvicorn 0.0.0.0:18000，隔离 PG 测试库
`ai_learning_os_test` + `VOICE_ASR_ENDPOINT=http://127.0.0.1:8010/v1`）
+ FunASR（8010 running）；App baseUrl 配置为 `http://192.168.8.3:18000`
（同网段 WLAN）。

**流程与结果**（adb 实操 + uiautomator 断言）：

| 步骤 | 结果 |
| --- | --- |
| `gradlew assembleDebug` 构建 + 安装 | ✓（签名冲突卸旧装新） |
| baseUrl 配置 → API 状态「正常」（本地模式 + health 200） | ✓ |
| 学习页 → 试卷库 → 「语音陪练」（函数与导数基础） | ✓ |
| 会话创建 + 开始读题推进（`commands` 200 ×3） | ✓ |
| 麦克风权限：系统弹窗「仅使用期间允许」 | ✓ |
| **真机麦克风录音**（「● 正在录音」+ 扬声器回环播放真实中文语音样本 `asr_sample_zh.wav` 约 14 秒） | ✓ |
| 停止录音 → 录音 WAV 上传 **`POST /api/v1/voice/transcribe 200`**（`192.168.8.7` 真机源——FunASR 真实转写执行） | ✓ |
| 转写文本 **`POST /api/v1/voice/sessions/{id}/intents 200`**（样本语音非答案 → 意图解析正确不提交作答） | ✓ |

**意义**：补上 M14-275 登记的真机缺口——此前华为浏览器 WebRTC
connect 失败（端侧兼容性，桌面同栈全通已证非服务端问题）；Android
**原生录音上传模式**（App 既有完整语音模块：AudioEngines/WavCodec/
VoiceViewModel 631 行）绕开浏览器 WebRTC 限制，真机麦克风 → 真实
语音 → 服务端 FunASR 转写全链实证。

**诚实边界**：未断言转写文本字面值（App 的 intents 流在非答案语音
下语义正确地不提交；FunASR 对同引擎同格式的文本转写已由 M14-272f
实证「希望你以后能够做到比」）；App 端无 LiveKit（原生录音上传
模式——与服务端 live 订阅链路互补，非同一路径）。

**验收后清理**：app 已卸载、验收 API 已停、一次性资源清理；生产
13 容器 healthy 无扰（生产栈零触碰）。

## 过程记录：Artemis 与浏览器路径（如实）

- Artemis 诊断 degraded（5/5 必需过，缺可选 scrcpy），但其 LLM
  网关凭据 401——agent 无法执行，降级为 adb 直接驱动（更可控）；
- 华为/夸克浏览器 WebRTC connect 均失败（token 通、ICE 不通）；
  `adb reverse tcp:3100` 解决了 localhost 安全上下文，但 ICE 仍不
  通——判定端侧浏览器兼容性，原生 App 路径绕开。

## 非声明

- 三 ready 标志均 false 不变；计数不变 implemented 60 / partial 3；
- 鸿蒙端开发待 hmharness 执行 M14-277 任务书（鸿蒙语音作答 MVP：
  AVRecorder → transcribe → commands——对标本验收已证的 Android
  原生路径）。

## 文档同步

- `docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` / `docs/CHANGELOG.md`
  同步更新（M14-275 转前一任务快照含合并结果）。
