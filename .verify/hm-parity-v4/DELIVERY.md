# hm-parity v4 交付总结 — 搜索 Tab 全流程

日期: 2026-10-25 · 分支: feat/hm-parity-v4(基于 origin/main c32652a8)

## 交付物

### 新增

**`apps/harmony/entry/src/main/ets/components/SearchApi.ets`**(136 行)
- 搜索全流程网络契约层: `postSearchPlan` / `postSearchQueries` / `getSearchRecord`
- 全部走 `AiosApi.requestJson`(bearer fail-closed + 4xx/5xx detail 如实透出)
- DTO 与服务端 `search.py` Pydantic 逐字段对齐(snake_case 原样):
  SearchPlanData{query,slots,plan} / SearchQueryData{query_id,results,skipped,...} /
  SearchRecordData{id,providers_skipped,results,created_at,...}
- `getSearchRecord` 本地预校验(正整数),providers 复用既有 `getSearchProviders`

### 重写

**`apps/harmony/entry/src/main/ets/components/SearchPane.ets`**(295 → 962 行)
- 从「providers 只读列表」升级为完整搜索工作流,对齐 Android SearchScreen:
  1. **搜索源注册表**(保留): name/kind 中文标签(本地语料/网页)/可用性/不可用原因
  2. **两段式搜索**: TextInput + 「预览计划」(POST /plan: 槽位识别 6 槽不虚报 +
     各源执行计划) + 「搜索」(POST /queries: 执行)
  3. **执行结果**: 概要卡(query_id/结果数/耗时/请求的源) + ResultCard 列表
     (标题/摘要/来源·provider·authority 中文标签/排序理由/URL)+ SkippedCard(跳过原因如实)
  4. **记录查证**: TextInput(Number) + 「回查记录」→ RecordCard(原查询/结果数/
     耗时/请求源/记录时间/跳过源/结果列表)
- 工程纪律全部沿用: disposed + 每类请求独立 generation 竞态防护;
  EVENT_ID_URL_CHANGED 监听刷新(先 off 再 on);aboutToDisappear 先 disposed 再退订;
  密钥/token 零持久化零日志;零结果如实空态文案(与 Android 一致)
- UI 状态机: 每区块独立 OpStatus(IDLE/LOADING/DONE/ERROR),互不干扰

### 工具脚本(.hm-tmp/, 不入 git)

- `v4-api-probe.cjs` — 生产 API 契约探针(login→plan→queries→record→404/422/401)
- `v4-dump.cjs` — 验收 dumpLayout(输出 .verify/hm-parity-v4/)
- `v4-record-flow2.cjs` — 回查交互闭环(swipe 滚动→点按钮→校验文案)
- `v4-forensic.cjs` / `v4-vmcheck.cjs` — 装机链取证(md5 对比定位旧包问题)

## 验收(全项通过, 详见 VERIFY.md)

1. BUILD SUCCESSFUL(release, CompileArkTS 一次通过)+ VM 装机 + 六 Tab 不回归
2. 搜索 Tab 新 UI 全上屏(dump + 视觉双证): 搜索/查询/预览计划/搜索/按编号回查/回查记录
3. providers 列表保留(未登录 401 如实展示)
4. record 查证: 服务端有端点, 生产实测 200/404 语义正确, 设备端空输入校验闭环
5. API 契约: 生产 4 端点全测(28+2 PASS, 2 个 FAIL 是探针 bug 已补测通过)
6. 证据: .verify/hm-parity-v4/{VERIFY.md,DELIVERY.md,*.json dump×10,screen-tab2.jpeg}

## 本次新踩的坑(供下棒参考)

1. **hdc file send 对 VM 已存在文件静默失败**: 重装前必须 `rm -f` 远端旧文件,
   push 后 `ls -la` 核对 size。bm dump 的 apiCompatibleVersion 无法区分新旧包
   (补丁包该值相同),要靠 hap 文件 size/md5。
2. **hdc file send 本地路径**: 必须反斜杠 `D:\\...`;正斜杠会被拼接成
   `D:\...\D:/...` 报 no such file。
3. **uitest uiInput 滚动**: `uitest uiInput swipe x1 y1 x2 y2 velocity` 有效;
   `uinput -T -m ... -mswipe` 形式无效。TextInput 内容 dumpLayout 不显示,文本
   注入受 VM IME 限制,按钮→校验→文案链路验证不受影响。
4. **ArkTS 大文件分段写**: 900+ 行组件一次 write 容易中段畸变;拆 ≤120 行部件
   拼接 + node 校验(括号平衡/关键结构/畸变标记)一次通过构建。

## 未尽事项(如实)

- 设备端已登录数据流验证受 VM 中文 IME 限制(v2 结论),由生产 API 探针同契约覆盖
- 生产 cloud-web 源当前网络超时(服务端 skipped 原因如实),非客户端问题
- git commit 由 supervisor 完成(本棒只写文件)
