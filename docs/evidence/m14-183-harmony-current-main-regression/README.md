# M14-183：Harmony current-main 回归证据收口（docs-only）

## 结论（先说结果）

| 项 | 结果 |
|---|---|
| docs 分支 | `harmony/m14-183-current-main-regression-evidence`，基于 main `7b1e68366e5238d340e8a5338d419f4df5b876ea`（PR #272 merge，精确基点） |
| 原始执行基点 | `38441625e909599c58627785aa6f147f30ab219d`（PR #270 merge），是 docs 基点的直接祖先链内提交 |
| 当前头部重跑 | **没有，也不作声明**——本切片零重跑、零构建、零签名、零安装、零启动、零设备操作、零网络请求 |
| 两基点间代码面 | `3844162..7b1e683` 变更仅 `tools/ops/`、`tests/ops/`、`.github/workflows/ci.yml` 与 docs——**零 Harmony 应用/工具代码变更** |
| Release 构建（已执行时点证据） | **通过**：exit 0，unsigned HAP **235,172 字节**，SHA256 `EC058A607726A8EBC83A48167E2B73AB59D3F6A7348F9857AE8D9F6743BA23B1` |
| 聚焦 release 测试 | **679 passed / 1 skipped**（21.84s） |
| mock 契约 | **85/85 passed**（M13 suite 58/58 + M14-89 auth suite 27/27） |
| 公共只读 preflight | **6/6 matched**（health/auth_status/version 200；privacy/ops_snapshot/audit 401） |
| install gate | **受控失败（fail-closed，如设计）**：恰一个 `install_output_error`，detail `{signature: "no signature file"}`；`verified_installed=null`、`mutation_performed=false`、cleanup `not_required` |
| 装后运行时/UI | **保持未运行**——start/settings_ui/home_view/background/uninstall 全部 `not_run`，本切片不作任何此类声明 |
| 硬阻塞 | 签名 debug identity 或 AGC 发布材料缺失，仍是 Harmony 分发的硬阻塞 |

## 1. 定性与边界

本切片是 **docs-only 证据收口**：回归执行是 supervisor 在本切片之外
（原始 worktree、基点 `3844162`）已完成并落档的时点事实；本切片只
**读取**该 worktree 既有的 gitignored `.verify/` 工件并入库证据 README
与两处台账条目。零代码、零模板、零测试源码、零 compose、零 env、零
infra 变更；不 rebuild、不 sign、不 install、不 start/stop、不变异
模拟器、不触碰生产/VPS/Nginx/frp/Docker/语音、不发出任何网络请求、
不读写任何 secret。原始工件内容不回显、不入库；本 README 只引用
文件名与事实。

## 2. 基点与代码面核查（为什么 docs 基点可直接落档执行基点的证据）

| 项 | 值 |
| --- | --- |
| 原始执行基点 | `3844162`（PR #270 merge，M14-179 生产滚动证据合入） |
| docs 落档基点 | `7b1e683`（PR #272 merge，M14-181 公网边缘优化设计合入），`git rev-parse` 复核与 `origin/main` 一致 |
| 祖先关系 | `git merge-base --is-ancestor 3844162 7b1e683` 成立 |
| 两基点间 diff | `.github/workflows/ci.yml`、`docs/CHANGELOG.md`、`docs/PROJECT_STATUS.md`、`docs/evidence/m14-180-*/README.md`、`docs/evidence/m14-181-*/README.md`、`tests/ops/__init__.py`、`tests/ops/test_public_edge_stability_probe.py`、`tools/ops/public_edge_stability_probe.py` |

即：`apps/harmony/`、`tools/harmony_release/`、`tools/harmony_mock/`、
`tests/harmony_release/` 在两基点之间**零字节变更**——已执行证据对
docs 基点仍描述同一 Harmony 代码面。尽管如此，本切片**不声称**在
`7b1e683` 头部做过任何运行时重跑；上述结论仅是 diff 事实，不是重跑
结论。

## 3. 已执行证据摘要（基点 `3844162`，时点 2026-09-29，引用 gitignored 工件）

### 3.1 Release 构建（`release_build.json` / `release_build.stderr.txt`）

- `hvigorw.bat clean` + `assembleHap --mode module -p product=default
  -p buildMode=release`，两步 exit 0、success marker 命中，工具
  `status=ok`、`failures=[]`；
- 工件 `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  （仓库内相对路径），**235,172 字节**，SHA256
  `EC058A607726A8EBC83A48167E2B73AB59D3F6A7348F9857AE8D9F6743BA23B1`，
  文件名含 `unsigned`（工件字段 `filename_has_unsigned=true`）；
- `require_materials=false`、`external_materials=null`——无任何签名
  材料参与，unsigned 边界口径与 M14-120/M14-126/M14-138 一致。

### 3.2 聚焦测试（`pytest_harmony_release.txt` / `harmony_mock_contract.txt`）

- `tests/harmony_release`：**679 passed, 1 skipped in 21.84s**
  （1 skipped 为 Windows FIFO 既有跳过口径）；
- `tools/harmony_mock/test_contract.py`：**85/85 passed, 0 failed**
  （M13 suite 58/58 + M14-89 auth suite 27/27）。

## 4. Host preflight（公共 API base，只读，6/6 matched）

对公共生产边缘（`api_base_mode=public_https`，`https://ndtool.cn/aios`）
的只读 preflight 全部 matched：

| 端点 | 期望 | 实测 |
| --- | --- | --- |
| `health` | 200 | 200（body JSON 有效） |
| `auth_status` | 200 | 200（body JSON 有效） |
| `version` | 200 | 200（body JSON 有效） |
| `privacy` | 401 | 401 |
| `ops_snapshot` | 401 | 401 |
| `audit` | 401 | 401 |

## 5. install gate 受控失败（`backend_smoke.json` → `backend_smoke_confirm.json`）

执行纪律为两步：先 dry-run（`backend_smoke.json`：`status=planned`、
`exit=0`、`dry_run=true`，计划核对无异议），再 `--confirm-mutation`
真实执行（`backend_smoke_confirm.json`）：

| 项 | 值 |
| --- | --- |
| 真实退出码 | **1**（stderr 摘要与 JSON `exit_code` 一致） |
| `status` | `failure` |
| failures | **恰好一个** `install_output_error`，detail `{signature: "no signature file"}`——安装器对未签名 HAP 的真实拒绝输出被 fail-closed 解析捕获（M14-172 加固口径） |
| `verified_installed` | `null`（未伪造安装成功） |
| `mutation_performed` | `false`（安装未成功，设备未留下变更） |
| cleanup | `not_required`（reason `install_not_successful`，`attempted=false`） |
| warnings | 空 |
| 设备侧命令数 | `commands_executed=1`（仅该次安装尝试；目标为 loopback 模拟器，记录为 sha256 前缀，原始串未记录） |

`evidence_boundaries` 全 false：`credentials_used=false`、
`device_logs_read=false`、`http_bodies_recorded=false`、
`lan_listener_created=false`、`layout_content_recorded=false`、
`production_touched=false`、`write_methods_used=false`。工具对子进程
剥离 `AIOS_HARMONY_*` 环境变量（仅记录键名，值未读取/未打印）。

## 6. 未运行步骤（全部如实 not_run，不得声称）

`start` / `settings_ui` / `home_view` / `background` 四步 reason 均为
`previous_step_failed`；`uninstall` reason 为 `install_not_successful`。
所有设备侧命令（`aa start`、`uitest`、`file recv`、`aa force-stop`、
`uninstall`）`executed=false`——install 被拦截后无任何前台/清理动作
执行。**装后运行时、UI、后台、清理证据全部保持未运行状态；任何后续
文档不得把本链路描述为已验证的设备回归。**（对照：M14-138 曾以签名
链路之外的方式完成过 auth smoke 全绿，但那是独立切片的独立证据，
不在本链路声明范围内。）

## 7. 本切片自身的验证（docs-only 口径，真实执行）

- 文档/版本守卫（零网络，主仓 canonical venv Python）：
  `services/api/tests/test_versioning_rollback.py::test_version_sources_in_sync`
  （VERSION == web package.json == CHANGELOG 顶部版本条目；M14-xxx 条
  目不参与版本头匹配）、
  `services/api/tests/test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  （状态文档措辞守卫）——全部通过；
- `git diff --check` 干净；
- 新增行 secret / 本地绝对路径（盘符或根路径形态，正反斜杠变体，
  http(s) URL 中 `s:/` 形态为已知误报排除）/ U+FFFD 扫描 **0 真实
命中**（heuristic 仅命中描述扫描口径本身的自指性关键词，无任何
secret 值或路径值）；
- 本切片引用的全部仓库相对路径与 gitignored 工件文件名经存在性核验。

## 8. 诚实边界

1. 本切片零生产服务与设备操作：不 rebuild、不 sign、不 install、不
   start/stop、不变异模拟器、不发网络请求——原始执行对设备的全部
   影响就是一次被安装门禁 fail-closed 拒绝的安装尝试
   （`mutation_performed=false`，cleanup `not_required`）。
2. 证据的执行基点是 `3844162`，不是 docs 基点 `7b1e683`；本切片对
   当前头部**没有**运行时重跑，也不作此声明。两基点间零 Harmony
   代码变更只是 diff 事实，不等于重跑结论。
3. install gate 的受控失败证明的是门禁本身按设计拦截未签名 HAP，
   **不构成** Harmony 生产分发就绪、签名能力或设备回归通过的声明；
   装后运行时/UI 证据保持未运行。
4. 签名 debug identity 或 AGC 发布材料仍缺失，为硬阻塞——unsigned
   HAP 边界与 `production_ready` 口径不变，release-approval 仍是
   human-only 门。
5. 单时点证据非持续保证；任何 token/密钥/密码/secret 值绝不写入；
   本地绝对路径不入库；原始运行工件位于 gitignored `.verify/`
   目录，不入库、不回显。
6. 本切片交付为 branch + 单 local commit；supervisor 审查与 remote
   发布（push/PR/合并）在其后进行。

## 9. 证据目录（gitignored，`.verify/m14-180-harmony-current-main-regression/`）

- `release_build.json` / `release_build.stderr.txt`（构建事实与空 stderr）
- `backend_smoke.json` / `backend_smoke.stderr.txt`（dry-run 计划）
- `backend_smoke_confirm.json` / `backend_smoke_confirm.stderr.txt`（真实执行，受控失败）
- `pytest_harmony_release.txt`（679 passed / 1 skipped）
- `harmony_mock_contract.txt`（85/85 passed）
- 子目录 `backend_smoke/`、`backend_smoke_confirm/`（空）

## tracked 改动边界

本切片 tracked 改动：本 README（新增）+ `docs/CHANGELOG.md` /
`docs/PROJECT_STATUS.md`（各一条 M14-183 条目）。`apps/harmony`、
`tools/harmony_release`、`tools/harmony_mock` 及任何生产源码零改动。
单 local commit；supervisor 审查与 remote 发布（push/PR/合并）在其后
进行。
