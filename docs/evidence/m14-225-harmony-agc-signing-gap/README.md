# M14-225H：Harmony AGC 签名/分发 gap report（read-only、机器可读、闭集词汇）

## 0. 结论与边界

- 切片：worktree `ai-learning-os-worktrees/m14-225h-agc-gap-report-retry`，
  分支 `harmony/m14-225h-agc-gap-report-retry`，rebase 后基 current main
  `92c80ad00be6b10b301a5d442e7b1e635fcf06fd`（PR #312 merge；原始
  authoring 基 d8bb1729 = PR #311 merge，rebase 区间对
  tools/harmony_release 与 tests/harmony_release 零变更）。交付为
  1 个本地 commit，不 push、不开 PR、不合并、不改远端配置。
- 交付物：`tools/harmony_release/agc_gap_report.py`（纯只读聚合器）+
  `tests/harmony_release/test_agc_gap_report.py`（25 项离线聚焦测试）
  + 本 README + 台账更新。无其他文件变更。
- **明确声明：当前没有任何 signed HAP**。本切片不产生、不声明、不验证
  任何签名产物；`signed_hap_generated=false`、`signing_performed=false`
  恒成立，签名证据维度永远报告 `signature_verification_evidence_absent`
  （即使输出目录出现名为 signed 的文件，presence 也不是签名判定）。
- 五维聚合，全部闭集词汇（status/blocker code/next_action 均为常量表，
  测试锁定无重复且输出不越界）：
  1. `signing_structure`——委托 `agc_signing_preflight`（import 复用，
     零子进程）：声明契约 + build-profile signingConfigs/product 绑定 +
     app manifest bundleName；
  2. `external_inputs`——材料/凭据 env 缺席如实报告（只记 present/valid
     布尔，绝不读取或序列化任何值/路径内容）；
  3. `signature_evidence`——仅 `lstat` 探测规范输出布局中 signed/unsigned
     HAP 占位是否存在（symlink/reparse point 不算存在），零 HAP 字节读取；
  4. `agc_distribution`——固定边界 blocker（本工具零 AGC 访问）：
     upload 未执行、release manifest 未下载、分发渠道未演练；
  5. `public_channel`——固定边界 blocker：公开渠道需要 signed release。
- `production_ready=false` 恒成立（reason=
  `gap_report_is_not_release_readiness`）：gap report 不是发布就绪声明。
- 退出码：0=无 gap（本切片结构性不可达，AGC 分发维是固定边界）；
  1=签名结构失败；2=结构有效但存在 gap。
- 硬边界（全程遵守）：不读取/不发明真实证书、密钥、profile；不把敏感
  材料放进仓库；不 spawn 外部工具；不触碰服务、设备、模拟器、Docker、
  WSL、生产、远端。唯一 I/O 是本地仓库文件的只读解析与 lstat。

## 1. 真实 checkout 报告（2026-10-03，rebase 前基 d8bb1729 树执行；rebase 后基 92c80ad 对 tools/tests 零变更，结论代码等价）

`python -m tools.harmony_release.agc_gap_report`（exit 1，符合结构失败
语义）关键投影：

- `status=failure / exit_code=1`；
- `signing_structure=failure`，failure_codes=`product_binding_missing,
  signing_configs_empty`（当前 checkout 真实形状：signingConfigs 空数组
  且 default product 无绑定）；
- `external_inputs=blocked`（材料与凭据 env 全部缺席，如实呈现）；
- `signature_evidence`：`signed_hap_present=false`；
- 14 个 blocker、8 个去重 next_action（从修 build-profile 绑定到运维
  执行真实 AGC upload/manifest/渠道演练的完整链）；
- `production_ready=false`、`agc_access=false`、
  `signing_performed=false`、`signed_hap_generated=false`。

## 2. 验证

- 聚焦：`pytest tests/harmony_release/test_agc_gap_report.py` →
  **25 passed**（结构 ok/failure/not_evaluated、材料缺席/无效/凭据缺席、
  signed HAP 存在性/符号链接拒认、AGC 与公开渠道固定边界、闭集词汇、
  排序与去重、确定性、ASCII 渲染、无本地路径泄漏、真实 checkout 两次、
  CLI stdout JSON；autouse fixture 双向清洗 6 个 AIOS_HARMONY_* env，
  fixture 值零泄漏）。
- 邻居：`pytest tests/harmony_release/` 全套 → **773 passed, 1 skipped**
  （回归零破坏；skip 为既有平台符号链接用例）。
- `ruff check tools/harmony_release/agc_gap_report.py
  tests/harmony_release/test_agc_gap_report.py` → All checks passed。
- 真实 checkout 运行（见第 1 节）为唯一非 fixture 验证，只读。

## 3. 剩余阻塞（gap report 如实列出，非本切片可消除）

1. `configure_release_signing_in_build_profile`——signingConfigs 空且
   default product 无 signingConfig 绑定（结构失败，优先修）；
2. `provide_external_signing_materials`——AGC 签名材料（.cer/.p7b/.p12）
   全部缺席；
3. `provide_signing_credentials`——key alias/密码 env 全部缺席；
4. `run_sign_hap_with_external_materials`——无 signed HAP；
5. `run_signature_verification`——签名验证证据缺席；
6. `operator_run_agc_upload_with_real_credentials` 等 3 项运维动作——
   需真实 AGC 账号与人工执行，agent 不代行。

真实材料/凭据的提供与 AGC 上传/分发演练均需运维显式授权；本工具的
职责是把这些缺口以闭集词汇机器可读地呈现，不虚构任何一项已就绪。
