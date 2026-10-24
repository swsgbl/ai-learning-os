# v3 修复棒：CreateCoursePage 契约对齐（supervisor 验收发现 3 处真错误）

## 背景

v3 代码已交付并装机（构建/签名/装机链全通，UI 五项 layout 验收全绿）。
supervisor 用宿主探针实测生产 API 后发现 **定制课程块契约与服务端/Android
不一致**（探针 422 实证）。AI 密钥块（AiKeysPane/V3Api 的 ai-keys 部分）
契约已核对**正确**，勿动。

服务端权威：`services/api/app/api/routes/course_workflow.py`
Android 权威：`apps/android/.../data/remote/Dtos.kt:128-146`

## 三处修复（全在两个文件）

### 1. `V3Api.ets` — DTO 与函数签名

- `GenerationDraft` / `GenerationDraftDetail`（现字段 topic/outline/error 全错）
  → 合并为一个，按服务端 `GenerationDraftOut`：

```ts
export interface GenerationDraft {
  id: string;
  goal: string;
  status: string;          // 落库即 "pending_review"
  chapter_count: number;
  generation_note: string;
  plan: Record<string, Object> | null;  // outline 章节数组在 plan['outline']
}
```

- `MaterializeResult` 加 `question_count: number`（服务端 201 返回它）。
- `createGenerationDraft` 请求体改 `{ goal: string }`（现在发的 topic 是 422
  根因）；返回类型 `ApiResult<GenerationDraft>`（**同步响应，POST 201 直接
  带完整草稿**）。删除「GET /generation-drafts/{id} 详情/轮询」函数
  （服务端有该端点但本页不再需要）。

### 2. `CreateCoursePage.ets` — 去轮询，直取响应

服务端 `POST /generation-drafts` **同步**跑完八阶段管线（LLM 兜底在线程池，
可达数十秒）后 201 返回完整草稿。**没有 pending→ready 异步流转，轮询
`status==='ready'` 永远等不到（真实值是 pending_review）→ 超时假报错。**

- 删除轮询循环 / POLL_INTERVAL / POLL_TIMEOUT / pollGeneration（disposed
  保留）。
- 「生成课程草稿」点击 → busy 态（提示「生成中…LLM 动态生成可达数十秒，
  请勿退出」）→ 一次 POST → ok 即渲染结果；detail 原样显示（422 概念图
  未覆盖 + LLM 失败的引导文案很长，服务端已写好，直接透出）。
- 大纲渲染：`plan['outline']`（Array，每章 {chapter_no,title,difficulty…}
  —— 以 plan 实际键为准，用 `(draft.plan?.['outline'] as Array<...>)`
  防御式取）展示章节号+标题；同时展示 `chapter_count` 与
  `generation_note`。取不到 outline 时显示 generation_note（不硬编码
  「(大纲为空)」误导）。
- materialize 成功提示加 `question_count`（「已固化 N 题练习卷」）。
- UI 变量名 `topic` 可保留（仅是本地 state 名），但**请求体必须 `goal`**；
  页面注释同步改（去掉「每 3s 轮询」描述）。

### 3. 回归自检

- `cd apps/harmony && hvigorw assembleHap --mode module -p product=default
  -p buildMode=release --no-daemon`（**勿改 build-profile.json5**）
- BUILD SUCCESSFUL 后停手——装机与生产探针复验由 supervisor 做。

## 禁改

AiKeysPane.ets / V3Api 的 ai-keys·profiles·models 部分 / HomePane /
Index.ets / ExamPage.ets（均已验收通过）。

## 已验证事实（勿重验）

- 装机链已通：`node .hm-tmp/v3-install.cjs`（rm -rf 行已修，勿恢复）
- 五项 UI 验收全绿：首页入口卡 / CreateCoursePage 打开 / AiKeysPane
  「AI 密钥管理(BYOK)」/ 语音 Tab 选卷入口 / 学习 Tab 双入口卡不回归
- ai-keys 探针 6/6：GET {keys,profiles} / PUT {slot,configured,last4} /
  models 502 detail 透出 / profiles 201 {id,label,last4,active} /
  activate 200 / DELETE 200
