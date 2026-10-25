# 深开鸿 v5：语音陪练命令集 + 会话恢复槽（语音块收官）

## 上一轮成果（v1-v4 已合并 main · 0f53eaac）

v1 学习冲刺+教材工作台；v2 三作答+考试；v3 AI 密钥+定制课程+语音选卷；
v4 搜索全流程。装机链/验收配方全固化。从 origin/main 切
`feat/hm-parity-v5`。

## v5 目标：语音陪练三缺口（对齐 Android VoiceScreen/VoiceViewModel）

现状：`voiceexam/VoiceExamApi.ets` 已有 createSession/resume/
postAnswerProposed（answer_proposed 一条命令）/transcribe/report；
VoiceExamPage 是考试作答页。

### 缺口 A：命令集补全（FSM 状态机推进命令）

Android VoiceScreen 用的命令（`viewModel.sendCommand(type)`）：
`question_read`（跳过播报读选项）、`options_read`（跳过开始作答）、
`start_reading`、`commit_confirmed`、`report_ready`、`skip`、`end`——
加上既有 `answer_proposed` 共 8 条。

**契约纪律（v3/v4 已验证的方法）**：服务端
`services/api/app/api/routes/voice.py`（约 643 行
`@router.post("/sessions/{session_id}/commands")` + VoiceCommandOut 与
FSM 事件定义）是唯一权威；对照 Android `AiosApi.kt` 的
`voiceCommand` 声明。命令按钮**按会话状态显隐**（VoiceScreen.kt 449-487
行各状态出现哪些按钮逐一对齐——读源码，勿凭猜测）。

### 缺口 B：会话恢复槽（断线/进程重启继续作答）

对齐 Android `data/local/VoiceResumeStore.kt`（逐行读，语义全抄）：
- 本地**只存 sessionId + examId 两键**（preferences，参照 SettingsStore
  模式；非凭据，可持久化——但 token/音频/transcript/判分绝不落本地）
- save 时机：开新会话/恢复成功；clear 时机：报告已取/404 会话不存在/
  409 已终态
- 恢复卡：VoicePane 选卷区显示「继续上次语音作答」入口（有槽时），
  点击走 GET resume 对齐服务端状态（本地槽只是提示，服务端是唯一真相）

### 缺口 C：状态提示文案对齐

VoiceScreen.kt 的 `hintFor(status)` 各状态中文提示逐条对齐（读源码抄）。

## 技术约定（v1-v4 全部沿用）

- 网络层 requestJson 模式（bearer fail-closed + detail 透出）
- 六 Tab 零改动；竞态防护 disposed+generation；409/404 detail 原样
- **勿改 build-profile.json5 SDK 版本**

## 构建与验收

构建：`cd apps/harmony && hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon`

装机链（全固化照抄）：`node .hm-tmp/hm-patch-hap-v2.cjs <in> <out>` →
`node .hm-tmp/v3-install.cjs`。
**注意 v4 新坑：hdc file send 对远端已存在同名文件静默失败——装机脚本
推文件前先 rm -f 远端旧文件并核对 size。**

设备：HDC 192.168.161.137:10178 · dump 参照 `.hm-tmp/v3-dump.cjs`
（输出到 .verify/hm-parity-v5/）

API 测试：生产 https://ndtool.cn/aios/api/v1 · qa_m15025 / Qa-M15-025!x9

## 验收清单

1. BUILD SUCCESSFUL + VM 装机 + 六 Tab 及 v1-v4 全入口不回归
2. 命令集：VoiceExamApi 导出 8 命令函数（或一条带 type 参数的通用函数）
   + VoiceExamPage 按状态显隐命令按钮（对齐 VoiceScreen 449-487 行）
3. 恢复槽：开卷后 VoicePane 出现「继续上次」入口；恢复→服务端对齐；
   完卷/404/409 后入口消失
4. **生产探针（重点——麦克风路径 VM 不可验，命令 HTTP 路径全可验）**：
   qa 开 session → 按状态逐命令发送 → 断言 VoiceCommandOut 响应与
   FSM 推进 → end 收口 → report
5. hintFor 状态文案 dump 抽查
6. `.verify/hm-parity-v5/{VERIFY.md,DELIVERY.md}` + dump + 探针输出

## 如实记录的限制

VM 无真实麦克风：transcribe/录音路径本棒不验（服务端 ASR 已在
生产验证过）；设备端命令交互以「结构 dump + 宿主命令探针」双轨。

## 交付物

新/改 ArkTS 源文件 + 验收证据（git commit 由 supervisor 做）
