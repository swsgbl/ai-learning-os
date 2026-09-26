# 移动分发清单（Android release / HarmonyOS signed / PWA）— M14-153

公网边缘的移动入口分发规范。配套模板：`infra/edge/download-manifest.example.json`
（部署为 `download.example.com` 的 `manifest.json`）。自动化验收入口：
`tools/ops/public_edge_preflight.py`（人工清单第 6/7 项对应 APK/HAP/PWA）。

## 0. 诚实边界（不可逾越）

- **未签名（unsigned）产物不得作为生产分发**：当前仓库的 Android 侧只有
  debug 签名 APK、HarmonyOS 侧只有 unsigned HAP——`tools/harmony_release/preflight.py`
  默认 `--expect-unsigned` 契约如实钉住这一边界（非空 signingConfigs 反而 FAIL）。
  在 AGC 签名材料与 Android release keystore 落地前，任何文档、清单、状态页
  都不得宣称移动端"生产可用/公开分发就绪"。
- 清单（manifest）里 `signed=false` 的条目只能停留在 `pending_unsigned`，
  永不进入 `files` 分发数组；debug APK 永不进入公开下载目录。
- 版本清单的每个 sha256 都必须在发布时**当场重算**，不得复制粘贴旧值。

## 1. Android release 分发清单

前置外部资源：release keystore（仓库外保管，绝不入库；丢失不可补发同签名）。

1. [ ] keystore 就绪（`AIOS_ANDROID_KEYSTORE_*` 部署变量/文件，600 权限，
       备份两处离线介质 + 保管记录）；
2. [ ] `apps/android` 侧 release 构建配置接入（signingConfigs 引用外部
       keystore，不把密码写进 build 文件/CI 明文）；
3. [ ] 构建：`gradlew assembleRelease`（CI 或本地，产物
       `app-release-signed.apk`）；
4. [ ] 校验签名方案（v2+v3）与 `versionCode`/`versionName` 递增；
5. [ ] 计算 SHA256（`sha256sum`）+ 尺寸，填入 `manifest.json` 对应条目
       （`signed: true`）；
6. [ ] 上传 APK + manifest 到 `download.example.com` 的 `/android/`；
7. [ ] 真机安装冒烟（沿用 `tools/android_smoke/` 套件对公网 API 端点跑一遍）；
8. [ ] preflight 人工清单 `mobile-android-apk` 项签认。

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

## 3. iPhone / 通用 PWA 清单

1. [ ] Web 已在 HTTPS（`app.example.com`）且有 manifest.webmanifest、
       icons、可离线壳（service worker 缓存策略与考试数据边界一致）；
2. [ ] iOS Safari「添加到主屏幕」体验可用（图标/启动屏/独立窗口）；
3. [ ] `download.example.com/pwa/` 引导页就绪。

## 4. manifest（SHA256/版本清单）规范

模板：`infra/edge/download-manifest.example.json`（schema
`aios-download-manifest/1`）。发布时复制到下载目录改名为 `manifest.json`：

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
  引导页版本号。
