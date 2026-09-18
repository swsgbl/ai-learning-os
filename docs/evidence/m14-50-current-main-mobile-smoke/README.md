# M14-50 移动端 current-main 冒烟证据回填（Harmony 模拟器 + Android USB 真机）

## 结论

supervisor 已于 2026-09-18 在两个独立验证 worktree 上完成移动端
current-main 双冒烟（均为 verification-only，验证基线为 detached
`main@b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，即 **PR #129 merge**）：

- **Harmony**（worktree `m14-50-harmony-current-smoke`，模拟器单目标
  `127.0.0.1:5555`）：release build ok；device smoke **6/6 命令全 ok**；
  崩溃指标全 0；卸载后 bundle 不存在；模拟器保持运行。
- **Android**（worktree `m14-50-android-physical-smoke`，USB 真机
  `EYFBB22923201473`，Huawei MGA-AL00）：**18/18 阶段全 passed**；mock 契约
  **19 expected / 0 unexpected / 19 total**；logcat FATAL / ANR / 本包
  crash 全 0；收尾 `adb reverse --list` 为空；App 按既有物理冒烟 teardown
  策略保留。

本回合（worktree `m14-50-mobile-smoke-backfill`，分支
`docs/m14-50-mobile-smoke-backfill`）为 docs-only 回填：不运行设备、不触碰
生产、不改代码、不 commit/push；只读取两个验证 worktree 的 gitignored
`.verify/` 源证据，独立复核后入库。

## 双基线与 docs-only 区间

| 基线 | 角色 |
|------|------|
| `b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`（PR #129 merge） | 两次冒烟的**验证基线**（两个验证 worktree 均 detached 于该 commit，起始 clean） |
| `15580f2`（PR #130 merge） | 本 docs 回填分支基点（`main@15580f2`） |

`b9e8cc8..15580f2` 仅 2 个提交（`849f49f` docs: record audit worm offline
execution + `15580f2` merge PR #130），diff 只触 `docs/CHANGELOG.md`、
`docs/ROADMAP.md` 与
`docs/evidence/m14-49-audit-worm-offline-execution/README.md`；对
`apps/harmony`、`apps/android`、`tools/harmony_release` 的区间 diff 为
**空**——两次冒烟验证的移动端源码与本回填基线完全一致，无源码漂移。

## 回填前只读复核（本 docs 回填回合，零设备/零生产）

- HAP 实测：`sha256sum` =
  `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`、
  `wc -c` = 188,984 —— 与源 REPORT 一致（报告形态为大写，同一哈希）。
- APK 实测：`sha256sum` =
  `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`、
  `wc -c` = 11,454,463 —— 与源 REPORT/summary.json 一致；该哈希与
  M13-04 / M14-48 已归档 APK 哈希相同（debug 构建可复现）。
- Harmony `SHA256SUMS.txt`：**11/11 条目**（哈希 + 大小）逐条重验全 OK。
- Android `MANIFEST.txt`：**19/19 条目** `sha256sum -c` 全 OK（含
  `summary.json` SHA-256
  `fccca9a20222bbffef6bdb018381c95d77c15ac6d2e05532a010eb5b87740496`）。
- 方法学注意：两份清单均为 Windows/PowerShell 生成的 CRLF 行尾，核验前需
  `tr -d '\r'`（否则文件名带 `\r` 导致假失败）。
- git 区间核验：`git log --oneline b9e8cc8..15580f2` 仅 2 提交；
  `git diff --stat b9e8cc8 15580f2 -- apps/harmony apps/android
  tools/harmony_release` 输出为空。

## Harmony 冒烟事实（源：gitignored `.verify/m14-50-harmony-current-smoke/`）

- 模式：verification-only；起始 `git status --porcelain` 空；报告含同日
  修正轮（补 HAP 尺寸解释、补对 M13-16 的语义 layout 对比、删除无凭据的
  「主屏恢复」声明；修正轮零设备/零构建/零生产触碰，清单仅 REPORT.md
  条目变化、其余 10 条与冒烟时逐字节一致）。
- 设备目标：`hdc list targets` 仅 `127.0.0.1:5555`；device-smoke 工具本身
  不做 discovery（`discovery_used: false`、`all_devices_forbidden: true`）。
- Release build：`python tools/harmony_release/release_build.py --repo-root .`
  → `status=ok exit=0`（clean + `assembleHap --mode module -p product=default
  -p buildMode=release --no-daemon`）。
- HAP：`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`，
  **188,984 bytes**，SHA-256
  `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`；
  **未签名**（`external_materials: null`，零签名材料触碰，
  `signedness_verified: false`）。
- 与 M13-16 的尺寸差异（已解释，只读复核）：M13-16 验收 HAP 452,446
  bytes 含 `ets/sourceMaps.map`（119,689 bytes）与 `ets/modules.abc`
  （328,372 bytes）；当前 HAP 无 source map（zip 仅 8 条目）、
  `modules.abc` 为 184,708 bytes；`7c0f7c2..b9e8cc8` 在 `apps/harmony` 与
  `tools/harmony_release/release_build.py` 无源码 diff。`modules.abc`
  缩小本身未在该验证回合调查。
- Device smoke：`python tools/harmony_release/device_smoke.py --repo-root .
  --target 127.0.0.1:5555 --hap … --confirm-mutation --layout-dir …` →
  `status=ok exit=0`、`dry_run=false`、`mutation_performed=true`、
  **6/6 命令**（install → `aa start` EntryAbility → `uitest dumpLayout` +
  recv → `aa force-stop` → uninstall）；bundle `com.ailearningos.app`。
- Layout：`device_smoke_layout.json` 2,708 节点 / 53,798 bytes / SHA-256
  `B31AECB764497A59615650D0D9AE8067A15349195FFB100C08AA6EFE5D7CC2F1`；
  与 M13-16 验收快照语义对比：树结构同为 82 节点 / 26 文本节点、顺序与
  嵌套一致；26 个文本节点中 23 个字节相同，其余 3 个仅时钟数字变化
  （`08:31` → `11:00`），无其他文本/结构差异。
- hilog 补充证据：`hilog -x` dump 882,948 bytes；`cppcrash`/`jscrash`/
  `appfreeze`/`FaultLog` 匹配 **0**；`PACKAGE_ADDED` ×13 /
  `PACKAGE_REMOVED` ×14；`SCBMain startSceneFromOther {…
  bundleInfo:EntryAbility/com.ailearningos.app/entry/0 …
  focusedOnShow:true}` 于 09-18 11:00:52.654；install 11:00:52 →
  uninstall 11:00:54。
- 收尾/后置状态：cleanup ok、`bundle_uninstalled: true`；独立复检
  `bm dump -n com.ailearningos.app` → "failed to get information"
  （**bundle 卸载后不存在**）；模拟器保持运行（未停止/重置）；无卸载后
  视觉捕获，**不主张**主屏恢复（修正轮已删除该无凭据声明）。

## Android 冒烟事实（源：gitignored `.verify/android-smoke/`）

- 模式：verification-only，detached `main@b9e8cc8`；执行窗口
  2026-09-18T02:58:58Z–03:01:13Z（UTC），duration 135.092544 s。
- 设备：物理真机 Huawei MGA-AL00，serial `EYFBB22923201473`。
- APK：`apps/android/app/build/outputs/apk/debug/app-debug.apk`，
  **11,454,463 bytes**，SHA-256
  `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`
  （哈希与 M13-04 / M14-48 归档记录相同）。
- 结果：runner 端口 8100；`overall_status=passed`；**18/18 阶段全 passed**
  （wait-device、setup-adb-reverse、install-apk、clear-app、clear-logcat、
  start-activity、wait-home、configure-base-url、五个 tab、search、
  governance、remove-adb-reverse、mock-contract、dump-logcat）。
- Mock 契约：**19 expected / 0 unexpected / 19 total**。
- Logcat：5,769 行；`fatal_exception_count=0`、`anr_count=0`、
  `package_crash_count=0`、`has_blocking_issue=false`
  （`androidruntime_crash_count=47` 为含 "AndroidRuntime" 的 uiautomator
  工具进程诊断文本噪音，非本包崩溃，与 M13-04 / M14-48 已归档口径一致）。
- 收尾：`adb reverse --list` 为空；`com.ailearningos.app` **保留安装**
  （既有物理冒烟 teardown 策略）。
- 凭据扫描：对 `summary.json` 与 `mock_requests.log` 定向扫描无凭据材料
  （UI XML `password="false"` 为非敏感 schema 文本）。

## 证据文件与清单锚点

- Harmony 源证据（gitignored，不入库）：
  `D:\AI Learning OS\ai-learning-os-worktrees\m14-50-harmony-current-smoke\.verify\m14-50-harmony-current-smoke\`
  —— REPORT.md（8,863 bytes）、release_build.log、device_smoke.log、
  layout/device_smoke_layout.json（53,798 bytes）、hilog_dump.txt
  （882,948 bytes）等共 11 条目；`SHA256SUMS.txt` 为三列格式
  （大写哈希/大小/文件名）。
- Android 源证据（gitignored，不入库）：
  `D:\AI Learning OS\ai-learning-os-worktrees\m14-50-android-physical-smoke\.verify\android-smoke\`
  —— `summary.json`（权威 runner 结果，SHA-256
  `fccca9a20222bbffef6bdb018381c95d77c15ac6d2e05532a010eb5b87740496`）、
  8 组 UI PNG/XML、logcat-full.txt、mock_requests.log 等 19 条目
  （`MANIFEST.txt` 两列 sha256sum 格式，不自哈希）。
- 原始截图/日志/XML 均不入 git；本 README 为唯一入库证据文件，以上哈希即
  完整性锚点。

## 边界（不声称）

- **不声称** Harmony 已签名或已上真机：无 AGC、无 release 签名材料、HAP
  未签名（`signedness_verified: false`）、仅模拟器 loopback 目标。
- **不声称** Android 真实 provider：本冒烟仅 USB 真机 loopback mock 回归
  口径，不构成真实 provider / 生产 API / 生产 DB / 云语音检索 LLM 的
  通过证明。
- **不声称**生产就绪：单次冒烟 ≠ 长期稳定 / 浸泡 / 跨设备；真实 provider
  smoke、release-readiness / cutover 审批、AGC 签名发布链、真正离线/离站
  副本与定期归档调度等仍开放。
- 全局判定不变：**`production_ready=false`**。
- 本回填回合零设备运行、零生产触碰、零代码改动、零 commit/push。
