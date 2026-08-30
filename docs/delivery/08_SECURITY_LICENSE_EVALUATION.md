# 安全、隐私与许可证评估 v2

## 1. 安全原则

1. 服务端是权威：身份、时间、答案、成绩、权限均以服务端为准。
2. 最小权限：Agent 工具按任务阶段动态挂载。
3. 默认拒绝：未知来源、未知授权、未知 URL 均不允许进入内容池。
4. 全链路审计：模型调用、抓取、评分和状态转移留 trace。
5. 沙箱隔离：模型生成代码只在受限环境执行。

## 2. 认证与授权

### 认证

- MVP：邮箱/密码或本地账号。
- 生产：OAuth/OIDC + refresh token rotation。
- 本地单用户模式可关闭对外暴露，但不得无鉴权暴露到公网。

### 权限矩阵

| 角色 | 课程 | 题库 | 考试 | 语音 | 搜索 | 管理 |
|---|---|---|---|---|---|---|
| Learner | read/learn | practice | own exam | own session | search/import request | no |
| Content Reviewer | review | review | paper draft | no | review queue | content only |
| Teacher | own course | own paper | own reports | optional | course sources | limited |
| Admin | all | all | audit | config | all | yes |

## 3. 考试安全

必须防御：

1. 修改客户端时间。
2. 刷新或重进延长考试。
3. 重复提交和并发提交。
4. 越权读取答案。
5. 考试中调用 search/hint/solution。
6. 语音模式泄露答案。
7. 事件乱序或重放。
8. 本地缓存答案被他人读取。

控制：

- 服务器时间权威。
- `submission` 唯一约束。
- event sequence 单调。
- exam tool policy 静态限制。
- 服务端再次校验答案所属题目和考试状态。
- 审计所有 submission transition。

## 4. 语音隐私

| 模式 | 音频去向 | 适用 |
|---|---|---|
| Local | 本机/局域网服务 | 隐私优先。 |
| Cloud | 供应商处理，需配置保留策略 | 质量优先。 |
| Hybrid | 本地 ASR，必要任务云端推理 | 平衡方案。 |

要求：

1. 默认不长期保存原始音频。
2. 如需保存，必须显式开启并设置保留期。
3. 云端调用记录 provider、目的、数据类型。
4. 声音克隆必须使用本人授权样本，不允许用于冒充他人。
5. 提供语音记录删除入口。

## 5. 搜索与抓取安全

### SSRF 防护

禁止抓取：

- private IP ranges。
- link-local 地址。
- cloud metadata endpoints。
- localhost/internal service names。
- 重定向到内网的目标。

控制：

- DNS resolve 后校验所有地址。
- 限制响应大小和超时。
- 禁止危险 scheme。
- 限制并发与每域名频率。
- 出网通过独立网络策略。

### 站点尊重

- 读取 robots 与服务条款。
- 不绕过登录、验证码、访问控制和付费墙。
- 不伪装身份获取受限内容。
- 对单站设置 rate limit。
- 保留抓取证据和政策快照。

## 6. 内容许可证评估

### 核心规则

公开访问不等于：

1. 可以复制保存。
2. 可以修改派生。
3. 可以商业使用。
4. 可以再分发。
5. 可以用于模型训练。

### License Registry 字段

```text
license_name
license_url
allows_private_copy
allows_derivative
allows_redistribution
allows_commercial
allows_ai_training
attribution_required
share_alike_required
non_commercial
verification_status
verified_at
```

### 常见来源策略

| 来源类型 | 策略 |
|---|---|
| 大学官方课程页 | 可链接、可摘要、可在授权下解析。 |
| 大学图书馆试卷 | 若访问受限，只保留入口和使用说明。 |
| MIT OCW | 遵守 CC BY-NC-SA 4.0 和 AI training 条款。 |
| CMU OLI/OpenStax | 遵守页面声明，注意非商业和 ShareAlike。 |
| GitHub 课程仓库 | 仓库 license 不一定覆盖内嵌教师讲义和教材。 |
| 社区资料仓库 | B 级，需检查上游权利，不视为学校授权。 |
| 官方 MOOC/Global Open Courses | 可链接和登录学习；默认不缓存、派生或再分发平台内容。 |
| 商业平台课程 | 默认不解析、不再分发。 |

## 7. 第三方开源许可证

| 项目/组件 | 许可证 | 使用注意 |
|---|---|---|
| DeepTutor | Apache-2.0 | 保留 notice；借鉴架构或按 license 使用代码。 |
| OpenMAIC | MIT | 保留版权和许可声明。 |
| LiveKit Agents | Apache-2.0 | 保留 notice，注意依赖链。 |
| Pipecat | BSD-2-Clause | 保留声明。 |
| Qwen3-ASR | Apache-2.0 | 模型和服务依赖另行确认。 |
| FunASR | MIT | 保留声明。 |
| CosyVoice | Apache-2.0 | 声音克隆和模型使用条款需遵守。 |
| EduStudio | MIT | 保留声明。 |
| pyKT | MIT | 保留声明。 |
| Docling | MIT | 保留声明。 |
| olmOCR | Apache-2.0 | 模型权重和依赖需分别检查。 |
| MinerU | Apache-2.0 + 附加条款 | MAU > 1 亿或月收入 > 2000 万美元需商业授权；对第三方在线服务必须显著标注 MinerU；模型权重另行检查。 |
| OATutor | 代码 MIT；内容库 CC BY 4.0 | 代码和内容分别管理署名、来源和版本。 |
| OLI Torus | MIT | 若借鉴或修改代码，保留版权和许可声明。 |
| Moodle | GPL-3.0 | 若修改分发需遵守 GPL；插件和主题许可证需单独审计。 |
| Crawl4AI | Apache-2.0 | 保留 notice，遵守目标网站条款。 |
| Open edX | AGPL-3.0 | 若修改网络服务源码，需评估开源义务。 |

## 8. Prompt Injection 与工具安全

风险：

1. 外部网页写入隐藏指令。
2. PDF 注入指令。
3. 学生在答案中诱导评分器。
4. 搜索结果诱导 Agent 访问内网。
5. 课程生成内容包含未授权表述。

控制：

- 外部内容标记为 untrusted data。
- 工具权限由服务端状态机决定，不由模型自行决定。
- URL allowlist + SSRF guard。
- 输出 schema 校验。
- 关键动作 human approval。
- 模型无法读取 secret。
- 评分 prompt 与学生答案分层，明确评分规则优先。

## 9. 数据治理

### 数据分类

| 数据 | 分类 | 默认保留 |
|---|---|---|
| 账号信息 | PII | 账号生命周期。 |
| 原始音频 | sensitive biometric-adjacent | 默认不保留。 |
| ASR transcript | learning behavior | 可配置。 |
| 学习事件 | behavioral | 长期，可导出/删除。 |
| 错题和掌握度 | derived personal data | 长期，可重算。 |
| 上传文档 | user content | 用户控制。 |
| 外部资源缓存 | third-party content | 按 license。 |
| 模型 trace | operational | 保留期可配置。 |

### 用户权利

- 导出课程、题目、答题、错题、学习记录。
- 删除语音和 transcript。
- 删除账号和个人学习数据。
- 查看云端调用类别。
- 切换 local/cloud/hybrid。

## 10. 上线前安全清单

1. 无硬编码 secret。
2. 前端 bundle 无 API key。
3. CORS 精确配置。
4. 登录速率限制。
5. API auth 全覆盖。
6. 对象存储不公开默认 bucket。
7. signed URL 短期有效。
8. 沙箱无宿主敏感挂载。
9. SSRF 测试通过。
10. prompt injection 测试通过。
11. 考试幂等与并发测试通过。
12. backup restore 演练通过。
