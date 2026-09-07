# M12-04 Android 搜索流 · 模拟器冒烟证据（loopback mock + emulator-5554）

- 日期：2026-09-07；分支 `feature/m12-04-android-search-flow`（HEAD `23fb626` + 工作区未提交改动，即含 `ResultUrlPolicy.kt` 与 `SearchScreen.kt` 当前形态）
- 执行者：Claude Code（本机）；监督口径：仅本机 loopback mock，无真实 provider / 生产服务 / 密钥；「无外网」为 App 与 mock 口径、非整机断网口径（模拟器系统层存在 Android 自动网络探测，见「结论」末条）
- 环境：Windows 宿主机 + `emulator-5554`（AVD Medium_Phone，Android 16 / SDK 36，1080x2400 @420dpi ≈ 411dp 宽）；APK `apps/android/app/build/outputs/apk/debug/app-debug.apk`（构建于 08:32:46，晚于全部工作区源码修改；`ResultUrlPolicy` 已确认在 `classes5/classes8.dex`）
- Mock：当次运行位置为 `.verify/m12-04-android-smoke/mock_api.py`（gitignored，不在库内）；冒烟所用的这套无密钥 mock 契约已归档为本目录 `mock_api_used.py`（纯标准库，仅绑定 `127.0.0.1:8000`；模拟器经 `10.0.2.2:8000` 即 debug 默认 base URL 访问，据此可复现）；固定 JSON：`/health`、`/api/v1/auth/status`、`/api/v1/system/privacy`、`/api/v1/search/providers`、`POST /api/v1/search/plan`、`POST /api/v1/search/queries`（query_id=9001）、`GET /api/v1/search/queries/9001`；结果 URL 固定长 ASCII `https://localhost/m12-04/smoke-source?trace=local&suite=android-360`
- 冒烟步骤：`adb install -r` → `pm clear com.ailearningos.app` → `am start` → 点底栏「搜索」→ 输入查询（ASCII：`2024 tsinghua advanced math mcq`，见下方工具说明）→「预览计划」→「搜索」→ 结果卡「打开」（ACTION_VIEW）→ 返回 App →「按编号回查」输入 9001 →「回查记录」

## 证据文件

| 文件 | 内容 | 关键断言（uiautomator dump 逐项核对） |
| --- | --- | --- |
| `01-home-five-nav.png/.xml` | 首页 | 底栏五项导航：首页/学习/搜索/语音/设置各 1 节点（y≈2251–2297）；`当前用户=本地模式（API 未启用认证）`、`服务（/health）=正常`、隐私基线布尔项如实（否/否）；mock 返回的非枚举路由字符串渲染为「未知」（不虚报，符合呈现边界） |
| `02-search-providers.png/.xml` | 搜索页·搜索源注册表 | `local-corpus（本地语料）可用`、`cloud-web（网页）不可用` + 禁用原因原文（`未配置云端检索通道…`）；查询输入/预览计划/搜索/按编号回查控件齐备 |
| `03-search-plan.png/.xml` | 计划预览（未执行） | `查询计划（预览，未执行）`；槽位：学科=高等数学、学校=清华大学、年份=2024、题型=选择题；`local-corpus 将搜索「…」`、`cloud-web 不会执行`+原因 |
| `04-search-results-top.png/.xml` | 执行结果·概要 | `查询编号 #9001`、`结果数 2 条`、`耗时 12 ms`、`请求的源 local-corpus、cloud-web` |
| `05-search-results-result-a.png/.xml` | 结果 A + 来源 URL 行 | 结果标题/摘要/来源行/排序理由原文；URL 断行为恰 2 行（`https://localhost/m12-04/smoke-source?` 恰 40 字符于 `?` 断开 + `trace=local&suite=android-360`），行宽 ≤ 40 无横向溢出；「打开」按钮启用 |
| `06-search-results-result-b-skipped.png/.xml` | 结果 B + 被跳过的源 | 结果 B 全字段；`被跳过的源：cloud-web 原因：不可用：未配置云端检索通道…` 原样透出 |
| `07-action-view-chrome-takeover.png/.xml/.txt` | 点「打开」后的 ACTION_VIEW 接管 | 点按前 `mCurrentFocus=com.ailearningos.app/.MainActivity`；点按后 `mCurrentFocus / ResumedActivity = com.android.chrome/org.chromium.chrome.browser.firstrun.FirstRunActivity`（Chrome 接管 https VIEW intent；模拟器 Chrome 首启为首运行页，未接受 ToS、不触发 Chrome 侧页面加载/联网行为——整机层面的 OS 自动探测与 Chrome 无关，见「结论」末条） |
| `08-record-lookup-9001.png/.xml` | 按编号回查 9001 | 输入 `9001` →「回查记录」→ 记录结果列表（标题/摘要/URL 两行断行/打开按钮）+ skipped 渲染 |
| `09-record-meta.png/.xml` | 回查记录元信息 | `原查询词=2024 tsinghua advanced math mcq`（mock 固定记录）、`结果数 2 条`、`耗时 12 ms`、`记录时间`、`请求的源`、URL 原文完整、skipped 原因 |
| `mock_requests.log` | mock 全量请求日志 | 模拟器会话实际命中：`/health`、`/api/v1/auth/status`、`/api/v1/system/privacy`、`/api/v1/search/providers`、`POST /plan`（body 43B）、`POST /queries`（body 54B，查询词 `2024 tsinghua advanced math mcq` + `limit:10`）、`GET /queries/9001`；前 5 条 00:46:50 为宿主机 curl 自检 |
| `logcat-full.txt.gz` | 全程 logcat 的无损 gzip（解压后 9603 行，逐字捕获**不做清洗**） | `FATAL EXCEPTION` 0 行、`ANR` 0 行、`AndroidRuntime`+本包 0 行；含系统层自动网络探测记录（NetworkMonitor 对 gstatic/google/play、Gboard 自 gstatic，见「结论」末条），均为 OS/输入法组件行为、非本 App 流量；.gz 为原始 logcat 的无损压缩——解压 SHA256 与原始捕获文件逐字节一致（`5d2c76c221d65e3e0058ea0f9556b34424b98a50c9a375d335d3e7f5b908c112`，1,283,682 字节；与此前 autocrlf 归一化的纯文本暂存 blob 仅差行尾 CR、已逐字节核对往返一致），原始 CRLF 行尾与系统日志行自带尾随空白（如 WindowManager 堆栈行）原样保留在压缩内容中；以 gzip 归档使 git 按二进制 blob 处理，避免 `git diff --check` 把 raw 文本当文本 diff 检查——前版纯文本暂存时该文件曾报 549 条尾随空白告警（全部位于本证据文件、属逐字原文，源码与文档零告警），换 .gz 后 `git diff --cached --check` 通过 |

## 结论

- 冒烟通过：五位导航、搜索源注册表（含不可用原因）、计划预览、执行结果（#9001 / 2 条 / skipped）、长 ASCII 结果 URL 原文可溯源展示且 ≤40 字符断行、「打开」仅对通过校验的 https URL 启用、ACTION_VIEW 由 Chrome 接管（dumpsys 前后对比）、9001 回查全字段、全程无 FATAL/ANR。
- 未发现应用缺陷（见 summary.json 的 observations：五例均为工具/环境口径说明，非缺陷；查询字段曾出现的 ` by` 尾巴是 adb 滑动误触 Gboard 滑行输入所致，发生在搜索已执行之后，实际请求体已核对为干净查询词）。
- 未触碰真实 provider/生产服务/密钥；「无外网」为 App 与 mock 口径而非整机口径：本 App 与 mock 未发起任何外网请求（mock 请求日志仅上述固定端点），但冒烟期间模拟器系统层存在 Android 自动网络行为——NetworkMonitor（system_server PID 1169）对 `connectivitycheck.gstatic.com` / `www.google.com` / `play.googleapis.com` 的联网可用性探测（gstatic DNS/HTTP 204 探测实际出网成功，google HTTPS 与 play fallback 探测超时，判定 `isPartialConnectivity=true`），以及 Gboard（`com.google.android.inputmethod.latin`，PID 1662）在查询输入框获焦时自 `www.gstatic.com` 请求 dictionarypack/emoji/next-word-predictor 清单——均为 OS/系统输入法组件行为、非本 App 流量、不涉及任何 AI provider/生产服务；mock 为本次自建自停（已确认 8000 端口释放）。
