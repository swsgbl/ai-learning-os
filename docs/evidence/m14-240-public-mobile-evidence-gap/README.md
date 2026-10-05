# M14-240: current public mobile evidence gap report（文件面缺口报告——docs-only，非就绪声明）

## 0. 结论与边界

- 切片：worktree
  `ai-learning-os-worktrees/m14-240-public-mobile-evidence-gap`，分支
  `ops/m14-240-public-mobile-evidence-gap`，基于 current main
  `2005412c1be2085422196e3ad4f00ac6e33dd49c`（PR #326 merge，即
  M14-239）。交付为 1 个本地 commit，不 push、不开 PR、不合并。
- 性质：**证据/缺口切片（evidence gap slice），不是 readiness 切片**。
  本切片把已合并的 M14-238 计划器
  （`tools/android_release/public_mobile_evidence_plan.py`）跑在一台
  **真实的当前证据清单**上，产出 M14-237 公网移动发布聚合门六个证据
  角色的**文件面**缺口报告。**本切片不声称任何公网移动就绪**：
  `complete=false` 与 blockers 是预期且如实的输出——只要任何角色
  真缺席，blocker 就必须在场。
- 零触碰边界（全程遵守）：零 Docker/compose/生产 restore/frpc/
  公网探测/设备/adb/签名/AGC/打包/网络探测/任何变更性命令；零
  secret 读取或打印；零生产代码/测试/workflow/infra 修改；不制造
  任何占位证据内容（缺席角色保持缺席并产生 blocker）；不改写、
  不弱化 M14-237/M14-238/M14-239 证据；`.verify/` 工件不入 git。
- 变更面恰 4 个 Markdown 文件：本 README + `docs/PROJECT_STATUS.md` /
  `docs/ROADMAP.md` / `docs/CHANGELOG.md` 三台账。

## 1. 方法（诚实清单规则）

- 只盘点**权威、在场、本地可得**的工件：每个角色取**能从当前仓库
  证据证明的最新有效源工件**；无工件的角色在 manifest 中登记其预期
  相对路径，由计划器如实报 `missing-file:<role>`。
- 候选来源歧义时**报告歧义而不是发明证据**（见 §4 edge_preflight
  的来源歧义说明）。
- 真实清单目录在 gitignored
  `.verify/m14-240-public-mobile-evidence-gap/`：源文件存在时**逐字节
  拷贝**入该目录（源与拷贝独立哈希核验，见 §6），manifest 只用相对
  POSIX 路径；`cloudflare_preflight` 无当前工件故为 `null`（M14-237
  可选角色，缺席合法）；`freshness_hours` 不设键（用契约默认 24——
  本报告不做也不暗示任何新鲜度判断，回放时窗口是操作者的显式选择）。

## 2. 角色 → 源工件对照表

源路径以仓库上下文描述（canonical checkout = 主检出；无任何本地
绝对路径/用户名/凭据）。字节与 SHA-256 为对源文件与 `.verify` 拷贝
**双侧独立重算**的一致值。

| 角色 | 状态 | 源工件（仓库上下文相对描述） | bytes | SHA-256 |
| --- | --- | --- | ---: | --- |
| `restore_preflight` | **present** | canonical checkout `.verify/m14-236-current-machine-preflight/machine-preflight.json`（M14-236 README 锚点哈希一致，见 §4） | 3142 | `4f3f4be1c39750c1e44b31c0d525e04638e7d94da9b5c9bf368c516eccd15113` |
| `edge_preflight` | **present**（来源歧义，见 §4） | canonical checkout `.verify/public-edge-preflight-post-recovery-partial-20261004.json` | 3211 | `84ef0b5b28db3dcff03ac245dc9a07dad4a34d36748bf11a2d92842cad5a135f` |
| `device_smoke` | **present** | worktree `m14-229-android-focus-probe` `.verify/m14-229-android-public-device-focus-fallback-r2/report.json`（M14-229 README run 2；连同其 8 个 evidence 同目录文件整目录拷贝，保住 M14-237 的 evidence.files 逐文件哈希复核可回放，锚点见 §7） | 3979 | `a7927e6bf92410769a9aa2453bef0cb16da42fe22fe863fb81ad2a53a7840b31` |
| `cloudflare_preflight` | **null**（可选，无当前工件） | 无——M14-205 交付的 `tools/ops/cloudflare_ingress_preflight.py` 从未真实 execute（该切片零真实云请求；后续也无真实执行工件） | — | — |
| `release_evidence` | **present** | worktree `m14-219-current-main-release-evidence` `.verify/m14-219-current-main-release-evidence/evidence/release-check.json`（M14-219 README 锚点哈希一致） | 2489 | `ebc29a4f100f1de60e37d07c290ecd219491e2a7a7bcb1836b4b2b3a23d879fb` |
| `attestation` | **MISSING**（无任何源工件） | 无——受约束人工签认从未产生；manifest 登记预期路径 `attestation/public-mobile-attestation.json`，目录保持为空，**未制造占位内容** | — | — |

选择依据（最新可证明者）：

- `restore_preflight`：M14-236 快照（2026-10-05T04:15:49Z，schema
  `/2`）是仓库证据锚定的最新 restore preflight；早于它的
  2026-10-04 两份顶层工件为旧 schema `/1`，不取。
- `release_evidence`：M14-237 白名单两个权威导出器中，取**最新**的
  `release-check` 导出（M14-219，2026-10-02T22:53:28Z，
  `all_green=true`）。备选导出器 provider-smoke-aggregate 的最新
  **完整聚合**仍是 M14-209（2026-10-01，search/llm fail；M14-222
  明确本轮无新聚合），更旧且槽位失败，不取；如实记录于此。
- `device_smoke`：M14-229 run 2（2026-10-03 18:04–18:05 UTC）是
  最新一次真实公网设备冒烟运行（M14-214/226/227 均更早）。
- `edge_preflight`：2026-10-04T06:34:27Z 工件是磁盘上最新的
  `public_edge_preflight.py` 输出；M14-235/236 快照中公网边缘均保持
  `public-edge-uncertain`（restore preflight 固定边界），其间无更新
  的边缘 preflight 运行。

## 3. 计划器真实运行（真实清单，非合成 demo）

从 worktree 根目录（命令与退出码原样留痕于
`.verify/m14-240-public-mobile-evidence-gap/console.log`）：

```sh
python tools/android_release/public_mobile_evidence_plan.py \
  --manifest .verify/m14-240-public-mobile-evidence-gap/manifest.json \
  --output .verify/m14-240-public-mobile-evidence-gap/out
```

- **退出码 1（BLOCKERS）**；控制台输出
  `[public-mobile-evidence-plan] BLOCKERS: complete=False blockers=1`
  与 `blocker: missing-file:attestation`。
- 报告 `generated_at=2026-10-05T21:16:11Z`；`complete=false`；
  blockers 恰 `['missing-file:attestation']`；五个文件在场角色
  present/bytes/SHA-256 全部与 §2 独立重算一致；可选
  `cloudflare_preflight=null` 零 blocker（镜像 M14-237 可选输入语义）。
- 出口语义复跑（`--output .../out-rerun`）再次 exit 1、同一 blocker
  集、角色事实与 replay 命令逐字段一致（仅 `generated_at` 随真实
  时钟不同）——exit 语义可复现。
- 生成的 M14-237 回放命令（计划器从 manifest 自身相对路径精确重构，
  仅供后续运维参考；本切片**未运行**该命令）：

```sh
python tools/android_release/public_mobile_release_gate.py \
  --restore-preflight restore-preflight/machine-preflight.json \
  --edge-preflight edge-preflight/public-edge-preflight-post-recovery-partial-20261004.json \
  --device-smoke device-smoke/report.json \
  --release-evidence release-evidence/release-check.json \
  --attestation attestation/public-mobile-attestation.json \
  --output <output-dir> --freshness-hours 24
```

（无 `--cloudflare-preflight` 旗标——该角色为 `null`。）

## 4. 文件面 ≠ 语义面：计划器边界与如实语义事实

**计划器只做文件面盘点**（在场性/basename/字节数/SHA-256），**绝不
解析子报告语义**——这是 M14-238 的固定边界。**权威语义门仍是
M14-237**：即使六个角色文件全部在场，M14-237 也会按各报告自身的
schema/tool/状态/时间戳/新鲜度语义独立判定。为避免误读，本切片如实
列出各在场角色的已知语义事实（均为只读自源 JSON 的机器导出字段；
**在 M14-237 回放时它们会各自成为 blocker**）：

- `restore_preflight`：schema `aios-production-restore-preflight/2`，
  `generated_at=2026-10-05T04:15:49Z`，**`verdict=blocked`**（blockers
  `local-image-missing`、`persistent-volume-missing:postgres-data`、
  `persistent-volume-missing:minio-data`；容器 `stack-absent 0/6`）。
  M14-237 要求 `verdict=healthy`——当前不满足。
- `edge_preflight`：schema `aios-public-edge-preflight/1`，
  `generated_at=2026-10-04T06:34:27Z`，**`exit_code=1`**、
  `summary failed=1/passed=5/total=6`（失败检查
  `api-cookie:ndtool.cn`）、`mobile_attestation.status=pending`
  （7 项缺项，含五项核心观察）。M14-237 要求 exit 0 + failed=0 +
  `mobile_attestation=attested`——均不满足。**来源歧义（如实报告）**：
  该文件是磁盘上最新的权威工具输出（JSON 自带 schema/tool/
  generated_at 机器字段），但**没有任何 tracked 证据 README 锚定它**
  （文件名与时间戳表明其产生于 2026-10-04 恢复会话窗口，紧随同日
  06:31 的 restore preflight 之后）；其文件面事实成立，仓库级出处
  锚点缺失即本节所述歧义——不发明解释，交由后续边缘 preflight
  重跑自然取代。
- `device_smoke`：`schema_version=1`、tool
  `android_release_public_device_smoke`、serial `EYFBB22923201473`，
  **`status=blocked`**（failure `health / health_network_unavailable`；
  该 run 装置→manifest→APK 下载→verify_artifact→replace-install→
  启动稳定性全过，止步于首个公网 API 探测，详见 M14-229）。
  另：该报告**早于** M14-237 给 `public_device_smoke.py` 增补的顶层
  `generated_at` 字段，故无该字段——M14-237 要求它在场。重跑当前
  工具即可自然补上。
- `release_evidence`：tool/gate `release-check`、
  `all_green=true`、`generated_at=2026-10-02T22:53:28Z`——白名单内
  导出器、语义为通过；但其语义是**本机隔离环境**（本地 SQLite +
  回环临时 API + auth-off）的 all_green，且时点早于当前 Docker
  数据面 blocked 状态的一切后续观察；新鲜度与就绪解释权在
  M14-237 回放时点。
- `cloudflare_preflight`：`null`（可选缺席，合法计划形态）。
- `attestation`：**无工件**——`missing-file:attestation` 是本报告的
  唯一文件面 blocker；五项必需覆盖（`mobile-4g5g-open`、
  `mobile-cross-origin-cookie`、`mobile-voice-connect`、
  `mobile-turn-relay`、`mobile-android-apk`）从未被人工签认。

## 5. 下一步（闭合形式；均需运维显式授权，agent 不代行）

1. **（a）外部 Docker 数据面恢复——归他处所有，非本切片**：从已
   验证的备份恢复 `postgres-data`/`minio-data`（或完成显式
   fresh-install 决策并留证；绝不静默 `compose up` 制造空卷），在
   受批窗口重建 pinned 本地镜像（`aios/api:m14-211-production`、
   `aios/web:m14-193-production`、
   `aios/minio:RELEASE.2025-10-15T17-29-55Z`）；随后**新的只读**
   `tools/ops/production_restore_preflight.py` 运行至
   `verdict=healthy`。在 (a) 完成前禁止一切 compose up/生产 restore/
   验收声明。
2. **（b）仅在健康生产之后才可再生的证据**：新的
   `tools/ops/public_edge_preflight.py`（exit 0、failed=0、
   `mobile_attestation=attested`）+ frpc status 验收；在 current
   main 上新的 provider-smoke 完整聚合（voice/search/llm 三槽位
   executed+pass）或新的 release-check 导出；公网 host 部署 AI
   Learning OS API 面（M14-229 时点 `/aios/health`、
   `/api/v1/auth/status` 均 404）后重跑
   `tools/android_release/public_device_smoke.py` 至 `status=passed`
   （当前工具自然会带 `generated_at`）。
3. **（c）真机/公网/语音/TURN/APK 签认缺口**：产出受约束人工
   attestation（`kind=public-mobile-attestation`，五项必需覆盖，
   `evidence_files` 至少一项与门实际消费输入 (basename, SHA-256)
   全等锚定）——该项只能由真实观察产生，不得代拟。
4. **（d）可选 Cloudflare 证据**：操作者供凭据后真实 execute
   `tools/ops/cloudflare_ingress_preflight.py`（凭据永不经 agent）；
   缺席不构成 blocker，提供即严格校验（zone 与 edge 公网 host
   归属一致）。

在 (a)–(c) 全部真实完成之前，不得声称公网移动就绪；本 README 的
`complete=false` 与 blocker 集即当前诚实状态。

## 6. 本切片验证（docs-only；全部只读）

- **逐字节同一性 + 独立哈希**：13 个拷贝文件（4 个角色文件 +
  9 个 device-smoke 同目录文件）源↔拷贝字节同一、SHA-256 双侧独立
  重算一致（`verify_copy.py`，`RESULT: ALL-COPIES-BYTE-IDENTICAL`，
  exit 0）；`attestation/` 目录确认为空（无占位内容）。
- **生成 JSON 解析与交叉核验**：`parse_check.py` **37 项断言全过**
  （schema/tool/complete=false/blockers 精确集/freshness 默认 24/
  五角色 present+bytes+SHA-256 与独立重算一致/attestation 缺席事实/
  cloudflare null 语义/replay 命令内容与无-cloudflare-旗标/复跑等价
  （角色事实与 replay 逐字段一致）/报告脱敏（零绝对路径、零 Windows
  用户名、零 U+FFFD）），exit 0。
- **计划器退出语义复现**：两次真实运行均 exit 1、同一 blocker 集
  （见 §3）。
- `git diff --check` 干净；新增行扫描：零 secret/凭据值、零本地
  绝对路径、零 Windows 用户名、零 U+FFFD。
- 仓库无既有 docs/markdown 校验命令（根 `package.json` scripts 仅
  apps/web dev/build/lint/typecheck 与 pytest，无 markdownlint 配置
  或依赖），该项如实注明未运行；docs-only 切片不跑 pytest。

## 7. `.verify` 工件锚点（gitignored，不入仓库）

路径前缀：`.verify/m14-240-public-mobile-evidence-gap/`

| 文件 | bytes | SHA-256 |
| --- | ---: | --- |
| `manifest.json` | 422 | `e6d1968234765d32c521f8f26bf523e7b645c56f94d865e4d996022f52e5687e` |
| `restore-preflight/machine-preflight.json` | 3142 | `4f3f4be1c39750c1e44b31c0d525e04638e7d94da9b5c9bf368c516eccd15113` |
| `edge-preflight/public-edge-preflight-post-recovery-partial-20261004.json` | 3211 | `84ef0b5b28db3dcff03ac245dc9a07dad4a34d36748bf11a2d92842cad5a135f` |
| `device-smoke/report.json` | 3979 | `a7927e6bf92410769a9aa2453bef0cb16da42fe22fe863fb81ad2a53a7840b31` |
| `device-smoke/download-manifest.json` | 1392 | `1e2ebb33cc6eeb1919ab35db922cdc88121f4fad3829bbda79df121d613ef3b5` |
| `device-smoke/ai-learning-os-0.1.0-release-signed.apk` | 8029570 | `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d` |
| `device-smoke/adb-device.json` | 347 | `9a26d343dcda06a81b2480f5c7ecc0878d9246a60a0173107655648887c0c0db` |
| `device-smoke/package-dumpsys.txt` | 4760 | `427c3c25c9cb451deb716406111974576c87886596b4a3f3fc56023af337384a` |
| `device-smoke/process-window.json` | 355 | `438a8e042c201bfb90af3d4f1fede60be0b94d468703003bace8fea57daa1a22` |
| `device-smoke/window-layout.xml` | 16994 | `0d1105a68ae688c549a26055edc6894626c4821cd18a143b833226cc350138d9` |
| `device-smoke/screenshot.png` | 4590 | `1a228f3e7ebc7742b17407eaf84bd356a0cddb052ae4d4bef55ebb1c965e4269` |
| `device-smoke/logcat.txt` | 407292 | `0ed32a0c8dc3e4129403cd68449ce5e96bb27e557b6d5b5354509a21439362bb` |
| `device-smoke/report.md` | 1599 | `82ccc86da0587a7a6d1cc1596c8ae990aafacdcc5bf79ee671aa95085b7a51eb` |
| `release-evidence/release-check.json` | 2489 | `ebc29a4f100f1de60e37d07c290ecd219491e2a7a7bcb1836b4b2b3a23d879fb` |
| `attestation/public-mobile-attestation.json` | — | — （**缺席**；目录为空，无占位内容） |
| `out/public-mobile-evidence-plan.json` | 2590 | `678e27c741803800479df93d05a16a205414f8ebf8b123a4d843ac166ea22a75` |
| `out/public-mobile-evidence-plan.md` | 1847 | `85e57084a1eb63ce591b9ea893cbe3b5946bd15177bb00047ba8766c8e34c362` |
| `out-rerun/public-mobile-evidence-plan.json` | 2590 | `b0bfd9d1e5f7f3f1facd3cf632dca856f696b035a4ce5e23456b9b51690ae97f` |
| `console.log` | 5558 | `569573e2d06ec034a97827d0ca23a4852a886acc1e78ee0ff9c645277467dc50` |
| `verify_copy.py` | 3206 | `d334ae5f1becaff6d17c4abbfdfd69eb0f77ca8b54dfd480d4c1392054c3eab7` |
| `parse_check.py` | 4712 | `7fd2e5a89e8aca374cfe2db491887281fb722dddb6bdf07bb9b6b29934883a57` |

（`out-rerun/public-mobile-evidence-plan.md` 与 primary 同构，
`generated_at` 随真实时钟不同。）
