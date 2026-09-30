# M14-187：Harmony auth smoke 驱动器 locator 缺陷修复 + current-main 模拟器回归（Round 2）

## 结论（先说结果）

| 项 | 结果 |
|---|---|
| 分支 | `harmony/m14-187-current-main-regression`（worktree `m14-187-harmony-current-main-regression`，基点 `a1adb03`，单 local commit，不 push、不开 PR） |
| 根因 | `auth_smoke.py::find_url_input()` 旧实现按「最长 URL 文本」启发式选节点且**不检查类型**——设置 Tab 中 DownloadPane 只读 `Text('服务地址: ${baseUrl}')` 恒比 TextInput 内容长，抢占输入定位，5 次尝试全部 `settings_input_mismatch` |
| 修复 | `find_url_input` 改为**严格 typed 定位**：① 优先选 `type=="TextInput"` 且 text 含 URL 的第一个节点（只读 Text 无论多长永不入选）；② 兼容 fallback：无 URL TextInput 时取树序第一个 TextInput（空输入场景——URL 字段是设置面板最顶 TextInput，M14-84 真实 dump 实证） |
| backend_smoke.py | **核查后无同缺陷，零改动**——其 `find_input_node` 本就是严格 `type=="TextInput"` 实现，Text 永不可能入选 |
| 回归单测 | **新增 4 个**（不弱化既有断言）：更长只读 Text 并存时选 TextInput、无 typed TextInput 返回 None、空输入 fallback 到第一个 TextInput、URL Text 出现在 TextInput 之后不抢占 |
| 聚焦测试 | 4/4 passed |
| 全量 release 套件 | **683 passed / 1 skipped**（1 skipped 为 Windows FIFO 既有口径；基线 679 → 683 = 净增 4，恰为新测试数） |
| mock 契约 | **85/85 passed, 0 failed**（M13 58/58 + M14-89 auth 27/27） |
| ruff | 修改文件 E4,E7,E9,F 全过；全规则基线对照 **HEAD=221 → worktree=220（delta=-1，零新增）** |
| compileall | 两修改文件通过 |
| HAP 复核 | size=**235172**，SHA256=**4DA92E1F37F80B91202E4B618255E69E6CC8F1CEDB984D0C8FAD2F037615AF4B**（MATCH，复用已构建产物） |
| attempt 3 真跑 | **预执行基础设施失败**（server_start_failed：系统 python 缺 sqlalchemy；0 stage 执行）——如实归档，不与 3b 合并 |
| attempt 3b 真跑 | **12 stage = 11 ok + 恰 1 not_run**（`auth_off_local`，reason=`auth_phase_skip`）；failure/request/toolchain/warnings **全 0**；cleanup/uninstall 完成 |
| 清理 | backend 进程停、60801 端口释放、无 python 残留、`bm dump -a` 65 bundle 中 ailearningos **零匹配**（卸载闭环）、Pura 90 (127.0.0.1:5555) 保持在线 |

## 1. attempt 1 根因（前一线程真实失败，证据保留）

attempt 1（`.verify/m14-187-harmony-current-main-regression/auth_smoke_launcher*.json|txt`）：
12 steps = **4 ok / 1 failure / 7 not_run**（summary `{'failure':1,'not_run':7,'ok':4,'total':12}`）。

- `host_auth_contract`/`install`/`start` ok；
- **`settings_ui` failure**：`failures` 含 5 个 `settings_input_mismatch`（attempt 1–5）——驱动器向定位坐标发 `uitest uiInput inputText` 后复 dump，TextInput 内容始终未变成目标 URL；
- 其后 `home_401_unauth`…`background` 7 步全部 `previous_step_failed` not_run；
- `uninstall` ok（清理未被失败污染）。

根因闭环（diag attempt 2 布局证据）：设置 Tab 中 URL **TextInput** 与 DownloadPane 只读 **Text**（`服务地址: ${baseUrl}`，`${baseUrl}` 渲染后恒比输入框自身内容长——前缀 `服务地址: ` 5 字符 + 完整 URL）并存；旧 `find_url_input` 遍历 `(text,bounds)` 对、选「看起来像 URL 的最长文本」，只读 Text 的文本更长 → 抢占定位 → 击键落在 Text 上 → 输入框保持旧值 → mismatch。产品 Settings 链路本身正常。

## 2. diag attempt 2 证明（前一线程，证据保留）

`diag_attempt2/01–05_*.json` 五步布局逐步证明**产品链路正常、缺陷在驱动器**：

| 步骤 | URL TextInput | 只读 Text | 结论 |
|---|---|---|---|
| 01_baseline | （不可见） | `服务地址: http://127.0.0.1:8000` | 初始态 |
| 03_after_input | `http://10.0.2.2:60880/` | `服务地址: http://127.0.0.1:8000` | **手工定位可输入**——产品输入框功能正常 |
| 05_after_save | `http://10.0.2.2:60880/` | `服务地址: http://10.0.2.2:60880/`（+`已保存: http://10.0.2.2:60880/`） | **保存链路完整**——下载面板镜像新 URL |

即：typed 定位（直接找 TextInput 节点）即可正确输入并保存；失败只发生在驱动的文本启发式定位上。

## 3. Round 2 修复（本切片 tracked 代码改动）

### 3.1 `tools/harmony_release/auth_smoke.py`

- 新增 `URL_INPUT_TYPE = "TextInput"` 常量；
- `find_url_input(typed: Sequence[Tuple[str,str,str]])` 重写：严格 `ntype == URL_INPUT_TYPE` 过滤；URL 命中即返回其中心坐标与文本；遍历中同时记录树序第一个 TextInput 作 fallback；无任何 TextInput 返回 None；
- 两个调用点 `_drive_settings_url` 内 `find_url_input(layout_texts(...))` → `find_url_input(layout_typed(...))`（typed 三元组流）；
- **不改任何生产 ETS/UI 代码**，只修测试工具。

### 3.2 `tests/harmony_release/test_auth_smoke.py`（+94 行）

新增 `_settings_node`/`_m14_187_settings_layout` 构造器（复刻 attempt 1 真实缺陷布局形态）+ 4 测试：

1. `test_find_url_input_prefers_typed_textinput_over_longer_readonly_text` —— TextInput(`http://10.0.2.2:60880/`) 与更长只读 Text(`服务地址: http://10.0.2.2:60880/`) 并存 → 选 TextInput（**直接复现并锁死 attempt 1 缺陷**）；
2. `test_find_url_input_returns_none_without_typed_textinput` —— 只有 Text 节点（含 URL）→ None（不再误选只读文本）；
3. `test_find_url_input_empty_input_falls_back_to_first_textinput` —— 空输入（无 URL 文本）→ 树序第一个 TextInput；
4. `test_find_url_input_ignores_url_text_after_textinput` —— TextInput 在前、URL Text 在后 → 仍选 TextInput。

## 4. attempt 3（预执行基础设施失败，如实归档）

用仓库规范解释器路径之外的系统 python 调 launcher → 进程内 import FastAPI 后端时 `server_start_failed`（RuntimeError：No module named sqlalchemy）。**0 stage 执行**，设备零接触。证据：`attempt3/auth_smoke_launcher.json`（status=server_start_failed）。

处置：launcher 无子进程/解释器参数机制（后端在进程内 import 真实 `app.ops.auth_smoke_server.AuthSmokeServer`），控制解释器的受支持方式即调用 launcher 的 python 本身；AGENTS.md 验证章节规定 `.venv\Scripts\python.exe` 为仓库规范解释器——**属文档化用法而非代码 workaround**，故零代码改动，改用 venv 解释器重跑并独立编号 attempt 3b。

## 5. attempt 3b（真跑，验收达成）

调用：`"<主仓>/.venv/Scripts/python.exe" -m tools.harmony_release.auth_smoke --repo-root <worktree> --target 127.0.0.1:5555 --evidence-dir attempt3b --confirm-mutation`（先 plan dry-run 后真跑；wrapper `.cmd` 后台执行，exit code 哨兵 `exit=0`）。

| stage | 结果 | 备注 |
|---|---|---|
| host_auth_contract | ok | 任务专属 loopback backend（127.0.0.1:60801）auth on 契约 |
| install | ok | unsigned HAP 复用（见 §6） |
| start | ok | aa start EntryAbility |
| **settings_ui** | **ok** | **修复验证点**：typed 定位命中 TextInput，输入+保存一次通过（attempt 1 此处 5×mismatch failure） |
| auth_off_local | not_run | reason=`auth_phase_skip`——本轮 expect_auth=on，off 阶段按设计跳过（唯一允许的 not_run） |
| home_401_unauth | ok | 匿名 401 契约 |
| wrong_password | ok | 错误密码 401 |
| login_and_refresh | ok | 登录+token 刷新 |
| cold_restart | ok | force-stop + 重启态保持 |
| logout | ok | 登出 |
| background | ok | 后台/前台切换 |
| uninstall | ok | 清理闭环 |

**summary `{'failure':0,'not_run':1,'ok':11,'total':12}`**；`request_failures=[]`、`toolchain_failures=[]`、`warnings=[]`、`cleanup_attempted=True`、`mutation_performed=True`。证据：`attempt3b/`（launcher JSON×2、stdout、stderr 空、plan、exit 哨兵、auth_smoke_on.json、run_attempt3b.cmd）。

## 6. HAP 产物复核（复用已构建产物，未重建）

`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`：size=**235172**、SHA256=**4DA92E1F37F80B91202E4B618255E69E6CC8F1CEDB984D0C8FAD2F037615AF4B**——与任务书给定值精确 MATCH。`release_build_stdout.json` 留存构建事实。

## 7. 验证链（本切片真实执行）

- 聚焦新测试 4/4；
- `python -m pytest tests/harmony_release -q`：**683 passed, 1 skipped**（23.32s；较 M14-183 基线 679+1 恰 +4 = 新测试数，对账自洽）；
- `python tools/harmony_mock/test_contract.py`：**85/85 passed, 0 failed**；
- ruff：修改文件 `--select E4,E7,E9,F` 全过；全规则 HEAD vs worktree 基线对照 **221→220（delta=-1）**——零新增，且新代码用 `Optional[...]`/`Tuple[...]` 与文件既有风格一致；
- `python -m compileall -q` 两修改文件通过；
- `git diff --check` 干净。

## 8. 清理证明

- backend：launcher 退出即停（JSON 记录 pid 51760，事后核验 `Get-Process 51760` 不存在、60801 端口 FREE、无 auth_smoke 相关 python 残留）；
- 设备：`bm dump -n com.ailearningos.app` 报错（bundle 不存在）；阳性对照 `bm dump -a` 正常返回 65 个 bundle、ailearningos **零匹配** → 卸载闭环；
- 任务临时文件：`.patch_m14_187*.cjs`、`auth_smoke_server.log`（0 字节残留）、`.tmp_*.py` 全部删除；
- **Pura 90 (127.0.0.1:5555) 保持在线**（任务边界）；127.0.0.1:15566 为其他会话的 KaihongOS 转发，未触碰。

## 9. tracked 改动边界

| 文件 | 改动 |
|---|---|
| `tools/harmony_release/auth_smoke.py` | typed locator 修复 |
| `tests/harmony_release/test_auth_smoke.py` | +94 行（4 回归测试 + 2 构造器） |
| `docs/evidence/m14-187-harmony-current-main-regression/README.md` | 本文件 |
| `docs/CHANGELOG.md` / `docs/PROJECT_STATUS.md` | 各一条 M14-187 条目 |

`apps/harmony/`（生产 ETS/UI）、`tools/harmony_mock/`、backend_smoke.py 及任何生产源码**零改动**。

## 10. 诚实边界

1. **unsigned + simulator + loopback，`production_ready=false`**：本次回归只证明 unsigned HAP 在 loopback 模拟器上 auth 契约链路可用；签名 debug identity/AGC 发布材料仍缺失，Harmony 生产分发硬阻塞不变；release-approval 仍 human-only。
2. attempt 1（前一线程）与 attempt 3（预执行失败）证据**原样保留**在 `.verify/m14-187-harmony-current-main-regression/`（attempt1 顶层 + attempt3/ + diag_attempt2/），未被 attempt 3b 覆盖或改写。
3. `auth_off_local` not_run 是本轮 expect_auth=on 的设计跳过（auth_phase_skip），**不构成** auth-off 链路已验证的声明。
4. 单时点模拟器证据非持续保证；任务书禁止的 push/PR/签名/AGC/真机/Android USB/CC Switch/代理全程零接触。
5. 原始运行工件位于 gitignored `.verify/`，不入库、不回显 secret；launcher JSON 中 auth_secret 已脱敏（`<configured>`）、seed_password `[REDACTED]`。

## 11. 证据目录（gitignored `.verify/m14-187-harmony-current-main-regression/`）

- 顶层：`auth_smoke_launcher.json|stdout.json|stderr.txt`（attempt 1）、`auth_smoke_server_attempt1.log`、`release_build_stdout.json|stderr.txt`、`diag_layout_inspect.py`、`diag_settings_chain.py`
- `attempt3/`：`auth_smoke_launcher.json|plan.json`（server_start_failed 归档）
- `attempt3b/`：`auth_smoke_launcher.json|stdout.json|stderr.txt|plan.json|exit_code.txt|auth_smoke_on.json|run_attempt3b.cmd`
- `diag_attempt2/`：`01_baseline.json` … `05_after_save.json`
