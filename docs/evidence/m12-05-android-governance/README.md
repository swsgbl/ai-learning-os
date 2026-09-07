# M12-05 Android 治理 / 发布只读面 · 冒烟证据归档

- 日期：2026-09-07
- 分支：`feature/m12-05-android-governance-release` @ `b6475c02071c280ec4c154f91989c54db94a432c`
- APK：`apps/android/app/build/outputs/apk/debug/app-debug.apk`
  SHA256 `56119e1a28199712b7ac0c7e9dbb54fee19c4bad52a23b3c85afd218a44c59a8`，11,454,463 bytes（debug）
- 模拟器：`emulator-5554`，Android 16（API 36），`sdk_gphone64_x86_64`（Google），1080x2400 @ 420dpi
- 结论：**通过** —— 入口可达、三个治理端点数据渲染正确、审计载荷不渲染、logcat 无崩溃、Android 全量门禁通过。

## 证据文件清单

| 文件 | 说明 | 关键断言 |
|---|---|---|
| `mock_api_used.py` | 本次使用的 mock 服务脚本（只读 GET-only 固定 JSON） | 仅绑定 `127.0.0.1:8000`；仅 6 个端点；写方法/其余路径一律 404；不代理/不转发/不触网/不读密钥 |
| `mock_requests.log` | mock 全部 12 条请求记录（utc/method/path/body_len，**无 header、无 body**） | 前 6 行宿主自检 → 中 3 行首页 App 请求 → 末 3 行治理页 App 请求 |
| `01-home-governance-entry.png` / `.xml` | 首页截图与 UI 层级 | 「治理 / 发布」入口可见；「本地模式」；服务 /health = 正常 |
| `02-governance-version.png` / `.xml` | 治理页版本区块 | 版本 `0.12.5-mock`、Git `m1205mockgit`、Alembic 当前/目标均为 `m1205mockrev` |
| `03-governance-ops-snapshot.png` / `.xml` | 运行快照区块 | generatedAt、databaseBackend=postgresql、workerRunning=true、papersTotal=34、searchQueriesTotal=456、auditEntriesTotal=1024、pendingReviewDrafts 1/2/4/5、usersByRole 2/128、resourcesParsed 40/3、parseJobs 33/2、voiceSessions 90/2 —— 18 项逐条 contains 比对 XML 节点 |
| `04-governance-audit.png` / `.xml` | 审计留痕区块 | 条目 1001（mock_admin / paper.publish / paper-m1205）与 1002（actor=null / worker.tick / job-m1205）各 6 字段；**`M12_05_BEFORE_SHOULD_NOT_RENDER` / `M12_05_AFTER_SHOULD_NOT_RENDER` 及「before：」「after：」文本均不出现**（not_contains×4） |
| `logcat-full.txt.gz` | 会话全量 logcat（4,453 行 / 原始 564,905 字节，gzip 无损） | 四项崩溃指标全 0（见下） |
| `logcat-stats.json` | logcat 解析统计（补充） | FATAL EXCEPTION=0、ANR=0（含大小写不敏感）、AndroidRuntime 崩溃行=0、`com.ailearningos.app` 崩溃行=0 |
| `mock-log-analysis.json` | mock 日志结构化断言（补充） | 12 条 = 6 自检 + 3 首页 + 3 治理页；全 GET；无多余路径 |
| `summary.json` | 机器可读汇总 | 上述全部断言 + 门禁 + 边界 |

## mock 请求日志（12 条）

| 段 | 时间（UTC） | 请求 | 来源 |
|---|---|---|---|
| 1–6 | 04:00:35 | `/health`、`/api/v1/auth/status`、`/api/v1/system/privacy`、`/api/v1/version`、`/api/v1/system/ops-snapshot`、`/api/v1/audit?limit=100` | 宿主机自检（6 端点全通） |
| 7–9 | 04:03:29 | `/health`、`/api/v1/auth/status`、`/api/v1/system/privacy` | App 首页启动 |
| 10–12 | 04:06:20 | `/api/v1/version`、`/api/v1/system/ops-snapshot`、`/api/v1/audit?limit=100` | App 治理页 |

App 请求合计 **6**（首页 3 + 治理页 3），全部 GET，无任何多余路径。

## logcat 崩溃统计（要求全 0）

| 指标 | 数值 |
|---|---|
| `FATAL EXCEPTION` | 0 |
| `ANR`（含大小写不敏感） | 0 |
| `AndroidRuntime` 崩溃行（FATAL/Process:/Error type/Exception） | 0 |
| 含 `com.ailearningos.app` 的崩溃行 | 0 |

说明：AndroidRuntime 标签原始出现 75 行，经逐行甄别全部为 `uiautomator dump` 工具进程（`com.android.commands.uiautomator.Launcher`）的生命周期日志（START / boot image / lock profiling / Calling main entry / Shutting down VM，15 组 × 5 行），非崩溃输出。App PID 11852 会话全程存活，归档时仍在运行。

## Android 全量门禁

| 门禁 | 结果 |
|---|---|
| 单元测试 | **347 tests / 0 failures / 0 errors**（30 个 suite，含新增 GovernanceDtoParsingTest / GovernanceRepositoryTest / GovernanceViewModelTest） |
| Lint | **0 errors**（19 warnings） |
| assembleDebug | **success**（即上文 APK） |

## 复现方式

1. 分支 `feature/m12-05-android-governance-release`，运行 `./gradlew :app:testDebugUnitTest :app:lintDebug :app:assembleDebug`（门禁）。
2. 启动 API 36 模拟器，安装 APK：`adb -s emulator-5554 install -r apps/android/app/build/outputs/apk/debug/app-debug.apk`。
3. 宿主机启动 mock：`python .verify/m12-05-android-governance/mock_api.py`（监听 `127.0.0.1:8000`）。
4. 模拟器内 App 的 BaseUrl 指向 **`http://10.0.2.2:8000`**（模拟器访问宿主机 loopback 的专用别名，映射到 mock）。**auth 为 disabled**（mock `/api/v1/auth/status` 返回 `auth_enabled=false`，本地模式直连）。
5. 打开 App 首页 → 点「治理 / 发布」进入治理页 → 依次浏览版本 / 运行快照 / 审计留痕区块；每个画面用 `adb exec-out screencap -p` 与 `adb exec-out uiautomator dump /dev/tty` 采集 PNG/XML。
6. 收尾：`adb logcat -d` 存档并统计崩溃指标；校验 `mock_requests.log` 顺序与内容。

## 边界与免责

- 全部数据来自**本地 loopback mock**（固定 JSON、明显 mock 值：`0.12.5-mock` / `m1205mockgit` / `m1205mockrev` 等），**不代表真机、真实 provider 或生产环境**。
- 未连接任何真实服务 / 数据库 / API 密钥；mock 不记录请求 header 与 body，日志不含任何凭据。
- 仅验证只读 GET 路径；mock 对 POST/PUT/PATCH/DELETE 一律 404，未覆盖写操作。
- 审计条目载荷（before/after）内嵌唯一标记验证 **UI 不渲染载荷**——App DTO 不建模这两个字段，属于预期安全行为。
- logcat 统计仅覆盖本会话缓冲区，不构成长期稳定性结论。
