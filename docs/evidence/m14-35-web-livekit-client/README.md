# M14-35 Web 真实 LiveKit 客户端连接验收（真实浏览器 + 当前部署栈）

- 本 README 为 **R2 修订版**：R1（Supervisor Review Round 1 八项返工后重跑）的 13/13 记录保留为历史；R2 修订判定——R1 曾把首跑 connect 失败记为「环境抖动」，R2 证据证实这是**默认拓扑的间歇性 ICE 失败**（非可忽略抖动），验收改为显式受控条件（见下节）
- R1 验收窗口：2026-09-16 04:1x（+08:00），终版 results.json / 截图落盘于 04:16:34–04:16:35（全量跑：含 build check）；R2 受控连跑时间戳见 gitignored `.verify/m14-35-web-livekit-client-r2/` 目录内原始文件
- 仓库基点：分支 `feat/m14-35-web-livekit-client`（单 commit；R1 返工与 R2 收口均 amend 同一 commit），基于 `main@a109c973ea7cc464b96f2f17804d040f19c2f5fd`
- 执行方式：`infra/verify_web_livekit_client.py`（主仓 canonical venv `D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe`，Playwright Chromium 151 + fake 麦克风设备/授权；R2 起 Chromium 显式带 `--allow-loopback-in-peer-connection` 受控 flag）
- 原始证据：gitignored `.verify/m14-35-web-livekit-client/`（R1：results.json、screenshots/01-initial.png、02-result.png；无 build-stderr.log——构建一次通过）与 `.verify/m14-35-web-livekit-client-r2/`（R2 受控连跑 run1–run6）——不入 git，本 README 仅摘录判定事实

## R2 根因与受控验收条件（非生产用户默认）

- **根因（默认拓扑间歇性 ICE 失败）**：当前 compose 生产栈 LiveKit 以 `--node-ip 127.0.0.1` 通告媒体地址且 UDP 端口只绑定 127.0.0.1；Chromium/WebRTC 默认不收集 loopback ICE candidate，ICE 配对依赖 Docker/Windows 网络路径，该拓扑存在**间歇性失败**
- **Codex 独立默认拓扑实证**：首跑 connect 步失败 `could not establish pc connection`（失败房间所有 ICE candidate pair failed）；同构建复跑 12/12 通过——**拓扑级不确定，非可忽略抖动**
- **R1 重跑过程（历史记录）**：R1 首跑（04:1x）connect 步失败 → 脚本按设计 fail-closed exit 1（DOM 断言不虚报；token 步已通过，房间名为本次新生成的唯一随机名）；同构建复跑（`AIOS_SKIP_BUILD=1`）12/12 通过；全量重跑（含 build）13/13 通过（终版证据取全量跑）。R1 当时记为「环境抖动」——**R2 判定该解释作废，实为上述默认拓扑间歇性 ICE 失败**
- **受控验收条件**：`infra/verify_web_livekit_client.py` R2 起 Chromium 显式加 `--allow-loopback-in-peer-connection`，使浏览器可收集 loopback candidate 与 LiveKit 的 127.0.0.1 媒体地址直接配对。该 flag 是本机 loopback LiveKit 部署的**受控验收条件**，生产用户浏览器默认并不具备
- **R2 受控连跑（gitignored `.verify/m14-35-web-livekit-client-r2/`）如实记录**：run1/run2 通过（run1 verdict=passed，12 checks）→ run3 connect 步失败（受控 flag 下间歇性未完全消除）→ run4/run5/run6 复跑连续三次通过（复核：全部 steps passed、检测窗口 console 零错误、`ws_url=ws://127.0.0.1:7880`）。**末段连续 3/3 构成受控拓扑下的验收通过；这不代表默认浏览器直连拓扑稳定**
- **生产阻塞项**：默认拓扑稳定化需要后续 LAN/TURN/显式绑定（node-ip 与 UDP 绑定面）的独立设计改造与验收（见 `docs/ROADMAP.md`「M14 下一生产阻塞点」）

## 验收拓扑（零服务重启）

| 组件 | 来源 | 动作 |
|---|---|---|
| API :8000 / LiveKit :7880 / DB | 复用当前运行的生产容器（aios-m14-03-production-rehearsal-*） | 只读探测（/health、7880 HTTP 应答）+ 合法业务端点写入（注册/登录验收用户 `learner_m14_35`、签发房间 token） |
| web | 本脚本在空闲端口自起【本分支构建】的 Next standalone 服务 | 构建期注入 `NEXT_PUBLIC_API_BASE_URL=""`（空串 → API_BASE 为相对路径）+ `AIOS_ACCEPTANCE_API_PROXY=http://127.0.0.1:8000`（next.config.ts env 门控 `/api/:path*` 同源 rewrite）；验收结束 terminate（15s 未退则 kill） |

同源 rewrite 的原因：生产 API 的 CORS 是精确 allowlist（仅 3000/3011 源），不为此验收放行新源；浏览器全程同源请求，HttpOnly `aios_auth` cookie（SameSite=Lax、host-only）自然携带。LiveKit WebSocket 由浏览器直连（ws 不受 CORS 约束）。rewrite 与 env 均为构建期编译。

R1 起该 env 经 `apps/web/src/lib/acceptance-proxy.ts` 的 `normalizeAcceptanceApiProxy` 结构化校验（仅 http/https、去尾斜杠、拒绝查询串/片段；错误信息不回显 env 原值）：

- **非法值 fail-closed 实测**：`AIOS_ACCEPTANCE_API_PROXY=ws://127.0.0.1:9000 npm run build` → `Failed to load next.config.ts … AIOS_ACCEPTANCE_API_PROXY 非法：仅允许 http/https 协议`，exit 1（构建期报错，绝不静默生成异常 rewrite）
- **默认未设置行为不变实测**：`env -u AIOS_ACCEPTANCE_API_PROXY -u NEXT_PUBLIC_API_BASE_URL npm run build` → 构建成功，`.next/routes-manifest.json` 的 rewrites 为 `{"beforeFiles":[],"afterFiles":[],"fallback":[]}`（与默认构建完全一致）

## 命令

```bash
"D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe" infra/verify_web_livekit_client.py
# 可选 env：AIOS_API_BASE（默认 http://127.0.0.1:8000）、AIOS_WEB_PORT、AIOS_SKIP_BUILD=1、AIOS_OUT
```

## PASS 判定表（R1 全量跑实测记录：verdict=passed，13/13 checks）

| # | 验收项 | 实测 | 判定 |
|---|---|---|---|
| 1 | env: 生产 API 可达 | GET /health 应答（只读探测） | ✅ |
| 2 | env: LiveKit 信令端口可达 | 127.0.0.1:7880 HTTP 应答（任何状态码即证明端口活着） | ✅ |
| 3 | seed: 验收用户可登录 | register（幂等）+ login 200 | ✅ |
| 4 | build: 本分支 web 验收构建成功 | `npm run build` rc=0（一次性通过，无 build-stderr.log） | ✅ |
| 5 | web: 自建验收服务就绪 | 空闲端口 Next start，/login 60s 内应答 | ✅ |
| 6 | web: UI 登录成功（同源 rewrite 网关生效） | UI 表单登录 → 跳转 `/`（HttpOnly cookie 经同源 rewrite 下发） | ✅ |
| 7 | ui: voice 首页渲染检测卡片 | `/voice` 出现 `[data-livekit-check]` | ✅ |
| 8 | hard: token/connect/data/cleanup 全部 passed | 五步 data-check-status：token/connect/data/mic/cleanup 全 `passed` | ✅ |
| 9 | hard: 麦克风步 passed 或 skipped（不虚报） | mic=`passed`（fake 设备真实发布音轨到生产 LiveKit，WebRTC ICE 在 127.0.0.1 通） | ✅ |
| 10 | hard: DOM 不含 JWT 形态 token | `JWT_RE.search(page.content()) is None`（token 只经组件局部变量喂 SDK，不进 state/DOM） | ✅ |
| 11 | hard: ws_url 回显为可达地址 | `ws_url="ws://127.0.0.1:7880"`（ws/wss 前缀） | ✅ |
| 12 | hard: 结论与麦克风步一致 | outcome=`passed` 与 mic=`passed` 一致 | ✅ |
| 13 | hard: 检测窗口 console/pageerror 零错误（R1 新增） | `console_error_count=0`（进入 /voice 起算，硬断言） | ✅ |

浏览器侧判定依据为 **DOM 级断言**（`data-check-outcome` / 每步 `data-check-status` / `data-check-ws-url` + 全页 JWT 正则扫描），等待条件为 `wait_for_function(outcome !== 'incomplete')` + 500ms 收尾渲染，非肉眼读图。

## 截图摘要（R1 全量跑产物，佐证，gitignored）

- `screenshots/01-initial.png`（90674 B，full page 1440×900）：登录后 `/voice` 初始态——检测卡片 + 五步「待检测」
- `screenshots/02-result.png`（96454 B，full page）：检测完成态——五步「通过」、结果横幅「检测通过：浏览器已连接 LiveKit 并发布麦克风音轨」、服务器地址 `ws://127.0.0.1:7880`
- 判定权威为上表 DOM 断言；截图为原始佐证。首轮交付时对 02-result.png 的本地视觉二次核验两次尝试均被工具侧阻断（GLM 视觉 API ECONNRESET×2；另一远端工具因 CDN URL 含反斜杠解析失败 400）——非任务阻塞，如实记录，未影响 DOM 断言结论

## 控制台观察（R1：分窗采集 + 硬断言）

- **登录窗口**（打开 /login 至登录成功）：console error 1 条——`Failed to load resource: the server responded with a status of 401 (Unauthorized)`，即登录页加载时 `auth/me` 会话探测被拒（未登录时的预期形态），单独记录为 `login_console_error_count=1`，不计入检测错误
- **检测窗口**（进入 /voice 起）：console error 与 pageerror **均为 0**（`console_error_count=0`），已升级为硬性 check（判定表 #13）——非零即 FAIL
- results.json 落盘前经 JWT 正则脱敏（`[REDACTED-JWT]`），本次实际无命中

## R1 返工八项落点

1. **唯一随机房间**：`LIVEKIT_CHECK_ROOM` 固定常量已删除；`generateCheckRoom()` 每次检测生成 `web-check-<16 位 URL 安全随机字符>`（Web Crypto `crypto.getRandomValues` + 拒绝采样防取模偏置，>=248 字节丢弃重掷），组件每次点击调用。测试：房间名匹配后端 `^[A-Za-z0-9_-]{3,64}$`、形如 prefix+16 位字母数字、500 次全唯一、mock 随机字节决定输出、偏置字节被拒重掷（5 项）
2. **UI 约束**：`livekit-connect-card.tsx` 容器 `rounded-xl`→`rounded-lg`（8px，符合设计系统），未触碰其他旧文件
3. **CI**：`.github/workflows/ci.yml` Web job 更名 `Web (test / typecheck / lint / build)`，build 前新增 `npm run test --workspace apps/web`（根 package.json 的 `test` 指向 pytest，必须 workspace 定向才能跑 web 的 vitest）——vitest 成为 PR 门禁
4. **验收代理安全**：`AIOS_ACCEPTANCE_API_PROXY` 结构化校验（见上文拓扑节，含 fail-closed 与默认不变两向实测）+ 7 项 vitest
5. **Console hard gate**：见上文「控制台观察」
6. **首页文案**：`voice-index-view.tsx` 改为「练习仍使用浏览器本地语音；LiveKit 连接检测已接入，完整 ASR/TTS 会话尚未接入」
7. **依赖安全**：`npm audit --omit=dev --registry=https://registry.npmjs.org`（仓库根，官方 registry）→ **found 0 vulnerabilities**（exit 0）
8. **证据重跑同步**：本 README 与 PROJECT_STATUS / ROADMAP / CHANGELOG 全部按重跑事实更新（29/29 测试、13/13 checks、console_error_count=0、audit 0 漏洞），未保留返工前结论

## 本地验证（R1 重跑留证）

- 单测：RED（新断言先行，`generateCheckRoom`/`normalizeAcceptanceApiProxy` 未导出 → 5 failed）→ GREEN **29/29 passed**（`npm run test --workspace apps/web`：livekit-check 22 项 + acceptance-proxy 7 项，Duration 153ms）
- `npm run typecheck`：通过（0 error；期间修复一处 mock 类型收窄——lib.dom 的 getRandomValues 形参是 `ArrayBufferView | null`，测试经 Uint8Array 视图写入）
- `npm run lint`：0 errors / 11 warnings——逐文件核对，warning 全部来自既有文件（exam/governance/library/papers/progress/voice-studio/api/gsap），无一来自本任务新增/修改文件
- `npm run build`（仓库根，默认无验收 env）：成功（9 路由），routes-manifest rewrites 为空（见拓扑节）
- 非法 proxy env 构建：fail-closed 报错（见拓扑节）
- `npm audit --omit=dev --registry=https://registry.npmjs.org`：0 vulnerabilities
- 真实浏览器验收：13/13 checks，verdict=passed（见判定表；R2 修订判定见上节——受控条件口径）
- API 合约未改动（仅前端新增消费方 `api.voiceToken`，对齐 services/api 既有 voice token 契约）→ pytest 不适用，如实说明而非跳过隐瞒
- R2 收口为 docs-only：不重跑浏览器验收（R2 受控连跑事实见上节），单测/typecheck/lint/build/audit 沿用 R1 留证；对 `infra/verify_web_livekit_client.py` 仅做轻量语法检查（`py_compile` / `ruff`）+ `git diff --check`

## 诚实边界（未覆盖与不宣称）

1. 本验收是**连接诊断切片**：验证「真实浏览器 → 获取 token → Room.connect 加入当前部署 LiveKit → 数据通道 publishData →（权限可用时）发布本地麦克风音轨 → 退出清理」这条链路；**不代表完整语音会话（ASR/TTS）已接入**，UI 内亦有边界声明文案
2. 麦克风为 Chromium fake 设备（`--use-fake-device-for-media-stream` + 授权），证明的是「浏览器侧 getUserMedia + publishTrack 到真实 LiveKit」链路可用，非真实声学采集质量
3. verdict 分级：token/connect/data/cleanup 硬性必须 passed；mic 允许 skipped-with-reason（verdict=passed-with-skip，同样 exit 0）；环境不可达 exit 2；任何硬断言失败 exit 1（fail-closed，不伪造 PASS）。R1 全量跑与 R2 受控通过段实跑均为最高档 passed
4. 每次检测唯一随机房间解决了「student grants 可订阅 → 固定房间并发互听」的隐私缺陷；但单次单浏览器仍 ≠ 长稳/并发/多客户端（远端订阅、多人房间、重连恢复均未覆盖）；**且通过是在 loopback 受控条件（`--allow-loopback-in-peer-connection`）下取得——默认拓扑存在间歇性 ICE 失败（受控 flag 下 run3 仍失败），默认拓扑稳定化（LAN/TURN/显式绑定设计）为独立生产阻塞项**；**不构成全局 `production_ready=true` 的依据，全局 `production_ready=false` 不变**
5. 自建 web 验收服务用本分支构建（验收 env），与生产 :3011 容器不同实例；生产容器本身零触碰零重启
6. M14-35 全程零生产触碰：未 start/stop/restart FunASR/CosyVoice/voice sidecar/Docker/WSL/wslrelay/CC Switch/本地代理/计划任务；未读取/打印任何 key/token/env 值（token 仅在浏览器内存中经局部变量使用）；port-guard 钩子对既有端口的预警均为对既有服务的只读探测或字符串常量（fail-closed 演示用的 `ws://127.0.0.1:9000` 未实际连接），按钩子指引忽略
