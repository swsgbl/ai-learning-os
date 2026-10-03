# M14-228：Harmony 签名结构与 preflight value-free 契约

## 0. 结论与边界

- 切片：worktree `m14-228-harmony-signing-structure`，分支
  `harmony/m14-228-signing-structure`，基 current main
  `1746df06`（PR #314 merge，rebase 后基座；原基于 b079692b）。交付为 1 个本地 commit，不 push、
  不开 PR、不触碰设备/模拟器/AGC/Docker/WSL/代理。
- 交付物（4 类文件）：
  1. `apps/harmony/build-profile.json5`——新增 value-free 的 release
     signingConfig（仅 `name`/`type` 两个键，无任何材料路径/密码/
     凭据），并把 `default` product 绑定到 `signingConfig: "release"`。
  2. `tools/harmony_release/preflight.py`——默认期望模式从
     "signingConfigs 必须为空数组" 收紧为 "value-free 契约"：空数组
     或仅含 `name`/`type` 键的结构化声明均合法；任何条目携带其他键
     （材料路径、凭据、密码等）或非对象，fail-closed 报
     `signing_config_carries_values`（只回显键名与索引，绝不回显值，
     无法泄漏路径/秘密）。`--expect-signed` 语义不变：非空 +
     三项外部材料 present 且 valid；`signed_contract` 摘要新增
     `signing_configs_value_free` 并纳入 `satisfied` 判定。
  3. 测试更新：`tests/harmony_release/test_preflight.py`（真实
     checkout 期望改为 value-free 结构 count=1；新增空数组仍合法、
     参数化 extra-key 三例、非对象条目、双模式值携带 fail-closed 等
     用例）；`tests/harmony_release/test_agc_gap_report.py`（真实
     checkout structure 维度从 failure 转为 ok 的转变断言）。
  4. 本 README + 标准台账更新。
- **明确声明：当前没有任何 signed HAP**。本切片不产生、不声明、不
  验证任何签名产物；未执行任何签名操作，未发明任何材料/凭据。
- 硬边界（全程遵守）：不读取/发明真实证书、密钥、profile；不把任何
  材料或凭据值放进仓库（`.gitignore` 的 `*.p12/*.p7b/*.cer/*.csr/
  *.jks` 防线不变，preflight 的 repo 材料扫描不变）。

## 1. 契约细节（M14-228 前后对比）

| 场景 | 旧默认模式 | 新默认模式 |
| --- | --- | --- |
| `signingConfigs: []` | ok | ok（空数组是 value-free 的） |
| `[{name, type}]` 结构声明 | fail `signing_configs_not_empty` | ok（无值携带） |
| `[{name, type, storePath}]` | fail `signing_configs_not_empty` | fail `signing_config_carries_values`（detail 只含 index+keys） |
| 条目为字符串等非对象 | fail `signing_configs_not_empty` | fail `signing_config_carries_values`（reason=not_an_object） |
| `--expect-signed` + 空数组 | fail `signing_configs_empty` | 不变 |
| `--expect-signed` + 材料缺席 | exit 2 blocked | 不变 |

- 新默认模式对秘密卫生 **严格强于** 旧规则：旧规则只拒绝非空，新规则
  对任何携带值的结构化声明精确拒绝并命名违规键名。
- 确定性 JSON 契约不变：`render_json` 仍 sorted-keys、ASCII-safe；
  新增字段 `build_profile.signing_configs_value_free`（所有模式）与
  `signed_contract.signing_configs_value_free`（仅 signed 模式）。
- 退出码语义不变：0=ok/blocked（默认），1=fail-closed，
  2=blocked 且要求材料（`--require-materials` 或 `--expect-signed`）。

## 2. AGC gap report 转变（真实 checkout）

build-profile 的结构化声明恰好满足 `agc_signing_inputs.json` 声明的
规范形状（唯一 `release` config + `default` product 绑定 + bundleName
匹配），因此在真实 checkout 上：

- `python -m tools.harmony_release.agc_gap_report` 仍 exit **2**
  （blocked），但 `signing_structure` 维度从 **failure** 转为 **ok**
  （failure_codes 空、blocker 空）；
- 首要 next_action 从
  `configure_release_signing_in_build_profile`（结构失败，优先修）
  前进为 `provide_external_signing_materials`（材料缺席）；
- `signature_evidence` 仍 `signed_hap_present=false`；
  `production_ready=false`、`signing_performed=false`、
  `signed_hap_generated=false` 恒成立。

该转变由 `tests/harmony_release/test_agc_gap_report.py` 的
`test_current_checkout_reports_gaps_not_failure_pretense` 锁定。

## 3. 验证（父仓 venv，basetemp 独立目录）

- 聚焦：`pytest tests/harmony_release/test_preflight.py` →
  **59 passed**；`pytest tests/harmony_release/test_agc_gap_report.py`
  → **25 passed**；`test_agc_signing_preflight.py` → **36 passed**
  （三文件合计 120 passed）。
- 邻居：`pytest tests/harmony_release/` 全套 → **777 passed,
  1 skipped**（较 M14-225H 的 773 passed 净增 4，skip 为既有平台
  符号链接用例；含本次新增/改写用例）。
- `ruff check --select F,E9 tools/harmony_release/preflight.py
  tests/harmony_release/test_preflight.py
  tests/harmony_release/test_agc_gap_report.py` → All checks passed
  （实际验证口径为 `--select F,E9` 作用域；ruff 默认规则集对既有
  代码存在已知存量 finding，不属本切片门禁）。
- `python -m compileall tools/harmony_release` → clean。
- `git diff --check` → 无空白错误；秘密/本地路径/U+FFFD 扫描 → 干净。
- 真实 checkout 双向运行：`python -m tools.harmony_release.preflight`
  exit 0（status=blocked_by_external_materials，
  signing_configs_value_free=true）；`--expect-signed` exit 2。

## 4. 剩余阻塞（与 M14-225H 相比，第 1 项已消除）

1. ~~`configure_release_signing_in_build_profile`~~——**已完成**
   （本切片：value-free 结构 + product 绑定；gap report structure
   维度转 ok）。
2. `provide_external_signing_materials`——AGC 签名材料
   （.cer/.p7b/.p12）全部缺席，需运维带外提供。
3. `provide_signing_credentials`——key alias/密码 env 全部缺席。
4. `run_sign_hap_with_external_materials`——无 signed HAP（本切片
   未签名任何 HAP）。
5. `run_signature_verification`——签名验证证据缺席。
6. `operator_run_agc_upload_with_real_credentials` 等 3 项运维动作
   ——需真实 AGC 账号与人工执行，agent 不代行。

签名/AGC/公开渠道就绪仍被外部材料阻塞；本切片只完成仓库内的
结构声明与 preflight 契约收紧，不虚构任何一项已就绪。
