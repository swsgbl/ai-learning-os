# M11-06 最新 main Release Candidate 证据刷新

采集方式：GitHub Actions / Artifacts API 只读查询（runs / jobs / steps / artifacts 元数据）。RC run 由运维显式 `workflow_dispatch` 触发。全程零生产写入。

采集日期：2026-09-05；分支：`feature/m11-06-latest-main-rc-evidence`（基于本地 `58016b7`，tree=`d56a893f3488a58718370d778abeecf382734a6a`，与远端 main merge commit `f169c47c29f9529e7f0ff6c465bdf8bc1f617dd8` 的 tree 完全一致）。

**结论：production_ready=false 保持不变。RC 成功不等于生产就绪。**

## 远端 main 状态

远端 main 已到 `f169c47c29f9529e7f0ff6c465bdf8bc1f617dd8`（PR #25 merge commit，merged_at 2026-09-05T08:23:42Z；PR head `58016b7`）。

## 合并后 main CI

| 项 | 值 |
| --- | --- |
| run | 33955138480 |
| head | f169c47c29f9529e7f0ff6c465bdf8bc1f617dd8 |
| jobs | Web / API / Docker 三 job 全绿 |
| API pytest | 1365 passed, 5 skipped |

## 最新 main 手动 RC

| 项 | 值 |
| --- | --- |
| workflow | Release Candidate (manual only) |
| run | 33955603683 |
| event | workflow_dispatch |
| head_branch | main |
| head_sha | f169c47c29f9529e7f0ff6c465bdf8bc1f617dd8 |
| 状态 | success |
| 时间 | 2026-09-05T08:33:50Z 至 2026-09-05T08:37:37Z |
| URL | https://github.com/swsgbl/ai-learning-os/actions/runs/33955603683 |

job **Build RC package (local scope, no publish)** 全步骤 success，包括：构建 API 镜像、构建 Web 镜像、compose 冒烟、独立 verify、artifact 上传。

### 独立 verify 日志结论

manifest / 校验和 / 逐档哈希全部一致（verify 未加载 Docker 镜像，纯本地文件哈希复核）。`SHA256SUMS` 恰好覆盖以下三档：

- `aios-api-v0.1.0.tar`
- `aios-web-v0.1.0.tar`
- `release-manifest.json`

## Artifact

| 项 | 值 |
| --- | --- |
| id | 9966304718 |
| name | release-candidate-v0.1.0 |
| size_in_bytes | 189076705 |
| digest（外层 zip） | sha256:04025053a205f069474cfa6d7b8accec157d06af24838b11760c6de19f85924c |
| created_at | 2026-09-05T08:37:34Z |
| expires_at | 2026-09-19T08:37:16Z |
| expired | false |

**口径声明**：上述 digest 是 GitHub artifact **外层 zip** 的 digest（Artifacts API `digest` 字段）——它是 GitHub 在 upload 后对整包 zip 计算的哈希，**不是**包内 `SHA256SUMS` 所列三档文件哈希的替代表示，两者计算对象与用途不同，不可互相换算或替代。包内档级完整性以 run 内独立 verify 的结论为准。

## 边界（未做事项，勿视为已完成）

- 不打 git tag、不发 GitHub Release、不推镜像 registry。
- 不部署、不启停任何服务。
- 不连接/不写入生产 DB。
- 不读取/输出任何密钥。
- `production_ready=false` 保持不变；RC 成功不等于生产就绪。
