# M14-68 生产收口 manifest 证据（AGC 收口 builder + release-closure-manifest 聚合器 + 真实收口冒烟 + docs 回填）

- 任务形态：两个代码 PR 已合并 main（#152、#153）；本任务为 docs-only
  证据回填（单 local commit；push/PR/CI/merge 由 supervisor 收口）。
- **全局判定不变：`production_ready=false`**——真实收口冒烟 exit 1（预期），
  四项 blockers 如实透出（见下）；本任务不创建任何审批文件、不代签、不代批。

## 合并与 CI 事实（git 历史 + GitHub Actions，均 5 job 全 success）

| 变更 | head | merge | PR CI run | main CI run |
|---|---|---|---|---|
| PR #152 M14-68H2 AGC closure manifest builder（`tools/harmony_release/agc_closure_manifest.py` + `tests/harmony_release/test_agc_closure_manifest.py`，+1432 行） | `fa1b9ee` | `50c9110` | `35458613813` success | `35458835384` success |
| PR #153 M14-68 release-closure-manifest 只读收口聚合器（`services/api/app/ops/release_closure_manifest.py` + `app/ops/cli.py` 接线 + `tests/test_release_closure_manifest.py`，+1455 行） | `c89abe1` | `3211336` | `35459552349` success | `35459754814` success |

- final main = `3211336`（Merge PR #153）；收口 manifest 记录的 git HEAD 即
  `3211336af6bfb0afe8f45fdba234756d9534f782`（来源 `explicit`）。

## 本地验证（PR #153 口径）

| 项目 | 结果 |
|---|---|
| 聚焦测试（release-closure-manifest 矩阵/边界） | 37 passed |
| ruff | 通过 |
| py_compile | 通过 |
| `git diff --check` | 干净 |
| full API 套件 | 3722 passed / 33 skipped；3 个失败为 Windows WSL 存量（在 main 复现同样失败，非本分支引入）；远程 Linux CI 5 job 全绿 |
| Harmony 套件 `pytest tests/harmony_release -q` | 417 passed / 1 skipped（1 skip 为新增 AGC 用例的 symlink/mkfifo 路径在 Windows 宿主按既有 `pytest.skip` 幂等语义跳过；M14-67 时为 380 passed） |

## 真实收口冒烟（main@`3211336af6bf…4f782`，generated_at `2026-09-19T18:05:18.176381+00:00`）

- **消费证据**：15 文件 / 12863 字节——与 M14-67 证据页
  （`docs/evidence/m14-67-release-readiness/README.md`）记录的 15 文件
  逐一 SHA-256 完全相同（同一证据集；manifest 本身只记录相对文件名，
  15 个文件的字节与哈希核对无一漂移）。
- **产物**（gitignored `artifacts/temp/m14-68/`，certutil 独立复验一致）：
  - `release-closure.json`：6874 字节，SHA-256
    `2332F47808ACA4C53DCA4D53CFA72970B96C379D335561B49001A5005C600D86`
  - `release-closure.md`：5104 字节，SHA-256
    `098F8D8FCA1865D34A3762D6731BB9C3E1544112BBD4D947690FAD28EFAFFF3B`
- **退出码 1（预期）**：聚合未全 pass；`production_ready=false`（readiness
  `release_ready` 与 gap `overall=pass` 的诚实合取）。
- 只读聚合子集（与 M14-67 结论一致，零漂移）：
  - release-readiness：`release_ready=false`；pass=7 / pending=0 / blocked=1 /
    missing=2 / malformed=0 / tampered=0；未过必需门 `provider-smoke`、
    `release-approval`；未过 optional 门 `turn-tls`。
  - production-evidence-gap：overall=blocked；governance=pass、
    audit-chain=pass、provider-smoke=blocked、cutover-approval=not_executed。
- **Blockers（4 项，manifest 原文）**：
  1. release-readiness 必需门未通过: provider-smoke
  2. release-readiness 必需门未通过: release-approval
  3. production-evidence-gap 类别未通过: provider-smoke=blocked
  4. production-evidence-gap 类别未通过: cutover-approval=not_executed

## 生产栈

- 零触碰：`aios-m14-03-production-rehearsal` 7 容器 healthy，API/Web probe 200。

## 边界（不声称）

- **不创建审批、不代签、不代批**：`cutover-approval` / `release-approval`
  按流程由审批人在其余门全 pass 后人工完成；manifest 输出的六条下一步命令
  是占位符形态（`<evidence-dir>` / `<artifacts-dir>` 由运维替换），不是
  已执行记录。
- **provider 凭证缺口未变**：cloud-voice / llm 冒烟未重跑（provider-smoke
  证据与 M14-67 字节一致），`provider-smoke` 维持 blocked——本任务不声称
  任何 provider pass，M14-67 记录的云凭证缺失依然成立。
- **AGC 收口 builder 无签名材料可消费**：Harmony 链保持 blocked/unsigned，
  签名性零结论（signedness 只认 `verify_signature` 的 `signed_and_valid`；
  本切片 `production_ready` 构造上恒 false）。
- 本任务 docs-only 证据回填：单 local commit；push/PR/CI/merge 由
  supervisor 收口。
- `production_ready=false` 不变。
