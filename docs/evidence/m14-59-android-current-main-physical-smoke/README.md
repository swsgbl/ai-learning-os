# M14-59 Android current-main 真机物理冒烟证据回填（docs-only）

## 结论

supervisor 已于 2026-09-19 04:03:13–04:05:34（GMT+8；UTC
`2026-09-18T20:03:13.939116Z–20:05:34.206432Z`，duration 140.267316 s，
exit_code 0）在验证 worktree `m14-59-android-current-main-physical-smoke`
（detached `main@38de33f`，即 **PR #139 squash（Add Harmony device
read-only preflight）**，接手时 tracked-clean）完成 Android **current-main
物理真机冒烟**，判定 **PASS**：18/18 阶段全 passed、mock 契约 19
expected / 0 unexpected / 19 total、logcat 阻塞计数
（fatal/ANR/本包 crash）全 0、`has_blocking_issue=false`。

验证基线 `main@38de33f` 到本 docs 回填基点 `main@028e4bb`（PR #140
merge）对 `apps/android`、`tools/android_smoke`、`tests/android_smoke`
的区间 diff 为**空**（区间仅 2 提交：`783a383` 只改
`tests/harmony_release/test_verify_signature.py` + `028e4bb` merge）——
Android 源码等价，本冒烟即 current-main 口径。

验证与回填发生在**同一 worktree**：冒烟执行时 worktree detached 于
`38de33f`；本 docs 回填回合在同 worktree 创建分支
`docs/m14-59-android-current-main-physical-smoke`（基于 `main@028e4bb`，
PR #140 merge），docs-only 回填：**零设备运行、零生产触碰、零代码
改动、不 push、不建 PR**；只读取本 worktree gitignored
`.verify/m14-59-android-current-main-physical-smoke/android-smoke-20260919-main-38de33f/`
证据，独立复核后入库。summary.json（3,455 bytes）为权威 runner 结果，
本 README 为该 Android 冒烟唯一入库证据文件。

## 双基线与 docs-only 区间

| 基线 | 角色 |
|------|------|
| `38de33f`（PR #139 squash，Add Harmony device read-only preflight） | 冒烟**验证基线**（验证 worktree detached 于该 commit，接手时 tracked-clean，冒烟全程唯一 HEAD） |
| `028e4bb`（PR #140 merge，deterministic signature tests） | 本 docs 回填分支基点（`main@028e4bb`，分支 `docs/m14-59-android-current-main-physical-smoke`） |

- `38de33f..028e4bb` 共 2 个提交（`783a383` test(harmony) 仅改
  `tests/harmony_release/test_verify_signature.py`、`028e4bb` merge PR
  #140），对 `apps/android`、`tools/android_smoke`、`tests/android_smoke`
  的区间 diff 为**空**——冒烟验证所用 Android 源码与本回填基线完全
  一致，无源码漂移。

## 回填前只读复核（本 docs 回填回合，零设备/零生产）

- APK 独立重哈希（只读，本 worktree
  `apps/android/app/build/outputs/apk/debug/app-debug.apk`，gitignored）：
  SHA-256 `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`、
  **11,454,463 bytes**——与 summary.json `apk_sha256` 一致；该哈希与
  M13-04 / M14-48 / M14-50 已归档 APK 哈希相同（debug 构建可复现，同
  产物、不同执行）。
- `summary.json`（权威 runner 结果）独立复核：SHA-256
  `b480c77409ad919a45c3be942a709c3935e89407151099022edbcfd9a1631dce`、
  **3,455 bytes**；`"status": "passed"` 恰好 **18** 处（对应 18 个阶段）、
  `exit_code` 0、`overall_status` passed、`has_blocking_issue` false。
- `mock_requests.log`：**2,581 bytes / 19 行**请求记录，与 summary
  `request_stats` total 19 一致；时间戳 UTC 20:03:40–20:05:31 全部落在
  执行窗口内。
- `logcat-full.txt`：575,012 bytes，独立行数复核 **4,744 行**，与 summary
  `total_lines` 一致；设备型号 MGA-AL00 在 logcat 中确认。
- 源证据目录全量清单重derive（`sha256sum` + `wc -c`，只读）：**19 文件 /
  1,815,238 bytes**，逐文件哈希见下节；确定性聚合清单（按路径排序的
  `"<sha256>  %8d  <path>\n"` 行串接取 SHA-256，路径含 `./` 前缀，与
  Stage B / M14-50 回填同命令形态）=
  `74cc3883f7fc7ae268f0a01e0703c54b06bb86cd656cb5ed62a9ee8e87a8708f`。
- **本 run 无 REPORT.md**（19 文件；M14-50 已归档 run 为 20 文件含
  REPORT.md 3,407 bytes）——summary.json 是本 run 唯一权威 runner 结果。
- 与 M14-50 已归档 current-main 真机冒烟
  （`docs/evidence/m14-50-android-current-main-physical-smoke/`）的区分
  事实——证明本次为独立新回合，两组证据不可互换：summary.json 哈希
  `b480c774…1dce`（本次）vs `f69c8e4d…418b`（已归档）；logcat 4,744 vs
  5,291 行；`androidruntime_crash_count` 42 vs 49；duration 140.267316 s
  vs 138.344335 s；执行窗口 UTC 20:03–20:05 vs 04:22–04:25；mock 端口
  8110 vs 8100；本 run 无 REPORT.md；APK 哈希相同（同可复现产物、不同
  执行）。
- git 区间核验：`git log --oneline 38de33f..028e4bb` 共 2 提交；
  `git diff --stat 38de33f 028e4bb -- apps/android tools/android_smoke
  tests/android_smoke` 输出为空。
- 源 `.verify/` 目录本回合只读未写（`.gitignore` 覆盖 `.verify/`，原始
  产物不入 git）。

## Android 冒烟事实（源：gitignored `.verify/m14-59-android-current-main-physical-smoke/android-smoke-20260919-main-38de33f/`）

- **模式**：verification-only；runner `tools.android_smoke.runner`；本 run
  mock 端口 **8110**（`tools/android_smoke/server.py` 源码
  `ThreadingHTTPServer(("127.0.0.1", port), handler)` 锁定仅绑宿主回环，
  端口按 run 指定；端口 8110 的直接证据是
  `05-tab-settings.xml` 中显示的已配置 base URL
  `http://127.0.0.1:8110/`）。
- **设备**：物理真机 Huawei MGA-AL00（logcat 确认），USB serial
  `EYFBB22923201473`；被测包 `com.ailearningos.app`。
- **18/18 阶段全 passed**：wait-device、setup-adb-reverse、install-apk、
  clear-app、clear-logcat、start-activity、wait-home、configure-base-url、
  tab-home / tab-study / tab-search / tab-voice / tab-settings、search、
  governance、remove-adb-reverse、mock-contract、dump-logcat（带产物的
  阶段为 01–08 的 png/xml 成对）。
- **Mock 契约**：**19 expected / 0 unexpected / 19 total**；17 GET + 2
  POST（`POST /api/v1/search/plan` body 43 bytes、`POST
  /api/v1/search/queries` body 54 bytes——均为 task-owned mock 端点）；
  GET 路径分布：`auth/status` ×4、`health` ×3、`system/privacy` ×3、
  `papers`、`search/providers`、`voice/providers`、
  `search/queries/9001`、`system/ops-snapshot`、`audit?limit`、`version`；
  task-owned mock 仅绑宿主 `127.0.0.1:8110` 经 `adb reverse` 通道服务，
  **零生产请求**。
- **Logcat 分析**：total_lines **4,744**；`fatal_exception_count=0`、
  `anr_count=0`、`package_crash_count=0`、`has_blocking_issue=false`
  （`androidruntime_crash_count=42` 为含 "AndroidRuntime" 字样的
  uiautomator 工具进程 I/D 级诊断行——26 D 级 + 16 I 级，E 级
  AndroidRuntime 行为 0，且 0 行含 `com.ailearningos`——非本包崩溃，
  与 M13-04 / M14-48 / M14-50 已归档口径一致）。
- **收尾**：`remove-adb-reverse` 阶段 passed（summary.json 记载）；本 run
  证据集无 REPORT.md、无独立 teardown 记录文件，收尾后
  `adb reverse --list` 终态不在本 README 声称范围。

## 证据文件与清单锚点

- 源证据（gitignored，不入库）：本 worktree
  `.verify/m14-59-android-current-main-physical-smoke/android-smoke-20260919-main-38de33f/`
  —— **19 文件 / 1,815,238 bytes**：summary.json（3,455 B，权威 runner
  结果）、logcat-full.txt（575,012 B）、mock_requests.log（2,581 B）、
  8 组 stage 截屏/布局 XML（01–08）。
- 原始截图/XML/日志均不入 git；以下逐文件 SHA-256 即完整性锚点（本回合
  从源目录独立重derive）：

```
c8f3500bd755573d472fc80228c339b436979f58d335f36067f2474ce2a26b7e    130788  01-tab-home.png
4dc1cb76293603b45f255a1461247fa9afcc90741cb73c4137219a9d71b08a1e     20911  01-tab-home.xml
dbd8bc2eef6c078fb3d0c4b5d2d39e4618c8d34423802ea8dda475df353771c6    129514  02-tab-study.png
14344cde421b1ae0afd4447d75f85b2ed933183454c91d2bb9acc56d05ee353d     20618  02-tab-study.xml
c109292637a35db302774e6436df943baf2a2d4b9e9c136bd772ff455254297d    139779  03-tab-search.png
5d63178843034bf51f08498781eee606c21458aeb8bd5f7bd7e550ff99c6d1e2     18355  03-tab-search.xml
5da7e57e2d2a3a855fc001a96e594e8b40b2a76b3fc5beb8fbfe9e99bfac4496    105001  04-tab-voice.png
851244b6a571dd62fdeaf13a1add2399f745321eb32d3097885fe396ef583531     12079  04-tab-voice.xml
862769c4020f2e73900a127e9c419ccb88acbdd178fdca29ef3999c013d6706e    134698  05-tab-settings.png
85ea7f1b17621873847accb3495e4c99bbdaafd994f582ebfbccdf5b87450a8e     17750  05-tab-settings.xml
2d9ff22fce480543ccd2010e77b8357f3e7411eabe55ec5dd37f781b1a6a90d    130965  06-configure-base-url.png
4dc1cb76293603b45f255a1461247fa9afcc90741cb73c4137219a9d71b08a1e     20911  06-configure-base-url.xml
c0c0d68923e73eb06fe4e9ab75f9297d2f60c89710dae3304513f37ae7a762c5    178976  07-search.png
f6e16eb7bb8ced0dc1da3f43eb9f197dd1c975b65e3bbab3e53602bde449ece8     20623  07-search.xml
0621a54f31d2bea71c4ef3ea3ab1598b0489291e493da30ac06947a3fa551bd0    132311  08-governance.png
4dc1cb76293603b45f255a1461247fa9afcc90741cb73c4137219a9d71b08a1e     20911  08-governance.xml
79fc6eafc6170adbdf719ff48d7f00a797e9df162a4bc588294459567430615d    575012  logcat-full.txt
c125ff1355f6cfea92fb24b22b8ec295831bc55c22968845a4afecb84c40a110      2581  mock_requests.log
b480c77409ad919a45c3be942a709c3935e89407151099022edbcfd9a1631dce      3455  summary.json
```

- 确定性聚合清单：对上表按路径排序的 `"<sha256>  %8d  <path>\n"` 行串接
  取 SHA-256 =
  `74cc3883f7fc7ae268f0a01e0703c54b06bb86cd656cb5ed62a9ee8e87a8708f`
  （可复现复核命令形态：`find . -type f | sort | while read f; do printf
  '%s  %8d  %s\n' "$(sha256sum "$f"|cut -d" " -f1)" "$(wc -c<"$f")" "$f";
  done | sha256sum`）。
- 值得注意的哈希事实：`01-tab-home.xml` ≡ `06-configure-base-url.xml` ≡
  `08-governance.xml`（同哈希 `4dc1cb76…08a1e`——runner 在这三个阶段捕获
  的窗口布局转储逐字节相同）；8 份布局 XML 中 **7 份（01/02/03/04/06/07/08）
  与 M14-50 已归档 run 逐字节相同**，仅 `05-tab-settings.xml` 不同（唯一
  内容差异即已配置 base URL 端口 8100 → 8110，同 17,750 bytes）——布局
  转储跨执行确定性再次实证；各 stage PNG 哈希互异（像素含时钟等时变
  内容）。

## 边界（不声称）

- **不声称**真实 provider / 生产 API / 生产 DB / 云语音检索 LLM：本冒烟
  仅 USB 真机 loopback mock 回归口径（task-owned mock 仅 `127.0.0.1:8110`
  + `adb reverse` 通道），零生产请求。注意：本 run 有 2 个 mock POST
  （search plan / queries，均为 mock 端点）——正确表述是「零生产请求」，
  不是「零写方法」。
- **不声称**长期可靠性：单次约 2.3 分钟冒烟 ≠ 长期稳定 / 浸泡 / 跨设备；
  真机 USB 长连接稳定性未验证（本轮未掉线）。
- **不声称** Harmony 侧任何结论：本 README 只覆盖 Android。
- 本 run 证据集无 REPORT.md、无独立 teardown 记录：`remove-adb-reverse`
  阶段 passed 仅以 summary.json 为据，收尾 `adb reverse --list` 终态不在
  声称范围。
- 全局判定不变：**`production_ready=false`**。
- 本回填回合零设备运行、零生产触碰、零代码改动、不 push、不建 PR。
