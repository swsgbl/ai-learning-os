# M14-50 Android current-main 真机物理冒烟证据回填（docs-only）

## 结论

supervisor 已于 2026-09-18 12:22:43–12:25:01（GMT+8；UTC
`04:22:43.033963Z–04:25:01.378298Z`，duration 138.344335 s，exit_code 0）在验证
worktree `m14-50-android-physical-smoke`（detached
`main@b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，即 **PR #129 merge**，起始
tracked-clean）完成 Android **current-main 物理真机冒烟**（同日第二次独立执行），
判定 **PASS**：18/18 阶段全 passed、mock 契约 19 expected / 0 unexpected /
19 total、logcat 阻塞计数全 0、收尾 `adb reverse --list` 为空、App 按既有物理
冒烟 teardown 策略保留。

验证 worktree HEAD `b9e8cc8` 到冒烟当日 current main `d613667`（PR #131
merge）对 `apps/android`、`tools/android_smoke`、`tests/android_smoke` 的区间
diff 为**空**——Android 源码等价，本冒烟即 current-main 口径。

本回合（worktree `m14-52-mobile-harmony-evidence-backfill`，分支
`docs/m14-52-mobile-harmony-evidence-backfill`，基于 `main@fba3bb1`，
PR #134 merge）为 docs-only 回填：**零设备运行、零生产触碰、零代码
改动、不 push、不建 PR**；只读取源 worktree gitignored
`.verify/android-smoke-20260918-main-d613667/` 证据，独立复核后入库。源
REPORT.md（3,407 bytes）与 summary.json（3,455 bytes）为权威事实来源，本
README 为该 Android 冒烟唯一入库证据文件。

## 双基线与 docs-only 区间

| 基线 | 角色 |
|------|------|
| `b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`（PR #129 merge） | 冒烟**验证基线**（验证 worktree detached 于该 commit，起始 clean，全程唯一 HEAD） |
| `d613667a500d22bef912229f40c7704223c35d2e`（PR #131 merge） | 冒烟时 current main —— `b9e8cc8..d613667` 对 Android 路径区间 diff 为空（源码等价） |
| `fba3bb1`（PR #134 merge） | 本 docs 回填分支基点（`main@fba3bb1`） |

- `b9e8cc8..d613667` 共 6 个提交（`d613667` merge PR #131 移动端冒烟回填、
  `761f3fd` merge PR #132 归档调度器、`298807a` docs mobile smoke、`f9c2c84`
  ops audit archive scheduler、`15580f2` merge PR #130、`849f49f` docs audit
  worm offline），对 `apps/android`、`tools/android_smoke`、`tests/android_smoke`
  的区间 diff 为**空**。
- `b9e8cc8..fba3bb1` 共 10 个提交（上述 6 个 + `37b5b22` docs Stage B、
  `965eb91` merge PR #133 Stage B 回填、`74d099d` ops readiness CLI、
  `fba3bb1` merge PR #134 就绪 CLI），对 `apps/harmony`、`apps/android`、
  `tools/harmony_release`、`tools/android_smoke`、`tests/android_smoke` 的区间
  diff 同为**空**——冒烟验证所用移动端源码与本回填基线完全一致，无源码漂移。

## 回填前只读复核（本 docs 回填回合，零设备/零生产）

- APK 独立重哈希（只读）：`app-debug.apk` → SHA-256
  `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`、
  **11,454,463 bytes**——与源 REPORT/summary.json 一致；该哈希与 M13-04 /
  M14-48 / M14-50 首次冒烟已归档 APK 哈希相同（debug 构建可复现）。
- `summary.json`（权威 runner 结果）独立复核：SHA-256
  `f69c8e4d12a49e154121a2f911cb6ca994c76afca61f94578c0b47c5acac418b`、
  **3,455 bytes**；`"status": "passed"` 恰好 **18** 处（对应 18 个阶段）、
  `exit_code` 0。
- `mock_requests.log`：**2,581 bytes / 19 行**请求记录，与 summary
  `request_stats` total 19 一致。
- `logcat-full.txt`：647,464 bytes，独立行数复核 **5,291 行**，与 summary
  `total_lines` 一致。
- 源证据目录全量清单重derive（`sha256sum` + `wc -c`，只读）：**20 文件 /
  1,885,407 bytes**，逐文件哈希见下节；确定性聚合清单（按路径排序的
  `"<sha256>  %8d  <path>\n"` 行串接取 SHA-256，路径含 `./` 前缀，与 Stage B
  回填同命令形态）=
  `a7a35fcd78bf8c112b25372bbd05317da90d80c25ae855d092630b21cb24e393`。
- 与同日已归档首次冒烟（`docs/evidence/m14-50-current-main-mobile-smoke/`，
  源 `.verify/android-smoke/`）的区分事实——证明本次为独立新回合，两组证据
  不可互换：summary.json 哈希 `f69c8e4d…418b`（本次）vs `fccca9a2…0496`
  （已归档）；logcat 5,291 vs 5,769 行；`androidruntime_crash_count` 49 vs
  47；duration 138.344335 s vs 135.092544 s；执行窗口 UTC 04:22–04:25 vs
  02:58–03:01；APK 哈希相同（同产物、不同执行）。
- git 区间核验：`git log --oneline d613667..fba3bb1` 共 4 提交；
  `git rev-list --count b9e8cc8..fba3bb1` = 10；`git diff --stat b9e8cc8
  fba3bb1 -- apps/harmony apps/android tools/harmony_release
  tools/android_smoke tests/android_smoke` 输出为空。
- 源 `.verify/` 目录本回合只读未写（`.gitignore` 覆盖 `.verify/`，原始产物
  不入 git）。

## Android 冒烟事实（源：gitignored `.verify/android-smoke-20260918-main-d613667/`）

- **模式**：verification-only；runner `tools.android_smoke.runner`；runner 端口
  8100 一次绑定成功（无 M14-48 式端口冲突重试）。
- **设备**：物理真机 Huawei MGA-AL00（HWMGA-H），USB serial
  `EYFBB22923201473`；被测包 `com.ailearningos.app`。
- **18/18 阶段全 passed**：wait-device、setup-adb-reverse、install-apk、
  clear-app、clear-logcat、start-activity、wait-home、configure-base-url、
  tab-home / tab-study / tab-search / tab-voice / tab-settings、search、
  governance、remove-adb-reverse、mock-contract、dump-logcat。
- **Mock 契约**：**19 expected / 0 unexpected / 19 total**；task-owned mock 仅绑
  宿主 `127.0.0.1:8100` 经 `adb reverse` 通道服务，**零生产请求**。
- **Logcat 分析**：total_lines **5,291**；`fatal_exception_count=0`、
  `anr_count=0`、`package_crash_count=0`、`has_blocking_issue=false`
  （`androidruntime_crash_count=49` 为含 "AndroidRuntime" 字样的 uiautomator
  工具进程 I/D 级诊断行、E 级 AndroidRuntime 行为 0，非本包崩溃，与
  M13-04 / M14-48 / M14-50 首次冒烟已归档口径一致）。
- **收尾**：`adb reverse --list` 为空；`com.ailearningos.app` **保留安装**
  （既有物理冒烟 teardown 策略）；`emulator-5554` 预检时已 offline、终检时
  不在设备列表（该模拟器自行脱离，本验证未触碰、未清理）。

## 证据文件与清单锚点

- 源证据（gitignored，不入库）：验证 worktree
  `m14-50-android-physical-smoke` gitignored
  `.verify/android-smoke-20260918-main-d613667/` —— **20 文件 /
  1,885,407 bytes**：REPORT.md（3,407 B，权威报告）、summary.json（3,455 B，
  权威 runner 结果）、logcat-full.txt（647,464 B）、mock_requests.log
  （2,581 B）、8 组 stage 截屏/布局 XML（01–08）。
- 原始截图/XML/日志均不入 git；以下逐文件 SHA-256 即完整性锚点（本回合从
  源目录独立重derive）：

```
86b3b5ef2097e691b83ddf7440c9d22c6a52e2040d51990526ec7a48ee784229    130570  01-tab-home.png
4dc1cb76293603b45f255a1461247fa9afcc90741cb73c4137219a9d71b08a1e     20911  01-tab-home.xml
12d6489100e124e23ae7cf31407bfca8f5a6b366dff56daa2fe8c7838d546313    129627  02-tab-study.png
14344cde421b1ae0afd4447d75f85b2ed933183454c91d2bb9acc56d05ee353d     20618  02-tab-study.xml
cdd539264b12435ea5d9dc991529fba8cb46294692a7fafbaa8d9bcaca794b86    139362  03-tab-search.png
5d63178843034bf51f08498781eee606c21458aeb8bd5f7bd7e550ff99c6d1e2     18355  03-tab-search.xml
d004a95628b783a52d5d5e38e3742bfe35b4864c78d282afa426e53bd2eaa9a2    104615  04-tab-voice.png
851244b6a571dd62fdeaf13a1add2399f745321eb32d3097885fe396ef583531     12079  04-tab-voice.xml
aef4336e2ff62b7d13e4eed9c75a39532c41bb6a6aefe194d514ad256b5d1139    134323  05-tab-settings.png
30dbefa150f5a4284048f5b3c1b9142168b02df4152a7eee1c4e6f63ec9a162c     17750  05-tab-settings.xml
016f3d7abcf9c189b2409129b2769529564c805d6c5998b39cb709dae5ff5cdf    130416  06-configure-base-url.png
4dc1cb76293603b45f255a1461247fa9afcc90741cb73c4137219a9d71b08a1e     20911  06-configure-base-url.xml
b6ea488e4ad4fc38a44deb8b9cf1679e75da38ac0cf0439434aa1bf4121232fe    177093  07-search.png
f6e16eb7bb8ced0dc1da3f43eb9f197dd1c975b65e3bbab3e53602bde449ece8     20623  07-search.xml
cbf9999908a6ed718f710c15f72218a930b1ae68b3dd58b783b1769ace161801    130336  08-governance.png
4dc1cb76293603b45f255a1461247fa9afcc90741cb73c4137219a9d71b08a1e     20911  08-governance.xml
7b16f748ba26050d72b58bd35084f63a1bdeaf99b14cc5ecb42401a2123cb85d      3407  REPORT.md
98d53b5c9e017b7ed85b70b61f73fec47c631d1c6024dfef691bae790820994f    647464  logcat-full.txt
1e50b8e005bc8ddba7816b4f2ce57b4a068183269a6461230fe5ed195e241142      2581  mock_requests.log
f69c8e4d12a49e154121a2f911cb6ca994c76afca61f94578c0b47c5acac418b      3455  summary.json
```

- 确定性聚合清单：对上表按路径排序的 `"<sha256>  %8d  <path>\n"` 行串接
  取 SHA-256 =
  `a7a35fcd78bf8c112b25372bbd05317da90d80c25ae855d092630b21cb24e393`
  （可复现复核命令形态：`find . -type f | sort | while read f; do printf
  '%s  %8d  %s\n' "$(sha256sum "$f"|cut -d" " -f1)" "$(wc -c<"$f")" "$f";
  done | sha256sum`）。
- 值得注意的哈希事实：`01-tab-home.xml` ≡ `06-configure-base-url.xml` ≡
  `08-governance.xml`（同哈希 `4dc1cb76…08a1e`——runner 在这三个阶段捕获的
  窗口布局转储逐字节相同）；其余各 stage XML/PNG 哈希互异。

## 边界（不声称）

- **不声称**真实 provider / 生产 API / 生产 DB / 云语音检索 LLM：本冒烟仅
  USB 真机 loopback mock 回归口径（task-owned mock 仅 `127.0.0.1:8100` +
  `adb reverse` 通道），零生产请求。
- **不声称**长期可靠性：单次约 2.3 分钟冒烟 ≠ 长期稳定 / 浸泡 / 跨设备；
  真机 USB 长连接稳定性未验证（本轮未掉线）。
- **不声称** Harmony 侧任何结论：本 README 只覆盖 Android；Harmony Stage C
  模拟器终验见同批
  `docs/evidence/m14-50-harmony-emulator-stage-c/README.md`。
- `emulator-5554` 预检 offline / 终检缺席为该模拟器自行脱离，非本验证所致，
  本验证未触碰任何其他设备或模拟器。
- 全局判定不变：**`production_ready=false`**。
- 本回填回合零设备运行、零生产触碰、零代码改动、不 push、不建 PR。
