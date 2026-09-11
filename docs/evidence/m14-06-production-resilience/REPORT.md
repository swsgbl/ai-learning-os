# M14-06 Round 1 证据报告：生产恢复编排（compose restart 策略 + 恢复脚本 + env 护栏）

> **Round 2.1 修正（supervisor 评审 5 缺陷，2026-09-11）**：startup task 管理器
> 五项必修缺陷全部修复——①移除 `--python` 覆盖、preflight/dry-run/install 恒查
> repo 自带 `.venv`（worktree 无 .venv → dry-run 真实复验 **exit 1 fail-closed**，
> 外部 python 无法放行）；②Task XML 路径/Arguments 转义（&、<、>、" 含单测）；
> ③WorkingDirectory 缺失即 mismatch；④归属校验补齐 8 项逐项精确匹配（缺失/
> 漂移按字段名报告）；⑤uninstall 仅 URI+全部归属关键字段 exact-owned 才
> `/Delete /F`（其余状态零删除）。测试扩至 46 项（FakeSchtasks，零真实触碰）。
> 详见「Round 2.1 修正记录」。
>
> **Round 2（2026-09-11 09:30）**：新增隐藏 logon 自愈任务管理器
> `tools/ops/windows_startup_task.py`（dry-run/install/status/uninstall）与静默
> wrapper `run_production_recovery_silent.vbs`。**开发-only：未注册/未删除/
> 未运行任何真实 schtasks 任务**（status=missing，exit 2；全量列表过滤复核
> 零注册）；仅执行只读 /Query 与文件存在性检查（pin env 只查存在，未读值）。
> 真实编码实证：schtasks 错误输出为 OEM 代码页（GBK），UTF-16 强解丢输出 →
> 改为「全量列表 ASCII 任务名判存在 + 存在才 UTF-16 读 /XML」的编码无关查询。
> 测试 +28（全部 FakeSchtasks 注入，零真实 schtasks 写路径）；全套件
> **179 passed, 1 skipped**；ruff/py_compile/git diff --check 通过。详见
> 「Round 2 记录」。交接：supervisor 审查后在**主仓库检出**上执行
> `python tools/ops/windows_startup_task.py install`（wrapper 的 `<repo>\.venv`
> 须与任务工作目录一致）；回滚 = `uninstall`（幂等、仅删精确匹配任务）。
>
> **Round 1.1 修正（supervisor 评审，2026-09-11 03:10）**：`check_pins()` 修复
> fail-open 缺陷——在线容器存在但部分 PIN_KEY 事实缺失时曾被静默跳过（`ok`
> 可为 True）；现「缺事实」与「值不等」同权重拒绝（键名-only 报告），并新增
> 模板占位 secret（`<...>` 包裹/模板原文）恒拒绝（含栈未起、无在线容器可比
> 对的原 fail-open 关口）。回归测试 +8（部分在线事实×5、占位×3）；全套件
> **150 passed, 2 skipped**；ruff/py_compile/compose config 复验通过；真实
> 只读 dry-run（占位模板副本 `05-*.log`）与漂移复验（`06-*.txt`）均可见拒绝、
> exit 1、零 up。六容器 ID 与基线逐一相同、8010/8011 未触碰（仍为外部故障
> 停机态）。详见下文「Round 1.1 修正记录」。

- 分支/工作树：`feature/m14-06-production-resilience` @ `D:\AI Learning OS\ai-learning-os-worktrees\m14-06-production-resilience`（基线 6319eb0，M14-04）
- 日期：2026-09-11（R1 01:25–03:05；R1.1 03:06–03:10；R2 09:00–09:40；R2.1 10:50 起 GMT+8）
- 原始日志：`.verify/m14-06-production-resilience/`（gitignored，本机留存）；编排运行日志另见 `artifacts/recovery/`
- 边界遵守：**未停止/重启/重置任何容器、Docker Desktop、8010/8011、模拟器或代理**；未注册计划任务；无弹窗；无 secret 入库/入日志。

## 变更文件

| 文件 | 变更 |
|---|---|
| `infra/docker-compose.yml` | 六个长驻服务（postgres/redis/minio/api/livekit/web）新增 `restart: unless-stopped` + 注释 |
| `infra/env.production-recovery.example` | 新增：部署 pin env 模板（五必需键 + 纪律说明；占位值非真实 secret） |
| `.gitignore` | 新增 `infra/env.production-recovery`（真实 env 永不入库） |
| `tools/ops/production_recovery.py` | 新增：恢复编排（等引擎→校验→pin check→幂等 up -d --no-build→六服务健康→语音受控调和）；纯标准库；Runner/VoiceGateway 注入；secret 防御性 redact；Windows `CREATE_NO_WINDOW` |
| `tools/ops/README.md` | 新增：用法/安全性质/监督者交接 |
| `services/api/tests/test_production_recovery.py` | 新增：44 项契约测试（决策矩阵/状态空间交叉契约/pin 拒绝与不泄漏/compose 命令纪律/端到端全 fake） |
| `services/api/tests/test_compose_restart_policy.py` | 新增：restart 策略渲染（四 profile）+ PyYAML 静态回退 + env 模板/gitignore/索引护栏 |
| 本报告 | `docs/evidence/m14-06-production-resilience/REPORT.md` |

## 验证命令与结果（canonical venv：`D:\AI Learning OS\ai-learning-os\.venv`）

1. **基线快照（只读）**：`docker inspect` 六容器 id/started/restart/status/health
   → 全部 `running/healthy`、`restart=no`（运行中容器尚未获得新策略——预期，
   应用属 supervisor 后续 enforce 步骤）。证据 `00-baseline-restart-policy.txt`。
2. **compose 校验（只读）**：`docker compose config --quiet` × {无 profile,
   local, hybrid, cloud} → 4×PASS；`--profile local config --format json` 渲染
   → 六服务 `restart='unless-stopped'`。证据 `01-compose-config-validation.txt`。
3. **聚焦测试**：
   `python -m pytest services/api/tests/test_production_recovery.py services/api/tests/test_compose_restart_policy.py -q`
   → **43 passed, 1 skipped**（skip = 真实 env 文件尚未创建的磁盘护栏，按设计跳过）。
4. **回归（相邻套件）**：`test_voice_service_control.py + test_voice_local_scripts.py
   + test_compose_profiles.py` 连同新套件 → **142 passed, 2 skipped**，零回归。
5. **静态门禁**：`ruff check`（编排 + 两个测试文件）→ All checks passed；
   `py_compile` → OK。
6. **恢复 dry-run（真实栈，全程只读）**：
   - A（env 缺失）：`python tools/ops/production_recovery.py --dry-run`
     → 引擎就绪、config OK、六服务 healthy 快照、pin 缺失→「enforce 将拒绝」、
     exit **1**（镜像 enforce 拒绝）。证据 `02-recovery-dry-run-noenv.log`。
   - B（漂移 env，伪值）：`--dry-run --env-file .verify/.../drift.env`
     → 「一致键 2/5；不一致键: AIOS_IMAGE_TAG, AIOS_AUTH_SECRET,
     AIOS_LIVEKIT_API_SECRET（值不回显）」+ 拒绝 up，exit **1**；
     日志全程无 secret 值。证据 `03-recovery-dry-run-drift.log`。
7. **事后核查（只读）**：六容器 id/started-at 与基线**逐一相同**（零重建零触碰）；
   `docker compose up --dry-run` 旗标可用（supervisor 绿灯路径）。证据
   `04-post-verification.txt`。

## 发现（如实报告）

**语音引擎 8010/8011 在本会话中途自行退出**：01:30 基线两者 `/health` HTTP 200；
03:00 复查双双无监听（HTTP 000、netstat 无记录）。期间本会话全部操作为只读
（docker inspect / compose config 渲染 / 基于 fake 的 pytest / 文件编辑），
无任何停止动作——退出属外部因素（疑似 WSL VM 回收/宿主休眠），**恰为 M14-06
要自愈的故障形态**：dry-run 如实检出 `stopped → start`（受控，未执行——
Round 1 边界禁触碰 8010/8011）。Docker 六容器不受影响，全程 healthy。

## Round 1.1 修正记录（fail-open 缺陷）

**缺陷（supervisor 评审发现）**：`check_pins()` 中 `collect_live_pins()` 在 api
容器存在但只回出部分 PIN_KEYS 事实时（如 `docker port` 空输出、镜像 inspect
失败、容器 env 缺行），缺失键既不计 mismatch 也不计 matched——若其余键一致，
`report.ok=True`，违反「五键在线一致性」承诺（fail-open）。另一关口：栈未起
（无在线容器）路径从不校验值本身——照抄模板的占位 secret 可通过并用于 `up`
创建容器。

**修复（`tools/ops/production_recovery.py`）**：
- 新增 `missing_live_keys` 检测与报告：在线容器存在时任一 PIN_KEY 事实缺失 →
  `ok=False`（「inspect/port 探测不完整时不得按跳过放行」）；`PinReport` 增
  `missing_live_keys`/`placeholder_keys` 字段，与 `mismatched_keys` 分类分离。
- 新增 `placeholder_pin_keys()`：`<...>` 包裹值或模板原文
  （`TEMPLATE_PLACEHOLDER_VALUES`）→ 键名-only 拒绝；**在两条路径（有/无在线
  容器）均生效**——占位 secret 在任何情况下不得进入 compose up。
- ok 语义：`env 五键齐全 ∧ 无占位 ∧ (无在线容器 ∨ (在线五键齐全 ∧ 逐键相等))`。
- dry-run 退出语义与 enforce 对齐不变：pin 未就绪 → dry-run 记
  `pin-not-ready` 失败并 exit 1（镜像 enforce 拒绝）。

**回归测试（`test_production_recovery.py`，+8）**：部分在线事实（缺 web 端口
/ 缺镜像事实 / 缺 secret env 行 / 多键同缺 + 分类断言）×4 + 端到端 enforce
零 up ×1；占位（纯函数 / 在线栈存在照抄模板 / 栈未起照抄模板→原 fail-open
关口）×3。全部断言键名-only（占位文本与 secret 值均不回显）。

**复验（2026-09-11 03:06–03:10）**：
- 缺陷复现脚本（修复前 `ok=True` → 修复后 `ok=False`,
  `missing_live_keys=('AIOS_IMAGE_TAG','AIOS_WEB_PORT')`）。
- `pytest` 五套件（新 2 + 相邻 3）→ **150 passed, 2 skipped**（逐文件
  43/8+1s/64/30/5+1s）。
- `ruff check` / `py_compile` → OK；`docker compose config --quiet` ×4 → PASS。
- 真实只读 dry-run：占位模板副本（`05-recovery-dry-run-placeholder.log`）→
  占位键 + 不一致键双拒绝、零 up、exit 1；漂移复验 exit 1（`06-*.txt`）。
- 事后核查：六容器 ID 与基线逐一相同（零重建零触碰）；8010/8011 只读探测
  未触碰（仍为外部故障停机态）。

## Round 2 记录（Windows 登录自愈任务管理器，开发-only）

**变更文件**：`tools/ops/windows_startup_task.py`（新增，~430 行）、
`tools/ops/run_production_recovery_silent.vbs`（新增）、
`tools/ops/production_recovery.py`（Runner.run 增可选 encoding 参数——
schtasks /XML 管道输出 UTF-16 所需，向后兼容）、
`services/api/tests/test_windows_startup_task.py`（新增 28 测试）、README、本报告。

**操作语义**：
- Task `AIOS-Production-Recovery`；XML 关键项全部由契约测试逐项锁定（Hidden/
  LogonTrigger/InteractiveToken+LeastPrivilege/IgnoreNew/StartWhenAvailable/
  PT2H/电池不禁启停/wscript `//B //Nologo` + 仓库内 wrapper/cwd=repo/URI 标识）。
- install：预检（repo/venv python/recovery 脚本/VBS/pin env **存在性**）→ 双重
  存在性确认 missing → 临时 UTF-16 XML → `schtasks /Create /TN <task> /XML`
  （绝不 /F）→ 回读复查逐项匹配；任何一步不符 → 键名-only 可见拒绝。
- status：installed/missing/foreign/malformed/unknown 五态（exit 0/2/3/4/1）。
- uninstall：仅 STATE_INSTALLED 精确归属（exact-owned）才执行 `/Delete /F`；
  missing 幂等 exit 0；foreign/malformed/unknown 永不 force、永不 delete；
  工具从不调用 /Run 子命令（R2.1 修正语义，源码契约锁定）。
- 编码无关存在性判定：真实运行实证 schtasks 错误输出为 OEM 代码页（zh-CN=
  GBK），UTF-16 强解在读线程抛 UnicodeError 且输出丢失（首轮真实 dry-run 的
  unknown 误判根因）——改为全量列表 `/Query /FO CSV /NH`（ASCII 任务名跨代码
  页稳定）判存在，存在才以 UTF-16 读 `/XML`；列表失败 → unknown fail-closed。
- XML 解析加固：解析前拒绝 DOCTYPE/ENTITY（XXE/实体膨胀面），命中即 malformed。

**真实只读运行（`07-startup-task-readonly-runs.log`）**：dry-run（--python 指
向主仓库 canonical venv；worktree 无 .venv）→ 五项预检通过、任务 missing、
输出完整安装计划与回滚命令、**exit 0、零写操作**；status → missing（未注册，
符合 Round 2 边界）、exit 2；全量列表过滤复核本工具任务名出现 0 次。
（R2.1 注：该 exit 0 依赖 `--python` 外部覆盖——正是 supervisor 缺陷 1 所在；
覆盖面已废除，修正后 worktree dry-run 必须 exit 1，见「Round 2.1 修正记录」。）

**测试与门禁**：新套件 28 passed（FakeSchtasks 注入，零真实 schtasks 写路径、
零 Docker/WSL/8010/8011）；六套件合计 **179 passed, 1 skipped**（R1 的
env-on-disk 护栏测试在 supervisor 创建 pin env 后激活并通过——gitignore 覆盖
实证）；ruff / py_compile / git diff --check 全过。

**生产栈未触碰**：本轮零容器改动；只读复核时观察到六容器为 supervisor 轮间
enforce 后的新实例（Up ~25min，全 healthy；8010/8011 HTTP 200 已恢复）——非
本会话所为，特此如实记录。

## Round 2.1 修正记录（supervisor 评审 5 必修缺陷，2026-09-11）

**缺陷与修复（`tools/ops/windows_startup_task.py`）**：

1. **wrapper 调用目标与 preflight 一致（fail-closed）**：移除 `--python`
   CLI 覆盖（parser 不再接受该参数；`cmd_dry_run`/`cmd_install`/`preflight`
   签名均无 python_path）；preflight 恒检查
   `_repo_paths(repo_root)["venv_python"]`（= VBS wrapper 实际调用的
   `<repo>\.venv\Scripts\python.exe`）。feature worktree 无 `.venv` →
   dry-run/install 均拒绝——外部 python 路径无法使其成功。VBS 本就固定
   推导该路径，无需改动。
2. **Task XML 转义**：`build_task_xml` 的 Arguments（内含 VBS 路径）与
   WorkingDirectory（repo 路径）经 `xml.sax.saxutils.escape`（&、<、>、"）
   转义；单测构造含全部四种字符的 repo 路径，断言 XML 可解析（ElementTree）、
   解析后字段与原始路径精确相等、verify round-trip 仍 installed。
3. **WorkingDirectory 缺失即 mismatch**：`verify_task_xml` 不再
   `if working_dir and ...` 短路——元素缺失（None）直接计
   `Actions/Exec/WorkingDirectory` mismatch。
4. **归属校验补齐并逐项精确**：URI/Command 归属门之后共 13 项字段精确
   匹配——Arguments、WorkingDirectory、LogonTrigger 节点存在、
   LogonTrigger/Enabled=true、Settings/Enabled=true、Settings/Hidden=true、
   MultipleInstancesPolicy=IgnoreNew、StartWhenAvailable=true、
   DisallowStartIfOnBatteries=false、StopIfGoingOnBatteries=false、
   ExecutionTimeLimit=PT2H、Principal/LogonType=InteractiveToken、
   Principal/RunLevel=LeastPrivilege；缺失或漂移均按字段名报告，
   fail-closed。
5. **uninstall 安全删除**：`/Delete` 仅在 STATE_INSTALLED（URI+全部归属
   关键字段 exact-owned）分支执行，且附带 `/F`——schtasks 无 `/F` 会交互
   式确认，capture 管道无 stdin 时挂起（本轮修正的直接动因）；
   foreign/missing/malformed/unknown/全量列表查询失败一律可见拒绝、零
   `/Delete`、绝不 force。源码契约测试锁定：`"/F"` 全文恰好一次且仅在
   /Delete argv；/Create 永不带 /F；全文无 "/Run"。

**测试（`test_windows_startup_task.py`，28 → 46 项）**：新增——XML 特殊
字符转义（含 round-trip）；8 项归属字段漂移参数化；元素缺失参数化
（WorkingDirectory/Arguments/MultipleInstancesPolicy/RunLevel，缺失=
mismatch）；`--python` 拒绝 + 命令入口签名契约；repo 有 `.venv` dry-run
OK / 无 `.venv` fail-closed（外部 python 同时存在也不放行）/ 无 `.venv`
install 拒绝；uninstall exact-owned 的 `/F` argv 精确断言；全量列表查询
失败零删除；RunLevel 漂移零删除。既有测试同步迁移到无 python_path 签名。
全部经 FakeSchtasks 注入——零真实 schtasks 写路径、零任务注册/删除。

**真实只读复验（本 worktree 执行；未 install/uninstall 任何任务）**：

- `python tools/ops/windows_startup_task.py dry-run`
  （`08-startup-task-r21-dryrun-failclosed.log`）→「预检 venv Python
  （wrapper 实际调用目标，不可覆盖）: 缺失」+ install 计划输出但结论为
  拒绝，**exit 1 fail-closed**。（R2 同机位曾因 `--python` 指向主仓库
  venv 而 exit 0——即缺陷 1 的真实复现面，已关闭。）
- 同命令附 `--python <主仓库 venv>` → parser 拒绝（unrecognized
  arguments），exit 2——覆盖面已不存在。
- `python tools/ops/windows_startup_task.py status`
  （`09-startup-task-r21-status-readonly.log`）→ missing（未注册，符合
  Round 2 边界）、exit 2，只读退出。

**门禁（R2.1）**：聚焦套件 46 passed；ruff（services/api/app +
services/api/tests + tools/ops）All checks passed；py_compile（3 个变更
Python 文件）OK；`git diff --check` 干净。全量复跑（`10-full-pytest-r21.log`，
隔离 basetemp 置于仓库外临时目录）：`python -m pytest --basetemp=<仓库外
tmp> -q` → **1907 passed, 31 skipped，exit 0**（零回归）。如实录：首次误以
仓库内 `--basetemp` 运行曾报 34 个「路径护栏」类失败——tmp_path 落入 repo
改变了「repo 外路径」语义所致的 basetemp 伪影；两例抽检 + 上述全量复跑
（仓库外 basetemp）均通过，非代码回归。

## Supervisor 交接（后续轮）

1. 创建 pin env：`Copy-Item infra\env.production-recovery.example
   infra\env.production-recovery` 并填入部署真实值（AIOS_IMAGE_TAG=
   m14-03-prod-rehearsal、AIOS_APP_ENV=production、AIOS_WEB_PORT=3011、两个
   secret 取自部署事实）。
2. 绿灯预检：`python tools/ops/production_recovery.py --dry-run` → 应见
   `pin: OK` + `up --dry-run` 计划（首跑会标注六容器重建一次以获得
   restart 策略）。
3. 获准窗口执行 enforce `python tools/ops/production_recovery.py`（一次性重建
   六容器 + 自动受控拉起两语音引擎——engine start 首次含依赖检查，模型缓存
   复用不重下，见 M14-02/03 证据）。
4. Round 2：隐藏登录自启任务安装器（Task Scheduler + 静默 wrapper +
   dry-run/uninstall）——本轮按边界未注册任何任务。
