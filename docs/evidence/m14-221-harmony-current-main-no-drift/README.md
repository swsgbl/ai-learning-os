# M14-221H 证据：Harmony current-main 无漂移验证收口（docs-only）

## 0. 结论与边界

- 本切片是 **docs-only 证据收口**：把已在 M14-220H 完成的「current main
  无漂移验证」固化为仓库证据并更新三本台账。验证本身在
  M14-220H 会话（verification-only，零 commit、零设备变更）中完成，
  本切片 **不重跑** 构建/预检/设备步骤，不产生新的运行时证据。
- 审计源：gitignored 目录
  `.verify/m14-220-harmony-current-main-no-drift/`（worktree
  `ai-learning-os-worktrees/m14-220-harmony-current-main-no-drift`），
  其 `REPORT.md` 与全部引用证据在写入本 README 前已被独立复核
  （见 §5）。
- 诚实边界：本切片 **不** 声称 `signedness_verified`、signed HAP、
  AGC 材料、Harmony 真机/公开发布、`release_ready`、
  `production_ready` 或 `public_ready`。快照是 unsigned 边界
  （`signing_configs_count=0`），主机上无 AGC 外部材料
  （`.cer`/`.p7b`/`.p12`）。
- 验证会话遵守与 M14-220H 相同边界：不碰生产、Docker/WSL、ADB
  生命周期、secrets 或 AGC 材料。

## 1. 锚点（M14-220H 验证时捕获，本切片独立复检）

| 项目 | 值 |
| --- | --- |
| 验证 worktree 分支 | `verify/m14-220-harmony-current-main-no-drift` |
| 验证 worktree HEAD | `df3d9969bb7195f819a5e2e4cf0ab36c083933eb`（= `origin/main` = upstream） |
| 本 docs worktree 基线 | `df3d9969bb7195f819a5e2e4cf0ab36c083933eb`（分支 `docs/m14-221-harmony-current-main-no-drift-evidence`） |
| 漂移基线 commit | `b1f1a72b55513716d9281e22d6597b1c580742bf`（存在，M14-213H 验证基线） |
| 验证前/后 tracked-clean | `git status --porcelain --untracked-files=no` 两次均为空 |
| 验证日期 | 2026-10-03 |

## 2. 无漂移判定（区间 `b1f1a72..df3d9969`）

命令：

```
git diff --name-only b1f1a72b55513716d9281e22d6597b1c580742bf..df3d9969bb7195f819a5e2e4cf0ab36c083933eb -- apps/harmony tools/harmony_release tests/harmony_release
```

结果：**空**（exit 0，0 个文件）——M14-213H 验证过的 Harmony 面
（`apps/harmony`、`tools/harmony_release`、`tests/harmony_release`）在
current main `df3d9969` 上代码等价。

同区间全部变更 30 个文件，全部在 Harmony 面之外（docs/、services/api
ops+tests、`infra/smoke_llm.sh`、tests/android_release、
tools/android_release、tools/voice），逐字记录于
`changed-files-b1f1a72..HEAD.txt`。按任务规则，**未触发** 748 测试套件
与模拟器认证冒烟重跑（无 Harmony 面漂移）。

## 3. 未签名发布构建与预检快照（M14-220H 运行，本切片不重跑）

### 3.1 unsigned release build

命令（cmd.exe，worktree 根）：
（路径中 `<repo>` 为主仓库检出目录、`<repo-worktrees>` 为其下 worktree 父目录的占位写法；与既往 m14-213H 证据同口径，不记录机器绝对路径。）

```
"<repo>\.venv\Scripts\python.exe" -m tools.harmony_release.release_build --repo-root "<repo-worktrees>\m14-220-harmony-current-main-no-drift"
```

- exit code：**0**。权威源是工具自身 stderr 汇总行
  `harmony_release_build: status=ok exit=0 artifact=apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap size_bytes=235176 sha256=3019D5D0...`；
  `release_build.exitcode.txt` 被 cmd `%` 转义损坏，不作为证据。
- 产物：`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`。
- HAP 大小：**235176 字节**。
- HAP SHA256：**`3019D5D0C457AFAA7DAC35E54184BDF902261F4D0E3471ACCADB16E35A2C3A45`**
  （M14-220H 会话内 PowerShell `Get-FileHash` 独立复算一致；同值亦嵌入两份
  preflight JSON 的 `hap.sha256`；本 docs 会话只读复算再次一致，见 §5）。

### 3.2 预检（unsigned 边界 + fail-closed 探针）

默认（unsigned 边界）：

```
"<repo>\.venv\Scripts\python.exe" -m tools.harmony_release.preflight --repo-root "<repo-worktrees>\m14-220-harmony-current-main-no-drift" --hap "apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap"
```

- exit code：**0**（`preflight_default.exitcode.txt`）。
- `preflight_default.stdout.json`：`status="blocked_by_external_materials"`、
  `failures=[]`、`warnings=[]`、`signing_configs_count=0`、
  `unsigned_boundary=true`、`hap.filename_has_unsigned=true`。按 M13-16a
  契约（无 failures 时 EXIT_OK=0），这是预期的诚实 unsigned 边界而非失败。

`--require-materials`（fail-closed 探针）：

```
"<repo>\.venv\Scripts\python.exe" -m tools.harmony_release.preflight --repo-root "<repo-worktrees>\m14-220-harmony-current-main-no-drift" --hap "apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap" --require-materials
```

- exit code：**2**（`preflight_require_materials.exitcode.txt`）。
- JSON：`status="blocked_by_external_materials"`、
  `require_materials=true`、三个 `AIOS_HARMONY_*_PATH` 全部 `not_set`/
  `present=false`、`failures=[]`。与预期 fail-closed 行为一致
  （preflight.py EXIT_MISSING_MATERIALS=2 分支）。

### 3.3 模拟器目标盘点（只读）

`hdc list targets` → `127.0.0.1:5555`。未对该目标做任何安装、启动、
停止、重启或其它变更。

## 4. M14-220H 验证的原始结论（逐条与本仓库证据对应）

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 分支/HEAD/tracked-clean/upstream 锚点 | **PASS**（全部匹配；upstream=HEAD=origin/main=df3d9969） |
| 2 | Harmony 面 diff `b1f1a72..HEAD` 为空 | **PASS**（0 文件） |
| 2b | 区间内 Harmony 面之外变更已枚举 | **PASS**（30 文件，无一在 Harmony 面） |
| 3 | unsigned release build exit code + HAP 哈希 | **PASS**（exit 0；235176 B；SHA256 3019D5D0...A45，交叉验证） |
| 4a | 预检默认 unsigned 边界 | **PASS**（exit 0；blocked_by_external_materials；无 failures/warnings） |
| 4b | 预检 require-materials fail-closed | **PASS**（exit 2；blocked_by_external_materials） |
| 5 | 只读 `hdc list targets` | **PASS**（127.0.0.1:5555 存在；零变更） |
| 6 | 证据限于 gitignored `.verify/` 目录 | **PASS**（`.gitignore:16`；tracked 树保持 clean） |

## 4b. 本收口切片不重跑的声明

以下步骤按任务边界 **不重跑**，其既有证据保持 M14-220H 会话记录：
748 测试套件与模拟器认证冒烟（仅 Harmony 面漂移才触发，漂移=无）；
HAP 构建与预检（§3 已固化哈希与退出码）；一切设备操作。

## 5. 本 docs 切片的独立复核（写入前完成）

- 读取 m14-220 worktree（`ai-learning-os-worktrees/m14-220-harmony-current-main-no-drift`，仓库父目录下）中 `.verify/m14-220-harmony-current-main-no-drift/REPORT.md` 全文。
- 在本 worktree 复检锚点：`git rev-parse HEAD` =
  `df3d9969...`；`git status --porcelain --untracked-files=no` 空；
  `git diff --name-only b1f1a72..df3d9969 -- apps/harmony tools/harmony_release tests/harmony_release` 空（0 文件）；
  同区间全量 `git diff --name-only` 计数 30。
- 在 M14-220 worktree 只读复检：tracked-clean；`.verify` 证据目录 13 个
  文件齐全（REPORT.md + git-anchors.txt + changed-files + 2×build 输出
  + 6×preflight 输出 + run_preflight.cmd）；HAP 只读 `Get-FileHash` →
  `3019D5D0C457AFAA7DAC35E54184BDF902261F4D0E3471ACCADB16E35A2C3A45`、
  235176 字节，与 REPORT 一致。
- 解析两份 preflight JSON：default exit 0 / require-materials exit 2，
  `status` 均为 `blocked_by_external_materials`，`hap.sha256` 与构建
  哈希一致，`signing_configs_count=0`。
- `git-anchors.txt` 逐行核对：验证分支、HEAD、upstream、origin/main、
  baseline 存在性、tracked-clean、区间 log。

## 6. 本 docs 切片的校验记录

- Markdown sanity：新增/修改的 Markdown 文件均为合法 UTF-8（无 BOM）、
  CRLF 行尾、以换行符结尾；表格/代码块语法检查通过。
- 敏感值扫描：新增/修改行无 secret 形状值（密钥/token/密码模式）、
  无 U+FFFD、无本机绝对路径（命令以 `<repo>`/`<repo-worktrees>` 占位
  记录，与既往 m14-213H 证据同口径）。
- `git diff --check`：通过（无空白错误）。
- 提交：分支 `docs/m14-221-harmony-current-main-no-drift-evidence` 基于
  `df3d9969` 的 **恰好一个** 本地 commit。不 push、不 PR、不合并。

## 7. 局限（诚实边界）

- 这是 **unsigned** 快照：`signingConfigs` 为空（仓库既定边界）；主机
  无 AGC 外部材料；`signedness_verified=false`；本切片不声称 true，
  也无法从本证据得出。
- 无签名、无真机运行、无 AGC 交互、无公开发布：
  **release_ready / production_ready / public_ready 均不被声称。**
- 748 测试套件与模拟器认证冒烟按任务规则未重跑（无 Harmony 面漂移），
  其最后记录证据仍是 M14-213H 在基线 `b1f1a72` 的运行。
- 构建可复现性（工具链版本、hvigor 缓存状态）不在本证据范围内；只
  固化单次命令构建的退出码与产物哈希。
- `release_build.exitcode.txt` 内容损坏（`EXIT=%0%`，cmd 转义伪影）——
  构建退出码的权威证据是工具 stderr 汇总 `status=ok exit=0` 加产物
  存在性与哈希；预检退出码经 `run_preflight.cmd` 干净捕获。

## 8. 证据清单（gitignored 源目录，不入库）

`.verify/m14-220-harmony-current-main-no-drift/`（worktree
`m14-220-harmony-current-main-no-drift`）：

- `REPORT.md` — M14-220H 验证报告（本 README 的审计源）
- `git-anchors.txt` — 分支/HEAD/upstream/baseline/log 锚点
- `changed-files-b1f1a72..HEAD.txt` — Harmony 面空 diff + 30 文件清单
- `release_build.stdout.json` / `release_build.stderr.txt` /
  `release_build.exitcode.txt`（损坏，见 §7）
- `preflight_default.*` ×3、`preflight_require_materials.*` ×3
- `run_preflight.cmd` — 产出预检运行与退出码文件的证据脚本

**结论**：M14-213H 验证过的 Harmony 面在 current main `df3d9969` 上保持
代码等价；未签名构建/预检快照按预期复现，fail-closed 边界行为正确。
本切片仅将其固化为仓库证据，不新增任何运行时声明。
