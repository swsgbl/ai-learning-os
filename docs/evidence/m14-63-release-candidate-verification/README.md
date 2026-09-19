# M14-63 Release Candidate 独立验收证据（v0.1.0，local scope）

- **切片**：M14-63，docs-only 证据回填。零生产触碰、零代码/测试/工作流改动、不 push、不建 PR。
- **分支/worktree**：`docs/m14-63-release-candidate-evidence`，基于 `main@6ef12f878e1e3c774404478dc80099208edcbb57`（PR #145 merge commit），单 local commit。
- **回填人**：本 worktree 文档执行智能体（Claude）；RC 构建与独立下载/校验事实由 supervisor 完成并提供 gitignored 源证据。
- **本 README 为唯一入库证据文件**。原始 zip/tar/json/log 证据不入库（见文末溯源）。

## 1. 功能上下文（M14-62 / PR #145）

- 功能提交 `b5d458ef66d1d7e780f066ca18658904c6ea22a8` 随 **PR #145** 合并 main（merge commit `6ef12f878e1e3c774404478dc80099208edcbb57`，本地 git 可验证）。
- 要点：RC builder 在 `compose --no-build` 冒烟前按 compose 锚定构建本地 MinIO 镜像；MinIO 仅用于 RC 冒烟，不入 API/Web 归档与 manifest；含 CRLF/LF 与 MSYS/WSL 可移植性回归。
- CI（两组均五项 job 全 success）：
  - PR #145 checks run `35425541504`：Web test/typecheck/lint/build、API ruff/pytest/migration、Docker compose build+healthy+smoke、Android unit/lint/assemble、Release tools Python tests 全 SUCCESS。
  - 合并后 main CI run `35425724400`（workflowName `CI`，headSha `6ef12f8…`，2026-09-19T06:06:41Z 创建）：API (ruff/pytest/migration) 105851196983、Release tools (Python tests) 105851197067、Android (unit/lint/assemble) 105851197082、Docker (compose build+healthy+smoke) 105851197083、Web (test/typecheck/lint/build) 105851197154 全部 success。

## 2. Release Candidate workflow run

| 项 | 值 |
|---|---|
| run id | `35425921605` |
| workflow | `Release Candidate (manual only)`（手动触发） |
| head | `6ef12f878e1e3c774404478dc80099208edcbb57`（= main） |
| tag | `v0.1.0` |
| conclusion | `success` |
| 时间窗 | 2026-09-19T06:11:08Z → 2026-09-19T06:15:53Z |
| job | 唯一 job `Build RC package (local scope, no publish)`（databaseId `105851719018`，06:11:12Z→06:15:52Z）success |

关键 steps 全部 success：

1. `Resolve tag from input or VERSION`（step 5）
2. `Build release candidate package (images + compose smoke + archives + manifest)`（step 6，06:11:32Z→06:15:29Z）
3. `Independently verify package (manifest + checksums, no docker load)`（step 7）
4. `Upload package as workflow artifact (NOT a GitHub Release)`（step 8，06:15:30Z→06:15:47Z）

## 3. Artifact 独立下载与哈希互证

| 项 | 值 |
|---|---|
| artifact id | `10579069779` |
| name | `release-candidate-v0.1.0` |
| size（GitHub API） | `197012661` bytes |
| API digest | `sha256:097b324a077deaff4026e10492bbfd1ca57ed5f225e1fd0e4b29ff3b333c46b0` |
| workflow_run | id `35425921605`，head_branch `main`，head_sha `6ef12f8…` |
| created_at / expires_at | 2026-09-19T06:15:47Z / 2026-10-03T06:15:30Z（expired=false，捕获时点） |

supervisor 下载方式：**gh API 原样下载外层 zip**（`actions/artifacts/{id}/zip`，非 PowerShell 二进制重定向）。本地实测：`197012661` bytes，SHA-256 `097b324a077deaff4026e10492bbfd1ca57ed5f225e1fd0e4b29ff3b333c46b0`——与 GitHub API digest **逐字节互证一致**。

## 4. 包内清单（解压后恰 4 个文件，逐档哈希锚定）

| 文件 | 字节数 | SHA-256 |
|---|---|---|
| `aios-api-v0.1.0.tar` | 331,864,576 | `61881604175a205ebdf6524c0b1a82e6515438648a0418c6807b66c3d0cb4402` |
| `aios-web-v0.1.0.tar` | 239,172,096 | `8adf4466751c9c9346539a5b41c92b665314cdf9ee4ddc6d2eddb8ff6a319f50` |
| `release-manifest.json` | 2,022 | `03d5113bc2b7857a48fe7763322d9966475620faa9c2cceb68e77d0429e7a4e5` |
| `SHA256SUMS` | 260 | `b7ea75b2983f1f8f29b2b3b7f1804b6330c4a5d6551eedf4796314e0361def93` |

两个 `.tar` 镜像归档在本回填中**未打开、未解析**（任务书禁令）；其内容事实全部来自 manifest 与哈希互证。

## 5. release-manifest.json 自述关键字段

- `version` `0.1.0` / `tag` `v0.1.0` / `git_commit` `6ef12f878e1e3c774404478dc80099208edcbb57` / `scope` `local-release-candidate`；`generated_at` 2026-09-19T06:15:27Z。
- API 镜像 `aios/api:v0.1.0`，local image id `sha256:c8af43d312258a6babe7edcde98f40cb076a0c436f6b0086a00c5ee1c161bd6e`（local_only）。
- Web 镜像 `aios/web:v0.1.0`，local image id `sha256:a29626f10fc3e5bb81b81560fcf986af4ebe575c02a83a958d999edd1940c62b`（local_only）。
- compose `infra/docker-compose.yml` sha256 `9bfb8642a434caa0a2c5605fc3233035034b892f27f8b640339cdb102997eb0d`；smoke `infra/smoke_docker.sh` **passed**。
- web build arg `NEXT_PUBLIC_API_BASE_URL` = `http://127.0.0.1:8000`。
- boundary_note（原文要义）：LOCAL RELEASE CANDIDATE ONLY —— NOT a production readiness declaration、does not authorize any deployment、no registry push、no git tag creation、no GitHub Release publication。

## 6. 独立校验（supervisor 在主仓 canonical venv 执行）

- 命令（于 services/api）：`python -m app.ops.cli release-candidate verify`
- 结果：**exit 0**，`ok=true`，`problems=[]`，**6/6 checks pass**：
  1. `version_tag_consistency`
  2. `version_file_consistency`（VERSION）
  3. `checksums_coverage`
  4. `archive_hash`（aios-api-v0.1.0.tar）
  5. `archive_hash`（aios-web-v0.1.0.tar）
  6. `archive_hash`（release-manifest.json）
- **未加载任何 Docker 镜像**（verify 设计即 no docker load——文件级校验）。

## 7. 证据溯源（gitignored 源证据）

源证据目录（gitignored、不入库，位于主仓仓库根下）：`.verify/m14-62-release-candidate/run-35425921605/`

本回填读取并核对的 8 个小文件：

1. `rc-run.json` —— RC workflow run/job/steps 事实（§2）
2. `artifact-api.json` —— GitHub artifact API 元数据（§3）
3. `pr-145-checks.json` —— PR #145 五项 checks（§1）
4. `main-ci-run.json` —— 合并后 main CI run（§1）
5. `package-inventory.json` —— 外层 zip 与 4 文件字节数/SHA-256（§3、§4）
6. `verify-standalone.log` —— 独立 verify CLI 输出（§6；其 `package_dir` 字段含绝对本机路径，按纪律不复制）
7. `package/release-manifest.json` —— manifest 原文（§5）
8. `package/SHA256SUMS` —— 3 行校验和清单（api tar / web tar / manifest）

外层 `.zip` 与两个 `.tar` 按任务书禁令未打开/未解析；未读取任何 env/backup/secret。

## 8. 诚实边界

- 本证据是 **local Release Candidate 文件级独立复核**：不是 production readiness、不授权任何部署、不代表真实 provider 冒烟或长稳。
- RC 是 **workflow artifact，不是 GitHub Release**：无 registry push、无 git tag 创建、无 GitHub Release 发布、无发布审批。
- GitHub artifact 有保留期（expires_at 2026-10-03T06:15:30Z）——到期后需重新触发 RC workflow 再验收。
- 独立 verify 不加载 Docker 镜像——镜像可运行性以 RC workflow 内 compose 冒烟（smoke passed）为准，本回填不重复执行。
- 真实 provider 凭证与冒烟、soak 长稳、release readiness / 发布审批、Harmony 真机收口仍开放；全局 `production_ready=false` 不变。

## 9. 回填门禁（本切片自身）

- 仅 4 个 tracked 文档路径变更（本 README + PROJECT_STATUS + ROADMAP + CHANGELOG）。
- `git diff --check` 通过；对最终 diff 做 secret 扫描（无 token/key/password/secret）。
- 恰好一个 local commit：`docs: record release candidate verification`；不 push、不建 PR，remote 发布由 supervisor 负责。
