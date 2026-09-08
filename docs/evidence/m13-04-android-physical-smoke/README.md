# M13-04 Android USB 物理设备冒烟 — 验收证据归档

- 日期：2026-09-08
- 分支：`feature/m13-04-android-physical-smoke`（基于 `origin/main@0b125319f8b2ef4f05428d75925e8293c3e8d4a6`，分支内先合并 `origin/main@529b4d4`；PR head `832862bf46da0ac279d9019d682f90b11e6824c3`）
- 状态：**PR #58 已合并**——merge commit `029281639ba645742d9ea089b96f2493774b8b68`（本地 git 可验证；merge commit tree `fb8315da1767eafb1ba114226151633cf8c1d2ac` 与 PR head tree 逐字节一致）
- 原始证据路径：`.verify/m13-04-android-physical-smoke/`（gitignored，不入库；含 `physical-EYFBB22923201473{,-r2,-r3}/` 三次真机运行与 `REPORT.md`）
- 入库证据：本 README（唯一入库文件）
- 结论：模拟器 15/15 stage 与真机 18/18 stage 全 passed；首跑失败（硬编码 swipe 越界 + 设备掉线丢 summary）经两轮评审修正后通过，全过程记录于 `docs/DEVELOPMENT.md` M13-04 节

## 真机验收事实（r3，最终通过运行）

| 项目 | 结果 |
|---|---|
| 设备 | Huawei **MGA-AL00**（`adb devices -l`：product:MGA-AL00 model:MGA_AL00 device:HWMGA-H），屏幕 **720x1600**，USB serial **`EYFBB22923201473`** |
| APK | `app-debug.apk`，sha256 `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`（与 Gradle `assembleDebug` 产物逐字节一致，`--rerun-tasks` 重建不变） |
| 运行命令 | `python -m tools.android_smoke.runner --serial EYFBB22923201473 --device-type physical --apk apps/android/app/build/outputs/apk/debug/app-debug.apk --port 8000` |
| 结果 | `overall_status=passed`、`exit_code=0`、时长 140.7s（started 2026-09-07T20:28:14Z） |
| 阶段 | **18/18 stage 全 passed**：wait-device、setup-adb-reverse、install-apk、clear-app、clear-logcat、start-activity、wait-home、configure-base-url（8）＋ tab-home/study/search/voice/settings（5）＋ search、governance（2）＋ remove-adb-reverse、mock-contract、dump-logcat（3） |
| mock 契约 | **19 expected / 0 unexpected / 19 total**（`mock_requests.log` 逐条可查） |
| logcat | **FATAL 0 / ANR 0 / 本包 crash 0**（4831 行；AndroidRuntime 标签行为 uiautomator 工具进程诊断计数） |
| reverse 清理 | 运行后 `adb -s EYFBB22923201473 reverse --list` 输出为**空**（teardown 验证） |
| summary | `.verify/m13-04-android-physical-smoke/physical-EYFBB22923201473-r3/summary.json`（`device_type=physical`） |

## 关键实现口径

- mock `LoopbackMockServer` 只绑定宿主机 `127.0.0.1`（`ALLOWED_HOSTS` 不变）；真机 transport 为 `adb reverse --no-rebind tcp:<port> tcp:<port>`（设备回环→宿主回环），App 端 base URL 经真实 Settings UI 配成 `http://127.0.0.1:<port>/`，无 LAN 暴露。
- 滚动坐标按 `adb shell wm size` 实测尺寸（Override 优先）百分比换算：1080x2400 模拟器上逐值等价旧硬编码 `(540,1800,540,600,400)`，720x1600 真机换算为 `(360,1200,360,400,400)`（旧 y=1800 越界，首跑失败根因）。
- finalize 韧性：dump logcat 失败（如 USB 掉线）时 dump-logcat 阶段记 failed、stats 置零并标 `unavailable:true`、summary 照常写出——缺失的 logcat 不会变成隐性通过（首跑时设备掉线曾导致 summary 缺席，此为修正根因之一）。

## 本地门禁

- Python（canonical venv `D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`，隔离 `--basetemp`）：`pytest tests/android_smoke -q` → **285 passed, 1 skipped**（skip 为既有 Windows symlink 特权测试），merge 前后各一次均 exit 0。
- Android（`apps/android`）：`gradlew.bat testDebugUnitTest lintDebug assembleDebug --rerun-tasks --console=plain` → **BUILD SUCCESSFUL**；test-results 30 文件 **347 tests / 0 failures / 0 errors / 0 skipped**；lint **0 errors / 19 warnings**；APK 重建 sha256 不变（见上）。
- `git diff --check` clean；模拟器回归（`--device-type emulator`）15/15 stage passed（18/0/18 请求）。

## 远端 CI（gh api 权威事实）

| 运行 | 事件 / head | 结论 | 四项 job |
|---|---|---|---|
| PR CI run `34160683530` | pull_request @ `832862b`（2026-09-07T20:46:39Z 起） | success | API（ruff/pytest/migration）、Android（unit/lint/assemble）、Docker（compose build+healthy+smoke）、Web（typecheck/lint/build）全 success |
| main CI run `34176554925` | push @ `0292816`（2026-09-08T01:24:44Z 起） | success | 同上四项全 success |

## 首跑失败与修正记录（保留为事实，不掩盖）

- r1（`.verify/.../physical-EYFBB22923201473/`）：安装/reverse/Settings 配置/tab 巡检/搜索执行均通过，但 720x1600 上「搜索结果概要」在视口外，搜索阶段等待超时；随后 USB 掉线，`dump_logcat` 抛错导致 summary.json 缺席。
- r2（`…-r2/`）：滚动与 finalize 修正后 search 通过；暴露两个真机缺陷——EMUI 治理页零尺寸 bounds 节点（`pending：3` 的 `[0,0][0,0]）致解析失败、ANR 裸子串匹配把 `fileCanRead:false` 误判 blocking；两项均已修正并有单测锁定。
- r3（`…-r3/`）：全绿（见上表）。

## 已知边界

- mock only：无真实 provider、无生产后端、无生产 DB、无凭据；真机验证为本地 USB 口径，不构成生产放行。
- 不打 git tag、不发 GitHub Release、不部署。
- `production_ready=false` 语义不变。
- 真机 USB 连接存在 `device↔offline` 抖动史（Windows Huawei composite problem code 10 曾阻塞，2026-09-08 恢复稳定 `device` 态后完成验收）；重跑前先 `adb devices` 确认 serial 为 `device` 态。
