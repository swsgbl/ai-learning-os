# hm-parity v5 交付说明 — 语音陪练命令集 + 会话恢复槽(语音块收官)

分支 `feat/hm-parity-v5` · 基于任务书 48d6694a · 2026-10-25

## 交付物

### 1. 语音陪练命令集(缺口A)

服务端 `POST /api/v1/voice/sessions/{id}/commands` 的 8 条 FSM 命令在鸿蒙端全量可用:

| 命令 | FSM 迁移 | UI 入口(VoiceCommandPane,按状态显隐) |
|---|---|---|
| start_reading | SESSION_READY/NEXT_QUESTION → READING_QUESTION | 「开始读题」/「读下一题」 |
| question_read | READING_QUESTION → READING_OPTIONS | (播报链自动;按钮矩阵对齐 Android) |
| options_read | READING_OPTIONS → WAITING_ANSWER | (同上) |
| answer_proposed | WAITING_ANSWER/CLARIFYING/ANSWER_COMMITTED → ANSWER_COMMITTED | 录音→转写→「提交作答」(v3 既有,补契约字段) |
| commit_confirmed | ANSWER_COMMITTED → NEXT_QUESTION | 「确认，进入下一题」 |
| skip | WAITING_ANSWER/CLARIFYING/ANSWER_COMMITTED → NEXT_QUESTION | 「跳过本题」 |
| report_ready | NEXT_QUESTION → REPORT_READY | 「完成全卷」 |
| end | 任意非终态 → REPORT_READY | 「结束语音陪练」(红字) |

权威=服务端 `voice_session_fsm.py`;客户端零状态推演,409/404 的 detail 原文展示后 GET resume 对齐。

### 2. 会话恢复槽(缺口B)

`VoiceResumeStore.ets`(preferences 两键):本地只存 `session_id`+`exam_id` 且只作恢复提示;
点「继续上次语音作答」必走 GET resume 对齐服务端真相;404(不存在)/409(已终态)/报告已取→清槽。
音频/transcript/判分结果绝不落本地。选卷页有槽时显示 `VoiceResumeCard`(继续/放弃恢复)。

### 3. 状态文案(缺口C)

`statusLabel`/`hintFor` 照抄 Android VoiceScreen.kt:8 状态中文标签 + 6 状态操作提示,
在 QuestionPane 状态行合并展示。

### 4. 隐藏缺口修复(探针实证)

- **鉴权**:VoiceExamApi 原自建 postJson/getJson 无 Authorization——服务端 `_require_owned_session`
  要求 owner,生产必 401。已统一 `buildProtectedHeader()`(内存 bearer)。
- **report 前置**:voice report 要求考试已提交判分(`get_submission` 非 null)。UI 收口命令
  (end/report_ready/交卷)后先 `submitExam`(幂等,已提交 409 忽略)再取报告。

## 验收证据

- `.verify/hm-parity-v5/VERIFY.md`(完整验收记录)
- `probe-run2.txt`:生产探针 16/16 PASS(ndtool.cn,qa_m15025,FSM 逐命令推进→终态→报告→409/401 边界)
- `dump-*.txt`:设备结构 dump(六 Tab/语音 Tab/VoiceExamPage/选卷/权限弹窗)
- 仓库门:typecheck/lint/build/ruff 全绿;语音域 pytest 78 passed

## 构建链(复现)

```
cd apps/harmony
hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon
node ../../.hm-tmp/hm-patch-hap-v2.cjs entry/build/default/outputs/default/entry-default-unsigned.hap ../../.hm-tmp/entry-v5-patched.hap
node ../../.hm-tmp/v3-install.cjs ../../.hm-tmp/entry-v5-patched.hap   # 192.168.161.137:10178
```

## 遗留与边界

- VM 无麦克风:录音→转写链路按任务书不在装机验收范围(dump+探针双轨已覆盖命令集/恢复槽/文案)
- 设备 IME 拦截宿主注入的字母文本:登录后 UI 交互链由生产探针背书(同端点/同命令体/同 FSM)
- git commit 由 supervisor 编排执行
