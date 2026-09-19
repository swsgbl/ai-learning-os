# M14-67 Release Readiness 证据（真实产物汇总）

- 分支：`worktree-m14-67-release-readiness`（base `a813b3b`，即 PR #149 merge commit `a813b3bfbd33a9f6318e21cbfd4b06d083b2c3ef`）
- 证据目录：`.verify/m14-67-release-readiness/evidence/`（15 个文件，全部来自真实运行产物或据其确定性包装，无任何发明 pass/approval）
- 聚合器输出：`artifacts/temp/m14-67/{cutover-rehearsal,release-readiness,production-evidence-gap,provider-smoke}.json`（gitignored，见本页哈希）
- **production_ready = false**（如实：云 voice/LLM 冒烟失败、审批缺失，见"精确阻断项"）

## 关键事实（全部来自真实运行）

| 事实 | 值 |
|---|---|
| release-check（full） | all_green=true，10/10；API 套件 3687 passed / 33 skipped；SHA256 `d42aea267810193c1dca02a73bd8255c623535847417a7bf3b1cf37a24e1500c` |
| CI（GitHub Actions） | run `35441378690` @ `a813b3bfbd33a9f6318e21cbfd4b06d083b2c3ef`，conclusion=success |
| backup-restore | manifest SHA256 `772c76c9caaa05c517a7d71ec1d98a6f1be2a449126a4525d134ec4f1fef2491`，restore verified=true，inserted_rows=32 |
| preflight pre-migration | pass=4 / pending=1 / fail=0（pending 属迁移前预期形态） |
| preflight post-migration | pass=5 / pending=0 / fail=0（放行形态） |
| 审计链 | verify valid=true，entries=0；anchor up-to-date，anchors=1（m14-42 锚文件副本自洽）；WORM archived=true（M14-44/M14-49 建立并合并） |
| 治理 | legacy-papers pending=0；draft-ownership pending=0（governance-evidence 推导，本任务补充 `gate` 字段并重新生成） |
| provider 冒烟 | search=pass；cloud-voice=fail；llm=fail（云凭证缺失，如实记录） |
| 聚合治理测试 | `tests/test_governance_evidence.py` 82 passed；ruff 通过 |

## 三个聚合器结果（命令退出码如实为 1）

| 工具 | overall | 明细 |
|---|---|---|
| cutover-rehearsal（exit 1） | blocked | 10/13 步 pass；blocked: cloud-voice-smoke、llm-smoke；not_executed: cutover-approval |
| release-readiness（exit 1） | NOT READY | pass=7 / blocked=1 / missing=2；未过门: provider-smoke(blocked)、release-approval(missing)、turn-tls(missing，optional) |
| production-evidence-gap（exit 1） | blocked | governance=pass、audit-chain=pass、provider-smoke=blocked、cutover-approval=not_executed；production_ready=false（固定） |

## 证据矩阵（15 文件）

| 文件 | 来源 | step / gate | 结论 |
|---|---|---|---|
| ci-main.json | 包装（GitHub Actions 事实） | ci-main / ci-main | pass（success） |
| release-check.json | 真实导出 | release-check / release-check | pass（all_green 10/10） |
| search-smoke.json | 真实导出 | search-smoke | pass |
| cloud-voice-smoke.json | 真实导出 | cloud-voice-smoke | fail（云凭证缺失） |
| llm-smoke.json | 真实导出 | llm-smoke | fail（云凭证缺失） |
| legacy-papers.json | governance-evidence 重新生成 | legacy-papers / legacy-papers | pass（pending=0） |
| draft-ownership.json | governance-evidence 重新生成 | draft-ownership / draft-ownership | pass（pending=0） |
| preflight-pre-migration.json | 真实 preflight 包装 | preflight-pre-migration / production-preflight | pass（无 fail） |
| preflight-post-migration.json | 真实 preflight 包装 | preflight-post-migration / production-preflight | pass（5/0/0） |
| backup-restore.json | 真实导出 | backup-restore / backup-restore | pass（verified，32 行） |
| audit-chain-verify.json | 真实 verify 输出包装 | audit-chain-verify / audit-chain-anchor | pass（valid，0 entries） |
| audit-chain-anchor.json | 真实 anchor-verify 输出包装 | audit-chain-anchor / audit-chain-anchor | pass（up-to-date，1 anchor，WORM archived） |
| audit-anchor.jsonl | m14-42 锚文件副本 | —（companion） | 副本校验自洽 |
| production-preflight.json | 门文件（post 形态） | production-preflight（gate） | pass |
| provider-smoke.json | provider-smoke-aggregate 机器聚合 | provider-smoke（gate） | fail（voice/llm） |

## 证据 SHA256（evidence 目录）

```
4a292f1bfc03289bc61c62a6f29a91021c50033f88f5f44d4d9187484da093fa  audit-anchor.jsonl
afe4a54ce61ed1e120f7c8b301bdbedb09656a9ad281563660f46b6f21cca90a  audit-chain-anchor.json
8344609399e331b5d4cb2c630fdf56135d20f68964cad9d17e1921b562cb493b  audit-chain-verify.json
532d291b0e7d4a6f107750eac0c371d892497fc0fabcf635d0d83856f62c78b3  backup-restore.json
f118e7149b6c1f614cd66aa31957a318201ac4aa4570706fbe22911cedb50ee2  ci-main.json
3b699b303e6441a5e23f72b5e988ea46ea32e2ad1b8109e3eea2e873846aa377  cloud-voice-smoke.json
f96b28988b61d9ec03e5d4adffacffa31f75f6b04a629ca3e5fd0e52c99ad0de  draft-ownership.json
65d22f50c55982031d20eae758f120d96edaa973a03fc173e94cef1df77f3245  legacy-papers.json
413f91ea86112f9c7b076063ef784ae702aeda5901d00094d1879fba803f0763  llm-smoke.json
ae6ab0d3c4a33d5ac5ef0338d65a53e866944f5d2df987b92277951f61320437  preflight-post-migration.json
6101ff7346c6e593602c382336ee45f45c0d3a9d4055decf4837186dbd52f546  preflight-pre-migration.json
a230cc972bb2abe955048fee35cdb0bf24bd586613ecc0957134f02fddd5cd15  production-preflight.json
3855f91633e6633e684811b650a432e50f9d3fed297cf7eb91fe0573c836b0d7  provider-smoke.json
d42aea267810193c1dca02a73bd8255c623535847417a7bf3b1cf37a24e1500c  release-check.json
28e99915c152d22dc83b75617806ac258d94cb0de6666cce8bde256dbba052d5  search-smoke.json
```

## 精确阻断项（production_ready=false 的原因）

1. **cloud-voice-smoke = fail**：云 ASR/TTS 部署凭证缺失，`bash infra/smoke_voice_cloud.sh` 无法通过；运维补齐部署 key 后重跑并以 provider-smoke-export 重新导出。
2. **llm-smoke = fail**：LLM 网关云凭证缺失；运维以部署 key 冒烟通过后重新导出。
3. **cutover-approval / release-approval 缺失**：按流程由审批人在其余门全 pass 后人工填写（本任务明确不创建任何 approval 文件）。
4. turn-tls.json 缺失（optional 门，本机/LAN 发布形态不需要；如实透出不计入 fail）。

## 边界声明

- 未重跑 release-check-isolated 与生产 backup/restore（沿用既有真实产物）。
- 生产栈（aios-m14-03-production-rehearsal，7 healthy，API/Web 200）未被触碰。
- 聚焦治理契约修复（cli.py / governance_evidence.py / test_governance_evidence.py 三文件）：批次条件必需 + 证据输出补 `gate=step_id`。
