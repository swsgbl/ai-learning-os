# M14-36 Web 验收工具进程生命周期修复（Windows 孤儿进程树精确回收）

- 仓库基点：分支 `fix/m14-36-acceptance-process-cleanup` 基于 `main@3a5c095`，本 Claude 开发回合独占 worktree、单 local commit、不 push
- 原始证据：gitignored `.verify/m14-36-acceptance-process-cleanup/`（results.json + screenshots/01-initial.png、02-result.png；无 build-stderr.log——构建一次通过）——不入 git，本 README 仅摘录判定事实

## 缺陷（监督者盘点实证）

`infra/verify_web_livekit_client.py` 旧版 `start_web` 以 **npm 包装链**（mise.exe → cmd.exe npm.cmd → node）作为 Popen 对象，`main` 的 finally 只对该包装器 PID `terminate()`/`kill()`。**Windows 上进程终止不级联子进程**——`next start` 的 node.exe 与中间 shim 全部存活为孤儿；跨次验收累计 **26 个 node.exe/mise.exe 孤儿进程**，锁死 worktree 文件句柄并累积资源（监督者于既往验收后盘点发现并已清理；本任务开工时孤儿扫描为 0，代码级缺陷独立成立）。

## 修复（验收工具生命周期，不动任何生产服务）

| 面 | 旧 | 新 |
|---|---|---|
| 启动 | `[npm(), "run", "start", "--", "-p", <port>]`（npm 包装链为 Popen 父进程） | `resolve_node_executable()` 以 `node -p process.execPath` 解析**真实 node 可执行文件**（mise/npm shim 只做短命引导），直接 Popen `[<真实node>, <next_bin()>, "start", "-p", <port>]`——Popen PID 即 next start 的 node 宿主进程本身，无中间 shim |
| next CLI 入口 | 无（经 npm run） | `NEXT_BIN_CANDIDATES`（npm workspaces 提升 → 仓库根 `node_modules/next/dist/bin/next` 优先，兼容 `apps/web/node_modules/...`）+ 惰性 `next_bin()`：都缺 → ENV-BLOCKED fail-closed |
| 停止 | `proc.terminate(); proc.wait(15); 超时 proc.kill()`（只打包装器单 PID） | `stop_process_tree(proc)`：**有界、精确、只针对本脚本 Popen 出的 PID 树**——Windows `taskkill /PID <pid> /T /F`（/T 恰沿该 PID 树递归）；POSIX `start_new_session=True` 自成进程组（pgid==pid）→ SIGTERM → 有界宽限 → SIGKILL；进程已退出零动作；最后统一有界 wait 回收句柄 |
| 红线 | — | **绝不按端口或宽进程名扫描/杀**（无 taskkill /IM、pkill、killall、netstat、Get-Process、wmic）——杜绝误伤生产进程 |

`npm()`/`build_web()` 不变（构建是同步有界的，无孤儿面）。

## 测试（TDD，全 mock 零真实杀进程）

新增 `services/api/tests/test_verify_web_livekit_client.py` **20 项**（monkeypatch subprocess/os，绝不真正执行 taskkill/killpg/Popen，不触网不装依赖不读 secret）：

- `resolve_node_executable` 3 项：探测 argv 恰为 `["node", "-p", "process.execPath"]`（check=False、capture_output、有界 timeout）；非零退出码/空输出/找不到 node/探测超时 → SystemExit fail-closed
- `next_bin` 3 项：候选覆盖 workspaces 根 + apps/web 双路径；取第一个存在者；全缺 → SystemExit
- `start_web` 3 项：argv[0]==真实 node（断言非 .cmd/.bat/mise.exe、不含 npm）；POSIX `start_new_session=True`、Windows 不设
- `stop_process_tree` 6 项：Windows 恰一次 `["taskkill", "/PID", "<pid>", "/T", "/F"]`（check=False、capture_output、timeout≤60）且零 killpg；已退出（双平台）零动作；POSIX killpg `[(pid,15),(pid,9)]`（pgid==Popen pid）；优雅退出仅 `[(pid,15)]`；组已消亡容忍且 kill() 兜底
- 文本契约 5 项：源码含 `process.execPath`/`start_new_session`/`taskkill`//T//F/`killpg`/`stop_process_tree(proc)` 锚点；禁词（/IM、pkill、killall、netstat、Get-Process、Stop-Process、Get-NetTCPConnection、wmic）零出现；旧缺陷形态 `npm(), "run", "start"` 必须消失

TDD RED→GREEN 实证（含两次返工：`os.name` 全局 monkeypatch 在 Windows 宿主上破坏 pathlib → 引入 `_is_windows()` 可注入判定；Windows os 模块无 killpg → `raising=False`）。

## 验证

| 门 | 结果 |
|---|---|
| 聚焦 `pytest services/api/tests/test_verify_web_livekit_client.py` | **20 passed** |
| 全量 `pytest services/api/tests`（修复后终版代码） | **2826 passed / 0 failed / 33 skipped**（3:34；中途一次过渡态运行的 4 failed 全为本次随即修正的 NEXT_BIN→next_bin() 测试同步项，非 main 既有失败） |
| `ruff check services/api infra/verify_web_livekit_client.py` | All checks passed |
| `py_compile`（脚本 + 测试） | 通过 |
| `git diff --check` | 干净 |

## 真实受控浏览器验收（一次执行，生产栈零重启）

命令：`AIOS_OUT=.verify/m14-36-acceptance-process-cleanup "D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe" infra/verify_web_livekit_client.py` → **verdict=passed，13/13 checks，exit 0**：

- 生产 API(:8000)/LiveKit(:7880)/DB 复用当前容器，只读探测 + 合法业务端点（验收用户 register/login/room token）；**零服务重启**
- 本分支构建 → **真实 node 直启** next start（空闲端口）→ UI 登录 → /voice 检测：token/connect/data/**mic**/cleanup 五步全 `passed`（fake 麦克风音轨真实发布到生产 LiveKit）；`ws_url=ws://127.0.0.1:7880`；DOM 不含 JWT；检测窗口 console/pageerror 零错误
- 受控条件同 M14-35 R2：Chromium `--allow-loopback-in-peer-connection`（本机 loopback LiveKit 部署的受控验收条件，非生产用户默认）

### 孤儿进程断言（本缺陷的核心验收）

| 扫描 | 方法 | 结果 |
|---|---|---|
| 验收前基线 | `Get-CimInstance Win32_Process`（Name=node.exe/mise.exe/cmd.exe 且 CommandLine 含本 worktree 路径） | **FOUND 0** |
| 脚本退出后 | 同上 | **FOUND 0** |

旧缺陷形态下（npm 包装链 + 单 PID terminate）脚本退出后必然遗留带 worktree 路径的 `next start` node.exe；修复后**退出即零残留**——taskkill /PID /T /F 恰好回收 Popen PID 的进程树，未触碰任何生产进程。

## 诚实边界

1. 本修复只覆盖**验收工具自建 web 进程**的生命周期；生产 :3011 web 容器与其余生产服务零触碰
2. POSIX 组杀路径在本 Windows 开发机只经全 mock 契约测试验证，未在 POSIX 真机跑过
3. 单次真实验收（13 checks）+ 前后两次孤儿扫描证明本次退出零残留；不构成跨多次连跑/异常路径（脚本被外部强杀、node 崩溃半途）的穷尽证明——那些路径下孤儿面本就最小化（Popen PID 即最终进程，无中间 shim 链）
4. 验收通过仍属 M14-35 R2 的受控拓扑口径（loopback flag）；默认拓扑 ICE 间歇性失败未消除，为独立生产阻塞项
5. **不构成 `production_ready=true` 依据，全局 `production_ready=false` 不变**；全程不读取/打印任何 key/token/env 值
