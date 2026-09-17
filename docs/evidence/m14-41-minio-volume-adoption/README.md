# M14-41 MinIO 生产卷属主采纳工具

## 范围

- 纯 Python 标准库 CLI，三阶段命令：`plan` / `execute` / `rollback`
- 主工具：`tools/ops/minio_volume_adoption.py`
- 契约测试：`services/api/tests/test_minio_volume_adoption.py`（零 Docker）
- plan 输出风险与阶段 JSON；execute/rollback 产出 JSON+MD 报告
- 执行工件统一落盘 `.verify/artifacts/m14-41-minio-volume-adoption/`

## 安全设计

- 属主迁移方向固定：root → uid 1000（MinIO 运行用户）
- chown 前完成备份：tar 归档 + SHA-256 + manifest（记录旧镜像 ID 与迁移前属主）
- 备份路径在停止容器前即完成边界校验与目录准备
- rollback 先校验备份工件与镜像 ID（image-id-mismatch 即拒绝），再停止/恢复
- 仅 MinIO 容器发生变更，其余容器仅做只读基线采集
- 一次性 helper 容器以 `--user 0:0` 运行并最小化挂载，chown 完成后销毁
- 停止容器前有 compose/env 元数据门禁（--env-file + --profile local）
- 一次性 helper 容器与工件路径全部 fail-closed，含符号链接组件检查（原始路径）
- argv token 白名单（GateRunner），未知参数一律拒绝

## 验证

- 卷采纳套件：106 passed
- 镜像采纳套件：127 passed
- Codex 合并运行：233 passed
- 本地 Windows 卷+镜像组合测试：233 passed
- ruff / py_compile / git diff --check 全部干净
- 单一本地提交

## 生产采纳（2026-09-17，已随 PR #118 合并 main `5bc74c4`）

- 合并前完成跨平台测试修正，PR CI run `35172658746` 五个 job 全部通过
- 合并后 main 真实 `plan` exit 0
- 真实 `execute` stamp `20260917-020446` 于 2026-09-17T02:05:03Z 判定 pass：
  - root 普查迁移为 uid 1000
  - 备份 `minio-data-backup-20260917-020446.tar`（57344 bytes，SHA-256 `c56298300087f1493f59c257f9f65206231aca10c82a4c7f35a6f3806f8a38b8`），tar / .sha256 / .manifest / report 工件齐备
  - MinIO 以容器 `8e4f3d855ffb` 重建（本地 pin 镜像 `sha256:0f1c79afdb0b5fcdd49e385c46bca065f97cdd89917522593b84436a2e61bcd6`）并 healthy
  - API `b3e62b355703` / Web `5be2e19db2e7` / LiveKit `49935f127ca0` / Postgres `d17d5a93a079` / Redis `89f3ed0fd02a` 容器 ID 不变且 healthy
- 采纳后 M14-40 preflight stamp `20260917-020606` 全部检查通过——采纳边界解除
- 采纳后 `production_recovery --dry-run`（合并后 main）exit OK：compose config OK、pin 9/9、六服务 healthy、健康栈跳过 up、FunASR 与 CosyVoice managed-running health 200 且未触碰

## 边界

- 开发全程零 Docker、零生产操作，验证全部来自 mock 契约测试；生产执行事实为 supervisor 获准窗口的记录（本回填回合零生产操作）
- rollback 未在真实生产演练（仅契约测试覆盖）
- execute pass 与 preflight pass 不等于 production_ready
- production_ready = false
