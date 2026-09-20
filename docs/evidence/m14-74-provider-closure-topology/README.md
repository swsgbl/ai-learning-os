# M14-74 provider 收口拓扑调和（provider closure topology）证据

分支 `ops/m14-74-provider-closure-topology`（基于 origin/main@b05a449，
即 PR #160 合并 M14-73 后的 main），单次本地提交 `ops: reconcile
provider closure topology`（不推送）。本 README 为唯一入库证据文件。

## 交付语义（8 文件，+904/−210）

- `services/api/app/ops/production_evidence_gap.py`：provider-smoke
  类别的 steps 由 cutover-rehearsal 演练步改为 **provider 槽位**
  （voice/search/llm，与 `release_readiness.SMOKE_PROVIDERS` 交叉锁定，
  与演练 step id 词汇不交），类别状态 **defer 到 release-readiness 的
  provider-smoke 门**（`provider-smoke.json` 聚合）——不再从演练步骤
  重新聚合（门级 pending 无法由槽位词汇重建）。固定映射：pass/pending/
  blocked 同名透传；missing→not_executed；malformed/tampered→blocked。
  **missing/malformed/tampered/fail 全部保持 fail-closed**。顶层新增
  provider_smoke 块透出权威门状态 + 拓扑选轨：`voice_mode=local` 消费
  **local-voice-smoke** 轨，hybrid/cloud 消费 **cloud-voice-smoke** 轨
  （`voice_track` 如实透出；拓扑未知则两者如实为 null）。build() 对
  rehearsal 与 readiness 各恰调用一次，输出护栏前置。
- `services/api/app/ops/release_closure_manifest.py`：聚合仍**只消费
  gap/readiness 产物**（零 provider 语义重复，source-guard 测试实证）；
  NEXT_STEPS 修正为 M14-70 拓扑形态（provider-smoke-aggregate 的
  `--voice <local-voice-smoke.json|cloud-voice-smoke.json>` +
  `--voice-mode <local|hybrid|cloud>`、双语音导出轨道），**七条扩为
  八条**拓扑感知下一步。
- cutover-rehearsal：**13 步演练时间线不变**；新增兼容性测试证明
  聚合文件出现在证据目录时仅记录为未识别、不阻断演练评估。
- 测试：16 条过期断言修复；新增 17 条 focused 测试（gap 12 / closure 3 /
  readiness defer 契约 1 / rehearsal 兼容 1）。
- 文档：`docs/DEVELOPMENT.md` 与 `services/api/app/ops/cli.py` docstring
  更新为 M14-74 双源契约。

## 验证（全部在 worktree 内）

- focused 套件：**248 passed**（supervisor 复核）；Claude focused
  **422 passed**。
- `ruff` / `py_compile` → 干净。
- npm typecheck → 通过；npm lint → 通过（**11 条既有 warning、
  0 error**）；npm build → 通过。
- 全量 API 套件：**3859 passed / 33 skipped**，其中 3 例为本地
  WSL-bash 环境失败；改选 Git Bash 后
  `services/api/tests/test_smoke_search_script.py` 全部 **7 passed**。

## supervisor 真实形状只读验证（assembled read-only shape fixture）

使用**真实脱敏 M14-71 证据**与**真实 M14-73 soak 报告**组装只读形状
夹具（worktree 本地 gitignored `.verify/m14-74-provider-closure-topology/`
另有实现会话的形状等价重构验证记录）：

- provider 门 **pass / local**（voice_mode=local，
  voice_track=local-voice-smoke）。
- long-soak **blocked**（97 selected、94 ok、3 warn、0 critical）。
- cutover-approval **not executed**。
- closure **production_ready=false**（**12 blockers** 如实透出）。

输入锚点（sha256 / bytes，如实转录）：

```
68686e7a881dbf26318129e16cf7f6c966a4dff19a68e684f06990cd653af152  302   search-smoke.json
a509be4fb95bcec3a08531087248455ca0d4b47a9aaf9234ab816047de698d6a  307   local-voice-smoke.json
f15db9742ca2d7a2a89a86ac16a1d34649ea59ca0b3855cc3339af2a58b6f8ab  300   llm-smoke.json
5f3befb8adb707c53ab9875a45a7ee1be7fc415d08bf4f46142748bd6ca6c996  564   provider-smoke.json
f9459ad4069771e45921a5f048ed7851cfd72903872242da65b569e63991b008  856   soak-audit-report.json
```

输出锚点（sha256 / bytes，如实转录）：

```
aa5888fbae3714f45adcc97df563fe0efc92966da447e8590b49ecfed634730e  8827   release-readiness.json
3f74bc0674d1f6df0146ae97f6e49d5225a7db0c7b5191ea30e05a11f613812e  12345  production-evidence-gap.json
df91764642d8b41712ef09f3e1cbb3a6a4899baa3981ebcb6968d422fa060d83  6950   release-closure.json
```

## 诚实边界

`production_ready=false` **不变**：本里程碑不声称任何 release
approval，不执行也不声称 production cutover。全程不推送、不建/不改
PR、不触碰远端、密钥、env 文件、生产容器与未跟踪工件。
