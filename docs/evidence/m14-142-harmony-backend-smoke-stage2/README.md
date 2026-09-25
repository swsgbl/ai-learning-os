# M14-142 Harmony 后端冒烟重复周期包装器 Stage 2（真实三周期模拟器执行）

切片：Stage 2 真实运行证据（基于 M14-142 Stage 1 已合并的
`tools/harmony_release/backend_smoke_repeat.py` 与预热加固后的
`tools/harmony_release/backend_smoke.py`；**零 Python/工具文件改动**——
全部使用已合并未修改工具）。worktree
`m14-142-harmony-backend-smoke-stage2`，分支
`harmony/m14-142-backend-smoke-stage2`，基
`239b881b02514f50d8ad6177b61bcb84fb908ac5`，单 local commit、不
push、不开 PR。

## 1. 执行环境

- 模拟器：Pura 90（phone · HarmonyOS 6.1.1(24) Beta1 · 1320x2856），
  本切片**受支持启动器启动**（`harmony_emulator_start`，新进程 PID
  `74332`——前线程验证用的 PID `71768` 在切片开始前已停止），目标
  `127.0.0.1:5555`，`bootevent.boot.completed=true` 复核通过。
- **从未使用** `127.0.0.1:15566`/KaihongOS 目标。
- 后端：既有运行中 FastAPI（`127.0.0.1:8000` LISTENING，host PID
  `18656`）——**零后端/容器/DB/MinIO/语音引擎/调度器/secrets/代理启停**，
  本切片仅对其做只读匿名 GET 预检。
- 模拟器**执行后保持运行**（未 stop）。

## 2. 构建

`python tools\harmony_release\release_build.py --quiet`（exit 0，
`status=ok`）：

| 项 | 值 |
| --- | --- |
| HAP | `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap` |
| 大小 | **220008 bytes**（unsigned） |
| SHA256 | `0335E5BE261AE3DE80748BEECABFE5F1CAF78E710EAB80A101D602F6606C7271` |
| 构建 | exit 0（clean + assembleHap 均 0） |

## 3. 重复周期包装器（计划 + 执行）

同一套合并未修改的 `backend_smoke_repeat.py`，参数
`--repo-root . --target 127.0.0.1:5555 --cycles 3 --hap
apps\harmony\entry\build\default\outputs\default\entry-default-unsigned.hap
--api-base http://127.0.0.1:8000/ --evidence-dir
.verify\m14-142-harmony-backend-smoke-stage2\repeat`。

| 调用 | 结果 |
| --- | --- |
| 计划模式（无 `--confirm-mutation`） | exit **0**，`status=planned`，`subprocesses_spawned=0`，`cycles_executed=0`——零子进程、零 HTTP、零 hdc |
| 执行模式（`--confirm-mutation`） | exit **0**，`status=ok`，`cycles_executed=3`，`subprocesses_spawned=3`，`fail_stopped_at=null` |

## 4. 三周期逐周期结果（执行模式聚合 JSON 转写）

聚合 JSON（`repeat/backend_smoke_repeat.json`）记录口径（如实）：
顶层 `status`/`exit_code`/`cycles_executed`/`cycles_requested`/
`subprocesses_spawned`/`failures`/`fail_stopped_at`；每周期字段为
`index`/`evidence_dir`/`child_exit_code`/`child_status`/
`child_mutation_performed`/`child_failures`/`seconds`/`returncode`/
`status`/`argv`（argv 已脱敏占位）。**聚合 JSON 不保留
`convergence_retries` 字段，也不含子进程逐步明细（preflight/install/
start/settings_ui/home_view/background/uninstall 各步单独记录）与
cleanup 细节**——下表每周期值全部转写自聚合 JSON 保留字段；逐步链路
与 cleanup 结论仅来自子进程运行时的控制台输出，非聚合 JSON 内容。

| 周期 | evidence_dir | child_exit_code | child_status | child_mutation_performed | child_failures | seconds |
| --- | --- | --- | --- | --- | --- | --- |
| cycle_1 | `cycle_1` | 0 | ok | true | [] | 26.062 |
| cycle_2 | `cycle_2` | 0 | ok | true | [] | 25.031 |
| cycle_3 | `cycle_3` | 0 | ok | true | [] | 24.922 |

每周期子进程（`backend_smoke.py --confirm-mutation`）运行时全链路：
host_preflight（匿名 loopback GET 6 端点全 matched：3×200 JSON
字段在位 + 3×401 诚实未授权）→ install → start →
settings_ui → home_view → background → uninstall（cleanup
`required=true`，三周期均 `attempted=true / status=ok`——**每周期均完成
卸载清理**）。设备侧状态三周期末均为 `bundle_uninstalled=true`。

每周期 `cycle_<n>/` 子目录（每周期**立即**把全局 temp 暂存布局复制入
本目录，证据归属按周期隔离）：
`backend_smoke_layout_settings.json` + `backend_smoke_layout_home.json`
（原始布局 dump；SHA-256/内容不入聚合 JSON）。

## 5. 验证矩阵

| 项 | 结果 |
| --- | --- |
| 三周期全部 executed 且 ok | 3/3（上表，`child_status=ok`、`child_exit_code=0`） |
| Settings 收敛 | 三周期运行时日志显示 `settings_ui` 均一次 dump 即收敛（无重试发生）；**聚合 JSON 不记录 `convergence_retries` 字段**，此结论来自运行时控制台输出，非聚合 JSON 可核验值 |
| Home 断言 | 三周期运行时日志显示 `home_view` 均 ok（`请求失败 (HTTP 401)` ×≥3、`0.1.0` ×≥1 全过）；聚合 JSON 对应字段为 `child_status=ok` + `child_failures=[]`（home 布局 digest 入子结果，聚合 JSON 不含） |
| 卸载清理 | 三周期运行时日志显示 `uninstall` 均 ok（`cleanup.status=ok`）；聚合 JSON 不保留 cleanup 明细，仅 `child_failures=[]` + `child_mutation_performed=true` |
| `git diff --check` | 干净（docs-only commit 后） |
| Python 改动扫描 | 本切片**零** Python/工具改动（ruff/py_compile 按任务书不适用——无 Python 变更） |
| 新增行秘密/本地路径/U+FFFD 扫描 | 本 README 新增行：127.0.0.1 字面量为任务书给定目标（预期，非命中）；无 secret 形态值、无盘符绝对路径、无 U+FFFD |

## 6. 诚实边界

- **顺序全局 temp 暂存口径（supervisor 已知并拒绝整改）**：
  `backend_smoke.py` 的布局暂存文件恒为 `tempfile.gettempdir()` 下固定
  文件名（`backend_smoke_layout_{settings,home}.json`），三周期**严格顺
  序**执行（fail-stop、绝无并发），且每周期结束后布局**立即**复制入
  `cycle_<n>/` 证据子目录——周期证据归属正确、无跨周期污染。该暂存
  路径本身被拒绝改为按周期隔离（全局 temp 共享暂存是本次执行如实承
  受的边界）。
- unsigned HAP、模拟器非真机、loopback 后端非生产语义：不构成签名/
  真机/生产就绪声明；`production_ready=false` 不变。
- 本切片零生产触碰：零容器/DB/MinIO/语音/secrets 变更，零部署；
  仅对运行中后端做 6 个只读匿名 GET。
- 模拟器保持运行未 stop；Kaihong 目标全程未触碰。
- 原始证据（`release_build.json`、`repeat_plan.json`、
  `repeat_execute.json`、`repeat/backend_smoke_repeat.json` + `.md`、
  各 `cycle_<n>` 布局）均在本 worktree gitignored 的
  `.verify/m14-142-harmony-backend-smoke-stage2/` 下，不入仓。
