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
- ruff / py_compile / git diff --check 全部干净
- 单一本地提交

## 边界

- 开发全程零 Docker、零生产操作，验证全部来自 mock 契约测试
- 真实 plan/execute/rollback 生产采纳等待 supervisor 执行
- production_ready = false
