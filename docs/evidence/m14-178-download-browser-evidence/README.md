# M14-178：/download 公共下载页真浏览器验收证据（Download Browser Evidence）

- 切片：worktree `ai-learning-os-worktrees/m14-178-download-browser-evidence`，
  分支 `ops/m14-178-download-browser-evidence`，基于 main
  `a33efb7977b401cc647c5d9239bfdd175398e872`（PR #267 merge =
  M14-177 证据收口合入，精确基点）。
- 定性：**verification/docs-only 切片**——零 app/运行时/模板/测试代码
  变更；不部署、不重启/停止任何服务、不动 Docker/frp/CC Switch/语音、
  不读任何 secret、不宣称 Android 真机冒烟。原始工件（截图、
  results.json、MCP 快照/控制台日志）全部留在 gitignored 的
  `.verify/m14-178-download-browser-evidence/`，本 README 为唯一入库
  证据文件。
- 工具：Playwright MCP 管理的真实桌面 Chromium（Windows NT 10.0，
  dpr 1；该浏览器配置文件与其他会话共享——历史 tab 与既有 PWA 安装
  状态均非本切片产生，见 §6 边界）。

## 1. 通过的机械断言（真实浏览器，2026-09-29 时点）

| 检查 | 结果 |
| --- | --- |
| 视口 | 桌面 **1366×768**、移动 **390×844**（`browser_resize` 精确设定，`innerWidth/innerHeight` 复核一致；移动端 `clientWidth=375` 因 Windows Chromium 15px 滚动条，如实记录） |
| 文档加载 | `navigate` 类型，HTTP **200**，标题「下载与安装 — AI Learning OS」，transferSize 5228 bytes |
| 三张渠道卡 | `channel-pwa` / `channel-android` / `channel-harmony` 全部存在且可见 |
| PWA 卡 | 徽章「可用」；安装引导 UI 在位（DOM 探测时为「安装到本机」按钮；截图时刻为「已安装——你可以从主屏或桌面直接打开砚席」，见 §6 边界 4——两者均为 install-guided 形态） |
| Harmony 卡 | 徽章「待发布（AGC 签名未就绪）」，卡内**零链接**（DOM 断言 + 视觉复核一致） |
| 横向溢出 | 桌面 `scrollWidth 1366 == clientWidth 1366`；移动 `scrollWidth 375 == clientWidth 375`——**均无横向溢出** |
| 卡片包围盒 | 桌面：PWA(187,232,992×171)、Android(187,427,490×212)、Harmony(689,427,490×212) 两列网格；移动：单列堆叠（x=16,w=343）；**两视口两两重叠面积均为 0**（移动端 Harmony 卡上缘 y=785 部分位于 844 折叠线下方，属正常滚动布局） |
| 截图 | 桌面 1366×768 / 130,641 bytes / SHA256 `4384097f…8276`；移动 390×844 / 98,185 bytes / SHA256 `8008aacd…5f92`；两图均经真实视觉模型分析确认**非空白、渲染完整**（导航、三卡、徽章文字与 DOM 断言逐字一致） |
| console/网络审计 | 首次加载仅 2 项已识别错误、全新 reload 仅 1 项（见 §4）；**零未解决错误**、零未捕获页面异常、零失败文档/子资源加载（已识别项之外） |

## 2. 边缘 manifest 复核（页面内真实 fetch，与 M14-177 发布值一致）

`fetch('/aios/download-manifest.json')`（no-store）：**200**、
`application/json`、`Cache-Control: no-store`；schema
`aios-download-manifest/1`、version `0.1.0`、android 频道恰一个
`signed: true` 条目：url `/android/ai-learning-os-0.1.0-release-signed.apk`、
sha256 `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d`、
size_bytes **8029570**、versionName `0.1.0`、versionCode `1`——与
M14-177 已落档发布值完全一致。**本切片未下载 APK、未在浏览器侧重算
APK 哈希**（M14-177 已有公网下载字节复核 + apksigner 复核记录，不重复
宣称）。

## 3. 核心发现：生产 Web 包滞后——Android 卡验收断言未达成（blocker）

任务要求的「Android 卡从 manifest 态升级 available、唯一下载链接
href 精确为 `/android/ai-learning-os-0.1.0-release-signed.apk`」
**在生产上不可达成**，且这是**生产部署状态问题，不是浏览器自动化
故障**（自动化本身全部完成）。三重独立证据：

1. 全新 reload 并等待 2500ms 水合稳定后，performance 资源表中**页面
   自发的 `download-manifest.json` 请求为 0**（对照：当前 main 源码的
   `download-panel.tsx` 挂载即调用 `fetchAndroidChannelState()`）；
2. 该次加载的全部 **17 个** `/_next/static/chunks/*.js`（DOM script +
   performance 条目合并去重，逐一取回、全部 ok）中，**0 个**包含
   `download-manifest` 字符串——M14-174 的运行时接线不在部署包内；
3. 两视口下 Android 卡徽章恒为「待发布（release 签名未就绪）」、卡内
   零 `<a>`。

结论：**部署的家机 Web 镜像构建于 M14-174 合入之前**（含 M14-164 的
下载页/PWA 卡，但不含 manifest 驱动的 Android 升级）。边缘侧
（M14-176/177）行为完全正确——manifest 就位且值正确，只等 UI 消费。
**修复路径 = 从 M14-174 之后的 main 重建并滚动家机 Web 镜像**——
该操作超出本切片禁令（不部署/不重启/不动 Docker），如实移交
supervisor/Codex 后续执行。修复后需重跑本节验收断言（含 §2 复核）。

次要观察（如实记录，不深究）：验收期间无 Service Worker 控制页面
（`navigator.serviceWorker.controller === null`）、加载期间未发起
`sw.js` 请求——与部署包早于当前 web 代码的结论一致；根因（镜像确切
构建版本）未进一步追查。

## 4. console / 网络错误审计（全部已识别，零未解决）

| 错误 | 分类 |
| --- | --- |
| `GET https://ndtool.cn/favicon.ico` → 404（首次加载） | **既有宿主根路径问题**：浏览器自动请求域根 favicon，宿主站点未提供；在应用 `/aios` basePath 之外，与本次验收对象无关 |
| `GET /aios/api/v1/auth/me` → 401（两次加载） | **预期行为**：登出访客的匿名鉴权探测，应用正常流程 |
| `GET /aios/_next/build-manifest.json` → 404 | **本验收脚本自己的诊断探针**所致（Next 16 不在该路径发布该文件），非页面错误 |

无任何未捕获页面异常（pageerror）、无字体/分析类外部请求失败、无其他
失败子资源。

## 5. 本切片的验证（真实执行）

- 文档/版本守卫：
  `services/api/tests/test_versioning_rollback.py::test_version_sources_in_sync`
  与 `services/api/tests/test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  ——**2 passed**；
- apps/web vitest download 相关测试**未运行**，理由：本切片为
  verification/docs-only，零 web 代码变更（vitest 针对的是源码契约，
  而本切片对象是生产行为；且本 worktree 未安装 node_modules，按
  docs-only 切片纪律不做依赖安装）；相关源码契约已由 CI 在
  PR #266/#267 上全绿覆盖；
- `git diff --check` 干净；新增行 secret / 本地绝对路径 / 私有 SSH
  别名 / IP / 密码 / token / U+FFFD 扫描 **0 真实命中**。

## 6. 诚实边界

1. **Android 卡升级断言未达成**（§3）——绝不以源码/静态 HTML 测试
   替代真浏览器证据来宣称通过；本文件记录的恰是生产真实现状：
   边缘就绪、UI 包滞后。
2. 本切片**未下载 APK、未重算 APK 哈希、未做 Android 真机安装冒烟**
   （`adb devices` 仍为空，M14-177 口径维持）。
3. PWA 卡「已安装」截图形态来自**共享 MCP 浏览器配置文件的既有安装**
   （其他会话此前安装），非本切片执行安装；DOM 探测时刻则为
   「安装到本机」按钮形态。徽章「可用」恒成立；PWA 安装子形态随
   浏览器环境而变，不属于本验收断言面。
4. Harmony 公开分发仍被 AGC 发布材料阻塞（M14-177 口径维持）。
5. 所有生产观察均为时点证据，不构成持续可用性保证；公网页面与 API
   依赖家机 frpc 常驻与 VPS Nginx/frps 存活。
6. 本切片交付为 branch + 单 commit；PR 创建即止；合并决策归
   supervisor 审查（supervisor 审查与 remote 发布在其后进行）。
