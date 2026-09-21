# M14-80 sign_hap claimed-signed fail-open 收口（Gap A）

## 范围

在 main 基 `40002dd`（PR #166 合并点）的独立 worktree（分支
`harmony/m14-80-sign-claim-hardening`）上，收口 M14-79 审计记录的
**Gap A**：`sign_hap` 在签名工具退出 0 但产物缺失或与输入字节相同
（size + SHA-256 全等）时，仍然报告 `status=ok, claimed_signed=true`。
本次翻转该行为为确定性失败（fail-closed）。单文件工具改动 + 聚焦测试
改动 + 本证据文档；零签名材料访问、零真实签名、零 AGC、零设备、零
生产触碰、不修存量 ruff。

## 行为变更（tools/harmony_release/sign_hap.py）

调用后产物校验（工具退出 0 之后、status/claim 终定之前）：

| 场景 | 之前 | 之后 |
| --- | --- | --- |
| 工具退出 0，产物缺失/不可读 | `failure`（`output_missing` / `output_unreadable`），不 claim | 不变（既有 fail-closed 保留） |
| 工具退出 0，产物 size+SHA-256 与输入全等 | **`ok` + `claimed_signed=true`**（fail-open，Gap A） | **`failure`（新 code `output_unchanged_from_input`，exit 1）+ `claimed_signed=false` + `claimed_signed_basis=null`** |
| 工具退出 0，产物字节已变化 | `ok` + `claimed_signed=true` | 不变：`ok`、`claimed_signed=true`、`signed=false`、`signedness_verified=false`（诚实 claim 语义保留） |
| 工具非零退出 | `failure`（`sign_tool_failed`），不 claim | 不变 |

实现要点：

- 校验位置：`run_sign_hap` 第 4 步，`_inspect_artifact` 成功返回之后。
  产物记录（`relpath` / `size_bytes` / `sha256` /
  `bytes_changed_from_input=false`）仍然诚实保留在 `artifact` 槽位，
  不被隐藏；只是不再承载 signing claim。
- `unchanged` 判定 = `sha256` 相同 **且** `size_bytes` 相同（双条件，
  避免碰撞误判，也避免仅凭 size 相同误伤合法输出——同长不同字节仍
  正常 claim，有测试钉住）。
- `CLAIM_BASIS` 更新为 `hap_sign_tool_exit_0_output_written_bytes_changed`
  （basis 名称本来即契约一部分；全库无其他字面量引用，已核实）。
- 新失败 `detail` 仅含 `relpath`（repo 相对）/ `size_bytes` / `sha256`，
  与既有报告语义一致；无路径值、无密钥、无 argv、无 stdout/stderr。
- 模块 docstring 的 Safety contract 与 fail-closed 清单同步更新。

对下游的一致性（未改动、已核实）：`agc_closure_manifest` 对 sign_hap
门的 genuinely-pass 检查要求 `status` pass **且** `claimed_signed is
True`（`agc_closure_manifest.py:388`）；本收口使被 Gap A 触发的历史
场景（ok+claim 但字节未变）不再可能出现，而新的 `failure` 状态会被
manifest 正确阻断——方向一致，无需改动 manifest。其测试 fixture 为
静态报告构造，不受本次行为翻转影响（全量套件零回归证实）。

## 测试变更（tests/harmony_release/test_sign_hap.py）

- **删除** `TestSuccess.test_unchanged_output_bytes_are_recorded_not_hidden`
  ——它把“输出未变仍是 ok + claimed_signed=true”的 fail-open 行为钉死
  为成功语义，与 M14-79 审计结论冲突，按任务要求移除。
- **新增**（`TestChildOutcome`）：
  1. `test_output_identical_to_input_fails_closed_after_exit_0`：工具
     退出 0 + 产物与输入 size/SHA-256 全等 → exit 1、
     `status=failure`、唯一 failure `output_unchanged_from_input`、
     claim 三件套（`claimed_signed` / `claimed_signed_basis` /
     `signedness_verified`）全 false/null、`artifact` 记录保留
     `bytes_changed_from_input=false`、`steps` 仍诚实记录
     `exit_code=0`；双 checkout 确定性（JSON 字节相等）。
  2. `test_same_size_but_changed_bytes_still_claims`：同长不同字节
     → `ok` + `claimed_signed=true`（防误伤合法签名输出）。
  3. `test_unchanged_output_failure_json_leaks_no_values`：新失败路径
     的报告卫生——JSON 与 summary 均无 tmp 绝对路径（含正斜杠形式）、
     无材料文件名、无三个密钥 marker、无 argv；summary 含
     `failures=output_unchanged_from_input`。
- 既有覆盖保持不动：missing output（`test_missing_output_after_success_
  is_failure`）、tool nonzero（`test_nonzero_tool_exit_is_failure_
  without_artifact`）、整体密钥卫生（`TestJsonSafety`）均为原测试。

## 命令与结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 聚焦测试 | `python -m pytest tests/harmony_release/test_sign_hap.py -q` | **74 passed**（基线 72；删 1 加 3） |
| 全量套件 | `python -m pytest tests/harmony_release -q` | **427 passed, 1 skipped**（基线 425+1；−1+3，零回归） |
| ruff（触碰文件） | `ruff check tools/harmony_release/sign_hap.py tests/harmony_release/test_sign_hap.py` | **74 findings，与基线逐类一致**（35 UP045 / 31 UP006 / 4 UP035 / 2 RUF059 / 1 PLW1510 / 1 C408），零新增 |
| py_compile | `python -m py_compile`（两个触碰 .py） | 通过 |
| 空白错误 | `git diff --check` | 通过 |

解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`（主仓
.venv，worktree 无 .venv；pytest 以 worktree 根优先入 sys.path，本
worktree 的 `tools/harmony_release` 包胜过 editable 安装）。

## 未变的边界（与 M14-79 一致）

1. **unsigned 边界未破**：无 AGC 签名材料，`signingConfigs` 为空，
   release 链路止于 unsigned HAP；`blocked_by_external_materials`
   （exit 2）语义与阻断优先级完全未动。
2. **无真实签名发生**：全部测试注入 fake runner / 占位文件，java 与
   hap-sign-tool.jar 从未执行，无任何签名材料被读取或写入。
3. **`production_ready=false` 保持**：本切片不声称生产就绪（该旗标在
   `agc_closure_manifest.py`，本次未触碰）。
4. **`signed` 恒 false / `signedness_verified` 恒 false**：本工具只做
   claim，不做密码学验证；验证仍是 `verify_signature` 的职责。
5. **ruff 存量不修**：`tools/harmony_release` 的 UP006/UP035/UP045 等
   存量与本切片无关，按任务边界保持。
6. **下游工具未改**：`agc_closure_manifest` / `verify_signature` /
   `release_build` 源码零改动。

## 剩余真实阻塞项（human-provided prerequisites）

- 提供 AGC 签名材料并配置 `AIOS_HARMONY_*_PATH`（材料不入库）后，
  release 链路才能进行到真实签名与真机验证。
- 真机（HarmonyOS 设备）连入后重跑 device_smoke confirm-mutation。
- AGC 上传通道仍未建立（本切片只收紧签名声明语义）。
