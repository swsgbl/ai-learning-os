# M14-48 Android USB 真机回归验证证据回填（验证-only，零代码改动）

- 回填日期：2026-09-18（本地 GMT+8）；验证执行窗口 2026-09-17T23:51Z–23:56Z（UTC）
- 回填基点：本 worktree `m14-48-android-physical-regression-record`、分支 `docs/m14-48-android-physical-regression` 基于 `main@1b35145`（PR #125 merge）
- 验证基点：验证 worktree `verify/m14-48-android-physical-regression` 基于 `main@c817ade`（PR #124 merge；分支头 = 基线，验证回合零新 commit）——两基点间仅 docs 变更，Android 代码面一致
- 本回填（M14-48）为 **docs-only**：只把已完成的 Android USB 真机回归验证（验证-only）事实转为可审计仓库证据；原始证据为另一 worktree gitignored `.verify/m14-48-android-physical-regression/`（REPORT.md + r1/r2 运行产物），**截图、XML dump、原始日志与 mock 日志均不入 git**，本 README 为唯一入库文件，仅摘录判定事实与哈希锚点
- 秘密边界：验证与回填全程未打印、未复制任何 secret/token/password；本 README 无凭据、无环境值、无真实绝对主机路径、无原始输出；mock 日志本身仅含脱敏五字段（timestamp_utc/method/path/query_keys/body_len）
- 结论：**最终判定 PASS（loopback mock 口径）——8/8 判定维度通过（#6 error/recovery 附条件）**；`production_ready=false` 不变

## 设备与边界遵守（验证-only）

| 项 | 事实 |
|---|---|
| 设备 | Huawei MGA-AL00（`product:MGA-AL00 model:MGA_AL00 device:HWMGA-H`），Android 10，720x1600，USB serial `EYFBB22923201473`，运行时 `device` 态、亮屏未锁（`mDreamingLockscreen=false`）；adb 清单仅此一台在线，未触任何 Harmony 模拟器 |
| 网络边界 | mock `LoopbackMockServer` 只绑宿主 `127.0.0.1`；真机 transport 仅 `adb reverse --no-rebind tcp:8100 tcp:8100`；App base URL 经真实 Settings UI 配为 `http://127.0.0.1:8100/`；无 LAN 暴露、无真实 provider/生产 API/DB/云服务/凭据 |
| 进程边界 | 未停止/重启任何用户进程（含占用宿主 8000 端口的 PID 23940）；仅对本项目包 `com.ailearningos.app` 执行 install / pm clear / am start，未卸载/修改任何其他应用 |

## 构建与 APK 锚定

- `gradlew.bat assembleDebug` exit 0（BUILD SUCCESSFUL in 25s，39 tasks: 16 executed / 23 from cache）
- APK 11,454,463 bytes，SHA-256 `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570`（sha256sum 与 Python hashlib 双算一致）
- **与 M13-04 归档 APK SHA-256 逐字节一致** → main@c817ade 的 Android 端与 M13-04 验收时源码一致，构建可复现

## r1 端口冲突（失败安全，零设备副作用）

- `--port 8000` 首跑失败：`PermissionError: [WinError 10013]`——宿主 `127.0.0.1:8000` 已被用户进程 PID 23940 监听（port-guard hook 与 netstat 双确认）；按边界**未停止该进程**
- 失败安全：exit 1、证据保留（03-smoke-log.txt）；bind 失败发生于 server 构造期，wait-device / adb reverse / install 均未执行——**零设备副作用，无需清理**
- 端口适配：8000/18080 被占用，试绑 `127.0.0.1:8100` OK（不在 netsh excludedportrange 内）→ r2 改用 8100
- r1 失败为宿主端口环境因素，非 harness/App 缺陷（harness `--port` 参数化正确处理该场景），无代码缺陷需要反馈

## r2 通过运行（宿主回环 8100）

| 项 | 结果 |
|---|---|
| 结果 | `overall_status=passed`、`exit_code=0`、时长 142.5s（2026-09-17T23:53:47Z–23:56:10Z，主仓 canonical venv Python 3.11.15） |
| 阶段 | **18/18 stage 全 passed**：wait-device、setup-adb-reverse、install-apk、clear-app、clear-logcat、start-activity、wait-home、configure-base-url（Settings UI 配 base URL + 首页 /health「正常」）、5 tab 巡检、search、governance、remove-adb-reverse、mock-contract、dump-logcat |
| mock 契约 | **19 expected / 0 unexpected / 19 total**，与 M13-04 r3 请求形态完全一致；全部落在 EXPECTED_ENDPOINTS；零写方法（PUT/PATCH/DELETE=0）；`GET /api/v1/audit` query_keys 精确为 `["limit"]`；`/health` 仅在 configure-base-url 保存后出现（reverse 通道真实生效） |
| logcat | **FATAL 0 / ANR 0（词边界）/ 本包 crash 0**（4998 行，`has_blocking_issue=false`）；`androidruntime_crash_count=52` 为**诊断计数**——含 "AndroidRuntime" 的全部行，抽样均为 uiautomator dump 工具进程 D 级启动日志（每次 dump 3+ 行 × 16 次 dump ≈ 52），与 M13-04 已归档口径一致，**非 App 崩溃**；其中含本包名的行数为 0 |
| reverse 清理 | 运行后 `adb -s EYFBB22923201473 reverse --list` 输出为**空**（remove-adb-reverse stage + 事后独立复核） |
| UI 证据 | 8 组 .png 截图 + .xml 结构 dump；study/search/settings 正向内容经**视觉与结构双重核实**：settings 保存值 `http://127.0.0.1:8100/` ×2 可见、首页 /health 卡片值「正常」、试卷库渲染 mock 确定性数据（"Attention Is All You Need" / Vaswani NeurIPS 2017、"BERT" / Devlin NAACL 2019）、搜索回查结果卡「原查询词」可见 |

## 判定表（8/8，#6 附条件）

| # | 维度 | 判定 |
|---|---|---|
| 1 | build | PASS |
| 2 | install | PASS |
| 3 | launch | PASS |
| 4 | UI navigation | PASS |
| 5 | mock contract | PASS |
| 6 | error/recovery | PASS（附条件） |
| 7 | logcat health | PASS |
| 8 | cleanup | PASS |

**总判定：PASS——仅代表 loopback mock 冒烟口径通过。**

## 证据完整性锚定

验证 worktree gitignored `.verify/m14-48-android-physical-regression/` 全量清单（回填时以 sha256sum/stat 对目录内全部 26 个文件逐一实测，按路径排序；路径均相对该 `.verify/m14-48-android-physical-regression/` 目录）：

| 文件 | 大小（bytes） | SHA-256 |
|---|---:|---|
| `00-environment.txt` | 580 | `B6E8B9760EB28D9C42E1353D0072A60D9C4FB053CD554467DDC0AA7ABE14B0C4` |
| `01-build-log.txt` | 2,285 | `323A491BFDDF1B80D9E3FA37BE0B04DDE92F0513E319D834F08C230C1041B4E8` |
| `02-apk.txt` | 334 | `B494488750B0E88CF47E72848F230284E1F3C62841732560EBB9414501F52B82` |
| `03-smoke-log.txt` | 432 | `7E8BB0FC05A601888C9E355ACADC3DC01F6C114E5DF3B1D35F6164FA6F4FB2DB` |
| `04-smoke-log-r2.txt` | 95 | `14A8CC6C2FBA6F215D1EFDFDE21D22EC8AA797BA28CDF55008483056FBFA6608` |
| `REPORT.md` | 9,414 | `B3AB9D671C98F7C20486904F577D9F419F725EE994E4E9FA32F09A5DEAB6B2A9` |
| `physical-EYFBB22923201473-r2/01-tab-home.png` | 130,277 | `7DA9C7E5940FD0EBFFA93E559A5D03C076A0B3CA7359B6D71B4BAFDC14245F94` |
| `physical-EYFBB22923201473-r2/01-tab-home.xml` | 20,911 | `4DC1CB76293603B45F255A1461247FA9AFCC90741CB73C4137219A9D71B08A1E` |
| `physical-EYFBB22923201473-r2/02-tab-study.png` | 130,422 | `969796D4D42765DE37584EF3AC61394525C54FC4D59BEB03ACC0EFC79DE47D89` |
| `physical-EYFBB22923201473-r2/02-tab-study.xml` | 20,618 | `14344CDE421B1AE0AFD4447D75F85B2ED933183454C91D2BB9ACC56D05EE353D` |
| `physical-EYFBB22923201473-r2/03-tab-search.png` | 140,742 | `DBF1A0CFF4349E64FEBED8DE5EE46BB78E4BA49B6C68F4904915F89208772A38` |
| `physical-EYFBB22923201473-r2/03-tab-search.xml` | 18,355 | `5D63178843034BF51F08498781EEE606C21458AEB8BD5F7BD7E550FF99C6D1E2` |
| `physical-EYFBB22923201473-r2/04-tab-voice.png` | 105,888 | `E24A6B294318F71D112F7FDBAB9809581A71F9E716733E02AB9AE572D5C5095A` |
| `physical-EYFBB22923201473-r2/04-tab-voice.xml` | 12,079 | `851244B6A571DD62FDEAF13A1ADD2399F745321EB32D3097885FE396EF583531` |
| `physical-EYFBB22923201473-r2/05-tab-settings.png` | 135,546 | `4BF4C96124D51C42742A796F83F3E0B18BFDC2F00C862A4084752A19778458AA` |
| `physical-EYFBB22923201473-r2/05-tab-settings.xml` | 17,750 | `30DBEFA150F5A4284048F5B3C1B9142168B02DF4152A7EEE1C4E6F63EC9A162C` |
| `physical-EYFBB22923201473-r2/06-configure-base-url.png` | 130,309 | `E45E2339364D83CA0984F2FC25942F377E98A308643C6C1775C67C4FEC629700` |
| `physical-EYFBB22923201473-r2/06-configure-base-url.xml` | 20,911 | `4DC1CB76293603B45F255A1461247FA9AFCC90741CB73C4137219A9D71B08A1E` |
| `physical-EYFBB22923201473-r2/07-search.png` | 177,387 | `7063FF0055355FF98792C889D0C7E7EDED00878831E4979D3AB3EDA4E2DCC85B` |
| `physical-EYFBB22923201473-r2/07-search.xml` | 20,623 | `F6E16EB7BB8CED0DC1DA3F43EB9F197DD1C975B65E3BBAB3E53602BDE449ECE8` |
| `physical-EYFBB22923201473-r2/08-governance.png` | 130,591 | `AF1AD1A296F4BF8524778D3613F7637E7EA28158DC95BEDBEDAD120351818FDE` |
| `physical-EYFBB22923201473-r2/08-governance.xml` | 20,911 | `4DC1CB76293603B45F255A1461247FA9AFCC90741CB73C4137219A9D71B08A1E` |
| `physical-EYFBB22923201473-r2/logcat-full.txt` | 610,381 | `979C584054814DF324AD44FE7CF8417C5A740FF3F6479E0C4201BC36B42C4745` |
| `physical-EYFBB22923201473-r2/mock_requests.log` | 2,581 | `A182CED7117A011EECCA8017C7082F82412E97504F20C08D4D2A3567A694B5C1` |
| `physical-EYFBB22923201473-r2/summary.json` | 3,448 | `FFF95BBEAB4B6906E59888686CEFAC5E7810D33BDBE72E630B70A05F31B17E57` |
| `physical-EYFBB22923201473/mock_requests.log` | 0 | `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855` |

全部 26 个源证据文件已在回填时逐一哈希，目录保持 gitignored（`.gitignore` 的 `.verify/` 规则，抽查 REPORT.md / r2 截图 / r1 mock 日志均命中）——**截图、XML dump、原始日志与 mock 日志均不入 git**，上表哈希即完整性锚点。两点如实说明：r1 `physical-EYFBB22923201473/mock_requests.log` 为 0 bytes（对应 r1 bind 失败发生于 server 构造期、mock 未收到任何请求），其 SHA-256 即空输入标准摘要；`01-tab-home.xml`、`06-configure-base-url.xml`、`08-governance.xml` 三条目大小与 SHA-256 相同，系源目录中三个文件逐字节一致的实测结果，非清单重复或誊写错误。

## 如实披露与诚实边界（residuals）

1. **App 侧异常分支仅间接覆盖**：r2 一次通过，App 内异常分支（网络中断重试、mock 未知路径 404 的 UI 呈现、finalize 韧性中 dump logcat 失败降级）本轮未走异常路径——由 M13-04 r1/r2 历史证据与 `tests/android_smoke` 单测基线（285 passed）间接覆盖，本轮无新增证据，不声称直接覆盖。
2. **error/recovery 为附条件 PASS**：直接验证的是环境层——r1 宿主端口冲突失败安全（exit 1 / 证据保留 / 零设备副作用）+ 换 8100 端口后 r2 全绿。
3. **本地 USB + 回环 mock 口径**：不构成真实 provider / 生产 API / 生产 DB / 云语音检索 LLM 的通过证明；mock 数据为确定性固定契约。
4. **真机 USB 存在掉线史**（M13-04 r1）；本轮 r2 未发生掉线。
5. **收尾状态**：`adb reverse --list` 为空；`pm clear` 已执行；App 保留在设备（versionName 0.1.0，与 M13-04 验收口径一致）；验证 worktree tracked clean。
6. 单次验证 ≠ 长期稳定；未打 tag、未发 Release、未部署、未 commit/push（验证回合）。
7. **`production_ready=false` 不变**。

## 边界遵守声明（本回填回合）

本 M14-48 回填回合零代码改动（全部事实摘自已产出的 gitignored 原始证据，仅摘录判定事实与哈希）；未 commit/push；未打印或复制任何 secret/token/password；截图、XML dump、原始日志与 mock 日志不入 git；仅改动 docs 三文件（本 README + PROJECT_STATUS + ROADMAP）。
