# M11-07 RC artifact 本机取证与独立 verify

采集方式：Codex 在本机对 M11-06 记录的 RC artifact（id 9966304718）做文件级取证与独立 verify——下载、外层 zip 哈希核对、解压、包内校验和核对、repo venv 复跑 `release-candidate verify`。全程只读本机 gitignored 产物与 GitHub Actions/Artifacts 元数据；零生产连接、零生产写入、零密钥读取。

取证日期：2026-09-05；分支：`feature/m11-07-rc-artifact-local-verify`（基于 main `b2d13dae9271820414a0e6a331b34a2a328819d3`，即 PR #26 合并后 main）。

**结论：本机文件级复核全部一致（VERIFY OK）；`production_ready=false` 保持不变。本切片是本机文件级复核，不等于生产就绪。**

## 取证对象（RC run + Artifact 元数据）

| 项 | 值 |
| --- | --- |
| RC run | 33955603683 |
| workflow | Release Candidate (manual only) |
| event | workflow_dispatch |
| head_sha | f169c47c29f9529e7f0ff6c465bdf8bc1f617dd8 |
| run 状态 | success |
| artifact id | 9966304718 |
| artifact name | release-candidate-v0.1.0 |
| 外层 zip size_in_bytes | 189076705 |
| 外层 zip SHA256 | 04025053A205F069474CFA6D7B8ACCEC157D06AF24838B11760C6DE19F85924C |
| created_at | 2026-09-05T08:37:34Z |
| expires_at | 2026-09-19T08:37:16Z |

## 取证链（五步，命令与结果）

### 1. 下载到 gitignored 路径

外层 zip 下载至 `artifacts/rc-f169c47/release-candidate-v0.1.0.zip`（`artifacts/` 整目录被 `.gitignore` 覆盖，产物不入 git）。

### 2. 本机核对 zip 字节数与哈希（外层）

```powershell
Get-Item artifacts\rc-f169c47\release-candidate-v0.1.0.zip | Select-Object Length
Get-FileHash artifacts\rc-f169c47\release-candidate-v0.1.0.zip -Algorithm SHA256
```

结果：`Get-Item` Length **189076705**（与 Actions API `size_in_bytes` 一致）；`Get-FileHash` SHA256 **04025053A205F069474CFA6D7B8ACCEC157D06AF24838B11760C6DE19F85924C**，与 Actions API `digest` **完全一致**——下载字节与 GitHub 侧一致。

### 3. 解压到新建 gitignored 目录（未覆盖已有目录）

解压到新建目录 `artifacts/rc-f169c47/package`（gitignored；此前不存在，未覆盖任何已有目录）。布局恰四文件：

| 文件 | 大小（bytes） |
| --- | --- |
| aios-api-v0.1.0.tar | 338336256 |
| aios-web-v0.1.0.tar | 229956608 |
| release-manifest.json | 2022 |
| SHA256SUMS | 260 |

`release-manifest.json` 自述：version `0.1.0`、tag `v0.1.0`、git_commit `f169c47c29f9529e7f0ff6c465bdf8bc1f617dd8`（与 RC run head 一致）、scope `local-release-candidate`（boundary_note 明示非 production readiness、不授权部署）。

### 4. 包内 SHA256SUMS（档级哈希）

`SHA256SUMS`（260 bytes）恰好覆盖三档：

| 文件 | sha256 |
| --- | --- |
| aios-api-v0.1.0.tar | 6ecb1287f5d2b6b7d7b47c85250dbcef3c87a2f563bc6c275db4c5770f5c6c32 |
| aios-web-v0.1.0.tar | 3a43eb7c95e5b2b37d1d646562e76013b988a0d12da3935dbfd18356e2176758 |
| release-manifest.json | 536400dc58ec999be8bd85ca2b4144bf8f9b9920421312d429e76d4b76d81aed |

**口径声明（两层哈希是不同对象，不可互换或替代）**：第 2 步的外层 digest 是 GitHub 对 artifact **整包 zip** 计算的哈希（Artifacts API `digest` 字段）；第 4 步的 SHA256SUMS 是**包内三档文件各自**的档级哈希。两者计算对象与用途都不同——外层 digest 一致只证明「下载字节 == GitHub 侧字节」；包内档级完整性由第 5 步独立 verify 的 6 项检查证明。任何一侧都不能替换另一侧作完整性证据。

### 5. 独立 verify（repo venv，纯文件哈希复核，不加载 Docker 镜像）

从 `services/api` 目录执行：

```
..\..\.venv\Scripts\python.exe -m app.ops.cli release-candidate verify --package-dir D:\AI Learning OS\ai-learning-os\artifacts\rc-f169c47\package --version-file ..\..\VERSION
```

结果：**ok=true，problems=[]，6/6 checks pass**，输出 **VERIFY OK**：

| 检查 | 结果 |
| --- | --- |
| version_tag_consistency | pass（tag `v0.1.0` 符合 vX.Y.Z、剥离 v 后 == version `0.1.0`、api/web 镜像 tag 同步为 `v0.1.0`） |
| version_file_consistency | pass（repo 根 `VERSION` = 0.1.0，与 manifest 一致） |
| checksums_coverage | pass（SHA256SUMS 恰覆盖三档，无缺漏无多余） |
| archive_hash × 3 | pass（api tar / web tar / manifest 档级哈希独立重算全部一致） |

verify 为纯本地文件哈希复核，**未加载任何 Docker 镜像**（无 docker load）。

## 边界（未做事项，勿视为已完成）

- 本切片是**本机文件级复核**，不等于生产就绪；`production_ready=false` 保持不变。
- 未打 git tag、未发 GitHub Release、未推镜像 registry。
- 未部署、未启停任何服务、未加载 Docker 镜像。
- 未连接/未写入生产 DB。
- 未读取/输出任何密钥。
- 取证产物只在 gitignored `artifacts/rc-f169c47/` 本地留存，不入 git。
