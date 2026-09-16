# M14-37 LiveKit 默认拓扑稳定性（默认/受控浏览器模式开关 + 媒体面独立绑定）

- 仓库基点：分支 `fix/m14-37-livekit-default-topology` 基于 `main@54e8005`（PR #113 merge），本 Claude 开发回合独占 worktree、单 local commit、不 push
- 原始证据：gitignored `.verify/m14-37-default-topology/run1..run8/`（每轮 results.json + screenshots/01-initial.png、02-result.png；run1 含构建步 13 checks，run2–8 `AIOS_SKIP_BUILD=1` 复用构建 12 checks）、`.verify/m14-37-controlled-regression/run1/`、`.verify/m14-37-illegal-value-probe/`（ENV-BLOCKED 早退，无 results.json 为预期）——均不入 git，本 README 仅摘录判定事实

## 背景（M14-35 遗留生产阻塞）

当前 compose 生产栈 LiveKit 以 `--node-ip 127.0.0.1` 通告媒体地址且 UDP 端口只绑 `127.0.0.1`；Chromium/WebRTC 默认不收集 loopback ICE candidate，默认浏览器直连存在**间歇性 ICE 失败**（Codex 独立默认拓扑首跑 connect failed：`could not establish pc connection`，同构建复跑通过——拓扑级不确定）。M14-35 R2 的 `--allow-loopback-in-peer-connection` 受控 flag 只是**验收口径**，生产用户浏览器默认并不具备；M14-36 诚实边界第 4 条再次确认默认拓扑稳定化为独立生产阻塞项。

## 修复（双侧：验收口径诚实化 + 媒体面独立绑定拓扑）

### 1. 验收工具：默认/受控浏览器模式严格开关（`infra/verify_web_livekit_client.py`）

| 面 | 旧（M14-35 R2/M14-36） | 新（M14-37） |
|---|---|---|
| Chromium argv | 恒定注入 `--allow-loopback-in-peer-connection`（受控条件硬编码） | `AIOS_LIVEKIT_BROWSER_LOOPBACK` 严格开关：未设置/`0` = **default 模式，绝不注入 loopback flag**（代表生产用户默认浏览器拓扑，失败即真实生产阻塞证据）；字面 `1` = **controlled 模式**，追加 `LOOPBACK_FLAG` 常量（M14-35 R2 受控口径保留） |
| 非法开关值 | — | 任何其他值（`true`/空串/空白/`01`/带换行等 10 变体）→ **ENV-BLOCKED fail-closed**（SystemExit 退出码 2，拒绝执行，绝不静默当默认——防拼错开关把受控拓扑误标成默认拓扑验收） |
| flag 字面量 | 内联在 launch 调用注释块 | `LOOPBACK_FLAG` 常量为**全源码唯一定义处**（launch 只引用常量；契约测试锁定源码中该字面量恰好出现一次，杜绝旁路硬编码把它带进 default 模式） |
| 可审计性 | — | results.json 新增 `browser` 段：`mode`（default/controlled）、`loopback_env_var`、`loopback_flag_present`、`chromium_launch_args` 摘要（不含任何凭据）；另有 browser 段永不泄露 JWT/token 的契约测试 |

### 2. 部署拓扑：LiveKit 媒体面独立绑定（`AIOS_LIVEKIT_BIND_IP`）

修复形态：LiveKit 单独绑本机 LAN IP，浏览器拿到的是**常规 LAN candidate**（无需受控 flag）；API/Web 仍走 `AIOS_BIND_IP`，不必为本机浏览器 ICE 稳定性整体公开。

| 面 | 行为 |
|---|---|
| 端口绑定回落链（`infra/docker-compose.yml`） | livekit 7880/7881/UDP 7882-7892：`AIOS_LIVEKIT_BIND_IP` → `AIOS_BIND_IP` → `127.0.0.1`；api/web/postgres/redis/minio 一律不受影响；**未设置 = 存量行为零变化**（livekit 仍跟随 `AIOS_BIND_IP`） |
| `--node-ip` 通告回落链 | `AIOS_LIVEKIT_EXTERNAL_IP`（显式优先，M9-08 局域网语义保留）→ `AIOS_LIVEKIT_BIND_IP` → `127.0.0.1`（单变量路径下绑定与通告同一 LAN IP 自洽；**务必填具体本机 IP，不要 0.0.0.0**——node-ip 通告 0.0.0.0 不可配对，等于没修） |
| 意图透传（`services/api/app/core/config.py` + compose env） | `HOST_LIVEKIT_BIND_IP` → settings `host_livekit_bind_ip`（空 = 未启用） |
| fail-closed 启动校验（`services/api/app/core/security.py` `validate_exposure` 新增 `livekit_bind_ip` 参数；`app/main.py` 接线） | LiveKit 单独非 loopback（API/Web 仍 loopback）时只要求媒体面自身前置：1) 强 `LIVEKIT_API_SECRET`（≥32 字节且非仓库公开默认占位值）；2) `PUBLIC_LIVEKIT_URL` 为浏览器可达地址（不得缺失/为容器内部或 127.0.0.1 地址）。任一不满足即 RuntimeError 拒绝启动。该场景 API 未公开，APP_ENV=production/CORS 门禁**不适用**（同机 localhost Web 源是合法配置）；API/Web 面公开时 M9-06/M9-07 原四项校验语义零改动 |
| 文档 | `.env.example`（AIOS_LIVEKIT_BIND_IP 注释块含回落链/安全边界/0.0.0.0 警告）+ `docs/DEVELOPMENT.md`（部署绑定节 + 「默认浏览器 ICE 稳定拓扑（M14-37）」新小节：一键命令、回落链、fail-closed 前置、TURN 后备衔接、验收口径） |
| 远程设备后备 | 跨对称 NAT/严格防火墙仍连不上时走既有 M10-05 TURN 部署模板（`docs/COTURN_DEPLOYMENT.md`），本任务不启用 TURN |

## 小返工（review rework，amend 回原单 commit，不新增 commit）

监督者 review 提出三点修正，同 commit amend 交付（纯启动校验收紧 + 测试修正，不触碰任何运行中容器/服务）：

1. **`validate_exposure` LiveKit bind IP fail-closed 收紧**（`services/api/app/core/security.py` 新 `_resolve_livekit_bind_ip`）：`livekit_bind_ip` 不再只靠字符串集合判断，先经 stdlib `ipaddress` 解析——
   - wildcard（`0.0.0.0`/`::`，含解包后的 `::ffff:0.0.0.0`）→ RuntimeError：可作端口 bind 但不能作 `--node-ip` 通告地址，放它进入公开分支是无效修复配置（文档既有 0.0.0.0 警告的代码化）；错误信息明确要求**具体本机 LAN IP**；
   - 非法字面量（段数错误/空格/换行/误带端口）→ RuntimeError（fail-closed，不静默当 loopback/未启用）；
   - IPv4-mapped（`::ffff:a.b.c.d`，Windows dual-stack 常见形态）先解包为 IPv4 再分类：`::ffff:127.0.0.1` = loopback、`::ffff:0.0.0.0` = wildcard——Python ipaddress 对 mapped 地址的 is_loopback/is_unspecified 按 IPv6 字面判定，直接用会把 `::ffff:127.0.0.1` 误判为非 loopback（口径已在测试注释写明）；
   - **空串保留为「未启用」哨兵**（`""` 等价 `None`）：compose 默认部署 `${AIOS_LIVEKIT_BIND_IP:-}` 未设置时向 API 透传 `HOST_LIVEKIT_BIND_IP=""`（test_m905 compose 契约锁定），拒绝空串会使全部默认部署启动失败——「拒绝非法」聚焦非空字面量，此为本返工的显式口径决定而非遗漏；
   - loopback/非 loopback 判定对存量输入（`127.0.0.1`/`::1`/`localhost`/LAN IP/作为 host bind 的 `0.0.0.0`）语义零变化；拒绝与 API/Web 面状态无关（API 公开 + LiveKit wildcard 同样拒绝）。
2. **恒真断言修复**（`services/api/tests/test_verify_web_livekit_client.py`）：`assert "token" not in blob.lower() or report`（占位恒真，永不检验）替换为真实否定断言——browser 段 JSON 序列化后不得出现 `token`/`jwt`/`secret` 字样（原 JWT 三段形态正则断言保留），锁死报告结构不得新增凭据类字段。
3. **测试补齐（TDD）**：`test_admin_audit.py` 新增 `test_exposure_livekit_bind_rejects_wildcard_and_invalid_literals`（wildcard ×3、非法字面量 ×5、API 公开面下 wildcard、mapped-loopback 放行、合法 LAN IP IPv4/IPv6 ULA 原语义通过）；RED 先行实证（旧实现下 `0.0.0.0` DID NOT RAISE）后 GREEN。

返工验证：聚焦三测试文件 **77 passed**（原 76 + 新增 1）；ruff/py_compile/`git diff --check` 全绿；增行 secret 扫描 0 命中。既有证据（默认拓扑 8/8、受控回归 1/1、非法值探针）不受影响——本返工未重启/未重建/未变更任何生产栈状态。

## 测试（TDD，全 mock 零真实浏览器/零容器运行）

新增 **14 个测试函数**（test_verify_web_livekit_client.py +10，文件累计 27；test_admin_audit.py +3；test_m905_ownership_audit.py +1；含小返工补齐的 fail-closed 用例与恒真断言修复）：

- **开关语义 4 项**（monkeypatch env）：未设置 = default off；显式 `0` 仍 off；`1` = controlled on；非法值参数化 **10 变体**（`true`/`TRUE`/`yes`/`on`/空串/空白/`2`/`01`/`0 `/`1\n`）全部 SystemExit fail-closed
- **argv 纯净性 1 项**：default 模式 launch args 恰为基础两条（fake 麦克风 + 免授权 UI），不含 loopback flag；controlled 恰追加一条且有序
- **可审计与脱敏 3 项**：browser 段记录 mode/env var/flag present/argv 摘要；browser 段**永不泄露 JWT/token**（含三段 JWT 形态）；`sanitize` 对 JWT 形态脱敏
- **源码文本契约 2 项**：`--allow-loopback-in-peer-connection` 字面量在全源码**恰好出现一次**（唯一定义在 `LOOPBACK_FLAG` 常量，杜绝旁路硬编码）；`results` 记录 browser 段
- **暴露校验 2 项**（纯函数，零启动）：LiveKit 单独非 loopback 时缺强 secret 或 URL 不可达 → RuntimeError；loopback/未设置 → 零门禁（存量行为）
- **compose 渲染矩阵 1 项**（`docker compose config` 渲染，不启容器）：`AIOS_BIND_IP=127.0.0.1` + `AIOS_LIVEKIT_BIND_IP=<LAN>` 时 livekit 全部端口 host_ip=LAN 而 api/web 仍 127.0.0.1；`HOST_LIVEKIT_BIND_IP` 透传；node-ip 回落链 EXTERNAL > BIND > 127.0.0.1

## 验证

| 门 | 结果 |
|---|---|
| 聚焦 `pytest tests/test_verify_web_livekit_client.py tests/test_admin_audit.py tests/test_m905_ownership_audit.py` | **76 passed**（首发终验复跑）；小返工后 **77 passed**（+1 新用例） |
| 全量 `pytest services/api/tests` | **全绿**（前线程报告；本收尾回合未重跑全量） |
| `ruff check`（8 个改动 Python 路径） | All checks passed |
| `py_compile`（改动 Python 文件） | 通过 |
| `git diff --check` | 干净 |
| 增行 secret 扫描（JWT 三段 / key/secret 赋值 / LiveKit 凭据行） | **0 真实凭据命中**（仅 2 处良性占位：DEVELOPMENT.md 示例命令的 `'<至少 32 字节随机串>'` 描述、测试 fixture 引用仓库公开默认占位串作为待拒绝弱值） |

## 真实默认浏览器验收（8 连跑，生产栈零重启）

命令（run1 全量含构建；run2–8 加 `AIOS_SKIP_BUILD=1` 复用构建）：`AIOS_OUT=.verify/m14-37-default-topology AIOS_LIVEKIT_BROWSER_LOOPBACK 不设置` 执行 `infra/verify_web_livekit_client.py`——**8/8 run 全部 verdict=passed**：

- 每轮 browser 段 `mode=default`、`loopback_flag_present=false`（argv 摘要可审计，绝无受控 flag）
- 每轮五步 token/connect/data/**mic**/cleanup 全 `passed`（fake 麦克风音轨真实发布到生产 LiveKit；无 mic skip）
- `ws_url=ws://127.0.0.1:7880`、DOM 不含 JWT 形态 token、检测窗口 console/pageerror 零错误（登录窗口预期 401 单独记录不计入）
- 生产 API(:8000)/LiveKit(:7880)/DB 复用当前容器，**零服务重启、零生产 env 变更**

### 受控模式回归（M14-35 R2 旧口径不破坏）

`AIOS_LIVEKIT_BROWSER_LOOPBACK=1` 单跑 `.verify/m14-37-controlled-regression/run1`：verdict=passed，browser 段 `mode=controlled`、`loopback_flag_present=true`，五步全 passed——受控验收能力保留且与 default 模式可区分。

### 非法开关值 fail-closed 探针

`AIOS_LIVEKIT_BROWSER_LOOPBACK=true`（典型拼错形态）真实执行：**ENV-BLOCKED 退出码 2**，早退于任何浏览器/生产栈动作之前；`.verify/m14-37-illegal-value-probe/` 仅空 screenshots 目录、无 results.json（脚本设计使然，与 docstring 声明一致）。

### 孤儿进程断言（M14-36 修复不回归）

本 worktree 专属进程扫描（`node.exe`/`mise.exe`/`cmd.exe` 命令行含本 worktree 路径）：8 连跑 + 受控回归 + 探针全部结束后 **FOUND 0**——M14-36 精确进程树回收在新开关路径下保持零残留。

## 诚实边界

1. **8/8 默认连跑是「间歇性失败未在本批复现」的实证，不是 loopback 拓扑已稳定的证明**——M14-35 已实证该拓扑失败为间歇性（Codex 独立首跑失败、同构建复跑通过）；本任务未改动生产栈绑定（ws_url 仍 `ws://127.0.0.1:7880`，生产 env 零变更），loopback 拓扑上的间歇性 ICE 风险**依然存在**
2. **拓扑修复以能力形态交付，未应用到运行中的生产栈**：`AIOS_LIVEKIT_BIND_IP` 独立绑定 + fail-closed 校验 + compose 渲染矩阵测试 + 文档已就绪，但按「不动生产 env/不重启容器」红线，实际切换（设 LAN 绑定 + 强 secret + 可达 URL 后 `docker compose up -d`）留给运维显式执行；切换后的真实浏览器默认拓扑验收同理待运维执行本工具
3. 8 连跑为同机、同栈、单浏览器（Chromium headless + fake 麦克风、每次唯一随机房间）；不覆盖远程设备/跨 NAT（那是 M10-05 TURN 后备域）、多人房间、重连恢复、长稳/并发
4. compose 渲染矩阵经 `docker compose config` 渲染验证（不启容器）；`validate_exposure` LiveKit-only 分支经纯函数测试验证，未以真实弱配置启动过 API 容器（fail-closed 行为由测试锁定）
5. **不构成 `production_ready=true` 依据，全局 `production_ready=false` 不变**；全程不读取/打印任何 key/token/env secret 值
