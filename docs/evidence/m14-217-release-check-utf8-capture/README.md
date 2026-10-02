# M14-217 release-check UTF-8 capture 证据

## 1. 范围与基线

- Worktree：
  `ai-learning-os-worktrees/m14-217-release-check-utf8-capture`
- 分支：`fix/m14-217-release-check-utf8-capture`
- 基点：`4ac69889feb4ccbd0492b26e69f1378702833f69`
- 创建阶段的远端只读复核已确认：该 SHA 是当时 current main / PR #305
  merge，tree 为 `a9b37b86f4a8d6b0e49d5975c968fdd4a0fb7529`，main CI run
  `37019038982` 为 5/5 success。该 CI 绑定修复前基线；本切片未 push，
  不声称修复分支远端 CI 已验证。

## 2. 问题与实现

M14-216 的 full isolated 门禁虽已 10/10，但真实运行仍出现两次 Windows
reader-thread GBK `UnicodeDecodeError`。旧捕获点使用 `text=True` 时按进程
locale 解码；在 zh-CN Windows 上为 cp936/GBK，遇到非 GBK 字节会让
`communicate` reader 线程抛异常，并把捕获输出退化为 `None`。

本切片在 `services/api/app/ops/release_check.py` 新增统一入口：

- `run_captured(...)` 固定 `encoding="utf-8"`、`errors="replace"`，
  保留 `cwd`、`env`、`timeout` 与 `check=False` 语义。
- `utf8_replacement_note(...)` 在输出含 U+FFFD 时返回
  `OUTPUT_REPLACEMENT_NOTE`（「子进程输出含非 UTF-8 字节，已按 U+FFFD
  替换」）。
- `_execute_command`、`check_migration_current`、
  `run_alembic_upgrade` 三个原裸文本捕获点统一改用 `run_captured`。
- command detail、migration current 失败/通过 detail、isolated migration
  detail 与 uvicorn log tail 均会追加 replacement note。
- `redact_secrets` 仍在最终 evidence/detail 边界执行；uvicorn 启动、
  日志重定向、terminate/kill 清理语义不变。

同时修复两个 API 测试自身的 Windows 子进程编码契约：它们按 UTF-8 解码
被测 Python CLI 输出并断言中文消息，但没有声明子进程 I/O 编码。现在仅对
这两个测试子进程显式设置 `PYTHONIOENCODING=utf-8`；没有设置外层全局
环境，也没有用外层 `PYTHONIOENCODING` 掩盖 release-check 捕获问题。

## 3. 聚焦验证

命令与结果：

```powershell
cd services/api
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q tests/test_release_check_utf8_capture.py
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_release_check_isolated.py `
  tests/test_release_checklist.py `
  tests/test_release_check_utf8_capture.py
```

- Ruff（services/api）：`All checks passed!`
- 新增 UTF-8 capture 契约：**8 passed**
- release-check 三件套聚焦回归：**73 passed / 1 warning**
- 两个 Windows CLI 子进程测试：**2 passed / 1 warning**

新增测试覆盖 UTF-8 中文保真、GBK/非法字节替换、reader 线程不抛
`UnicodeDecodeError`、空/长/失败进程、replacement note 进入 evidence、
secret 脱敏、timeout/env/cwd 合约、isolated migration 成功/失败/超时、
migration current 与 uvicorn log tail。

## 4. full release-check 真实执行

在当前 worktree 内新建隔离依赖：`uv venv`（CPython 3.13.13，59 packages）
与根目录 `npm ci`（411 packages；既有 `unrs-resolver` postinstall 被
allowScripts 阻止的警告保持不变）。未设置外层 `PYTHONIOENCODING`。

第一次真实运行使用 `artifacts/m14-217-isolated/`，结果诚实失败：
**exit 1 / all_green=false / 8/10 pass**。失败项为 `web-build` 与
`api-test`；api-test 为 2 failed / 5695 passed / 36 skipped / 4 warnings，
detail 已出现 replacement note。聚焦复现确认两个 API 失败来自上述测试
子进程按 GBK 输出中文而被严格 UTF-8 解码；`web-build` 随后单独真实通过，
最终 full 复跑也通过，因此不对首次构建失败作无证据归因。失败报告保留：
2813 bytes，SHA256
`2f409df9ba35bf4eb59a8c5eae5a389524ec9ddf5c6a20ecd5d4b73dc75d664e`。

修正后用新工作区 `artifacts/m14-217-isolated-r2/` 复跑：

```powershell
cd services/api
.\.venv\Scripts\python.exe -m app.ops.cli release-check-isolated `
  --workdir "<worktree>\artifacts\m14-217-isolated-r2" --json
```

结果：**exit 0 / all_green=true / 10/10 pass**。

- api-lint：pass。
- web-lint：pass。
- web-typecheck：pass。
- web-build：pass。
- api-test：pass，**5697 passed / 36 skipped / 2 warnings in 320.20s**。
- migration：pass，`current == head == 0027_audit_chain`。
- backup：pass，30 tables / 1 file。
- voice：pass，local 合成 `audio/wav` 17324 bytes。
- license：pass。
- e2e：pass，5 steps / 1365 ms。

最终报告：`artifacts/m14-217-isolated-r2/release-check-isolated.json`
（gitignored 本地审计产物），2489 bytes，SHA256：
`98a58ed4cb720b466767b086c25aa40a016688062a795f39b33ae3780943f990`。
报告解析复核：`all_green=true`、`passed=10`、`total=10`、
`failed_ids=[]`；最终 JSON 的 check detail 不含 U+FFFD，因此没有
replacement note。两次 full 命令的外层执行输出均未出现 reader-thread
`UnicodeDecodeError`。实际 bash resolver 结果：
`C:\Users\hongfu\AppData\Local\hermes\git\bin\bash.exe`。

## 5. 边界与剩余 blocker

- 本切片只做本地代码、测试与 docs；不 push、不开 PR、不合并。
- 未查询、修复、重启或修改 Docker Desktop/本机容器；未修改 WSL、系统
  PATH、生产服务、secrets、CC Switch、本地代理或设备。
- release-check 10/10 只表示本修复分支的隔离本地 full 门禁通过，不是
  production readiness，不授权发布；`production_ready=false` 不变。
- provider-smoke search/llm fail、human-only release-approval、optional
  turn-TLS 与历史 long-soak 窗口边界不变。
