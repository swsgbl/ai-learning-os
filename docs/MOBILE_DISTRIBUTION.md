# 移动分发清单（Android release / HarmonyOS signed / PWA）— M14-153 / M14-164 / M14-174

公网边缘的移动入口分发规范。配套模板：`infra/edge/download-manifest.example.json`
（部署为 `download.example.com` 的 `manifest.json`）。自动化验收入口：
`tools/ops/public_edge_preflight.py`（人工清单第 6/7 项对应 APK/HAP/PWA）。

## 0. 诚实边界（不可逾越）

- **未签名（unsigned）产物不得作为生产分发**：当前仓库的 Android 侧只有
  debug 签名 APK、HarmonyOS 侧只有 unsigned HAP——`tools/harmony_release/preflight.py`
  默认 `--expect-unsigned` 契约如实钉住这一边界（非空 signingConfigs 反而 FAIL）。
  在 AGC 签名材料与 Android release keystore 落地前，任何文档、清单、状态页
  都不得宣称移动端"生产可用/公开分发就绪"。M14-171A 只让 release 构建管线
  **能消费**外部管理的 keystore（opt-in、四项齐全或失败、无 debug 回退）；
  M14-173 补的 `tools/android_release/verify_artifact.py` 只是**验收 readiness
  工具**（对未来的 `assembleRelease` 签名产物做可重复 fail-closed 校验）；
  M14-174 再补两件 readiness 工具与页面接线——`tools/android_release/
  material_bootstrapper.py`（能**安全生成**仓库外签名材料）、
  `tools/android_release/stage_download.py`（能把已验签 APK + manifest
  **原子发布**到本地 staging 下载目录）、以及 `/download` 按真实同源
  manifest 显示 Android 可下载（`apps/web/src/lib/download-manifest.ts`）。
  但 keystore、签名 APK 与公网 manifest 本身**仍未生成/未上传**：release
  产物与 `/download` 渠道状态不变（unsigned / pending，见
  `apps/web/src/lib/download.ts`）。真实发布链条（材料生成、离线备份、
  `assembleRelease`、verify、stage、公网上传、真机验收）仍需 Codex 以
  运维身份在仓库外执行——本仓库只提供可测试的工具与诚实的展示。
- 清单（manifest）里 `signed=false` 的条目只能停留在 `pending_unsigned`，
  永不进入 `files` 分发数组；debug APK 永不进入公开下载目录。
- 版本清单的每个 sha256 都必须在发布时**当场重算**，不得复制粘贴旧值。

## 1. Android release 分发清单

前置外部资源：release keystore（仓库外保管，绝不入库；丢失不可补发同签名）。

1. [ ] keystore 就绪（`AIOS_ANDROID_KEYSTORE_*` 部署变量/文件，600 权限，
       备份两处离线介质 + 保管记录）。M14-174 readiness：
       `tools/android_release/material_bootstrapper.py` 提供
       `plan`（只读预检：目标在仓库外、无 symlink、不存在）/ `execute`
       （显式确认短语 + keytool 生成 RSA 2048、validity ≥10000 天、
       拒绝 debug alias/dname；密码用 OS 随机源生成、绝不输出到
       stdout/报告/git；生成 keystore + `AIOS_ANDROID_SIGNING_PROPERTIES`
       可消费的 properties 文件；POSIX 0600 / Windows icacls ACL 收紧；
       失败清理本次新建文件、不误删既有文件）/ `verify`（复检存在性、
       仓库外边界、properties 四键、权限、证书 SHA256 指纹与有效期窗口）
       三个子命令，keytool/文件系统/时钟均可注入（零真实 keytool 的
       单测见 `tests/android_release/test_material_bootstrapper.py`）。
       **真实执行 generate 属于 Codex 运维步骤**，产物必须立即离线备份；
2. [x] `apps/android` 侧 release 构建配置接入（M14-171A：`apps/android/app/
       build.gradle.kts` 的显式 opt-in 签名配置——四项外部输入
       `AIOS_ANDROID_KEYSTORE_PATH` / `_STORE_PASSWORD` / `_KEY_ALIAS` /
       `_KEY_PASSWORD` 环境变量，或 `AIOS_ANDROID_SIGNING_PROPERTIES` 指向的
       仓库外 properties 文件，env 优先；任一输入出现即 opt-in，四项必须
       齐全否则构建直接失败，绝不回退 debug 签名；无外部输入时 release
       保持 unsigned 诚实默认。密码绝不写入 build 文件/CI 明文，静态契约由
       `tools/android_release/preflight.py` fail-closed 钉住）；
3. [ ] 构建：`gradlew assembleRelease`（CI 或本地，产物
       `app-release-signed.apk`）；
4. [ ] 校验签名方案（v2+v3）与 `versionCode`/`versionName` 递增
       （M14-173 readiness：`tools/android_release/verify_artifact.py`
       fail-closed 验收——APK 常规文件/SHA256/尺寸与 manifest 一致、
       `apksigner verify` v2+v3 均为 true 且拒绝 debug 证书
       （`androiddebugkey` / `CN=Android Debug`）、`aapt dump badging`
       实际包名/`versionCode`/`versionName` 与 manifest 精确一致（manifest
       android 频道已最小扩展 `package_name` 字段）、`versionCode` 相对
       previous 严格递增（无 previous 输入时如实输出 `not_provided`）；
       apksigner/aapt 按 Android SDK build-tools 约定发现，缺失即
       fail-closed，绝不下载安装；输出确定性 value-free JSON，不回显
       证书主体/密钥/环境变量值/本地绝对路径）；
5. [ ] 计算 SHA256（`sha256sum`）+ 尺寸，填入 `manifest.json` 对应条目
       （`signed: true`）；
6. [ ] 本地 staging（M14-174 readiness：`tools/android_release/
       stage_download.py`——先原样复用第 4 步 verify_artifact 语义，
       `versionCode` 严格递增；目标仅允许 staging root 的 `android/`
       子目录 + `manifest.json`，拒绝 symlink/绝对 URL/遍历/query/
       fragment/重复条目/已存在目标；APK 与 manifest 原子写入
       （temp+fsync+rename）后独立复核（重读重算 SHA256、重 parse
       schema）；任何失败恢复 previous manifest 字节、不删除既有
       previous APK；单测覆盖半写/中断/目录替换/previous schema 错误，
       见 `tests/android_release/test_stage_download.py`）；
7. [ ] 上传 APK + manifest 到 `download.example.com` 的 `/android/`
       （Codex 运维步骤；同源 web 侧的 manifest 命名为
       `download-manifest.json`，见第 3 节）；
8. [ ] 真机安装冒烟（沿用 `tools/android_smoke/` 套件对公网 API 端点跑一遍）；
9. [ ] preflight 人工清单 `mobile-android-apk` 项签认。

## 2. HarmonyOS signed 分发清单

前置外部资源：AGC 发布证书（.cer）、Profile（.p7b）、密钥库（.p12）——全部
仓库外保管（`AIOS_HARMONY_CERT_PATH` 等环境变量引用，见
`tools/harmony_release/preflight.py` 的 MATERIAL_ENV_VARS）。

1. [ ] AGC 创建发布证书与 Profile（绑定应用/设备范围）；
2. [ ] `--expect-signed` 模式过 preflight（材料存在且有效、signingConfigs
       非空；材料缺失 = 外部阻塞 exit 2，不是失败也不放行）；
3. [ ] release 构建 + 签名：`tools/harmony_release/release_build.py` →
       `sign_hap.py`（链条证据：build 输出哈希 = sign 输入哈希）；
4. [ ] 签名验证：`verify_signature.py` 必须 `signed_and_valid`
       （`agc_closure_manifest.py` 只认这一状态——文件名带 "signed" 不算数）；
5. [ ] **分发走 AppGallery**：AGC 提审/发布（官网只放引导页，不自签名
       sideload 作为生产通道）；
6. [ ] 官网 `download.example.com` 的 harmony 频道更新 manifest（阶段字段
       `agc-submitted`/`agc-released`；未发布前 `unsigned-blocked` 如实展示）；
7. [ ] 真机冒烟：`tools/harmony_release/device_preflight.py` +
       `auth_smoke.py` 对公网 API；
8. [ ] preflight 人工清单 `mobile-harmony-pwa` 项签认。

## 3. iPhone / 通用 PWA 清单（M14-164 应用内实施）

Web 应用自带通用安装入口（`/download` 路由），不再单独依赖下载站引导页：

1. [x] PWA manifest（`apps/web/src/app/manifest.ts` metadata route）：构建期
       生成 `/manifest.webmanifest`，`start_url` / `scope` / `id` / icons
       随 `NEXT_PUBLIC_BASE_PATH`（空 或 `/aios`）正确变化（契约见
       `apps/web/src/lib/pwa.test.ts`）；
2. [x] 图标：`apps/web/public/icons/` 192/512 × any/maskable 真实 PNG +
       `public/apple-touch-icon.png`（180，iOS 主屏）；生成脚本
       `apps/web/scripts/generate-pwa-icons.mjs` 入库、零依赖、可重复执行；
       maskable 内容收缩在中心 80% 安全区（契约见
       `apps/web/src/lib/icon-assets.test.ts`）；
3. [x] service worker（`apps/web/public/sw.js` + `sw-register.tsx` 注册）：
       仅同源 GET；API（`/api/`，含 `/aios/api/`）、认证、考试、语音、
       上传流量双侧 bypass（请求侧 + 响应侧 Set-Cookie / JSON 守卫）；
       导航 network-first、离线回退缓存壳；cache 版本化并清理旧版本
       （契约见 `apps/web/src/lib/sw-contract.test.ts`）；
4. [x] iOS Safari「添加到主屏幕」指引在 `/download` 页内联展示
       （`apps/web/src/components/download/download-panel.tsx`）；
5. [ ] 公网验收（supervisor / 真机）：iOS Safari 与 Android Chrome 实机
       安装冒烟——安装按钮出现、图标正确、独立窗口打开、离线壳可用。
       SW 的 scope 推导使其在根路径与 basePath 构建下行为一致，验收只需
       对公网实际部署形态各跑一次。

### /download 页面诚实边界（M14-164 / M14-174）

- 渠道状态的静态单一事实来源是 `apps/web/src/lib/download.ts`：
  PWA = `available`；Android（release keystore 未落地）与
  HarmonyOS（AGC 证书未落地）= `pending`；
- 静态数据层**不存在任何下载链接**（`href` 恒为 `null`，结构上
  排除误配；`download.test.ts` / `download-page.test.ts` 钉住页面与数据
  全量不出现 `.apk` / `.hap` 引用）；
- M14-174 运行时接线：Android 卡在浏览器端读取**同源** download
  manifest（`apps/web/src/lib/download-manifest.ts`，URL 为
  `${NEXT_PUBLIC_BASE_PATH}/download-manifest.json`，与 PWA 静态资源的
  basePath 拼接规则一致）。仅当 manifest 的 android 频道里**恰好一个**
  `signed: true` 且 URL/size/hash 结构可信的条目（相对 `/android/` 路径、
  无 query/fragment/协议/遍历、sha256 为 64 位十六进制、size_bytes 为
  正整数——判据与 `verify_artifact.py` 的 ENTRY_URL_RE 同语义）时，
  Android 卡才升级 `available` 并渲染下载按钮；manifest 获取失败、
  非 200、坏 JSON、schema 不符、零个或多个可信条目一律**静默降级
  pending**，绝不显示错误链接。manifest 内容不写死进任何组件
  （契约见 `download-manifest.test.ts` / `download-page.test.ts`）；
- 本切片**不宣称移动端全量生产可用**：原生包状态就绪与否以
  `download-manifest` 的 `signed: true` 为准（见下节规范），签名材料
  落地前 /download 仅如实展示 pending 与原因。

## 4. manifest（SHA256/版本清单）规范

模板：`infra/edge/download-manifest.example.json`（schema
`aios-download-manifest/1`）。发布时复制到下载目录改名为 `manifest.json`
（独立下载站形态）；同源 web 形态下部署为 `apps/web/public/`
下的 `download-manifest.json`（`/download` 页运行时读取，见第 3 节），
由 Codex 上传——本仓库不提交真实 manifest：

- `version` 与仓库 `VERSION` 一致；android `versionCode` 单调递增；
- 每个文件条目必填 `sha256`（64 位十六进制，发布时重算）、`size_bytes`、
  `signed`、`url`（站内相对路径）；
- `pending_unsigned` 数组**如实保留**未签名产物及原因（这就是当前真实状态）；
- 更新检查：客户端读 manifest 比对 `versionCode`/`version` 提示更新，
  下载后先校验 SHA256 再安装。

## 5. 下载站（download.example.com）纪律

- 目录只有：`/android/*`、`/harmony/*`、`/pwa/*`、`manifest.json`、安装说明页；
- 静态文件由 Caddy `file_server` 提供（缓存策略见 `infra/edge/Caddyfile.example`）；
- 绝不放：debug APK、unsigned HAP、任何 keystore/证书/Profile/密钥、
  内部构建日志；
- 发布 = 上传 + 重算 manifest + 真机冒烟 + preflight 签认，四步齐才更新
  引导页版本号。上传前的本地 staging 用 `tools/android_release/
  stage_download.py`（见第 1 节第 6 步）；公网上传本身由 Codex 执行，
  不在本仓库自动化范围内。
