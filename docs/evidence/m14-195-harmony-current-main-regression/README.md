# M14-195：Harmony current-main 模拟器回归证据（docs-only 收口）

## 0. 交付与边界

- 切片：worktree `ai-learning-os-worktrees/m14-195-harmony-current-main-evidence`，
  分支 `docs/m14-195-harmony-current-main-evidence`，基于 main
  `6d9b106f9d2cb131a6e3b2473f59a97631635b13`（PR #285 merge =
  M14-193 生产滚动证据合入，精确落档基点）。
- 原始执行：hmharness 于 2026-09-30（UTC+8）在 worktree
  `ai-learning-os-worktrees/m14-195-harmony-current-main-regression`、
  分支 `harmony/m14-195-current-main-regression`，基于当时 main
  `a62ae76d18d999ebc847c5f13bb968bc85340d0a`（PR #284 merge =
  M14-194H AGC signing preflight 合入）执行 verification-only 回归：
  零 tracked 改动、零 commit、零 push。
- 双基点代码面：`a62ae76..6d9b106` 之间变更仅 M14-193 的三个 docs
  文件（`docs/CHANGELOG.md`、`docs/PROJECT_STATUS.md`、
  `docs/evidence/m14-193-production-rollout-evidence/README.md`），
  `apps/harmony`、`tools/harmony_release`、`tools/harmony_mock`、
  `tests/harmony_release` 零字节变更——执行证据对落档基点仍描述同一
  Harmony 代码面。本切片不声称在 `6d9b106` 头部做过任何重跑；上述
  只是 diff 事实，不是重跑结论。
- 定性：**docs-only 切片**。本切片零 app/运行时/模板/测试代码变更，
  零 production/Docker/公共边缘/secret 操作，只把 hmharness 已完成的
  回归事实落档。原始工件（构建 JSON、preflight JSON、布局 JSON、
  截图）位于 gitignored 的 `.verify/` 目录，不入库、不回显原文；本
  README 是唯一入库证据文件。单 local commit 并推送远端分支；
  supervisor 审查与 remote 发布（PR 开合/合并/release 门禁）在其后
  进行。

## 1. 结论（先说结果）

| 项 | 结果 |
| --- | --- |
| Release 构建 | **通过**：`release_build` 工具 exit 0，clean/assemble 双步 success marker 命中 |
| unsigned HAP（current release 口径） | **235,176 字节**，SHA256 `52143E0D972A2F65B0FDBE1A49CB70C97ACA29BBF0A2B7F41CC403A140A5584D` |
| debug 签名安装载体（非发布物） | 271,774 字节，SHA256 `609F058AA49AA376C7353BA410085412A5D0B1817E486209AE09680FEACBCAD4` |
| 安装 / 启动 / 首帧 | 三步全部成功：`install bundle successfully` → `start ability successfully` → hilog 首帧绘制完成，采样窗口无崩溃 |
| 公网默认 base URL | 出厂默认即 `https://ndtool.cn/aios/`（零配置改动） |
| 匿名只读边界 | health `ok`；version `0.1.0`；隐私模式/运维快照/审计日志匿名请求均 HTTP 401（预期拒绝） |
| Settings DownloadPane | PWA 网页版**可用**；Harmony 原生**待发布** |
| AGC preflight（默认契约） | exit 0：failures=[]、signing_configs=0、unsigned_boundary=true、仓库材料 0 命中 |
| AGC preflight（`--require-materials`） | **受控阻断（fail-closed，如设计）**：exit 2，status `blocked_by_external_materials`，三个材料环境变量均 `not_set` |
| 硬阻塞 | AGC 发布证书/Profile 未落地，Harmony 分发仍处 unsigned 边界 |

## 2. 模拟器身份（ground truth）

| 项 | 值 |
| --- | --- |
| 设备 | Pura 90（deployed emulator，本任务专用启动，非既有进程；启动前处于 stopped） |
| hdc target | `127.0.0.1:5555` |
| 型号/名称 | emulator / emulator（`param get const.product.model/name`） |
| API 版本 | 24 |
| 系统版本 | HarmonyOS 6.1.0.117（SP37DEVC00E115R4P11 log）= 6.1.1(24) Beta1 镜像 |
| 分辨率 | 1320×2856 |
| 无关目标 | 另一 hdc 目标 `127.0.0.1:15566`（Kaihong BotBook QEMU）与本任务无关，未触碰 |

## 3. Release 构建（`release-build.json`）

- 命令：repo 根 `python tools/harmony/release_build.py`，等价于
  `hvigorw clean --no-daemon` + `hvigorw assembleHap --mode module
  -p product=default -p buildMode=release --no-daemon`（cwd
  `apps/harmony`）。
- 结果：exit 0、status `ok`、failures=[]，clean 与 assemble 两步
  exit 0 且 success marker 均命中；`require_materials=false`、
  `external_materials=null`——无任何签名材料参与构建。
- 工件：`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  （仓库内相对路径，`filename_has_unsigned=true`），
  **235,176 字节**，SHA256
  `52143E0D972A2F65B0FDBE1A49CB70C97ACA29BBF0A2B7F41CC403A140A5584D`。
- 安装载体：仅为模拟器安装目的以 framework `harmony_sign` 追加 SDK
  debug 身份签名，产物 `entry-default-signed.hap`，271,774 字节，
  SHA256 `609F058AA49AA376C7353BA410085412A5D0B1817E486209AE09680FEACBCAD4`。
  **该签名产物不构成发布物**：debug 签名 ≠ AGC 发布签名，"current
  unsigned release" 事实以上述 unsigned 产物的大小/SHA256 为准。

## 4. 安装 / 启动 / 首帧（hilog 证据链）

- 安装：`hdc -t 127.0.0.1:5555 install entry-default-signed.hap` →
  `install bundle successfully`（bundle `com.ailearningos.app`）。
- 启动：`aa start -a EntryAbility -b com.ailearningos.app` →
  `start ability successfully`。
- 生命周期（hilog，时间戳 23:09:07–08）：`AMS StartAbility
  com.ailearningos.app/EntryAbility` → `LoadLifecycle:
  abilityName:EntryAbility` → `ForegroundLifecycle EntryAbility`；
  应用侧标记 `A00000/hmh: EntryAbility onCreate`（pid 3201）。
- 首帧：`NotifyCompleteFirstFrameDrawing: id 34, [com.ailearningos.app
  entry EntryAbility]`；页面栈 `Push page info <pages.Index>`。
- 采样窗口内 hilog 无 crash/fatal 字样。

## 5. UI 与公网只读回归（布局 JSON 文本证据）

### 5.1 Home（`layout-home.json` + `screenshot-home.jpeg`）

- 标题：`AIOS 只读面板`。
- 服务地址（出厂默认，零配置改动）：`服务地址: https://ndtool.cn/aios/`。
- 服务健康：`status: ok  service: ai-learning-os-api`——对生产公网
  真实只读 GET `/api/v1/health` 成功。
- 认证状态：`远程模式(认证已开启)`（未持有凭据，如实展示）。
- 匿名只读边界（预期 401，非故障）：隐私模式 / 运维快照 / 审计日志
  三区均显示 `请求失败 (HTTP 401)`——公网 API 对匿名请求正确拒绝。
- 服务版本（滚动后布局 `layout-download.json` 内可见）：版本
  `0.1.0`；Git 提交 / Alembic current / Alembic head =
  `not_available`（公网健康端点不暴露部署细节，如实降级，非缺陷；
  意味着本次未核对部署版本指纹）。
- TabBar：首页（选中）/ 学习 / 搜索 / 语音 / 设置 / 治理。

### 5.2 Settings DownloadPane（`layout-settings.json` + `screenshot-settings-download.jpeg`）

- 卡片标题：`下载与安装(只读)`；说明文案 `渠道状态 + 服务版本 +
  下载页入口;纯文本展示,不自动打开浏览器,不写剪贴板`。
- 服务地址：`服务地址: https://ndtool.cn/aios/`。
- 渠道状态（布局 JSON 文本证据）：
  - `PWA 网页版` → `可用:浏览器访问服务地址即可使用`；
  - `Harmony 原生` → `待发布:AGC 发布证书/Profile 未落地,暂无安装包`。
- Android 原生行：**本次回归无渲染级证据**——该行文本既未出现在
  截图可见区（视口下缘被 TabBar 截断），也未出现在
  `layout-settings.json` 布局 dump 中（dump 文本止于上述两行，全文件
  零 `Android` 字样）。该行仅存在源码级事实：
  `DownloadPane.ets` 渠道数组第三行
  `Android 原生 → 待发布:请用手机浏览器访问下载页,暂无 APK 直链`
  （纯静态文本）。hmharness 原报告称"Android 行由布局 JSON 完整落盘
  背书"，与布局 JSON 原文不符，本 README 予以更正（见 §7）。
- 边界：DownloadPane 仅 1 个只读 GET `/api/v1/version`，不向
  `/download` 发请求，无任何写操作。

## 6. AGC 签名 preflight（M14-194 工具，首个 main 级真实验证）

| 项 | 默认契约（`preflight.py --repo-root .`） | 要求材料（`--require-materials`） |
| --- | --- | --- |
| exit code | **0** | **2**（受控阻断，预期 fail-closed） |
| status 字段 | `blocked_by_external_materials` | `blocked_by_external_materials` |
| failures | `[]` | `[]` |
| build-profile | exists/parseable，`signing_configs_count=0`，`unsigned_boundary=true` | 同左 |
| 材料变量 | 三者均 `not_set`（未提供，默认契约不因此失败） | 三者均 `not_set` → `all_present=false`，按契约阻断 |
| 仓库材料扫描 | `repo_materials.count=0`（仓库内无任何签名材料残留） | `repo_materials.count=0` |

- 材料环境变量：`AIOS_HARMONY_CERT_PATH`（.cer）、
  `AIOS_HARMONY_KEYSTORE_PATH`（.p12）、`AIOS_HARMONY_PROFILE_PATH`
  （.p7b）——材料未读取、未暴露、不存在。
- 口径说明：`preflight.py` 的 `status` 字段描述材料在场状态
  （材料未全提供即 `blocked_by_external_materials`），exit code 才是
  契约判定——默认 unsigned 契约不要求材料，exit 0 即通过；仅当显式
  `--require-materials` 时缺材料才 exit 2 阻断。hmharness 原报告将
  默认运行概括为 "status ok"，与 JSON 原文字面值不符（实际 exit 0、
  status `blocked_by_external_materials`）；本 README 以 JSON 原文
  为准，默认契约"通过"的判定依据是 exit 0 + failures=[]（见 §7）。
- 本次结果与 M14-194 的"缺材料即阻断、不泄密"契约一致：fail-closed
  阻断而非报错失败，材料内容零读取零回显。

## 7. 对 hmharness 原报告的两处事实更正

hmharness 原报告（`VERIFICATION-REPORT.md`，gitignored）整体与工件
一致，但有两处概括性措辞与工件原文不符，本 README 以 gitignored
工件（JSON/布局 dump/源码）为准予以更正：

1. **preflight 默认运行的 status**：原报告写 "exit 0,status ok"；
   `preflight-default.json` 原文为 `exit_code: 0` 且
   `status: "blocked_by_external_materials"`（该字段是材料状态描述，
   默认契约下不构成失败）。§6 表格与口径说明按 JSON 原文记录。
2. **Android 行的布局背书**：原报告称 Android 行在截图上被截断但
   "布局 JSON 已完整落盘 / 文本级证据以 layout-settings.json 为准"；
   实际 `layout-settings.json` 全文件零 `Android` 字样，布局 dump 与
   截图一样只覆盖 PWA/Harmony 两行。Android 原生行仅有源码级证据
   （§5.2），本次回归对该行无渲染级证据，不作任何已验证声明。

## 8. 网络边界（诚实声明）

- 全程仅对生产公网 `https://ndtool.cn/aios/` 做匿名只读 GET
  （health/version）；未发送任何凭据；POST/PUT/DELETE 零次，无任何
  数据变更。
- 401 是公网认证开启下的预期行为，构成"匿名只读被正确拒绝"的正向
  证据；凭据态行为（登录后 200）未验证——超出本次"无凭据"边界。

## 9. 清理（hmharness 执行）

- 应用 force-stop（恢复前台态）；安装体按验证语义保留，未做数据
  变更。任务级 VPN（xray）使用后已关闭。执行 worktree 与分支保留
  供复核。

## 10. 诚实边界

1. debug 签名仅为 SDK debug 身份的安装载体，**不是 AGC 发布签名**；
   发布签名链路仍处 unsigned 边界（§6 preflight 佐证），Harmony
   分发硬阻塞不变，`production_ready=false` 口径不变，release
   approval 仍是 human-only 门。
2. Android 原生渠道行无渲染级证据（§5.2/§7）：截图与布局 dump 均未
   覆盖该行，只有 `DownloadPane.ets` 源码级静态文本事实。
3. 网络验证仅覆盖匿名只读 GET；凭据态未验证（§8）。
4. 模拟器为 x86 Beta1 镜像，与真机/正式版行为可能存在差异。
5. 单时点证据非持续保证；任何 token/密钥/密码/secret 值绝不写入；
   本地绝对路径不入库；原始运行工件位于 gitignored `.verify/`
   目录，不入库、不回显原文。
6. 本切片零生产服务与设备操作：不 rebuild、不 sign、不 install、
   不 start/stop、不变异模拟器、不发网络请求——只读取 hmharness
   已落盘工件并入库本 README 与两处台账条目。
7. 本切片交付为 branch + 单 local commit 并推送远端分支；supervisor
   审查与 remote 发布（PR 开合/合并/release 门禁）在其后进行。

## 11. 证据目录（gitignored，`.verify/m14-195-harmony-current-main-regression/`）

- `VERIFICATION-REPORT.md`（hmharness 原报告；§7 所列两处以本
  README 为准）
- `release-build.json`（构建事实：exit/status/artifact size/SHA256/steps）
- `preflight-default.json`（默认契约，exit 0）
- `preflight-require-materials.json`（要求材料契约，exit 2 blocked）
- `layout-home.json` / `layout-download.json` / `layout-settings.json`
  （uitest dumpLayout 原始文本）
- `screenshot-home.jpeg` / `screenshot-download.jpeg` /
  `screenshot-settings-download.jpeg`（1320×2856 截图）

## 12. 本切片自身的验证（docs-only 口径，真实执行）

- 文档守卫（canonical `.venv` Python，零网络）：
  `services/api/tests/test_versioning_rollback.py::test_version_sources_in_sync`
  与
  `services/api/tests/test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  全部通过。
- `git diff --check` 对 base 与 HEAD 干净。
- 新增行扫描（secret 形态 / 本地绝对路径（盘符或根路径形态，正反
  斜杠变体；http(s) URL 中 `s:/` 形态为已知误报排除）/ U+FFFD）
  **0 真实命中**；heuristic 仅命中描述扫描口径本身的自指性关键词
  （如"secret""绝对路径"字样），无任何 secret 值或路径值。
- changed files 恰为三个 docs 文件；README 为纯新增，台账为按
  reverse-order 风格的纯插入。

## tracked 改动边界

本切片 tracked 改动：本 README（新增）+ `docs/CHANGELOG.md` /
`docs/PROJECT_STATUS.md`（各一条 M14-195 条目）。`apps/harmony`、
`tools/harmony_release`、`tools/harmony_mock` 及任何生产源码、测试、
workflow、Docker、公共边缘零改动。单 local commit 并推送远端分支；
supervisor 审查与 remote 发布（PR 开合/合并/release 门禁）在其后
进行。
