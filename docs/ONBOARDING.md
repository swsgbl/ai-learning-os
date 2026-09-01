# Onboarding：10 分钟上手 AI Learning OS

> 验收语义（backlog M7-02）：新用户 10 分钟内完成**初始化、导入课程和第一套题**。
> API 侧全路径毫秒级（`python -m app.ops.onboarding` 实测 < 100ms），耗时主体是安装与阅读；
> 本指南的每一步都有可执行验证点，全部步骤由 `tests/test_onboarding_guide.py` 自动复刻。

## 第 0 步 · 前置条件

| 需要 | 说明 |
|---|---|
| Docker Desktop | 运行生产本地版（推荐，一条命令起全栈） |
| 或 Python 3.12 + Node 22 | 开发模式（见 README「本地启动」） |

## 第 1 步 · 一条命令起栈（初始化）

```bash
cd ai-learning-os
docker compose -f infra/docker-compose.yml --profile local up -d --build
```

等待全部服务 healthy（首次构建约 3-6 分钟，复用缓存约 12 秒）：

```bash
docker compose -f infra/docker-compose.yml ps
```

预期：postgres / redis / minio / api / web（/ livekit）状态列为 `healthy` 或 `running`。
API 就绪验证：

```bash
curl http://127.0.0.1:8000/health
# 预期 {"status":"ok",...}
```

> 开发模式替代方案：README「本地启动」节（uvicorn + Next dev）。
> 宿主端口冲突时设置 `AIOS_WEB_PORT`（web）——见 README 生产本地版节。

## 第 2 步 · 导入课程（授权治理路径）

AI Learning OS 的课程内容进入可复用池必须走授权治理：认定来源授权 → 上传（继承授权快照）→ 解析 → 生成导入草稿 → **人工审核通过**。

把示例课程文档存为 `course.json`：

```json
[
  {"question": "极限的定义", "answer": "描述函数当自变量趋近某点时的趋势"},
  {"question": "导数的定义", "answer": "函数增量与自变量增量之比的极限"},
  {"question": "积分的定义", "answer": "分割、求和、取极限"}
]
```

执行 5 个请求（识别出的概念：极限、导数、积分）：

```bash
# 2.1 认定来源授权（示例：开放许可）
curl -X POST http://127.0.0.1:8000/api/v1/sources -H "Content-Type: application/json" -d '{
  "id": "src_onboarding_demo",
  "name": "onboarding 示例来源",
  "source_type": "oer",
  "license_state": "OPEN_LICENSE",
  "homepage": "https://example.edu/"
}'

# 2.2 上传课程文档（继承来源授权快照）
curl -X POST http://127.0.0.1:8000/api/v1/resources/upload \
  -F "source_id=src_onboarding_demo" \
  -F "title=微积分概念讲义" \
  -F "file=@course.json;type=application/json"

# 2.3 解析资源（同步返回 chunks）
curl -X POST http://127.0.0.1:8000/api/v1/resources/{resource_id}/parse

# 2.4 生成导入草稿（命中概念提取模式「X 的定义」→ 极限/导数/积分）
curl -X POST http://127.0.0.1:8000/api/v1/courses/import-drafts -H "Content-Type: application/json" -d '{\
  "resource_id": "<resource_id>"
}'

# 2.5 人工审核通过（唯一离队路径）
curl -X POST http://127.0.0.1:8000/api/v1/courses/import-drafts/{draft_id}/approve \
  -H "Content-Type: application/json" -d '{"note": "内容审核通过"}'
```

验证点：
- 2.1 返回 **201**，`license_state=OPEN_LICENSE`
- 2.2 返回 **201**，记下响应里的 `id`（即 `{resource_id}`）
- 2.3 返回 **200**，响应含 `chunks` 计数
- 2.4 返回 **201**，`concepts` 为 `["极限", "导数", "积分"]`、`status=pending_review`
- 2.5 返回 **200**，`status=approved`（终态，重复审核返回 409）

> 课程生成的完整工作流（DAG/大纲/课时/习题/补救）见 M5-07：需要先发布概念 DAG，
> 属进阶用法，不占 10 分钟主线。

## 第 3 步 · 导入第一套题

把示例试卷存为 `paper.json`：

```json
[
  {
    "title": "我的第一套题",
    "duration_seconds": 900,
    "policy": "exam",
    "questions": [
      {
        "question": {
          "question_type": "mcq",
          "stem": "1. 函数在一点连续是可导的什么条件？",
          "options": ["必要不充分", "充分不必要", "充要", "既不充分也不必要"],
          "answer": {"option_index": 0},
          "explanation": "可导必连续，连续不一定可导。",
        },
        "score": 3.0
      },
      {
        "question": {
          "question_type": "true_false",
          "stem": "2. 可导函数一定连续。",
          "answer": {"value": true},
          "explanation": "可导必连续是标准定理。",
        },
        "score": 3.0
      },
      {
        "question": {
          "question_type": "short_answer",
          "stem": "3. 写出导数的定义式。",
          "answer": {"accepted": ["f'(x) = lim(h->0) [f(x+h) - f(x)] / h"]},
          "explanation": "导数即瞬时变化率，定义为增量比的极限。",
        },
        "score": 4.0
      }
    ]
  }
]
```

### 3.1 导入

```bash
curl -X POST http://127.0.0.1:8000/api/v1/papers/import \
  -H "Content-Type: application/json" -d @paper.json
```

验证点：返回 **201**，`imported` 数组含 paper_id，记下第一卷的 id（即 `{paper_id}`）。

## 第 4 步 · 第一场考试（开考 → 作答 → 交卷 → 报告）

```bash
# 4.1 开考（201，返回 exam_id 与 questions）
curl -X POST http://127.0.0.1:8000/api/v1/papers/{paper_id}/exams -H "Content-Type: application/json" -d '{"mode": "exam"}'

# 4.2 作答（200；sequence 从 1 连续递增）
curl -X PUT http://127.0.0.1:8000/api/v1/exams/{exam_id}/answers -H "Content-Type: application/json" -d '{
  "sequence": 1, "question_id": "<第1题id>", "answer": "A"
}'

# 4.3 交卷判分（200）
curl -X POST http://127.0.0.1:8000/api/v1/exams/{exam_id}/submit -H "Content-Type: application/json" -d '{}'

# 4.4 查看报告（200）
curl http://127.0.0.1:8000/api/v1/exams/{exam_id}/report
```

验证点：
- 4.1 **201**：`exam_id` + 3 题（`questions` 数组）
- 4.2 **200**：每题一个 `sequence`（1、2、3 连续递增），乱序返回 409
- 4.3 **200**：判分完成
- 4.4 **200**：`score_earned=6.0`（第 1、2 题答对）、`mistakes` 含第 3 题、补救任务已生成

## 第 5 步 · 今日任务与个性化选题

```bash
curl http://127.0.0.1:8000/api/v1/student/daily-plan
curl http://127.0.0.1:8000/api/v1/student/selection
```

验证点：
- **200**：plan 含 review / mistake_retry / new_learning 三类任务，每项带可解释 `reason`
- **200**：selection 含 retry / weak / advanced 分组，错题所在概念进入 retry/weak
- 刚交卷的错题（第 3 题）会出现在次日任务里（错题重测通道）

## 一键复刻全部步骤（可执行验证）

```bash
cd services/api
../../.venv/Scripts/python.exe -m app.ops.onboarding --base-url http://127.0.0.1:8000
```

输出每步耗时，断言 API 全路径 < 10 分钟预算（600s）；在 CI/测试中由
`tests/test_onboarding_guide.py` 以 TestClient 复刻同一 walkthrough。

## 常见问题

| 现象 | 处理 |
|---|---|
| 3000 端口被占 | 设 `AIOS_WEB_PORT`（web 宿主端口），语义不变 |
| 5432 被其他项目占用 | 本项目宿主侧用 **5433**（compose 已配），容器内网仍 5432 |
| 语音未启用 | `--profile local` 含 livekit；确认 `GET /api/v1/voice/providers` 的 voice_mode |
| 授权来源未认定 | 来源 seed 后 `license_state=UNKNOWN`，上传正文会被门禁拒绝（安全默认） |
| 手工重放指南步骤返回 409 | 同 id/name 的来源已存在（幂等守卫）；换一个 id/name 或直接跑一键复刻（自动加时间戳后缀可重放） |
