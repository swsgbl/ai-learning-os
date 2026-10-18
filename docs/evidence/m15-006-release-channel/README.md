# M15-006 证据：发布渠道 + 应用内更新检查（GitHub Releases 首发 v0.1.0）

- 基点 current main（#381 后）。四个 PR 交付：**#382** 服务端
  `/api/v1/meta/latest-app` + Android 设置页「应用更新」；
  **#383** CI 发布流水线（tag→签名→verify→pre-release）；
  **#384/#385** 发布门禁修复（资产名匹配 + DN 诊断）；
  **#386** verify_artifact 兼容 V-scheme DN 行格式；收尾 PR
  （compose env 槽位 + 本证据）。

## 服务端端点（#382）

`GET /api/v1/meta/latest-app?platform=android|harmony`：
部署 env 三项（version/version_code/download_url）**齐全才透出**，
缺任一 404 如实（不虚报有更新）；platform 白名单 422；**认证豁免**
（更新检查发生在登录前的首启流程）。6 项契约测试。

## Android 应用内检查（#382）

设置页「应用更新」区：当前版本 + 手动「检查更新」（无后台静默
轮询——更新意图由用户主导）；`UpdateCheckEvaluator` 纯判定
（versionCode 单调比较，null=未配置三态）；发现新版 →「下载更新」
系统浏览器打开分发地址（不做静默下载/安装，无 INSTALL_PACKAGES
权限诉求）。全量 **356 tests / 0 failures** + ruff + lintDebug。

## CI 发布流水线与首发实录（#383→#386，四轮迭代）

`release-android.yml`：tag `v*` → 四项 Secrets 前置守卫 + keystore
解码 → assembleRelease（env 四件套签名，无材料显式失败不回退
debug）→ `ci_make_manifest.py`（aapt2 读 APK 真实包名/版本，
sha256 当场重算）→ **资产规范名复制后** `verify_artifact.py`
fail-closed 门禁 → **tag↔APK versionName 一致性门禁** →
`gh release create --prerelease` 附 APK + manifest.json。
Actions pin 到 commit SHA（供应链审查项）。

四轮迭代（每轮失败原因均实证后修复）：

| 轮 | 结果 | 根因→修复 |
| --- | --- | --- |
| 1 | fail | manifest 条目按 APK basename 匹配——verify 前未复制规范名（#384 修）+ DN 行未解析（待诊断） |
| 2 | fail | 同上确认 + 诊断行（cat -A）上线 |
| 3 | fail | **CI apksigner 打印 `V3.0 Signer: certificate DN:` 格式**（cat -A 实证），原正则只认 `Signer #1 certificate DN:` → SIGNER_DN_RE 等价兼容两种格式，安全语义不变（#386） |
| 4 | **success** | — |

## 首发 v0.1.0（2026-10-18，tag 于 main `16d95fff`）

- Release「砚席 v0.1.0 (Android)」**prerelease=true**（诚实边界：
  未经 M14-237 公网移动发布门禁——notes 明示）
- 产物：`ai-learning-os-0.1.0-release-signed.apk`（8,029,570 B，
  v2+v3 签名、非 debug 证书经 verify 门禁）+ `manifest.json`
  （aios-download-manifest/1）
- **下载闭环验证**：gh release download 取回 → sha256
  `bfde3fa6…c5e54` 与 manifest **逐字节一致**
- 下载地址：
  `https://github.com/swsgbl/ai-learning-os/releases/download/v0.1.0/ai-learning-os-0.1.0-release-signed.apk`

## 生产接线（本收尾 PR）

- `infra/docker-compose.yml` api env +6 槽位
  （LATEST_ANDROID/HARMONY_*，默认空=404 如实）；rc-smoke
  compose 键集同步（零增零删契约）；pin 第七次显式更新
  （LF 归一化口径 `98b85af5…`）。
- 部署 env 注入三值（AIOS_LATEST_ANDROID_VERSION=0.1.0 /
  VERSION_CODE=1 / DOWNLOAD_URL=Release 地址）后端点即透出
  （运维步骤，不入库）。

## 非声明与待办

- 真机安装冒烟：发布时手机已断连（USB offline）——记待补；
  置信依据：sha256 一致 + CI verify 门禁（v2+v3 + 非 debug 证书）
  + 与真机全验证过的 debug 构建同源代码。
- 签名材料仅存 GitHub Secrets + 本地 `.secure/`（不入库）；
  Harmony 发布链（hap 附 Release）属后续切片。
- 三 ready 标志均 false；Release 为 pre-release。
