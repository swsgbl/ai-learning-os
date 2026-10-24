# 深开鸿 v3：AI 密钥管理 + 定制课程 + 语音 Tab 激活（一比一对齐 Android 收官轮）

## 上一轮成果（v1/v2 已合并 main · commit aceb209）

v1：学习冲刺面板 + 教材工作台 + VM 签名装机链。
v2：迁移检验/定向练习/复习三作答阶段 + 考试全流程（ExamPage）。
构建装机链全固化（.hm-tmp 脚本 + VM 侧签名长名参数）。从 origin/main 切新分支。

## v3 目标：三块收官（按优先级）

### 缺口 A：AI 密钥管理（设置 Tab 内 —— Android SettingsScreen 已有）

Android `AiosApi.kt` 的 6 个端点 + UI：

| 端点 | 说明 |
|---|---|
| GET /api/v1/me/ai-keys | 当前密钥槽位列表（{slots:[{slot,key_set,updated_at}...]}） |
| PUT /api/v1/me/ai-keys/{slot} | 保存/更新 {api_key}（脱敏返回 {slot,key_set}） |
| DELETE /api/v1/me/ai-keys/{slot} | 删除 |
| POST /api/v1/me/ai-keys/llm/models | 探测 {endpoint,api_key,model} → 可用模型列表 |
| POST /api/v1/me/ai-keys/llm/profiles | 建厂商配置 {provider,display_name,api_key,model,base_url} |
| POST /api/v1/me/ai-keys/llm/profiles/{id}/activate | 激活 |
| DELETE /api/v1/me/ai-keys/llm/profiles/{id} | 删除 |

**UI**：设置 Tab 第三个面板 `AiKeysPane.ets`（现有 SettingsPane + AuthPane 之后）：
- 未登录：提示「登录后可配置」
- 已登录：密钥槽位列表（脱敏显示）+ 表单（厂商选择/密钥输入/模型探测/保存/删除）+ LLM Profile 列表（激活/删除）
- 安全约束：**密钥只显示后 4 位**；输入框不回显已存密钥；删除需确认

### 缺口 B：定制课程（首页/学习 Tab 入口 —— Android CreateCourseScreen）

两端点 + 三步 UI：

| 端点 | 说明 |
|---|---|
| POST /api/v1/courses/generation-drafts | {topic} → {id,status:pending}（异步生成——轮询） |
| POST /api/v1/courses/generation-drafts/{id}/materialize | 固化 → {paper_id,title} |

**UI**：新 `CreateCoursePage.ets`（首页快速入口「定制课程」点击进入）：
1. 输入学习目标（textarea + 提交按钮）
2. 提交后轮询草稿状态（每 3 秒 GET——参考 Android CreateCourseViewModel 的
   轮询逻辑；超时 120 秒如实报错）
3. 生成完成后展示大纲 + 「固化入库」按钮 → materialize → 成功提示回到学习 Tab

### 缺口 C：语音 Tab 激活（现有 VoicePane 只读 → 可交互）

现有 `voiceexam/VoiceExamApi.ets` 已有 createSession/resume/postAnswer/report/transcribe
五个端点 + VoiceExamPage 语音考试页。缺：

1. **VoicePane 升级**：登录态显示「选择试卷开语音考试」入口（拉 /api/v1/papers 列表
   → 选卷 → pushUrl VoiceExamPage 传参）——对齐 Android VoiceScreen 的 Picker 模式
2. 补 `GET /api/v1/voice/providers` 列表显示（哪些语音引擎可用）

---

## 技术约定（v1/v2 已建立，全部沿用）

- 网络层：`AiosApi.ets` 的 `requestJson<T>`（bearer fail-closed + detail 透出）
- 密钥安全：**token/api_key 零持久化零日志**（内存态 only）；UI 显示只后 4 位
- 轮询：async 竞态防护（disposed + generation 计数器）
- 409/401/404/503 detail 原样显示
- 六 Tab 结构零改动；新页面走 router.pushUrl
- 构建：`hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon`
  **不要改 build-profile.json5 SDK 版本**（保持原值）

## 构建与验收

装机链：宿主构建 → node 重打包 patch API 24→23 → 推 VM → VM 侧签名
（长名参数 `-mode localSign -keyAlias "openharmony application release"`）
→ 先 `bm uninstall` 再 `bm install`（provision type 冲突坑）→ `aa start`

设备：HDC 192.168.161.137:10178（VMware 深开鸿 KaihongOS 6.1.2.08，API 23）
hdc 路径：`C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc`

API 测试：生产 https://ndtool.cn/aios/api/v1 · qa_m15025 / Qa-M15-025!x9
（设备端登录受 VM IME 限制——API 契约用宿主 curl/node 探针验证，UI 结构用
未登录态 layout dump 验证）

## 验收清单

1. 构建 BUILD SUCCESSFUL + VM 装机成功 + 六 Tab 不回归
2. 设置 Tab：AI 密钥面板出现（未登录态提示 / 登录态表单结构）
3. 首页：「定制课程」入口 → CreateCoursePage 打开（输入框 + 提交按钮）
4. 语音 Tab：升级后的入口结构（选卷列表或提示）
5. API 契约探针：ai-keys GET/PUT/DELETE、generation-drafts POST、materialize、
   voice providers——生产 qa token 实测
6. .verify/hm-parity-v3/{VERIFY.md,DELIVERY.md} + layout dump 证据

## 交付物

新/改 ArkTS 源文件 + 验收证据 + 交付总结
