# tools/ops —— 生产恢复编排（M14-06）

本机 Windows 生产彩排栈（Docker Desktop + WSL 语音引擎）的自愈编排。容器面
兜底由 `infra/docker-compose.yml` 的 `restart: unless-stopped`（M14-06）承担；
本目录的 `production_recovery.py` 承担编排面：等引擎 → 校验 → 幂等 up →
健康核查 → 本地语音受控调和。

## production_recovery.py

```
# 仓库根执行（canonical venv 或任意 Python ≥3.11，纯标准库）
python tools/ops/production_recovery.py --dry-run     # 全程只读体检 + 决策预告
python tools/ops/production_recovery.py               # enforce（需 pin env 就绪）
```

流程与安全性质（细节见脚本头注释与
`services/api/tests/test_production_recovery.py` 契约测试）：

1. **等 Docker 引擎**：轮询 `docker version`（默认 300s，`--engine-wait-seconds`
   可调）——登录自愈场景给 Docker Desktop 留启动时间。
2. **compose 静态校验**：`docker compose config --quiet`（零容器改动）。
3. **pin check（fail-closed）**：`infra/env.production-recovery`（gitignored，
   模板 `infra/env.production-recovery.example`）必须存在、五键齐全
   （`AIOS_IMAGE_TAG/AIOS_APP_ENV/AIOS_WEB_PORT/AIOS_AUTH_SECRET/
   AIOS_LIVEKIT_API_SECRET`）、不含模板占位值（`<...>` 包裹或模板原文）；
   在线容器存在时**五键在线事实必须齐全且逐键相等**（缺事实 ≠ 跳过——
   inspect/port 探测不完整同样拒绝）。仅报键名，值绝不回显（子进程输出写
   日志前经防御性 redact）。任一不满足 → enforce 在 `up` 之前可见拒绝——
   防止恢复路径用默认值/漂移值/占位值静默重建容器（tag/端口/密钥轮换）。
4. **幂等 up**：`docker compose -f infra/docker-compose.yml -p
   aios-m14-03-production-rehearsal --profile local --env-file <pin>
   up -d --no-build`（绝不 `--build`；dry-run 模式附加 compose 原生
   `--dry-run`）。
5. **六服务健康等待**：`compose ps` 轮询至 postgres/redis/minio/api/web/
   livekit 全 healthy（默认 420s）。
6. **本地语音调和（status first）**：经 `tools/voice/voice_service_control.py`
   的只读 inspect 取状态后决策——
   - `unmanaged-running`/`managed-running` 且 `/health` 200 → **不触碰**；
   - `stopped` → 唯一放行动作：经受控 CLI `start --engine <name>`（工具
     自身幂等 + fail-closed 语义不变）；
   - `unknown` / `managed-mismatch`（foreign）/ `port-mismatch` / 既有
     `managed-starting` → **可见失败**：不 spawn、不发信号、不清理 manifest；
   - `unmanaged-running` 非 200 → 不触碰（拒绝误杀边界）+ DEGRADED 退出。

退出码：`0` 完成/无需恢复；`1` 可见失败（含 pin 拒绝、健康未达、语音 fail
状态、DEGRADED）；`2` 参数错误。运行日志落 `artifacts/recovery/`
（gitignored）；Windows 侧子进程恒 `CREATE_NO_WINDOW`。

## 部署 env（pin 事实源）

```powershell
# supervisor 一次性创建（值来自部署事实；文件已 gitignore，绝不提交/回显）
Copy-Item infra\env.production-recovery.example infra\env.production-recovery
# 编辑填入真实值后：
python tools/ops/production_recovery.py --dry-run   # 应见「pin: OK」并给出 up 计划
```

注意：首次 enforce `up -d` 会因新增 `restart: unless-stopped` **一次性重建
六个容器**（compose 对 restart 策略变更的正常反应；之后恢复为 no-op）。该步
骤由 supervisor 在获准窗口执行——编排脚本自身不会在 pin 未就绪时动任何容器。

## windows_startup_task.py（Round 2/3）

隐藏 logon 自愈任务管理器 + 静默执行入口。**install 需提升令牌**：生产实证
（2026-09-11）`schtasks /Create /TN AIOS-Production-Recovery /XML <tmp>` 仅在
elevated token 下成功（非提升被 UAC 拒绝）；在提升的管理终端、主仓库检出上
执行。

```
python tools/ops/windows_startup_task.py dry-run    # 只读预检 + 安装计划（零写操作）
python tools/ops/windows_startup_task.py status     # 只读状态（exit 0/1/2/3/4 = installed/unknown/missing/foreign/malformed）
python tools/ops/windows_startup_task.py install    # 真实安装（提升令牌 + supervisor 审查后；见下）
python tools/ops/windows_startup_task.py uninstall  # 仅删本工具精确拥有的任务
```

语义与安全性质（契约测试 `test_windows_startup_task.py` 锁定；R2.1 + R3 修正）：

- Task：`AIOS-Production-Recovery`；XML 关键项 Hidden=true / LogonTrigger
  (Enabled=true) / Settings/Enabled=true / InteractiveToken+LeastPrivilege
  （当前用户）/ IgnoreNew / StartWhenAvailable / ExecutionTimeLimit=PT2H /
  电池不禁启不停；Action=`wscript.exe //B //Nologo "<repo>\tools\ops\
  run_production_recovery_silent.vbs"`；WorkingDirectory=repo；写入
  `RegistrationInfo/URI=urn:aios:m14-06:production-recovery` +
  `Description=AIOS production recovery (M14-06) …`（持久归属标记）。**XML
  生成时 repo/VBS 路径与 Arguments 经 xml.sax.saxutils.escape 转义**（&、<、
  >、"）——含特殊字符的路径仍产出可解析 XML，解析后字段精确还原。
- **归属判定适配 Task Scheduler 归一化（R3，生产 + COM 快照实证）**：
  schtasks 注册后回读的 XML 与写入不同——URI 被重写为 `\AIOS-Production-
  Recovery`（该形态对**任何**同名任务都会出现，不具区分性）；默认值元素
  （LogonTrigger/Enabled、Settings/Enabled、Principal/RunLevel）被省略；
  调度器自行新增 UserId/IdleSettings 等；Action/Arguments/WorkingDirectory/
  Hidden 与非默认设置逐字保留。exact-owned 判定 = URI 为两种形态之一 **且**
  Description 持久标记精确相等 **且** Command/Arguments/WorkingDirectory 与
  全部安全相关设置逐项精确。省略的默认值元素仅在（a）父节点/触发器类型
  在场、（b）其余必检字段全部精确匹配时按 Windows 默认值认可；显式非默认
  值（Enabled=false / RunLevel=HighestAvailable）仍逐项拒绝。
- **wrapper 调用目标一致（无覆盖面）**：VBS 固定调用
  `<repo>\.venv\Scripts\python.exe`；本工具不提供 `--python` 覆盖——
  preflight/dry-run/install 恒检查 `_repo_paths(repo_root)["venv_python"]`，
  feature worktree 无 `.venv` 即 fail-closed（dry-run/install 均拒绝，
  外部 python 路径无法使其成功）。
- 绝不覆盖同名任务（install 前双重存在性确认，且 install 绝不 /F）；绝不
  Run 子命令。**uninstall 安全删除**：仅当上述 exact-owned 判定全部成立才
  执行 `/Delete /F`（附 /F 是因为 schtasks 无 /F 会交互式确认、capture 管道
  下挂起）；元素缺失即 mismatch（不短路，归一化省略面除外且条件严苛）；
  foreign/missing/malformed/unknown/查询失败/解码失败一律键名-only 拒绝且
  零删除——归一化 URI 单独**绝不**构成归属凭据。
- 存在性判定编码无关（schtasks 错误输出是 OEM 代码页，UTF-16 强解会丢输出
  ——2026-09-11 实证）：先全量列表 `/Query /FO CSV /NH`（任务名 ASCII 跨代码
  页稳定）判存在；存在才取 `/XML` 明细——**原始字节**（`Runner.run_raw`）
  + `decode_schtasks_xml` 按**字节形态**严格解码。/XML 输出编码随捕获通道
  而变（**不宣称单一编码**）：R3 在 PowerShell 管道观测为 UTF-16LE 无 BOM
  （text-mode `encoding='utf-16'` 在读线程抛 UnicodeError 丢输出）；R4 在
  生产机经本工具实际路径（Python `run_raw` 原始捕获）观测为 **ASCII/UTF-8**
  无 BOM（len 1446、`<?xml` 起始、`\n</Task>` 结尾；prolog 仍声明 UTF-16——
  声明与字节可不一致，不作判定依据）。解码器严格接受四形态（UTF-16LE 无
  BOM / LE BOM / BE BOM / UTF-8 `<?xml` 起始），无 BOM 形态要求 prolog 起始；
  四形态之外/解码失败/截断按 unknown fail-closed；XML 解析前拒绝
  DOCTYPE/ENTITY（XXE/实体膨胀加固）。
- pin env 仅存在性检查（install 必需；dry-run 缺失降级为提示——recovery
  自身 fail-closed 兜底），绝不读取/展示值。

静默 wrapper `run_production_recovery_silent.vbs`：仓库根自脚本位置推导（无
盘符硬编码）；`WScript.Shell.Run(..., 0, True)` 隐藏窗口运行
`<repo>\.venv\Scripts\python.exe <repo>\tools\ops\production_recovery.py` 并等
待，退出码经 `WScript.Quit` 透传（Task Scheduler「上次运行结果」可见）；不
弹窗/不开浏览器/不写 secret（日志复用 recovery 的 artifacts/recovery/）。
预检失败专用退出码：2=venv python 缺失、3=recovery 脚本缺失、4=仓库根缺失。

回滚：`python tools/ops/windows_startup_task.py uninstall`（幂等；仅
exact-owned 才 `/Delete /F`，其余状态零删除、无级联）。

## 状态（Round 2 + R2.1 + R3，2026-09-11）

- Round 1 已交付：compose restart 策略、恢复编排 + 契约测试、env 模板/护栏
  （R1.1 修正五键 fail-closed 与占位拒绝）。
- Round 2 已交付：任务管理器 + 静默 wrapper + 契约测试。
- R2.1 修正（supervisor 评审 5 缺陷）：① wrapper/preflight 调用目标一致——
  移除 `--python` 覆盖，恒查 repo 自带 `.venv`；② Task XML 路径/Arguments
  转义；③ WorkingDirectory 缺失即 mismatch；④ 归属校验逐项精确；⑤
  uninstall 仅 exact-owned 才 `/Delete /F`。
- **R3 修正（真实安装回环实证，2026-09-11）**：supervisor 在 canonical 主
  仓库以提升令牌 install 成功（任务 `AIOS-Production-Recovery` 已注册），
  暴露三处真实行为——① `schtasks /Create` 需 elevated token（UAC）；②
  `/Query /XML` 输出编码随捕获通道而变（PowerShell 管道观测 UTF-16LE 无
  BOM，原 text-mode 读取炸读线程）；③ Task Scheduler 归一化存储（URI 重写/
  默认值元素省略/额外元素注入）。
  修复：`Runner.run_raw` 字节面 capture + `decode_schtasks_xml` 鲁棒解码；
  归属判定改为「URI 两形态 + Description 持久标记 + 全字段精确（条件认可
  归一化省略的默认值）」。
- **R4 修正（生产验收 blocker，2026-09-11）**：canonical 5bd3db7 上
  `status` 仍 unknown——生产机经本工具实际路径（`RealRunner.run_raw`
  Python 原始捕获）回读的 /XML 字节为 **ASCII/UTF-8**（len 1446、前缀
  `<?xml version="1`、后缀 `\n</Task>`），与 R3 在 PowerShell 管道观测的
  UTF-16LE 无 BOM 是**两种并存形态**。`decode_schtasks_xml` 改为按字节
  形态严格判定：接受 UTF-16LE 无 BOM / LE BOM / BE BOM / UTF-8 `<?xml`
  起始四形态，拒绝垃圾/截断/未观测形态（fail-closed 不变）。测试 66 项
  （FakeSchtasks，零真实 schtasks 写路径）。
- 边界不变：foreign/malformed/unknown 永不 force、永不删除；本目录工具
  绝不触碰 Docker/8010/8011。
