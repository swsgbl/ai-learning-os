# M14-44 审计锚点 WORM 真实执行记录

## 结论

M14-43 交付的 `tools/ops/audit_anchor_archive.py` 已在真实 MinIO
Object Lock 桶上完成 `preflight -> archive -> verify` 闭环：

- `preflight`: **pass**, `problems=[]`
- `archive`: **pass**, `created=true`, `worm_verified=true`
- `verify`: **pass**, `problems=[]`, `worm_verified=true`

执行窗口为 2026-09-17T19:08:57Z 至 2026-09-17T19:09:09Z。本回合为
docs-only 回填，不重启生产服务、不触碰已归档对象、不输出任何 S3 凭据。

## 版本与 CI 事实

- M14-43 feature head: `456181e3494425039bdeceb83507f6f895d018ae`
- PR: [#121](https://github.com/swsgbl/ai-learning-os/pull/121)
- Merge commit: `9c21311f00bc4bfee39d47ae0b0906242f9e5ec1`
- PR merge time: `2026-09-17T19:08:21Z`
- PR CI run `35262666887`: 5/5 jobs SUCCESS
- Merge 后 main CI run `35263086947`: 5/5 jobs SUCCESS

## 目标与源对象

- 源锚文件: `.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl`
- 大小: 354 bytes
- SHA-256: `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`
- anchor count: 1
- sequence: 0
- anchor hash: `59c672b9be0ea82dddd0d01e2f899b217674a4796b4f91f2381900e0af627c02`

### Windows 换行边界

`docs/evidence/m14-42-audit-chain-anchor/audit-anchor.jsonl` 的 Git blob
和 `.verify` 源文件均为 354 bytes、SHA-256 `d2bf…73aa`。Windows 工作区
因 Git 换行转换会显示 355 bytes、SHA-256 `4a292f…`；这不是 WORM 对象
漂移。归档与复核均以原始 354-byte LF 字节流为准。

## MinIO 与 WORM 目标

- MinIO 容器: `aios-m14-03-production-rehearsal-minio-1`
- Endpoint: `http://127.0.0.1:9000`（loopback，工具策略允许 HTTP）
- 原业务 bucket: `aios-objects`
- `aios-objects` 状态: versioning disabled、Object Lock disabled，未被修改
- 专用 WORM bucket: `aios-audit-worm`
- `aios-audit-worm`: 使用 MinIO `mc` 创建，语义为 `mb --with-lock`
- `mc` 镜像: 本机已有 pinned `minio/mc:RELEASE.2025-08-13T08-35-41Z`

`audit_anchor_archive.py` 自身不创建 bucket、不修改 bucket 配置；bucket
准备属于 supervisor 的显式运维动作。S3 凭据只在当前 PowerShell 进程环境
与 boto3 适配器内部使用，未输出到终端、Markdown 或 JSON 报告。

## 归档对象

- Bucket: `aios-audit-worm`
- Key: `audit-anchor/d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e/audit-anchor.jsonl`
- Version ID: `dc704b8d-6ebd-4acb-adb2-2135f89bb703`
- Content-Type: `application/x-ndjson`
- Size: 354 bytes
- SHA-256: `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`
- Retention mode: `COMPLIANCE`
- Retain until: `2036-09-17T19:10:00Z`
- Archive time: `2026-09-17T19:08:58Z`
- Verify time: `2026-09-17T19:09:09Z`

未执行 delete/overwrite 破坏性探针。COMPLIANCE retention 的服务端事实
已由工具的定向 head/get 与元数据核验证明；不能用尝试删除对象来“证明”
WORM。

## 证据文件

原始证据位于主仓 gitignored 目录
`.verify/artifacts/m14-43-audit-worm-archive/`：

- `preflight-20260917-190858-3cea872050da7fdafb1cf8954e9c9939.json`
- `archive-20260917-190858-92c42ed43b4ebf2dedfd0e3d7b287090.json`
- `archive-20260917-190858-92c42ed43b4ebf2dedfd0e3d7b287090.json.sha256`
- `verify-20260917-190909-84587d5101ad3daea43472cd03938890.json`

Archive JSON 复核 SHA-256:
`66703e41a9287ea2ab1fdab15eaa778d1db2618664e3cc1cb4c2b3d9b9dc4b96`。

## 边界

- 真实 WORM 归档通过，不等于全局生产就绪；`production_ready=false` 不变。
- 离线介质第二副本仍未完成。
- 定期审计锚定与归档调度仍未完成。
- 真实 search / cloud-voice / LLM provider smoke 仍未完成。
- release-readiness / cutover 审批证据仍未完成。
- 长稳与真实负载剩余面仍未完成。
- AGC 签名发布链仍未完成。
