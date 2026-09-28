# M14-172：Harmony install-gate 回归证据收口（Harmony Install-Gate Evidence）

- 切片：worktree
  `ai-learning-os-worktrees/m14-172-harmony-install-gate-evidence`，分支
  `m14-172-harmony-install-gate-evidence`，基于 main
  `ea1f1cddb0844629d992d9c004783db27d50aba0`（PR #259 merge，精确基点）。
- 定性：**docs-only 证据收口**——本切片零代码、零模板、零测试、零
  compose、零 env、零 infra 变更；不重启、不启动、不停止、不查询任何
  生产服务，不读写任何 secret。回归运行（M14-170B）由 supervisor 在
  本切片之外的既有 worktree 执行；本 README 为唯一入库证据文件，
  原始运行工件位于 gitignored 的 `.verify/` 目录，内容不回显、不入库。
  Windows 本地绝对路径在入库文档中以 `<仓库盘>` 占位（仓库既有惯例）。
- 核心结论：PR #259 合入的 `backend_smoke.py` install-gate 加固
  （install 输出 fail-closed 解析、post-install `bm dump` 存在性证明、
  纯前台守卫）在真实未签名 HAP 上通过了**受控失败回归**：未签名
  HAP 被安装门禁正确拦截并如实报告，无任何伪造安装/启动成功。

## 1. 基点与 PR 链

| 项 | 值 |
| --- | --- |
| main 基点 | `ea1f1cddb0844629d992d9c004783db27d50aba0`（PR #259 merge） |
| PR #259 | state **MERGED**，title "Harden Harmony smoke install and foreground gates"，merge commit 即上表基点 |
| merge-post main CI | **5/5 check runs SUCCESS**：Android（unit test / lint / assemble）、API（ruff / pytest / migration）、Web（test / typecheck / lint / build）、Docker（compose build + healthy + smoke）、Release tools（Python tests） |
| feature head | `ea9e2ef547403ee610a471b2d5edd2ef79eab782`（PR #259 final head，为基点的祖先） |
| feature commits | `f424e7a` gate Harmony backend smoke install output；`cb7ab5e` add Harmony foreground guard parser（M14-170 R2a）；`ea9e2ef` backend smoke one-shot start foreground proof（M14-170 R2b，fail-closed） |

PR #259 对 `tools/harmony_release/backend_smoke.py` 的加固（事实引用，
本切片零触碰）：

- **install 输出 fail-closed 解析**：安装器 rc 0 一律不可信；输出按
  已知签名分类，未签名 HAP 的 `no signature file` 命中
  `install_output_error`；
- **post-install `bm dump` 存在性证明**：安装声称成功后必须
  `bm dump -n <bundle>` 证明 bundle 在场（exit code / 输出非空 /
  bundle 命中），否则 fail-closed；
- **纯前台守卫**：新增 `tools/harmony_release/foreground_guard.py`
  纯解析器（不触设备），为一次性 start 提供可测试的 fail-closed
  前台判定；配套测试 `tests/harmony_release/test_backend_smoke.py`
  与 `test_foreground_guard.py`。

## 2. M14-170B 回归验证设置（supervisor 执行，本切片只落档）

| 项 | 值 |
| --- | --- |
| 验证分支 | `verify/m14-170b-install-gate-regression`，**精确基于 merge commit `ea1f1cd`**（运行前 `git fetch origin` 后自 `origin/main = ea1f1cd` 创建，`git rev-parse HEAD` 确认） |
| 运行 worktree | `ai-learning-os-worktrees/m14-169b-harmony-public-smoke`（既有隔离 worktree，tracked files 运行前后均 clean） |
| 目标设备 | `127.0.0.1:15566`（loopback 模拟器），运行前与运行后 `hdc list targets` 均在线 |
| 未签名 HAP | `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`，**220,008 bytes**，SHA256 `402464CA37D2F697F9C043B5A6ACF863D2DCAAB4A016BAE0D197B8ADE59BA59F`（certutil 与工具自报一致） |
| API base | `--api-base https://ndtool.cn/aios/` 与 `--device-api-base https://ndtool.cn/aios/`（公共生产边缘） |
| 变更确认 | `--confirm-mutation`（真实安装尝试授权） |

## 3. Host preflight（公共 API base，6/6 matched）

对 `https://ndtool.cn/aios/` 的只读 preflight 全部 matched：

| 端点 | 期望 | 实测 |
| --- | --- | --- |
| `health` | 200 | 200（body JSON 有效） |
| `auth_status` | 200 | 200（body JSON 有效） |
| `version` | 200 | 200（body JSON 有效） |
| `privacy` | 401 | 401 |
| `ops_snapshot` | 401 | 401 |
| `audit` | 401 | 401 |

## 4. 受控失败验收（install-gate 拦截未签名 HAP）

| 项 | 值 |
| --- | --- |
| 真实退出码 | **1**（批处理 `exit /b %ERRORLEVEL%` 捕获，与 JSON `exit_code: 1` 一致） |
| `status` | `failure` |
| failures | **恰好一个** `install_output_error`，detail `{signature: "no signature file"}`（安装器对未签名 HAP 的真实拒绝输出被 fail-closed 解析捕获） |
| `mutation_performed` | `false`（安装未成功，设备未留下变更） |
| `verified_installed` | `null`（未伪造安装成功） |
| cleanup | `not_required`（reason `install_not_successful`） |
| warnings | 空——**无 `settings_tab_not_found`** |

## 5. 未运行步骤（全部如实 not_run）

`start` / `settings_ui` / `home_view` / `background` 四步 reason 均为
`previous_step_failed`；`uninstall` reason 为 `install_not_successful`。
所有设备侧命令（`aa start`、`uitest`、`file recv`、`aa force-stop`、
`uninstall`）`executed=false`——install 失败后无任何前台/清理动作执行。
工具记录的 `stripped_env_keys` 仅列 `AIOS_HARMONY_*` 环境变量**键名**
（cert/keystore/profile 等），任何值未被读取或打印。

## 6. Post-run 证明（bundle 缺失 + 工作区干净）

- `hdc -t 127.0.0.1:15566 shell bm dump -n com.ailearningos.app` →
  `error: failed to get information and the parameters may be wrong.`
  ——**bundle 确实不在设备上**，与 install 被拦截的结论互证；
- 运行后 `git status --porcelain` 为空——tracked files 无任何改动；
- 目标 `127.0.0.1:15566` 运行后仍在线，模拟器与无关进程未受扰动。

## 7. 本切片的验证（docs-only 口径，真实执行）

- 文档/版本守卫（零网络）：
  `test_versioning_rollback.py::test_version_sources_in_sync`
  （VERSION == web package.json == CHANGELOG 顶部版本条目）、
  `test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  （状态文档措辞守卫）——全部通过；
- `git diff --check` 干净；
- 新增行 secret / 本地绝对路径（盘符或根路径形态，正反斜杠变体）/
  U+FFFD 扫描 **0 命中**（http(s) URL 中的 `s:/` 形态为已知误报，
  排除后 0 真实命中）。

## 8. 诚实边界

1. 本切片是 **docs-only 证据收口**：回归运行是 supervisor 在本切片
   之外执行的时点事实，本切片零生产变更、零设备操作、零 secret 读写。
2. 本次验证**只覆盖 smoke 门禁本身**：证明未签名 HAP 会被安装门禁
   fail-closed 拦截并如实报告。它**不创建任何 AGC 签名材料、不产出
   签名 HAP、不构成 Harmony 生产分发就绪的声明**——签名 HAP 分发
   仍是开放项。
3. 单次受控失败回归是时点证据，非对工具未来所有运行的持续保证。
4. 任何 token/密钥/密码/secret 值与生产 env 内容绝不写入；本地
   绝对路径以 `<仓库盘>` 占位；原始运行工件（JSON/stderr/bat 等）
   位于 gitignored `.verify/` 目录，不入库。
5. 本切片交付为 branch + PR，PR 创建即止；合并决策归 supervisor
   审查（supervisor 审查与 remote 发布在其后进行）。
