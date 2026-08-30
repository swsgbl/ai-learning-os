# 语音、考试与检索详细设计 v2

## Part A. 语音学习模式

## 1. 交互目标

语音模式面向三种场景：

1. 通勤/免盯屏学习。
2. 听觉学习者和无障碍需求。
3. 练习中的快速问答和复盘。

语音不是自由聊天入口，而是 Learning FSM 的实时控制通道。

## 2. Voice Quiz 完整流程

```text
用户：“开始语音做题”
  -> 创建/恢复 VoiceSession
  -> 选择 Paper 或自适应题组
  -> 播报规则和题量
  -> 读题干
  -> 读选项
  -> 等待答案
  -> 处理命令或答案
  -> 判分
  -> 短反馈
  -> 下一题
  -> 全卷完成
  -> 播报总分
  -> 逐题错题讲解
  -> 生成变式练习
```

## 3. 意图与答案规范化

| 用户说 | intent | normalized | FSM 行为 |
|---|---|---|---|
| 选 B | choose_option | B | 提交答案。 |
| 我选第二个 | choose_option | B | 提交答案。 |
| 我改成 C | change_answer | C | 追加覆盖事件。 |
| 重复题目 | repeat_question | null | 重读题干。 |
| 选项再说一遍 | repeat_options | null | 重读选项。 |
| 慢一点 | slow_down | null | 调整 TTS rate。 |
| 跳过 | skip | null | 标记 skipped。 |
| 不知道 | unknown_answer | null | 记录后进入讲解或下一题。 |
| 暂停 | pause | null | 暂存状态。 |
| 结束 | end | null | 生成当前报告。 |

歧义处理：

1. ASR confidence 低 -> 追问。
2. 多个选项 -> 追问。
3. 与题型不符 -> 追问。
4. 考试模式禁止回答泄露 -> 固定拒答。

## 4. 全卷报告

报告结构：

```text
1. 总分、正确率、完成题数、用时
2. 按概念得分
3. 错题列表
4. 每道错题：
   - 题目与你的答案
   - 正确答案
   - 考查概念
   - 为什么你的答案错
   - 正确思路第一步
   - 干扰项为什么诱人
   - 后续两道变式题
5. 今日复习建议
```

语音播报采用分层策略：

- 第一遍：短结论和总分。
- 用户选择某题后再深入讲解。
- 避免一次性读长报告。

## 5. 实时语音架构

### 在线模式

```text
Browser WebRTC -> LiveKit Cloud -> Voice Agent -> provider STT/LLM/TTS -> Learning API
```

适合快速启动、低维护和高质量语音。

### 本地模式

```text
Browser WebRTC -> self-hosted LiveKit -> Voice Agent
  -> FunASR or Qwen3-ASR
  -> local LLM optional
  -> CosyVoice
  -> Learning API
```

适合隐私优先和离线学习，但部署成本更高。

### 混合模式

```text
Local ASR + local personal context
  -> cloud reasoning only when needed
  -> local/cloud TTS
```

路由依据：

- 用户隐私设置。
- 任务复杂度。
- 网络质量。
- GPU/内存可用性。
- 成本预算。
- 是否涉及个人文档。

## 6. 延迟拆解

必须分别 tracing：

1. audio capture。
2. VAD endpoint decision。
3. ASR first token/final token。
4. intent normalize。
5. policy decision。
6. LLM first useful token。
7. TTS first audio。
8. network RTT。
9. state commit。

工程策略：

- 全链路 streaming。
- 题目和选项预合成或预加载。
- 常规命令走规则解析，不强制经过 LLM。
- TTS 按句切片。
- 状态提交与播报并行。
- Voice session sticky routing。
- 预热 ASR/TTS session。

## 7. 打断处理

当 AI 播报时用户说话：

1. VAD 判定用户开始说话。
2. 停止 TTS 输出。
3. 保留当前题状态。
4. 进入 ASR。
5. 判断是命令、答案还是无关语音。
6. 按状态机恢复或转移。

关键点：打断不能提交半成品答案，除非用户意图明确。

## Part B. 在线考试与审阅模式

## 1. 考试创建

输入：

- 课程或概念范围。
- 题型分布。
- 难度分布。
- 总分。
- 时长。
- 是否允许回看、修改、提示。

来源：

- 已审核题库。
- 用户上传试卷解析。
- AI 生成草稿 + 人工审核。

## 2. 服务端权威计时

流程：

1. `POST /exams/{id}/start` 时服务器写 `server_started_at`。
2. 根据服务器时间计算 `server_end_at`。
3. 客户端每秒显示由服务器同步校准后的剩余时间。
4. Redis/queue 注册 timeout 任务。
5. 数据库事务中做超时提交幂等保护。

客户端职责：

- 展示时间。
- 自动保存答案。
- 提示剩余时间。
- 断线时本地暂存。

客户端不负责：
- 决定是否超时。
- 生成最终成绩。
- 修改开始/结束时间。

## 3. 自动保存与断线恢复

答案事件：

```json
{
  "question_id": "q_001",
  "answer": "B",
  "client_event_id": "uuid",
  "client_occurred_at": "...",
  "base_revision": 31
}
```

服务端：

1. 校验 exam 未结束。
2. 校验 question 属于该卷。
3. 校验 revision。
4. append event。
5. 返回 new revision 和 server time。

断网时客户端暂存队列，恢复后按顺序提交；冲突按服务端 revision 解决。

## 4. 评分瀑布

```text
1. 格式校验
2. 缺答/未答策略
3. objective deterministic matcher
4. numeric/math verifier
5. coding sandbox tests
6. subjective rubric + evidence + LLM judge
7. confidence check
8. second judge / human review
9. report generation
10. student model update
```

### 主观题输出

```json
{
  "score": 7.5,
  "max_score": 10,
  "criteria": [
    {"name": "concept", "score": 3, "max": 4},
    {"name": "reasoning", "score": 2, "max": 3},
    {"name": "clarity", "score": 2.5, "max": 3}
  ],
  "confidence": 0.82,
  "evidence_ids": ["ev_1", "ev_2"],
  "judge_model": "model-x",
  "prompt_hash": "sha256:..."
}
```

## 5. 错题报告

维度：

- 总分和排名不做默认展示，避免无效比较。
- 题型正确率。
- 概念正确率。
- 用时异常题。
- 修改答案后变对/变错。
- 未答题。
- 重复误解。
- 先修知识风险。

错因分类：

| 类型 | 判断证据 |
|---|---|
| concept_error | 概念题反复错，讲解后仍错。 |
| prerequisite_gap | 目标题错因集中在先修节点。 |
| calculation_error | 思路正确但数值步骤错。 |
| interpretation_error | 题干条件误读。 |
| reasoning_error | 推导链断裂。 |
| memory_error | 曾对后错，间隔长。 |
| careless_error | 正确率高、时间短、修改频繁。 |
| time_pressure | 后半卷未答和超时占比高。 |

## 6. 补救路径

```text
错题
  -> 定位概念
  -> 判断先修缺口
  -> 最短讲解
  -> 1 道低难度修复题
  -> 1 道同构题
  -> 1 道迁移题
  -> 更新 mastery
  -> 安排 FSRS review
```

## Part C. 全网课程与真题检索

## 1. 查询理解

输入示例：“找 HKU 2023 到 2026 计算机视觉公开 final exam。”

解析：

- institution: HKU
- subject: Computer Vision
- material_type: exam
- exam_type: final
- year_range: 2023-2026
- language preference
- allowed sources

## 2. 搜索计划

并发查询：

1. 官方课程站。
2. 学校图书馆和 ExamBase。
3. 官方 GitHub organization。
4. OER 和开放课程平台。
5. 可信社区仓库。

每种来源使用不同策略：

- 官方站：优先。
- 图书馆：识别 restricted access。
- GitHub：检查仓库和内嵌材料授权。
- OER：读取具体 CC 条款。
- 社区：标记 B 级，需人工复核。

## 3. 结果排序

MVP 公式：

```text
score =
  0.30 * authority
 + 0.20 * recency
 + 0.15 * course_match
 + 0.15 * content_completeness
 + 0.10 * license_clarity
 + 0.10 * format_quality
```

硬性规则优先于分数：

1. 登录墙或访问受限 -> LINK_ONLY。
2. UNKNOWN license -> 不入公共池。
3. 疑似试卷答案分离 -> 不自动判分，先进入人工配对。
4. 题目解析失败 -> 不进入正式考试。

## 4. 文档解析与题目抽取

流程：

```text
PDF/image/DOCX
  -> OCR/layout parser
  -> page/block tree
  -> question boundary detection
  -> stem/options/answer/explanation extraction
  -> math/table/code normalization
  -> concept mapping
  -> confidence scoring
  -> human review
```

质量检查：

- 页码保留率。
- 公式还原率。
- 题目边界准确率。
- 选项配对准确率。
- 答案与解析配对准确率。
- 重复题检测。

## 5. 授权与导入动作

| 状态 | 动作 |
|---|---|
| OPEN_LICENSE | 可解析、可派生、按条款显示署名和 ShareAlike。 |
| ACCESS_CONTROLLED | 只给入口；用户自行访问，系统不缓存正文。 |
| ALL_RIGHTS_RESERVED | 只展示摘要和链接。 |
| UNKNOWN | 只作为线索，等待人工确认。 |
| PROHIBITED | 不导入。 |

## 6. 变式题生成

只有在满足以下条件时生成：

1. 原题或知识点有可用教学证据。
2. 不复制受保护表述。
3. 生成后通过 schema 校验。
4. 标记为 AI generated。
5. 与原题做相似度检查，避免无意义复写。

输出必须包含：

- 考查概念。
- 难度估计。
- 干扰项设计原因。
- 答案。
- 解析。
- 来源 evidence。

