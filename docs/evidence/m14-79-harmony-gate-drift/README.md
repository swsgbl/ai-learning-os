# M14-79 Harmony 门禁漂移审计与最小安全收口（证据链交叉校验）

## 结论

在 main 基 `376c4ee`（PR #165 合并点）的独立 worktree（分支
`harmony/m14-79-gate-drift`）上，对 M14-78/M14-79 通用发布变更之后的
Harmony 发布链路做源码级漂移复核，并落地**一处最小 fail-closed 收口**：
`agc_closure_manifest` 新增跨报告 HAP 哈希一致性校验（Gap B）。零设备、
零模拟器、零 AGC、零签名材料访问、零生产触碰、不修存量 ruff。

**不声称生产就绪**（`production_ready=false` 保持不变）。

## 漂移审计：2a0e911..376c4ee 零源码漂移

M14-76 证据基线：main@`2a0e911`，pytest 417 passed + 1 skipped，ruff
存量 278。复核命令（在 worktree 中，`git log --oneline -1` 确认 HEAD=
`376c4ee`）：

```
git diff --stat 2a0e911 376c4ee -- apps/harmony tools/harmony_release tests/harmony_release
# 输出为空 -> 零改动

git diff --stat 2a0e911 376c4ee | findstr /i "harmony"
# 唯一命中：docs/evidence/m14-76-harmony-simulator-closure/README.md | 127 ++++
# （仅证据文档入库，不属源码漂移）
```

判定：M14-76 基线的源码级适用性（source-level applicability）在
`376c4ee` 上**完全成立**。三棵树（`apps/harmony`、`tools/harmony_release`、
`tests/harmony_release`）零漂移；M14-78/M14-79 通用发布变更未触及 Harmony
发布链路源码。

## 候选缺口审计（离线可实现的 fail-closed 检查）

对 8 个闭环工具（preflight / release_build / sign_hap /
verify_signature / device_preflight / device_smoke / agc_closure_manifest /
json5lite）及其测试逐一复核，候选缺口按“纯新增、不翻转既有契约、完全
离线可测”筛出：

- **Gap A（sign_hap fail-open，未采纳）**：签名工具在输出字节与输入完全
  相同时仍报 `status=ok, claimed_signed=true`，且现有测试 pin 了该行为
  （`test_agc_closure_manifest.py` 既有断言依赖）。翻转属 spec 变更，
  超出本切片“最小安全”边界，留待人类决策。
- **Gap B（agc_closure_manifest 零交叉校验，已采纳）**：三份门报告
  （release_build / sign_hap / verify_signature）之间无任何交叉校验——
  陈旧证据（release_build 是构建 N，sign_hap 还指向构建 N-1 的 unsigned
  HAP）、混搭证据（不同构建的 HAP 哈希混用）或**哈希记录缺失的通过态
  报告**在逐门状态全 pass 时仍 manifest `ok`。这是纯新增检查，完全
  离线可实现，不翻转任何既有契约。

## Gap B 实现：跨报告 HAP 哈希链校验

真实报告 schema（字段已逐一核实于源码）：

- `release_build` 报告 `artifact` 记录（`release_build.py:305`）：含
  `relpath` / `size_bytes` / `sha256`。
- `sign_hap` 报告 `input` 记录（`sign_hap.py:660`，`_inspect_input`
  `sign_hap.py:341` 构造）：含 `relpath` / `size_bytes` / `sha256`。
- `sign_hap` 报告 `artifact` 记录（`sign_hap.py:662`，`_inspect_artifact`
  `sign_hap.py:526` 构造）：含 `relpath` / `size_bytes` / `sha256` /
  `bytes_changed_from_input`。
- `verify_signature` 报告 `input` 记录（`verify_signature.py:724`，
  `_inspect_input` `verify_signature.py:408` 构造）：含 `relpath` /
  `size_bytes` / `sha256`。

实现（`tools/harmony_release/agc_closure_manifest.py`）：

- `_hap_sha256_from`（L258-L276）：从报告 HAP 记录取经 `SHA256_RE`
  （L100）消毒的 sha256；缺失/局部/伪造记录返回 `None`。
- `_chain_consistency`（L278-L322）：校验两条链：
  - Link 1 `build_artifact_vs_sign_input`：
    `release_build.artifact.sha256` 必须 == `sign_hap.input.sha256`
    （签的就是这次构建出的 HAP）。
  - Link 2 `sign_artifact_vs_verify_input`：
    `sign_hap.artifact.sha256` 必须 == `verify_signature.input.sha256`
    （验的就是这次签出的 HAP）。
- `build_manifest`（L448 初始化 `chosen`，L477 记录每门选定报告，L495
  调用）：每侧哈希必须**在且良构**——选定报告缺失/损坏任一必需哈希槽位
  （follow-up：`CHAIN_HASH_RECORDS`）→ 新增 blocker `evidence_hap_missing`
  （`gate=evidence_chain`，`detail.slot` 指明缺失槽位）→ manifest
  `blocked`（exit 2），**不依赖逐门检查**；双侧哈希都在且不等 →
  `evidence_hap_mismatch`（`detail.link` 指明断链位置）→ `blocked`。
  仅整份报告缺席仍归 `missing_report` 管辖（既有行为不变）。
- `_next_action`（L412）：新 blocker 映射到
  `rerun_release_chain_so_evidence_shares_one_hap`。

不变式保持：只比较消毒后的哈希，不读环境、不 spawn、不碰设备、不比
路径；blockers 仍按 `json.dumps(sort_keys=True)` 确定性排序。

## 命令与结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 聚焦测试 | `python -m pytest tests/harmony_release/test_agc_closure_manifest.py -q --basetemp=%TEMP%\m1479_focused` | **41 passed, 1 skipped**（基线 37+1，新增 4） |
| 全量套件 | `python -m pytest tests/harmony_release -q --basetemp=%TEMP%\m1479_full` | **421 passed, 1 skipped**（基线 417+1，零回归） |
| 聚焦测试（follow-up） | `python -m pytest tests/harmony_release/test_agc_closure_manifest.py -q --basetemp=%TEMP%\m1479_fu_focused` | **45 passed, 1 skipped**（原 41+1，删 1 新增 5） |
| 全量套件（follow-up） | `python -m pytest tests/harmony_release -q --basetemp=%TEMP%\m1479_fu_full` | **425 passed, 1 skipped**（零回归） |
| ruff（follow-up 触碰文件） | `ruff check tools/harmony_release/agc_closure_manifest.py tests/harmony_release/test_agc_closure_manifest.py` | **All checks passed** |
| 空白错误（follow-up） | `git diff --check` | 通过 |
| ruff（触碰文件） | `ruff check tools/harmony_release/agc_closure_manifest.py tests/harmony_release/test_agc_closure_manifest.py` | **All checks passed** |
| ruff（存量和基线一致） | `ruff check tools/harmony_release` | 存量 278（UP006/UP035/UP045），与本切片无关，未触碰 |
| 空白错误 | `git diff --check` | 通过 |
| worktree 卫生 | `git status --short` | 仅两个目标文件改动，临时脚本已删除 |

解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`（主仓
.venv，worktree 无 .venv；pytest 以 worktree 根优先入 sys.path，本 worktree
的 `tools/harmony_release` 包胜过 editable 安装——已核实导入路径来自
worktree）。

## 新增测试（tests/harmony_release/test_agc_closure_manifest.py）

新增 `TestEvidenceChainConsistency` 类（4 个测试）：

1. `test_stale_sign_input_hash_blocks_manifest`：sign_hap input 指向上一
   次构建哈希（SHA_B ≠ release_build artifact 的 SHA_A）→ manifest
   `blocked`、exit 2，且**逐门状态仍然全 pass**（证明交叉校验独立于逐门
   检查捕获陈旧证据）。
2. `test_mixed_verify_input_hash_blocks_manifest`：sign_hap artifact=SHA_A
   但 verify_signature input=SHA_B → Link 2 断链 → `blocked`。
3. `test_matching_chain_still_ok`：链一致（build→sign input SHA_A，
   sign artifact→verify input SHA_B）→ `ok`、blockers 空、exit 0。

follow-up（fail-closed 收口，替换原第 4 条“单侧缺失静默”的不安全
测试）新增 5 个测试：

4. `test_missing_sign_input_hash_blocks_manifest`：sign_hap `input` 记录
   缺失 → `evidence_hap_missing`（slot=`sign_input`）→ `blocked`，且
   逐门状态仍全 pass（证明收口独立于逐门检查）。
5. `test_missing_signed_artifact_hash_blocks_manifest`：sign_hap
   `artifact` 记录缺失 → slot=`sign_artifact` → `blocked`。
6. `test_missing_verify_input_hash_blocks_manifest`：verify_signature
   `input` 记录缺失 → slot=`verify_input` → `blocked`。
7. `test_partial_or_invalid_sha256_records_block_manifest`：截断
   （63 位）与非十六进制 sha256 → `sign_input` 与 `verify_input` 两个
   missing 槽位各自成 blocker（确定性排序 `sign_input` < `verify_input`）。
8. `test_missing_whole_reports_stay_missing_report_blockers`：整份 sign/
   verify 报告缺席 → 仍只有 `missing_report`（completeness 检查不为
   缺席门报告触发）。

## Follow-up：链完整性收口（fail-closed on missing hash records）

Supervisor 复核发现阻断缺陷：原实现只在链路**双侧哈希都在**时比对，
一份通过的 sign/verify 报告只要**省略** HAP 哈希记录即可让链校验静默
放行（fail-open），且原第 4 条测试把该不安全行为钉死。修复（单提交，
不改 mismatch 行为与确定性排序）：

- `CHAIN_HASH_RECORDS`（`agc_closure_manifest.py`）：4 个必需哈希槽位
  ——`release_build.artifact`、`sign_hap.input`、`sign_hap.artifact`、
  `verify_signature.input`。
- `_chain_consistency`：选定（chosen）报告存在时，其全部必需哈希槽位
  必须在且良构；缺失/非法 → 新增 distinct blocker `evidence_hap_missing`
  → manifest `blocked`。该检查独立于逐门 pass 检查（逐门检查对
  sign/verify 的哈希记录不做要求）。整份报告缺席仍归 `missing_report`。
- `_next_action`：`evidence_hap_missing` →
  `regenerate_evidence_reports_with_hap_hash_records`。
- 测试 fixture（`sign_ok` / `verify_signed`）补齐真实报告 schema 的链
记录（`sign_hap.py:660/662`、`verify_signature.py:724`），删除钉死
fail-open 的测试，新增上述 4+1 个缺失/非法覆盖测试。

## 剩余真实阻塞项（human-provided prerequisites）

1. **unsigned 边界未破**：无 AGC 签名材料（`.p12` / `.p7b` / `.cer`），
   `signingConfigs` 为空，release 链路止于 unsigned HAP。M14-76 的
   preflight `blocked_by_external_materials` 依旧成立。
2. **仅模拟器验证**：全部设备证据来自 KaihongOS QEMU 模拟器
   （127.0.0.1:5555），无真机（real device）验证。
3. **无 AGC 上传**：`agc_closure_manifest` 是 AGC 上传前的门禁聚合器，
   本切片只加强其证据校验，AGC 通道本身未建立/未执行。
4. **production_ready=false**：`PRODUCTION_READY = False`（
   `agc_closure_manifest.py:66`）保持不变，本切片不声称生产就绪。
5. **Gap A 未收口**：sign_hap 的 fail-open（输出未变仍 claimed_signed）
   需 spec 决策（翻转会动既有测试 pin 的行为），留待人类拍板。
6. **ruff 存量 278**：`tools/harmony_release` 的 UP006/UP035/UP045 存量
   与本切片无关，按任务边界不修。

## 人工前置条件（下一步）

- 提供 AGC 签名材料（debug/release profile + 证书 + 密钥库）并配好
  `AIOS_HARMONY_*_PATH` 环境变量（材料本身不入库）。
- 真机（HarmonyOS 设备）连入后重跑 device_smoke confirm-mutation。
- 决策 Gap A（sign_hap fail-open 翻转）与 AGC 通道建立顺序。
