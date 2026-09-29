# M14-179：公开 /download 页生产滚动与公共浏览器验收证据（Production Rollout Evidence）

- 切片：worktree `ai-learning-os-worktrees/m14-179-production-rollout-evidence`，
  分支 `ops/m14-179-production-rollout-evidence`，基于 main
  `6fdb1f025e5dc58e4814557424c5dbfef2311e53`（PR #269 merge = M14-179
  噪音修复合入，精确基点）。
- 定性：**docs-only 切片**——零 app/运行时/模板/测试代码变更；本切片
  **不执行任何生产操作**，只把 supervisor 已完成的生产滚动、公共工件
  全新下载复核与公共浏览器验收事实落档；不读任何 secret、不宣称真机
  安装冒烟。原始工件（截图、探针日志）全部留在 gitignored 的
  `.verify/m14-179-public-download-noise/`（最终采纳为
  `PRODUCTION_DESKTOP_RUN6/` 与 `PRODUCTION_MOBILE_RUN3/`），本 README
  为唯一入库证据文件。

## 1. 生产滚动（supervisor 生产操作事实，本切片只落档）

| 项 | 值 |
| --- | --- |
| 功能链路 | PR **#269**（`fix/m14-179-public-download-noise`，feature commit `66d7230`），merge commit = 当前 main `6fdb1f0` |
| CI | PR run `36511364099` **5/5 passed**；merge 后 main run `36511744231` **5/5 passed** |
| 新生产 Web 镜像 | `aios/web:m14-179-public-download-production`，manifest digest `sha256:9650fa57…baaa5` |
| 滚动方式 | **Web-only** recreate，成功于 `2026-09-29T02:35:40Z`；新容器 ID 前缀 `e55de8fcb409`，状态 `running\|healthy` |
| 未动面 | API 镜像保持 `aios/api:m14-124-production` 未 recreate；DB / Redis / MinIO / LiveKit / SearXNG / frp / voice / proxy / gateway **全部未重启** |
| 变更前预防 | 仅改 `AIOS_WEB_IMAGE_TAG` 一个变量；改动前已做本地 env 备份 `infra/env.production-recovery.before-m14-179`（不入库，含生产环境值，永不提交） |
| 回滚锚点 | `aios/web:m14-164-public-pwa-production`（§8 镜像 tag 锚点流程：旧 tag + up -d） |

## 2. HTTP 检查（2026-09-29 时点）

| 目标 | 结果 |
| --- | --- |
| 本地 Web `/aios`、`/aios/download`、`/aios/icons/icon-192.png` | **200** |
| 公网 `https://ndtool.cn/aios`、`/aios/download`、`/aios/icons/icon-192.png`、`/aios/download-manifest.json` | **200** |
| 公网根 `/favicon.ico` | **404**（宿主域根，在 `/aios` basePath 之外，既有预期） |
| 公网 `/android/not-exist.apk` | **404**（R1 门禁：不存在的 APK 不放行） |

## 3. 公共工件全新下载复核（验收时真实重算，与 M14-177 发布值一致）

M14-179 生产验收（supervisor 提交前生产验证）**全新下载公网工件并
重算 size/SHA256**：

- 公网 manifest：**1392 bytes**，SHA256
  `1e2ebb33cc6eeb1919ab35db922cdc88121f4fad3829bbda79df121d613ef3b5`；
- 公网 APK：`/android/ai-learning-os-0.1.0-release-signed.apk`，
  **8029570 bytes**，SHA256
  `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d`。

两工件**重算值**与 M14-177 已落档发布值**逐字段一致**——这是独立
的下载复核闭环，不是对旧值的引用。边界区分：上述下载与重算发生在
supervisor 的 M14-179 生产验证内；本 docs-only 落档切片零生产操作，
不重复执行下载。

## 4. 公共浏览器验收（最终采纳：直接连接 Chrome 双视口）

方法：全新 Chrome 配置文件 + `--no-proxy-server`（**直连，绕开系统
代理**）；桌面 **1366×768**（`PRODUCTION_DESKTOP_RUN6/`）与移动仿真
**390×844**（`PRODUCTION_MOBILE_RUN3/`）各一次。两视口结果一致：

| 断言 | 结果 |
| --- | --- |
| `download-manifest.json` 请求计数 | **恰 1 次**（页面自发水合，无重复请求） |
| 匿名 `auth/me` 请求计数 | **0**（M14-179 修复目标②：公开路由跳过匿名探测，真浏览器达成） |
| 宿主根 `/favicon.ico` 请求计数 | **0**（修复目标①：basePath favicon 契约生效） |
| basePath favicon `/aios/icons/icon-192.png` | **200** |
| Android 卡 | **available**（M14-178 blocker——生产 Web 包滞后——就此闭环） |
| 下载链接 | **恰一个**，href `/android/ai-learning-os-0.1.0-release-signed.apk` |
| console / page 错误 | **0** |
| 资源加载错误 | **0** |
| 横向溢出 | **无** |
| 截图 | **非空** |

## 5. 本切片的验证（真实执行）

- 文档/版本守卫：
  `services/api/tests/test_versioning_rollback.py::test_version_sources_in_sync`
  与 `services/api/tests/test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  ——**2 passed**；
- `git diff --check` 干净；新增行 secret / 本地绝对路径 / 私有 SSH
  别名 / IP / 密码 / token / U+FFFD 扫描 **0 真实命中**。

## 6. 诚实边界

1. **未采纳的探针（如实记录）**：两次更早的本地探针经系统代理运行，
   出现瞬态 HTTP/2 / MIME 资源错误，**未采纳**为验收证据；最终证据
   全部来自直连 Chrome（`--no-proxy-server` 全新配置文件）。
2. 公共 manifest 水合可能超过 5 秒；最终探针**等待 Android 卡
   available 后**才采集断言（等待水合完成，不是跳过检查）。
3. 本验收为**浏览器仿真验收**，不是 Android 或 Harmony **真机安装
   冒烟**（`adb devices` 为空口径维持；AGC 发布材料未落地，Harmony
   公开分发仍待发布）。
4. 所有生产观察均为时点证据，不构成持续可用性保证；公网页面依赖
   家机 frpc 常驻与 VPS Nginx/frps 存活。
5. 本切片交付为 branch + 单 commit；PR 创建即止；合并决策归
   supervisor 审查（supervisor 审查与 remote 发布在其后进行）。
