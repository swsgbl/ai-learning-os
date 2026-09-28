# M14-177：Android 下载边缘生产部署证据收口（Android Download Edge Evidence）

- 切片：worktree `ai-learning-os-worktrees/m14-174-android-release-channel`
  （复用既有 worktree），分支 `ops/m14-177-android-download-edge-evidence`，
  基于 main `eda6659b676268e0a12f9d84e07fb1f6162e66a8`（PR #266 merge =
  M14-176 下载边缘路由合入，精确基点）。
- 定性：**docs-only 证据收口**——零运行时代码、零模板、零测试、零
  compose、零 env、零 infra 变更；不对生产做任何变更、不重启/停止任何
  服务、不读写任何 secret。本 README 为唯一入库证据文件；以下生产
  事实均为 Codex 执行、supervisor 复核落档的时点证据。任何 token /
  密码 / IP / 私有主机细节 / 本地 SSH 别名值一律不写入（仓库纪律）。

## 1. 基点与 PR / CI 链

| 项 | 值 |
| --- | --- |
| main 基点 | `eda6659b676268e0a12f9d84e07fb1f6162e66a8`（PR #266 merge，M14-176 "Android download edge routing"） |
| PR | **#266**，state **MERGED**，merge commit 即上表基点 |
| CI | PR checks **5/5 SUCCESS**（Android / API / Docker / Release tools / Web）；merge-post main CI **5/5 SUCCESS** |

## 2. 生产配置事实（VPS 边缘，Codex 部署）

| 项 | 值 |
| --- | --- |
| 部署片段 | `/etc/nginx/aios-base-path.locations.conf`（§3E/§3G 既定 include 位置；八个 location：六个既有 AIOS 路由 + M14-176 ⑦⑧ 下载静态路由，含 R1 `.apk` 门禁） |
| 生产配置 SHA256 | `3788358bcffa985e24dcafd3fb0cf3bb9e6da534b37c6917f15d7ad9bd749ec5` |
| 回滚备份 SHA256 | `21d593cc490527dee653862df9aa3e69a2a48ac01b164ecbee2783595cdaa660`（变更前副本，回滚锚点——见 §3G 回滚边界） |
| 配置校验与服务 | `nginx -t` 通过；reload 后 nginx 服务 **active** |

## 3. 已发布产物事实（`/var/www/aios-downloads/`，stage_download.py 形状 1:1 上传）

| 项 | 值 |
| --- | --- |
| download manifest | `manifest.json`，SHA256 `1e2ebb33cc6eeb1919ab35db922cdc88121f4fad3829bbda79df121d613ef3b5` |
| release APK | `ai-learning-os-0.1.0-release-signed.apk`，SHA256 `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d`，size **8,029,570 bytes** |

## 4. 外网验证事实（`https://ndtool.cn`，reload 后时点）

| 检查 | 结果 |
| --- | --- |
| `GET /aios/download-manifest.json` | **200**；`Content-Type: application/json`；`Cache-Control: no-store` |
| `GET /android/ai-learning-os-0.1.0-release-signed.apk` | **200**；`Content-Type: application/vnd.android.package-archive`；`Cache-Control: public, max-age=3600` |
| 公网下载字节复核 | 公网实际下载的 manifest / APK 重算 SHA256 与 §3 表内值**逐字节一致** |
| `GET /android/notes.txt` | **404**（R1 `.apk` 门禁：非 .apk 后缀） |
| `GET /android/foo.apk.txt` | **404**（R1 `.apk` 门禁：.apk 仅在中间） |
| `GET /android/foo.html` | **404**（R1 `.apk` 门禁：可渲染页面形态） |
| `GET /android/` | **404**（目录 URI 被门禁拒绝，无列表） |
| `GET /android/nope.apk` | **404**（门禁放行、缺文件） |
| `GET /aios` | **200**（§3E canonical 入口行为不变） |
| `GET /aios/health` | **200**（§3E 精确健康路由行为不变） |
| `GET /aios/download` | **200**（`/download` 页可达，Android 卡按 manifest 运行时判定） |

## 5. 公网 APK 签名复核（apksigner）

对**公网实际下载**的 APK（非本地 staging 副本）执行 `apksigner verify`：

| 项 | 值 |
| --- | --- |
| v2 | **true** |
| v3 | **true** |
| 签名证书 SHA256 | `b583ed9e75840ff4b3c019398c6ad7e3ee4e0d90f6b4d56ef4466c3d8e58e9bf` |

与 M14-173 verify 门禁（v2+v3、拒绝 debug 证书）及 M14-175 签名方案
钉死（v1 关、v2/v3 显式开）一致；结合 §4 的下载哈希复核，公网分发链
首末端字节一致、签名方案一致。

## 6. 本切片的验证（docs-only 口径，真实执行）

- 文档/版本守卫（零网络）：
  `services/api/tests/test_versioning_rollback.py::test_version_sources_in_sync`
  与 `services/api/tests/test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  ——通过；
- runbook/模板契约测试（§3G 收口段不破坏既有 needle）：
  `services/api/tests/test_public_edge_nginx_base_path.py` **30 passed**、
  `services/api/tests/test_edge_deployment_templates.py` **22 passed**、
  `tests/android_release/test_preflight.py` **51 passed**；
- `git diff --check` 干净；
- 新增行 secret / 本地绝对路径 / 本地 SSH 别名值扫描 0 命中
  （仅公开可得的哈希 / 状态 / 响应事实）。

## 7. 诚实边界

1. 本切片是 **docs-only 证据收口**：所有生产观察均为时点证据，不构成
   持续可用性保证。下载面本身只依赖 VPS Nginx 存活（下载流量不经
   家机隧道，家机停机不影响已发布 APK 下载）；但 `/aios` 页面与
   `/aios/api/` 仍依赖家机 frpc 常驻（§3F 既有口径）。
2. **Android 真机安装冒烟未执行**：`adb devices` 为空（无连接设备）。
   公网下载 + apksigner 复核不能替代真机安装/启动/登录冒烟；
   `docs/MOBILE_DISTRIBUTION.md` §1 第 8 步（真机冒烟）与第 9 步
   （preflight 人工清单签认）保持未勾选。
3. **Harmony 公开分发仍被阻塞**：AGC 发布材料未落地
   （`docs/MOBILE_DISTRIBUTION.md` §0/§2 口径维持）。
4. §2 的生产配置/回滚备份哈希是**部署副本**的各自事实（在仓库模板
   基础上由运维落地），不与仓库模板文件哈希互推；回滚 = 用 §2 备份
   哈希对应的副本还原 + `nginx -t` + reload（§3G 回滚边界，下载目录
   文件保留不动）。
5. 任何 token / 密钥 / secret 值、生产 IP、私有主机细节、本地 SSH
   别名值一律不入库；本文件仅含公开可得的哈希 / 状态 / 响应事实。
6. 本切片交付为 branch + 单 commit；PR 创建即止；合并决策归
   supervisor 审查（supervisor 审查与 remote 发布在其后进行）。
