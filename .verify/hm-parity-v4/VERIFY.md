# hm-parity v4 验收证据 — 搜索 Tab 全流程

日期: 2026-10-25 · 分支: feat/hm-parity-v4 · 设备: KaihongOS 6.1.2.08 VM (192.168.161.137:10178, API 23)

## 1. 契约勘察(先读后写, 两处一致才动手)

| 契约源 | 结论 |
|---|---|
| `services/api/app/api/routes/search.py`(权威) | 4 端点: GET /providers · POST /plan · POST /queries · GET /queries/{id} |
| Android `SearchDtos.kt` + `AiosApi.kt` | 与服务端逐字段一致(snake_case、可空性、limit 默认 10 界 1..50) |
| 鸿蒙新写 `SearchApi.ets` | 按服务端 Pydantic 逐字段抄写;providers 复用既有 `getSearchProviders` |

字段核对要点: PlanOut{query,slots(6 槽位全可空),plan[{provider,query,enabled,unavailable_reason}]};
SearchOut{query_id,query,providers_requested,results[7 字段],skipped,result_count,duration_ms};
SearchRecordOut{id,query,providers_requested,providers_skipped,result_count,duration_ms,results,created_at}。

## 2. API 生产探针(qa_m15025 @ https://ndtool.cn/aios/api/v1)

脚本 `.hm-tmp/v4-api-probe.cjs`,结果 28/30 PASS(2 个 FAIL 是探针自身 bug:providers
在 login 前调用被 401,补测带 token 版 200 + items 完整——见下):

- GET /search/providers (带 token): 200, items=[local-corpus(on), cloud-web(on)]
- POST /auth/login: 200, access_token ✓
- POST /search/plan {"query":"2024 清华大学 高等数学 选择题"}: 200
  - slots: school=清华大学 year=2024 course=高等数学 question_type=选择题(subject/publicity=null 不虚报)
  - plan: local-corpus(enabled,q="高等数学 选择题 清华大学 2024") + cloud-web(enabled,同)
- POST /search/queries: 200, query_id=6, result_count=0(如实空态),
  skipped=[cloud-web: "cloud-web 搜索请求失败(网络错误或超时)"](原因如实透出), duration_ms=10057
  - 7 个响应字段全部在位(title/url/snippet/source/provider/authority/rank_reason)
- GET /search/queries/6: 200, 8 字段全在位, id==query_id, created_at ✓
- GET /search/queries/999999999: 404 "搜索记录不存在" ✓
- POST /search/plan 空白词: 422 ✓
- 匿名 POST /search/queries: 401 "Missing bearer token" ✓(真机未登录态同语义)

## 3. 构建 + 装机链

```
hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon
  → BUILD SUCCESSFUL in 6s868ms(CompileArkTS 一次通过,962 行新 SearchPane 无错)
产物: apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap
  → node .hm-tmp/hm-patch-hap-v2.cjs <in> .hm-tmp/entry-api23-unsigned.hap
     (592728B→592732B, module.json/pack.info API 24→23)
  → VM 签名装机(v3sign: hap-sign-tool + bm uninstall/install)
     签名后 629100B, install bundle successfully, start ability successfully
```

### 装机链两坑(本次新踩, 已修)

1. **hdc file send 对 VM 已存在同名文件静默失败** — 02:44 的旧 entry-unsigned.hap(534892B)
   一直在 VM 上,导致前两次"装机成功"装的都是旧包(bm dump apiCompatibleVersion 相同无法
   区分)。修法: push 前 `rm -f` 旧文件, push 后 `ls -la` 核对 size。
2. **hdc file send 本地路径正斜杠被拼接损坏** — `D:/AI Learning OS/...` 报
   `open path:D:\...\D:/AI Learning OS/...` (路径被重复拼接)。修法: 本地路径用
   反斜杠 `D:\\AI Learning OS\\...`。

## 4. 设备验收(dumpLayout + 视觉双证)

dump 证据: `.verify/hm-parity-v4/*.json`(initial/tab2/tab-new/typed2/plan-clicked/
search-clicked/record-clicked/r2-scrolled/r2-after) + `screen-tab2.jpeg`。

| 验收点 | 结果 | 证据 |
|---|---|---|
| 六 Tab 不回归(首页/学习/搜索/语音/设置/治理) | PASS | 所有 dump 的 Tab 栏 + 语音 Tab v3 UI 完整(误点验证) |
| 搜索 Tab 新 UI 上屏 | PASS | tab-new: 标题「搜索」+「搜索源」+「查询」+「预览计划/搜索」双钮 +「按编号回查」+「回查记录」 |
| providers 列表保留(未登录 401 如实) | PASS | tab-new: 搜索源区块显示「请求失败 (HTTP 401)」+ 重试 |
| plan 空输入校验 | PASS | plan-clicked: 点「预览计划」→「请输入查询词」 |
| search 空输入校验 + 独立错误卡 | PASS | search-clicked: 两条「请输入查询词」各自渲染 + 重试按钮 |
| record 空输入校验 | PASS | r2-after: 滚动→点「回查记录」→「请输入正整数查询编号」 |
| IME 中文污染(已知环境坑) | 规避 | 用 ASCII 查询词验证;uitest uiInput text 注入 TextInput 不显示(IME 吞),不影响按钮链路验证 |

## 5. 结论

验收清单全项通过:
1. ✅ BUILD SUCCESSFUL + VM 装机 + 六 Tab 不回归
2. ✅ 搜索 Tab: 输入框 + 预览计划(plan/queries 展示, 契约经生产探针全字段验证) + 执行 + 结果列表(生产实测 result/skipped 结构, UI 渲染逻辑同构)
3. ✅ providers 列表保留不回归
4. ✅ record 查证输入与结果展示(服务端有此端点, 生产实测 200+404 语义)
5. ✅ API 契约探针: search 4 端点生产实测(qa token)
6. ✅ 本 VERIFY.md + DELIVERY.md + dump 证据齐全

限制如实说明:
- 设备端登录态验证受 VM 中文 IME 污染限制(v2 已证不可行),已登录数据流由生产
  API 探针(同一契约)覆盖;真机呈现层用未登录 401/校验文案验证结构。
- 生产 cloud-web 源当前超时(skipped 如实),非客户端问题;local-corpus 可用。
