# hm-parity v5 验收记录 — 语音陪练命令集 + 会话恢复槽

日期:2026-10-25 · 分支 `feat/hm-parity-v5`(基 48d6694a) · 验收人:hmh

## 一、范围与缺口闭环

| 缺口 | 内容 | 状态 |
|---|---|---|
| A | 语音陪练 8 命令集补全(契约=server voice.py L643 commands + VoiceCommandOut) | ✅ |
| B | 会话恢复槽(对齐 Android VoiceResumeStore M12-03 语义) | ✅ |
| C | hintFor(status) 状态文案对齐(照抄 Android 源码) | ✅ |
| 隐藏D | VoiceExamApi 鉴权缺失修复(owner 端点 401)+ 4xx detail 透出 | ✅(探针发现,必修) |
| 隐藏E | voice report 前置「考试已判分」——UI 收口前先 submitExam | ✅(探针实证 409 根因) |

## 二、代码变更(apps/harmony,五文件)

1. **AiosApi.ets**:导出 `buildProtectedHeader`(原 private;供 VoiceExamApi 复用 bearer)
2. **voiceexam/VoiceExamApi.ets**(重写,~330 行):
   - 全端点 POST/GET 附 `Authorization: Bearer`(fail-closed:token 缺失时不带,服务端 401 如实展示)
   - `httpErrorDetail`:409/404/503 尽力提取服务端 JSON `detail` 字符串如实展示;400/422 固定文案;5xx 固定兜底(语义对齐 AiosApi.requestJson)
   - 8 命令字面量常量 + `sendVoiceCommand(baseUrl, sessionId, req)` 通用透传(服务端 FSM 唯一权威,客户端不推演)
   - `VoiceCommandOut` 契约补全:`clarified_question`(进 CLARIFYING 时 TTS 追问文案)/`question_total`(report_ready 全卷完成透出)
   - `transcribeWav` multipart 头附 Authorization(transcribe 按 owner 解析 provider key)
3. **voiceexam/VoiceResumeStore.ets**(新增,~110 行):
   - preferences `aios_voice_resume` 两键 `voice_resume_session_id_v1`/`voice_resume_exam_id_v1`
   - `loadVoiceResumeSlot`(fail-open:null 槽不阻塞选卷)/`saveVoiceResumeSlot`/`clearVoiceResumeSlot`
   - 语义逐条照抄 Android:save=开新会话/恢复成功;clear=报告已取/404 不存在/409 已终态;服务端 GET resume 为唯一真相
4. **voiceexam/VoiceExamModels.ets**:`statusLabel`/`hintFor`(照抄 Android VoiceScreen.kt L304-324,8 状态+6 提示)
5. **voiceexam/VoiceExamViews.ets**:追加 `VoiceCommandPane`(按钮显隐矩阵对齐 Android AdvanceControls L444-493:
   SESSION_READY→开始读题 / ANSWER_COMMITTED→确认进下一题 / NEXT_QUESTION→读下一题+完成全卷 /
   REPORT_READY→生成报告 / WAITING_ANSWER|CLARIFYING→跳过本题 / 非终态恒显「结束语音陪练」红字)+
   `VoiceResumeCard`(继续上次语音作答/放弃恢复)
6. **pages/VoiceExamPage.ets**:
   - 状态:`resumeSlot`/`resuming`;aboutToAppear 读槽;startExam 成功存槽;alignResume 404/409 清槽;
     loadReport 成功清槽
   - `sendCommand(type)`:8 命令统一发送;end/report_ready → 先 `submitExam`(voice report 前置判分,探针实证)再取报告
   - ActiveView 挂 VoiceCommandPane;PickView 有槽时挂 VoiceResumeCard;QuestionPane status 传 `statusLabel+hintFor`

**约束遵守**:build-profile.json5 未动;六 Tab 结构零改动(main_pages.json 未增页,VoiceExamPage 为 v3 既有路由);
所有请求走 requestJson 同款鉴权模式。

## 三、构建与装机

- hvigorw assembleHap(release)×2 次(代码迭代后重装)均 BUILD SUCCESSFUL
- 装机链:`hm-patch-hap-v2.cjs`(API23 化)→ `v3-install.cjs`(设备侧签装)→ 192.168.161.137:10178
- 装机日志:`install bundle successfully` + `start ability successfully`

## 四、设备 UI 验证(structure dump,`hdc shell uitest dumpLayout`)

| 检查 | 结果 |
|---|---|
| 六 Tab(首页/学习/搜索/语音/设置/治理) | PASS(dump-home.txt) |
| 语音 Tab:🎙开始语音作答 + Providers 区 | PASS(dump-voice2.txt) |
| VoiceExamPage 加载:选卷视图 + 401 未登录如实展示 | PASS(dump-pick2.txt) |
| 麦克风权限弹窗 → 允许 → 进选卷 | PASS(dump-pick.txt) |
| App 重启无崩溃,hilog 无 error | PASS |

**已知限制**:VM 无麦克风(transcribe 不验,按任务书);设备 IME 拦截字母输入(qa 凭据无法键入,登录后 UI 链
由生产探针背书——同端点同命令体同 FSM)。

## 五、生产探针(宿主→ ndtool.cn 真服务,16/16 PASS)

`probe-v5-commands.cjs` → probe-run2.txt:

```
PASS login / papers(7) / createExam / createSession(SESSION_READY)
PASS start_reading    → READING_QUESTION
PASS question_read    → READING_OPTIONS
PASS options_read     → WAITING_ANSWER
PASS resume#1         → q=…SELECT 基础查询 id=… total=4
PASS answer_proposed  → ANSWER_COMMITTED(服务端确定性落库)
PASS answer_proposed#2(覆盖提交) → ANSWER_COMMITTED
PASS commit_confirmed → NEXT_QUESTION
PASS report_ready     → REPORT_READY total=4
PASS submitExam       → HTTP 200
PASS report           → HTTP 200 mistakes/remediation 投影
PASS post-terminal 409(终态拒 start_reading,detail 透出)
PASS no-auth 401(无 bearer 访问他人会话)
```

探针发现的两个真缺口(均已修):
1. VoiceExamApi 原实现无 Authorization → 全部 owner 端点 401(隐藏缺口D)
2. voice report 前置考试已判分,UI 收口命令后须先 submitExam(隐藏缺口E;探针先复现 409 后实证修复路径)

## 六、仓库验证门

| 门 | 结果 |
|---|---|
| npm run typecheck | PASS(tsc --noEmit 零错) |
| npm run lint | PASS(0 errors,15 warnings 均为 web 既有 react-hooks) |
| npm run build | PASS(Next.js 全路由产出) |
| pytest(venv·语音域 4 文件) | 78 passed |
| ruff check services/api | All checks passed |
| pytest(系统 Python) | 环境缺 sqlalchemy,与本次改动无关(未动 Python 侧) |

## 七、结论

三缺口闭环 + 两个探针实证的隐藏缺口修复;构建装机一次通过;设备结构验证 + 生产探针 16/16 全 PASS;仓库验证门全绿。语音块收官。
