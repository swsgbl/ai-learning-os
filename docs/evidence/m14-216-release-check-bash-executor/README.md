# M14-216 release-check bash executor 证据

## 1. 范围与基线

- Worktree：`ai-learning-os-worktrees/m14-216-release-check-bash-executor`
- 分支：`fix/m14-216-release-check-bash-executor`
- 基点：远端 current main
  `8d39772c5916d69651bb7ca653b4cf5400a67d8e`
- 创建后核验：`git rev-parse HEAD` 等于上述 SHA，`git status --porcelain`
  为空。
- 远端只读复核（本切片执行）：
  - `gh api repos/swsgbl/ai-learning-os/commits/main` 返回 SHA
    `8d39772c5916d69651bb7ca653b4cf5400a67d8e`。
  - main CI run `36998129756`（run_number 747，push@main@8d39772）
    为 completed/success。
  - 五个 job 全部 success：Docker、Android、Web、Release tools、API。
  该 CI 结果绑定的是修复前基线；本切片未 push，因此不声称修复分支远端
  CI 已验证。

## 2. 问题与实现

M14-215 在 `dcb8d380` 的真实失败为 `release-check-isolated` exit 1、
9/10 pass：唯一失败 `api-test` 内 pytest 25 failed / 5643 passed /
47 skipped。聚焦失败共同指向 Windows PATH 把 `bash` 解析到
`C:\WINDOWS\system32\bash.EXE`；该 WSL launcher 执行时失败：
`execvpe(/bin/bash) failed: No such file or directory`。

本切片不修改 WSL、发行版、PATH、Docker Desktop 或任何容器，改为在产品
代码内 fail-closed 解析 bash：

- 新增 `services/api/app/ops/bash_executor.py`。
- `AIOS_BASH` 显式优先；路径必须存在且可执行，无效或指向 WSL launcher
  时直接失败，不回退。
- POSIX/Linux/CI 保持 PATH 查找语义。
- Windows 排除 `System32` / `Sysnative` / `WindowsApps` 的 WSL launcher；
  按完整 PATH 顺序选择后续原生 bash，并可从 git 安装布局及常规 Program
  Files Git 根推导，不硬编码本机用户路径。
- `release-check-isolated` 在路径护栏后、建工作区/迁移/启动 API/执行
  门禁前做一次 bash preflight；不可用时输出单个明确 exit 2 错误。
- 解析结果通过 `AIOS_BASH` 注入 `api-test` 子进程环境；五个原先直接
  `shutil.which("bash")` 的测试模块改为使用同一产品解析器。

## 3. 聚焦验证

命令（仓库根）：

```powershell
services\api\.venv\Scripts\python.exe -m pytest -q `
  services/api/tests/test_bash_executor.py `
  services/api/tests/test_release_check_isolated.py `
  services/api/tests/test_release_checklist.py
```

结果：**72 passed, 1 warning**，exit 0。覆盖显式 override、override 不存在、
System32 launcher 拒绝/降级、Windows PATH/Git Bash 顺序、POSIX PATH 回归、
真实 bash 子进程执行、isolated preflight 副作用为零、解析结果传入
full gate。

M14-215 失败的五个模块重跑：

```powershell
services\api\.venv\Scripts\python.exe -m pytest -q `
  services/api/tests/test_smoke_search_script.py `
  services/api/tests/test_smoke_voice_local_script.py `
  services/api/tests/test_smoke_voice_cloud_script.py `
  services/api/tests/test_voice_local_scripts.py `
  services/api/tests/test_release_candidate.py
```

结果：**180 passed, 2 skipped, 1 warning**，exit 0。

Ruff：full release-check 的 `api-lint` 门为 `All checks passed!`。

## 4. full release-check 真实重跑

从当前 worktree 建立隔离依赖：`uv venv`（CPython 3.13.13，59 packages）
与根目录 `npm ci`（411 packages；既有 `unrs-resolver` postinstall 被
allowScripts 阻止的警告保持不变）。随后执行：

```powershell
cd services/api
.\.venv\Scripts\python.exe -m app.ops.cli release-check-isolated `
  --workdir "<worktree>\artifacts\m14-216-isolated" --json
```

结果：**exit 0 / all_green=true / 10/10 pass**。

- api-lint：pass。
- web-lint：pass。
- web-typecheck：pass。
- web-build：pass。
- api-test：pass，**5689 passed, 36 skipped, 1 warning in 331.68s**。
- migration：pass，`current == head == 0027_audit_chain`。
- backup：pass，30 tables / 1 file。
- voice：pass，local 合成 `audio/wav` 17324 bytes。
- license：pass。
- e2e：pass，5 steps / 1267 ms。

报告：`artifacts/m14-216-isolated/release-check-isolated.json`（gitignored
本地审计产物），2485 bytes，SHA256：
`8a1747edfc2789f664c3cd00bb98842afb97f509e0b65d5e5792192945446299`。

运行中仍出现 M14-215 已记录的 Windows 控制台 reader-thread GBK
`UnicodeDecodeError`（两次线程异常）；主命令自然完成、exit 0、JSON 完整
写出，10/10 结果不受影响。本切片不把该控制台编码噪声改写为通过依据。

## 5. 边界与剩余 blocker

- 本切片只做本地代码、测试与 docs；不 push、不开 PR、不合并。
- 未查询、修复、重启或修改 Docker Desktop/本机容器；未修改 WSL、系统
  PATH、生产服务、secrets、CC Switch 或本地代理；未执行 ADB、真机或
  模拟器操作。
- release-check 的 10/10 只表示本修复分支的隔离本地 full 门禁通过，
  不是 production readiness，不授权发布；`production_ready=false` 不变。
- provider-smoke blocker 未被本切片修复：M14-209 真实聚合仍是 voice
  pass、search/llm fail。
- release-approval 仍是 human-only 缺席；turn-tls 仍是 optional 缺席。
- long-soak 仍是历史 pass 窗口，不因本切片变成新的生产窗口证据。
