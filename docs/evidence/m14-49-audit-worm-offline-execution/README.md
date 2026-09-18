# M14-49 审计锚点 WORM 离线第二副本真实执行记录

## 结论

M14-49 交付的 `tools/ops/audit_worm_offline_copy.py`（已随 **PR #129** 合并
main，merge commit `b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，feature
commit `2461306`）已在真实离线根目录上完成 `preflight -> copy -> verify`
闭环，执行基点即 `main@b9e8cc8`（与合并后 main 同 commit）：

- `preflight`: **pass**, `problems=[]`, existing state `absent`
- `copy`: **pass**, `problems=[]`, `offline_verified=true`, `created=true`
- `verify`: **pass**, `problems=[]`, `offline_verified=true`, existing
  state `matching`（`audit-anchor.jsonl` / `verify-report.json` /
  `manifest.json` 三文件全部在位）

执行窗口为 2026-09-18T02:51:22Z 至 2026-09-18T02:52:06Z。本回合为
docs-only 回填：不重跑 preflight/copy/verify、不触碰 G: 上既有离线副本、
零生产触碰、不输出任何 S3 凭据。

## 回填前只读复核（本 docs 回填回合）

按 M14-47 的回填纪律，本回填对本地 gitignored 原始证据做了只读复核（未
触碰 G:、未重跑工具）：

- 源锚 `.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl`：
  354 bytes，SHA-256 `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e` 一致
- WORM verify 报告
  `.verify/artifacts/m14-43-audit-worm-archive/verify-20260917-190909-84587d5101ad3daea43472cd03938890.json`：
  1591 bytes，SHA-256 `ff2494a8e922203f2f6ceb140e06922279ec966b47e4ffd9311c844d38fc7ddd` 一致
- M14-49 三份报告（preflight/copy/verify）逐份读取：status/problems/
  offline_verified/created/existing 字段与本文记录一致
- 原始快照 `MANIFEST.txt`：16 行、SHA-256
  `dca39fa2e8fa09c7a93a3c135a085619d05bfacc2426dc707a4525102408be83` 一致

## 源对象与 WORM 事实

复制源为 WORM 归档闭环（M14-44）的两个权威工件：

- 锚源: `.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl`
  - 大小: 354 bytes
  - SHA-256: `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`
- verify 源: `.verify/artifacts/m14-43-audit-worm-archive/verify-20260917-190909-84587d5101ad3daea43472cd03938890.json`
  - 大小: 1591 bytes
  - SHA-256: `ff2494a8e922203f2f6ceb140e06922279ec966b47e4ffd9311c844d38fc7ddd`

对应的 WORM 对象（与 M14-44 记录一致，本切片未改动）：bucket
`aios-audit-worm`、key
`audit-anchor/d2bfd877…73aa/audit-anchor.jsonl`、version
`dc704b8d-6ebd-4acb-adb2-2135f89bb703`、`COMPLIANCE` 保留至
`2036-09-17T19:10:00Z`。

## 离线根与布局

- 离线根: `G:\AI-LearningOS-Audit-Offline\worm-root-v1`
- 根 marker: `AIOS-OFFLINE-COPY-ROOT.marker`
  - 内容: `AIOS audit anchor WORM offline copy root v1` + 单个尾随 `0A`
    字节（hexdump 证实 offset `0x2C` 处仅一个 `0A`，无 BOM、无 CRLF）
  - SHA-256: `0fabda6da8344649d870c050a7bf4ef914e41a5b8b8c57c48767280c008215fa`
- 布局: `audit-anchor/d2bfd877…73aa/` 目录下三文件
  - `audit-anchor.jsonl` — SHA-256 `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`
  - `verify-report.json` — SHA-256 `ff2494a8e922203f2f6ceb140e06922279ec966b47e4ffd9311c844d38fc7ddd`
  - `manifest.json` — SHA-256 `fc8886237ea7fb0045aba37bf177cf51167f419937b57e3fdab9d8139cf105f7`

三个离线文件哈希与源侧逐字节一致（工具执行时核验；原始
`offline-copy-hashes.txt` 快照入 gitignored raw 目录）。

## 介质诚实边界：独立物理盘在线副本，不是真正离线介质

执行时盘点了本机磁盘拓扑（`Get-Volume` / `Get-Partition` /
`Get-PhysicalDisk`，原始输出入 `drive-facts.txt`）：

- `G:` 是**独立物理 Disk 1**（NVMe `UNIS SSD S3 1TB`，Healthy，Fixed，
  NTFS，Online）——与仓库所在 `D:`/`E:`（同属 Disk 0 的两个分区）物理隔离
- `F:` 是 Ventoy 可移动介质，health 状态为 warning，**未用于本副本**

因此本副本的真实性质是：**独立物理磁盘上的、仓库之外的在线（online）第二
副本**。它防御的是 Disk 0 故障与仓库工作区误删/误改，但介质始终在线挂载、
非真正断开（disconnected）、非可移动轮换介质、非离站（offsite）存储——不
满足严格 3-2-1 口径中「1 份离线/离站」的要求。**`production_ready=false`
不变。**

## 证据文件

工具报告位于主仓 gitignored 目录
`.verify/artifacts/m14-49-audit-worm-offline-copy/`（JSON + MD + `.sha256`
sidecar）：

- `preflight-20260918-025122-e1228fd605f8983c60a6366d6db43b32.json` —
  status `pass`, `problems=[]`, existing `absent`
- `copy-20260918-025149-759571c89db8be880839ac20bc079fce.json` —
  status `pass`, `problems=[]`, `offline_verified=true`, `copy.created=true`
- `verify-20260918-025206-0826caff942ce82143d03f6db3609265.json` —
  status `pass`, `problems=[]`, `offline_verified=true`, existing `matching`

supervisor 原始执行快照位于 gitignored
`.verify/m14-49-audit-worm-offline-execution/raw/`（含 drive-facts、
marker-hash、marker-hex、offline-copy-hashes、execution-context、git-head
与三份报告副本等）。其 `MANIFEST.txt` 经独立核验：**16 条目、0 哈希失配**；
MANIFEST 自身 SHA-256
`dca39fa2e8fa09c7a93a3c135a085619d05bfacc2426dc707a4525102408be83`；
secret 扫描通过。原始快照与报告本体均不入 git，本 README 为唯一入库证据
文件，以上哈希即完整性锚点。

## 边界

- 本副本是独立物理盘在线副本，**不是**真正断开/可移动/离站介质（见上节）；
  离线/离站副本仍未完成。
- 定期审计锚定与归档调度仍未完成（当前锚链仅 sequence 0 一条锚）。
- 单次复制 + 单次核验 ≠ 长期介质健康保障；G: 盘健康状态需持续关注。
- 本回填未重跑任何工具命令、未读取/写入 G: 上任何文件。
- 真实 search / cloud-voice / LLM provider smoke、release-readiness /
  cutover 审批、长稳剩余面、AGC 签名发布链仍开放。
- 以上均不改变全局判定：`production_ready=false`。
