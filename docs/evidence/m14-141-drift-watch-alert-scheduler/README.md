# M14-141：production drift watch 告警周期任务 readiness（工具切片）

- 切片：分支 `ops/m14-141-drift-watch-alert-scheduler`（独立 worktree
  `m14-141-drift-watch-alert-scheduler`），基于 supervisor 本地 sync-base
  `c2f0a27e5e84235e748b2c470c41fbe3aa7b73ca`（任务书给定——其 tree 与
  merged remote main `220acd5485b01398b0b0cd0eb86b26e6d4a9ad77` 完全一致
  （M14-140 合入后基座），本切片在其上恰好一笔任务 commit），单次
  local commit（不推送、不建 PR；supervisor 审查与 remote 发布在其后
  进行）。
- 目标：闭合 M14-137 留下的「零调度集成——drift=true 自动触发分发仍
  缺位」缺口的**readiness 部分**：新增 M14-141 周期计划任务管理器
  `tools/ops/production_drift_watch_alert_scheduler.py` + 静默 wrapper
  `tools/ops/run_production_drift_watch_alert_silent.vbs` + 聚焦契约测试，
  **不改任何既有工具/任务语义**（M14-127 watcher / M14-129 scheduler /
  M14-135 dispatcher / M14-137 任务桥 / 各 VBS / 既有计划任务零触碰——
  M14-141 白名单对 watcher 任务名的任何 schtasks 形态一律拒绝）。
- **边界性质（置顶）：本切片零 schtasks 执行、零注册、零自然调度、零
  生产执行、零真实 webhook、零 secret 值读取、零 Docker/DB/MinIO/语音/
  设备接触。实际注册 supervisor-only；`production_ready=false` 恒不变。**

## 1. 交付物（3 新文件 + .gitignore 一条目 + 文档）

- `tools/ops/production_drift_watch_alert_scheduler.py`：子命令
  `plan` / `generate` / `status` / `install` / `uninstall`（M14-129 已
  验证模式同源；install/uninstall 需精确确认短语
  `EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE`——与
  M14-127/129/135/137 四短语互不通用，五短语交叉 pin）。
- `tools/ops/run_production_drift_watch_alert_silent.vbs`：静默 wrapper，
  只调用仓库内 M14-137 任务桥 execute 形态
  （`--secret-file <固定路径> --execute --confirm "EXECUTE PRODUCTION
  DRIFT WATCH ALERT TASK"`——M14-137 自有短语，经模块常量交叉 pin）；
  预检缺失专用退出码 2/3/4/5；零文件读写面、零网络、零盘符硬编码。
- `services/api/tests/test_production_drift_watch_alert_scheduler.py`
  （106 项契约测试，见 §2）。
- `.gitignore` 追加 `infra/env.production-drift-watch-alert-secret.json`
  （操作者 webhook secret JSON 固定路径防护——scheduler/wrapper 仅
  存在性检查，绝不读值）。

## 2. 关键设计（fail-closed）

- **独立任务身份**：`AIOS-Production-Drift-Watch-Alert` /
  `urn:aios:m14-141:production-drift-watch-alert` + Description 持久
  归属标记；与 M14-129 watcher / M14-06 / M14-14 / M14-77 四任务零
  身份冲突（契约测试逐一 pin，含 wrapper 文件名）；XML 只指向自己的
  wrapper。
- **非重叠 PT15M 周期**：TimeTrigger Interval PT15M（无 Duration=
  无限期）+ 固定 StartBoundary `2026-01-01T00:05:00`——与 watcher
  （`2026-01-01T00:00:00`）恒差恰 300s（契约测试从两模块常量计算断言）：
  watcher 槽位 :00/:15/:30/:45 先跑并产出报告（M14-127 原子写），告警桥
  在 :05/:20/:35/:50 对已完整落盘的最新报告做分发判定。病态全超时情形
  （watcher 单轮子进程硬顶 330s > 300s 偏移）下告警桥评估上一份**完整**
  报告——M14-137「最新合法 execute 报告」fail-closed 选择语义不受影响
  （绝不读半写文件）。
- **预算交叉 pin**：间隔 PT15M=900s > 执行时限 PT10M=600s > M14-137
  唯一 dispatch 子进程预算 CHILD_TIMEOUT_SECONDS=300s（常量经模块加载
  单一事实源交叉取值）——调度器绝不先于内部超时杀整任务，恒留 ≥300s
  收尾余量；IgnoreNew 防同任务自重叠（跨任务不重叠靠固定偏移）。
- **StartWhenAvailable=false（R1 supervisor 合并前修正）**：固定过去
  StartBoundary + StartWhenAvailable=true 会在注册后立即产生**不可控
  补跑**——告警任务恒 false：错失槽位绝不补跑，下一个固定 PT15M 节点
  运行。false 恰为 Windows 默认值，按 M14-06 实证的「值恰为默认值的
  元素注册后省略」规则，严格校验把该字段移入 defaultable 条件认可集
  （省略仅在其余字段全部精确时认可；显式 true 恒 malformed，专项测试
  `test_start_when_available_false_no_catchup_r1` 锁定写入面/归一化
  省略容错/补跑语义回归三面）。
- **secret 纪律（零值接触）**：操作者 webhook secret JSON 固定仓库相对
  路径 `infra/env.production-drift-watch-alert-secret.json`（gitignored；
  M14-06 `infra/env.production-recovery` 同款「固定路径 + 仅存在性检查」
  先例）。scheduler preflight 与 wrapper 都只查存在性（is_file 且非
  symlink），绝不读取/回显/落盘内容（内容校验全部由 M14-135 承担）；
  XML/wrapper/日志只允许出现该路径本身——sentinel 注入契约测试断言
  secret 值绝不进入任何输出面（plan 日志、generate 工件、wrapper 文本），
  源码契约锁定凡提及 secret 的行恒无读取面。
- **wrapper 内容校验（fail-closed）**：`verify_wrapper_content` 对仓库
  内 wrapper 逐项校验结构性标记（仓库根推导/隐藏窗口 `Run(...,0,True)`/
  退出码透传/固定 venv python/M14-137 任务桥目标/--secret-file/固定
  secret 路径两段常量/--execute/M14-137 自有确认短语——模块常量交叉
  pin），并拒绝盘符硬编码、网络/解释器/文件读写面（CreateTextFile/
  OpenTextFile/ADODB.Stream）token 与 secret 形态值；plan/generate/
  install 恒跑，不过即 fail-fast（零调度器调用 / generate 零写入）。
- **GatedSchtasks 白名单 + fail-fast 预检**：只读两形态（单任务明细仅限
  本任务名）；mutation 仅精确 create（绝不 /F）/delete（仅 exact-owned）
  两形态；watcher/兄弟任务名的任何形态在任何执行之前拒绝；install 前
  只读 query + 二次列表复核双确认 missing；plan/generate/install 预检
  （文件身份含 symlink 拒绝 + wrapper 内容 + secret 存在性）不过即零
  schtasks 调用 / 零写入。
- **generate**：UTF-16 with BOM 字节（与 XML 声明及 install 临时字节
  逐字节一致，外部解析器可直接加载）、原子写（tmp+fsync+os.replace）、
  回读磁盘真实字节经字节形态严格解码 + 归属校验复核、输出路径/祖先
  symlink 拒绝零写入；默认输出 gitignored
  `.verify/artifacts/m14-141-drift-watch-alert-scheduler/scheduled-task.xml`。

## 3. 契约测试（106 项，0.5s 量级，FakeSchtasks/合成 fixtures）

覆盖任务书要求的全部分面：精确 XML（身份/设置/Action 三件套/无
Duration/XML 恒无 secret 路径与值）+ 非重叠与预算交叉 pin（常量单一
事实源）+ _esc/特殊字符路径 round-trip；verify 四态（归一化条件认可/
漂移逐项报字段名/DOCTYPE-ENTITY 拒绝/garbage/foreign）；decode 四形态
+ 拒绝面；白名单门（读/建/删精确形态放行；/Run//Change//End/带 /F
create/兄弟与 watcher 任务名/值位置旗标形态逐项拒绝；POSIX tempfile
路径值位置回归）；plan（fail-fast 三预检失败形态零调度器调用；happy
path 恰一次列表查询；存在/unknown 拒绝）；generate（预检失败零写入
（目录不创建）/UTF-16 BOM 逐字节断言/BOM 感知解码语义/原子无 .tmp
残留/输出与祖先 symlink 零写入）；status 五态退出码 + 不可解码 XML
unknown；install/uninstall 短语门禁（四既有短语冒充零调用）/happy
path（临时 XML UTF-16 内容精确、用后即删、create 无 /F）/拒绝同名/
二次复核翻转/幂等卸载/exact-owned 才删；VBS wrapper 契约（无盘符、
隐藏窗口、透传、只调用 M14-137 不直调 M14-135、--secret-file、五
预检退出码、零读写/网络/解释器面）；verify_wrapper_content 参数化
八种篡改（含 M14-135 短语冒充、删 secret 路径、盘符、OpenTextFile、
https、secret sentinel）逐项拒绝且 plan 零调度器调用；symlink 身份
拒绝；secret sentinel 非披露（plan 日志/generate 工件/wrapper）+
源码零读取面；身份不冲突（四兄弟任务 + wrapper 文件名 + XML 无兄弟
标识）；源码契约（mutation token 门禁、subprocess 无 shell=、零
env/docker/网络面）；parser 契约；R1 专项：StartWhenAvailable 恒
false（写入面字面 pin + 固定过去边界）、注册后归一化省略（默认值）
条件认可、显式 true（补跑语义回归）恒 malformed。

## 4. 验证（canonical venv，Python 3.11.15）

- 新测试：`106 passed in 0.36s`（§5 命令 1；R1 修正后复跑）。
- 邻居回归（六套合跑，同 venv）：M14-129 契约
  `test_production_drift_watch_task.py`（82）+ M14-135 契约
  `test_production_drift_watch_alert_dispatch.py`（127）+ M14-136
  运行时 `test_production_drift_watch_alert_dispatch_runtime.py`（4）
  + M14-137 契约 `test_production_drift_watch_alert_task.py`（29）+
  M14-140 运行时 `test_production_drift_watch_alert_task_runtime.py`
  （3）+ 本切片新套件（106）→ **351 passed in 6.17s**（R1 修正后
  复跑）。
- ruff（默认 + `--select F,E9`）All checks passed（工具 + 测试；
  开发期修正：测试内 `_boundary` 补 timezone aware 消除 DTZ007）/
  `py_compile` 通过 / `git diff --check` 干净 / 新增行秘密与本地
  路径扫描 0 命中（唯一 secret 形态 token 为测试源码内合成 sentinel
  `sk-ZXm141sentinel012345`，仅存在于测试源码与 pytest 临时目录，
  断言其绝不进入任何输出面）/ markdown 结构 sanity（fence/标题层级）
  通过。
- 开发期实现者只运行过：`plan` 冒烟一次（见 §6 诚实披露）与上述
  pytest/ruff/py_compile 命令；未执行 status/install/uninstall 真实
  调用，未读取任何 secret 文件（仓库内该固定路径文件本就不存在）。

## 5. 验证命令清单（supervisor 可复跑）

```bash
cd <worktree>
<venv>/Scripts/python.exe -m pytest services/api/tests/test_production_drift_watch_alert_scheduler.py -v
<venv>/Scripts/python.exe -m pytest \
  services/api/tests/test_production_drift_watch_task.py \
  services/api/tests/test_production_drift_watch_alert_dispatch.py \
  services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py \
  services/api/tests/test_production_drift_watch_alert_task.py \
  services/api/tests/test_production_drift_watch_alert_task_runtime.py \
  services/api/tests/test_production_drift_watch_alert_scheduler.py -q
<venv>/Scripts/python.exe -m ruff check tools/ops/production_drift_watch_alert_scheduler.py services/api/tests/test_production_drift_watch_alert_scheduler.py
<venv>/Scripts/python.exe -m ruff check --select F,E9 tools/ops/production_drift_watch_alert_scheduler.py services/api/tests/test_production_drift_watch_alert_scheduler.py
<venv>/Scripts/python.exe -m py_compile tools/ops/production_drift_watch_alert_scheduler.py services/api/tests/test_production_drift_watch_alert_scheduler.py
git diff --check
```

## 6. 诚实边界与开发期披露

- **R1 supervisor 合并前修正（2026-09-25）**：初版 XML 写
  `StartWhenAvailable=true`（沿用 M14-129 watcher 先例）。supervisor
  审查指出：本任务用**固定过去 StartBoundary**（2026-01-01T00:05:00，
  注册即生效），叠加 StartWhenAvailable=true 会让 Task Scheduler 在
  注册后立即补跑（错失的调度起点「尽快启动」）——对告警任务是不可控
  的即时触发。修正为 **false**（错失槽位绝不补跑，下一个固定 PT15M
  节点运行）：XML 写入面、严格校验字段集（移入 defaultable 条件认可
  集——false 恰为 Windows 默认值，注册后归一化省略仅在其余字段全部
  精确时认可，显式 true 恒 malformed）、plan 输出措辞、专项测试
  （`test_start_when_available_false_no_catchup_r1`）、证据/四台账
  全量同步；全部验证组合（新 106 + 邻居 351 + ruff 双跑 +
  py_compile + markdown sanity + `git diff --check` + 新增行扫描）
  修正后复跑通过。watcher（M14-129）自身的 StartWhenAvailable=true
  属其既有已安装任务语义，本切片零触碰。
- **本切片零调度器写路径**：全部 schtasks 交互仅存在于注入
  FakeSchtasks 的契约测试；install/uninstall 短语门禁（五短语交叉 pin）
  + supervisor-only 边界不变。
- **开发期一次越界与修正（如实披露）**：实现者在修完 preflight 真值
  bug 前的首次冒烟中误以默认 RealRunner 运行了 `plan` 一次——执行了
  **一次真实只读 `schtasks /Query /FO CSV /NH` 全量列表查询**（该次
  输出显示本任务名不在列，故未发生 /XML 明细查询；零写路径、零
  install/delete/Run、零任务改动）。此行为超出「本回合零真实 schtasks
  query」边界；随后立即改为 FakeRunner 冒烟并把 plan 收紧为 fail-fast
  （预检不过零调度器调用——本次冒烟恰好暴露的原缺陷即 preflight 失败
  未生效，已修复并由契约测试锁定）。除此一次外零真实调度器触达。
- **preflight 真值缺陷（开发期发现并修复）**：初版 `cmd_plan` 等以
  `if not preflight(...)` 判定 dataclass 实例（恒真）——预检失败不
  生效（冒烟中 venv/secret 缺失仍 exit 0 并继续输出了计划）。已改为
  `.ok` 显式判定 + fail-fast，三种预检失败形态（venv/secret/wrapper
  篡改）的零调度器调用路径由契约测试锁定。
- **未实证面**：任务 XML 的注册后归一化（M14-131 已为 M14-129
  TimeTrigger 同款形态提供真实安装先例，但本任务自身未注册过）；
  自然调度轮、drift=true 真实告警触发、真实 HTTPS 送达、跨重启持续
  调度——全部留待 supervisor 获准窗口后续切片；`production_ready=false`
  不变，release-approval 仍是 human-only 门。
- **secret 文件本体不在仓库**：固定路径
  `infra/env.production-drift-watch-alert-secret.json` 由操作者在获准
  窗口创建（gitignored）；本切片对该文件只有存在性语义，零内容接触。
