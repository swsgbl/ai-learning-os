# M14-47 生产 web 容器浏览器验收证据回填（只读验收，零生产触碰）

- 回填日期：2026-09-18（本地 GMT+8）；验收执行窗口 2026-09-18 04:56–05:02（+08:00）
- 回填基点：本 worktree `m14-47-production-web-container-acceptance` 基于 `main@c817ade`（PR #124 merge，即 M14-46 回填入库后的 main；回填全程零生产触碰、零 Docker 操作、零代码改动）
- 本回填（M14-47）为 **docs-only**：只把 supervisor 已完成的只读浏览器验收事实转为可审计仓库证据；收口 M14-46 记录的「生产 web 容器未经浏览器验收」诚实边界
- 原始证据：主 checkout gitignored `.verify/m14-47-production-web-container-acceptance/`（4 张截图 + `ACCEPTANCE-REPORT.md` + `MANIFEST.txt` 全量 SHA-256 清单）——**截图与 MANIFEST 本体均不入 git**，本 README 仅摘录判定事实与哈希锚点。回填前已用 `certutil` 对清单内全部 5 个文件逐一复核 SHA-256，**5/5 与 MANIFEST 一致**；MANIFEST 自身 SHA-256（回填时计算）`3467969daf657ab2c8a2b229100b6ff213bd4d82ddbbb9aea4d673ac35b476fa`
- 秘密边界：验收与回填全程未打印、未复制任何 secret/token/password；本 README 无凭据、无环境值、无真实绝对主机路径、无原始输出
- 结论：**生产 web 容器对 desktop 与 390px mobile 匿名 `/` 与 `/login` 的直接渲染验收 PASS**；本回填不声称 `production_ready=true`

## 验收对象（只读，未重建/未重启/未重配置）

| 项 | 值 |
|---|---|
| 容器名 | `aios-m14-03-production-rehearsal-web-1` |
| 容器 ID | `5be2e19db2e745af96a0d08b2dfaa64f0529b34d80e549e639a021b7c74294e3`（前 12 位 `5be2e19db2e7`，与 M14-46 时点同 ID——本切片对其零接触） |
| 镜像引用 | `aios/web:m14-05-security` |
| Docker image ID | `sha256:793060d3210f93d331463bf86014f46943aa4b4e7fb8b2b32483f696d0115fc2` |
| 健康状态 | `healthy` |
| 启动时间 | `2026-09-17T06:34:50.959926311Z`（本次验收前已在运行） |
| Web 入口 | `http://127.0.0.1:3011`（有意绑定 loopback） |

## PASS 判定表

| # | 验收项 | 实测 | 判定 |
|---|---|---|---|
| 1 | HTTP `GET /` | **200**，HTML 17,759 bytes | ✅ |
| 2 | HTTP `GET /login` | **200**，HTML 16,170 bytes | ✅ |
| 3 | API 健康 | `GET http://127.0.0.1:8000/health` **200**，body `{"status":"ok","service":"ai-learning-os-api"}`（健康端点为 **`/health`**，不是 `/api/v1/health`） | ✅ |
| 4 | 桌面 Chromium `/` | title `AI Learning OS`；可见标题含 `把复杂留给系统，把简单留给你`、`学习工作台`、`考场审阅`、`语音陪练`；导航与未认证占位卡渲染 | ✅ |
| 5 | 桌面 Chromium `/login` | title `AI Learning OS`；可见 `登录` 标题、用户名/密码字段、登录/注册控件渲染 | ✅ |
| 6 | 页面 JS 异常 | `/` pageerror **0**；`/login` pageerror **0** | ✅ |
| 7 | 匿名认证行为 | 重复网络捕获中两次 `GET /api/v1/auth/me` 均返回 **401**——无会话时的预期行为 | ✅ |
| 8 | Mobile 390×844 | `/` 与 `/login` 均 200、渲染预期内容、pageerror **0**、**无水平溢出** | ✅ |

不计数项（如实披露）：首次冷加载出现过**单次 404 资源消息**，在后续聚焦网络捕获中**未复现**；按验收口径**不计入** pass 判定，也不作为失败判定。

## 证据完整性锚定

主 checkout `.verify/m14-47-production-web-container-acceptance/` 清单（摘自 MANIFEST.txt，回填前已逐文件复核一致）：

| 文件 | 大小（bytes） | SHA-256 |
|---|---:|---|
| `ACCEPTANCE-REPORT.md` | 2,966 | `33EFF5F0E464A692209C5595D7724D42051BFF6687D9A040DCE361B5CD1CD2A0` |
| `home.png` | 69,227 | `A8C4EA9D1D22DBBA81AFD6462905A5EF3233D7AC6435B4DC61E39E93FB0B39CD` |
| `login.png` | 35,562 | `3FCD64CF3251F9989FC6481940A5C4934564F8FE1E106BD9871BE997968E2A7C` |
| `mobile-home.png` | 61,264 | `59F8F8329350D90C6A45EA5C67D61991F04BF7CE0165817685744C7A19A2253C` |
| `mobile-login.png` | 32,333 | `A1FE3F6472EF4476B2660CF52AC5397A2DBE4DCF7DDB080A68C609F0FD666403` |

四张截图均经目视检查：desktop 与 mobile 布局、导航、内容层级、登录控件完整呈现，无重叠、无空白渲染。原始证据目录保持 gitignored，不入库；上表哈希即完整性锚点。

## 如实披露与诚实边界（residuals）

1. **覆盖面窄**：本验收只覆盖 desktop 与 390×844 mobile 匿名态 `/` 与 `/login` 的直接渲染。不含：认证后工作流、其他移动宽度、跨浏览器兼容、真实用户负载。
2. **无 LAN 公开 web 入口**：当前 web 服务有意绑定 loopback `127.0.0.1:3011`；`192.168.8.3` 是 **LiveKit media 绑定**，不是 web 绑定。LAN-public web endpoint 未验收、未开放。
3. **不隐含 provider smoke**：本验收只证明 web 容器渲染面，不重跑、不隐含 ASR/TTS provider 冒烟通过。
4. **WORM 离线第二副本 / 定期归档 / 长稳就绪仍开放**（M14-44 起开放项）。
5. **休眠并行 compose 项目未删除**（M14-46 遗留运维项）。
6. **LAN IP 静态假设**：生产拓扑 livekit 绑定仍依赖 LAN IP `192.168.8.3`，DHCP 变更风险不变。
7. **release/cutover 批准**：生产发布与正式 cutover 仍未获批、未执行。

单次只读验收 ≠ 长期稳定：本 PASS 仅覆盖本文列出的验收项，不构成全局就绪依据；全局 **`production_ready=false` 不变**。

## 边界遵守声明（本回填回合）

本 M14-47 回填回合零 Docker/零生产/零服务操作、零代码改动（全部事实摘自 supervisor 已产出的 gitignored 原始证据，引用哈希均经回填前复核）；未 commit/push；未打印或复制任何 secret/token/password；截图与原始 MANIFEST 不入 git；无真实绝对主机路径、无容器日志、无凭据、无原始输出；仅改动 docs 三文件（本 README + PROJECT_STATUS + ROADMAP）。
