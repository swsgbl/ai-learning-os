# M14-38 LiveKit LAN cutover 收口证据

- 日期：2026-09-16
- 分支：`ops/m14-38-livekit-lan-cutover` 基于 `main@902dca2`（PR #114 merge，M14-37）
- 生产栈：project `aios-m14-03-production-rehearsal`（`--profile local` 六服务）
- 结论：**LAN cutover 已在生产彩排栈真实执行并验证通过**（supervisor 执行，本回合零生产操作）；验收工具硬编码缺陷与恢复路径拓扑防漂移缺口已在 worktree 修复并固化为可重复 runbook 工具。

## 一、生产执行事实（supervisor 执行，本回合仅记录不重复）

| 事实 | 值 |
| --- | --- |
| 本机 LAN IP | `192.168.8.3` |
| env 原子更新 | `infra/env.production-recovery`（gitignored）中 `AIOS_LIVEKIT_BIND_IP=192.168.8.3`、`AIOS_PUBLIC_LIVEKIT_URL=ws://192.168.8.3:7880`（`AIOS_BIND_IP` 未动，仍 `127.0.0.1`——信令/媒体面独立绑定，正是 M14-37 交付的能力） |
| 切换前备份 | `artifacts/ops/livekit-cutover/20260916-122542-env.backup`（回滚锚点） |
| 重建范围 | **仅** `api` + `livekit`（新容器 api=`b3e62b355703`、livekit=`b21a1802e4ae`）；`web`/`redis`/`postgres`/`minio` 未触碰 |
| 切换后健康 | 六容器全部 healthy |
| API 事实 | 实测 `POST /api/v1/voice/token` 响应 `ws_url=ws://192.168.8.3:7880` |
| 默认浏览器验收 | **3/3 通过**（`AIOS_LIVEKIT_BROWSER_LOOPBACK` default 模式，绝不注入 loopback flag）——证据 `.verify/m14-38-production-lan-default-run1` / `run2` / `run3` |
| 切换后只读监控 | `overall_status=ok`，34 ok / 0 warn / 0 critical——证据 `.verify/m14-38-production-monitor-post-cutover` |

默认浏览器 3/3 通过同时收口了 M14-35 遗留的「loopback 媒体面下默认浏览器间歇性 ICE 失败」：媒体面绑 LAN IP 后浏览器收集的常规 ICE candidate 可直接配对，无需受控 flag。

## 二、本回合交付（worktree 代码，开发验证为 FakeRunner 契约测试；cutover 工具随后由 supervisor 完成两阶段真实 Docker 验收——见第四节；本 Claude 回合零生产操作）

1. **`infra/verify_web_livekit_client.py` LiveKit 信令面探测修复**：旧版 preflight 硬编码探测 `http://127.0.0.1:7880`——LAN cutover 拓扑下探测目标错误（验收工具会误报 ENV-BLOCKED）。现改为「契约派生优先 + 显式 override 严格校验」：
   - 默认（`AIOS_PUBLIC_LIVEKIT_URL` 未设置）：登录验收用户后请求 `POST /api/v1/voice/token`，以响应 `ws_url` 派生同源 http/https 健康 URL——与浏览器实际拿到的地址同源（探测即业务契约本身，不重造第二事实源）；loopback 拓扑派生 loopback 信令地址、LAN 拓扑派生 LAN 信令地址，**两类拓扑零改动覆盖**；
   - 显式 override（已设置）：严格校验（仅 ws/wss + 非空主机名）后采用；空串/非法 scheme/缺主机名 → ENV-BLOCKED fail-closed（绝不静默回退）；
   - 探测不可达 → ENV-BLOCKED（禁止自行启动/重启服务）；
   - 契约测试 72/72（`services/api/tests/test_verify_web_livekit_client.py`）。
2. **三拓扑键纳入 production recovery pin（九键防漂移）**：`tools/ops/production_recovery.py` 的 `PIN_KEYS` 从六键扩至九键——新增 `TOPOLOGY_PIN_KEYS = (AIOS_BIND_IP, AIOS_LIVEKIT_BIND_IP, AIOS_PUBLIC_LIVEKIT_URL)`。动机：compose 中 `AIOS_LIVEKIT_BIND_IP` 缺省回落 `AIOS_BIND_IP`→`127.0.0.1`，不 pin 则**恢复路径会把 LAN 拓扑静默重建回 loopback**。在线事实：`AIOS_BIND_IP`/`AIOS_PUBLIC_LIVEKIT_URL` 取 api 容器 env（`HOST_BIND_IP=`/`PUBLIC_LIVEKIT_URL=`，空串也是事实=漂移拒绝）；`AIOS_LIVEKIT_BIND_IP` 取 `docker port <livekit> 7880` 宿主绑定段。输出恒仅键名（拓扑值虽非密钥也不回显）。模板 `infra/env.production-recovery.example` 同步九键注释与占位。契约测试 53/53（`services/api/tests/test_production_recovery.py`，M14-38 段新增 8 测试）。
3. **`tools/ops/livekit_lan_cutover.py`（533 行）+ 契约测试 29/29**：把本次 supervisor 手工执行的切换流程固化为可重复、可审计的 runbook 工具（复用 production_recovery 的 Runner/RunLog/check_pins——pin 语义单一事实源）。见下节命令链。

## 三、runbook 命令链（`tools/ops/livekit_lan_cutover.py`）

```bash
# 1. 只读体检 + 切换 delta 预览（零改动零重建、零文件系统写——不建
#    artifacts 目录、不写日志文件，stdout 照常；退出码 2 = 参数错，1 = pin 未就绪）
python tools/ops/livekit_lan_cutover.py plan --livekit-ip 192.168.8.3

# 2. 执行切换（需精确确认令牌；流程与 supervisor 本次手工执行一致）
#    gate（九键全绿或仅缺拓扑键的首次采纳形态）→ 备份（+sha256 锚定）→
#    原子更新仅拓扑键（secret 行字节原样）→ compose config 静态校验（失败即
#    恢复备份、零重建）→ 最小范围重建 up -d --no-build --no-deps 仅 api livekit（--no-deps 隔断 compose 依赖解析，绝不连带重建 depends_on 依赖） →
#    健康等待（仅 api/livekit）→ 九键复核必须全绿
python tools/ops/livekit_lan_cutover.py apply --livekit-ip 192.168.8.3 \
    --confirm-rebuild rebuild-api-livekit

# 3. 回滚（伴生 sha256 校验先行——路径 <stamp>-env.sha256 由备份名推导，
#    缺失/格式非法/文件名不匹配/哈希不匹配任一即拒绝且零副作用：不读备份
#    pin 值、不建 safety 备份、不调 Docker、不写 env → 备份九键校验 →
#    回滚前 safety 快照 → 原子恢复 → 同样最小范围重建+复核）
python tools/ops/livekit_lan_cutover.py rollback \
    --backup-file artifacts/ops/livekit-cutover/<stamp>-env.backup \
    --confirm-rebuild rebuild-api-livekit
```

安全边界（源码契约测试锁定）：绝不构造 stop/rm/kill/down/restart/reset 子命令；绝不 `--build`；绝不触碰 web/redis/postgres/minio；secret 值永不进日志（RunLog 防御性脱敏）；重建失败打印确切回滚命令但不自动回滚（交人工裁决）；plan 恒零文件系统写。执行门 `--confirm-rebuild rebuild-api-livekit` 精确值，任何变体（空/yes/尾空格）即 USAGE 拒绝、零改动。

## 四、cutover 工具两阶段真实 Docker 验收（supervisor 执行，2026-09-16 补充）

| 阶段 | 事实 |
| --- | --- |
| 首轮真实 apply（彼时缺 `--no-deps`） | **暴露依赖隔离缺陷**：compose 仍试图连带重建 depends_on 依赖 minio，因 `aios/minio` 镜像缺失失败——可见失败；六容器原样保持 healthy（零隐性副作用） |
| 缺陷修复后的状态收敛 | env 采纳第九键 `AIOS_BIND_IP`，`plan` 复核达 **9/9** |
| 修复（补 `--no-deps`）后真实 apply `20260916-181457` | **成功**：`up -d --no-build --no-deps api+livekit`；api `b3e62b355703` 与 web/redis/postgres/minio 容器 ID 全部不变；livekit 重建为 `49935f127ca0` 并 healthy；复核 pin **9/9** |
| 修复后默认浏览器 LiveKit 验收 | **通过**——证据 gitignored `.verify/m14-38-production-lan-default-r4-post-nodeps`（voice-token-contract `ws://192.168.8.3:7880`，token/connect/data/mic/cleanup 全过，console/pageerror 0） |

首轮失败的价值是把依赖隔离缺陷显式暴露而非静默通过；该尝试本身违反最小范围契约，因此修复必须以 `--no-deps` 隔断依赖。修复后同一 runbook 在真实 Docker 上端到端通过。

## 五、判定表

| 判定 | 结果 | 依据 |
| --- | --- | --- |
| 生产 LAN cutover 实际生效 | ✅ | API `ws_url=ws://192.168.8.3:7880` 实测 + livekit 容器端口宿主绑定段为 LAN IP |
| 默认浏览器（无受控 flag）真实可用 | ✅ 3/3 | `.verify/m14-38-production-lan-default-run1..3` |
| 切换后栈与语音只读监控全绿 | ✅ | `overall_status=ok` 34/0/0，`.verify/m14-38-production-monitor-post-cutover` |
| 非目标服务零触碰 | ✅ | 仅 api/livekit 重建，web/redis/postgres/minio 容器未变 |
| 验收工具 LAN 拓扑可用 | ✅ | 信令探测契约派生（72/72 测试，两类拓扑零改动覆盖） |
| 恢复路径不回退拓扑 | ✅ | 九键 pin（53/53 测试：缺拓扑键/在线漂移/空串事实均 fail-closed 拒绝） |
| 切换流程可重复 | ✅ | cutover 工具 + 29/29 契约测试（rollback 伴生 sha256 四态 fail-closed，plan main 级零文件系统写）+ 两阶段真实 Docker 验收（第四节：首轮暴露缺陷 fail-visible、修复后 `20260916-181457` apply 全绿） |

## 六、边界声明（如实，不构成 `production_ready=true` 依据）

- `production_recovery --dry-run` 当前仍被 **MinIO 缺镜像**阻塞（既有 M14-23 阻塞点，与本次 LAN cutover 无关、未被本次操作改变）。
- `production_ready=false` 判定**不变**：本回合只收口 LiveKit LAN 拓扑与配套工具，不改变发布门禁状态。
- 远程设备接入 / 跨 NAT 场景仍属 **M10-05 TURN 后备**域，本次 LAN cutover 只覆盖同网段浏览器接入。
- 本回合（Claude 实现侧）零生产操作、零 secret 读取/输出；上述生产事实全部由 supervisor 执行并落证据。
