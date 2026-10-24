# 深开鸿 v4：搜索 Tab 全流程（AI 两段式搜索 + 记录查证）

## 上一轮成果（v1-v3 已合并 main · c32652a8）

v1 学习冲刺+教材工作台；v2 三作答阶段+考试全流程；v3 AI 密钥+定制课程+
语音选卷。构建装机链全固化（`.hm-tmp/v3-install.cjs` 一键）。从 origin/main
切新分支 `feat/hm-parity-v4`。

## v4 目标：搜索 Tab 从「providers 只读列表」升级为完整搜索工作流

现状：`SearchPane.ets`（295 行）只展示 /api/v1/search/providers 列表。
Android `SearchScreen.kt` + `SearchViewModel.kt` 的完整功能面（对齐目标）：

1. **搜索计划预览**（previewPlan）：输入查询 → 展示服务端拆解的
   plan/queries（两段式：先看 AI 怎么拆你的问题，再执行）
2. **执行搜索**（search）：跑 queries → 结果列表（来源/摘要/链接）
3. **记录查证**（lookupRecord）：recordId 输入 → 查证来源记录
4. providers 列表保留（引擎可用性展示）

## 契约勘察纪律（v3 血泪教训，硬性）

**服务端代码是唯一权威**：先读 `services/api/app/api/routes/search.py`
（端点/请求响应模型逐字段抄），再对照 Android
`apps/android/.../data/remote/AiosApi.kt` 的 search 相关 Retrofit 声明 +
`Dtos.kt` 对应 DTO。**两处一致才动手写；不一致以服务端为准。**

已知端点线索（以服务端实读为准，勿信本清单）：GET /api/v1/search/providers
（SearchPane 已用）。plan/执行/record 查证端点路径与请求体**自行从
search.py 读出**——v3 曾因按过期记录写契约返工一轮。

## 技术约定（v1-v3 全部沿用）

- 网络层：`AiosApi.ets` 的 `requestJson<T>`（bearer fail-closed +
  detail 透出）；新端点写进 SearchPane 内或新建 `SearchApi.ets`
- 未登录/401/404/503 detail 原样显示；异步竞态防护（disposed + generation）
- 六 Tab 结构零改动；SearchPane 在搜索 Tab 内原地升级
- ArkTS 规范参照既有组件；密钥/token 零持久化零日志

## 构建与验收

构建：`cd apps/harmony && hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon`
（**勿改 build-profile.json5 SDK 版本**）

装机链（全固化，照抄）：构建产物 → `node .hm-tmp/hm-patch-hap-v2.cjs <in> <out>`
（out 写 .hm-tmp/entry-api23-unsigned.hap）→ `node .hm-tmp/v3-install.cjs`
（勿改此脚本）。

设备：HDC 192.168.161.137:10178（VMware 深开鸿 KaihongOS 6.1.2.08，API 23）
hdc：`C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc`
验收 dump 参照 `.hm-tmp/v3-dump.cjs`（抄一份改 v4 用，输出到
.verify/hm-parity-v4/）。

API 测试：生产 https://ndtool.cn/aios/api/v1 · qa_m15025 / Qa-M15-025!x9
（宿主 node 探针验证契约；真机用未登录态结构验证）

## 验收清单

1. BUILD SUCCESSFUL + VM 装机 + 六 Tab 不回归（含 v1-v3 全部入口）
2. 搜索 Tab：输入框 + 预览计划（plan/queries 展示）+ 执行 + 结果列表
3. providers 列表保留不回归
4. record 查证输入与结果展示（若服务端有此端点；没有则如实记录跳过）
5. API 契约探针：search 各端点生产实测（qa token）
6. `.verify/hm-parity-v4/{VERIFY.md,DELIVERY.md}` + dump 证据

## 交付物

新/改 ArkTS 源文件 + 验收证据 + 交付总结（git commit 由 supervisor 做，
你只写文件）
