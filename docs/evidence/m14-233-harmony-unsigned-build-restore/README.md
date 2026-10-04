# M14-233：恢复 Harmony 可构建 unsigned 默认边界

## 0. 结论与边界

- 切片：worktree `ai-learning-os-worktrees/m14-230h-harmony-release-provenance`，
  分支 `harmony/m14-230h-release-provenance`，基 `origin/main`
  `9307918490819d95b9e4dcdf23eccf6f5c04a06e`；rebase 后 M14-230H 为
  `69d7a76079aa3187dc1c1e52c3c01e9ce66bf778`，本切片在其上交付恰好一个
  额外本地 commit。不 push、不开 PR、不合并。
- R2 的构建阻塞已修复：默认 `apps/harmony/build-profile.json5` 恢复为
  `signingConfigs: []`，default product 无 `signingConfig` 绑定；真实
  `hvigorw clean` 与 `assembleHap` 均通过，产物仍是
  `entry-default-unsigned.hap`。
- 本切片不产生、不声明、不验证 signed HAP；未读取或发明任何 AGC 材料/
  凭据。未来签名设计保持：材料与凭据只经 `AIOS_HARMONY_*` 外部输入，
  `sign_hap.py` 与签名验证语义不改、不放宽。
- R3 设备验证在安装门诚实 blocked：唯一在线目标
  `127.0.0.1:15566` 拒绝未签名 HAP（`no signature file`）。未伪造签名、
  未改设备安全开关、未启动或重置另一台模拟器，因此没有 Settings UI、
  PID、请求计数或截图可声称。

## 1. 契约修正

- `preflight.py` 默认 / `--expect-unsigned` 重新要求空数组：
  - 空数组 = buildable unsigned boundary，`signing_configs_count=0`、
    `unsigned_boundary=true`、`signing_configs_value_free=true`；
  - `[{name,type}]` 半结构 = `signing_configs_not_empty` 失败；该形状会让
    hvigor 在 clean 阶段要求 `material`，不是可构建的仓库默认；
  - 携带 `material/storePath/password` 等额外键时，继续叠加
    `signing_config_carries_values`，只回显键名，不回显值。
- `--expect-signed` 的未来契约不变：非空 value-free 结构 + 三项外部材料
  present/valid；当前真实 checkout 为空数组，因此 `--expect-signed`
  fail-closed 于 `signing_configs_empty`。
- `agc_gap_report` 的当前 checkout 断言恢复为诚实 unsigned 结构缺口：
  `signing_configs_empty` + `product_binding_missing`，不把 M14-228 的
  半结构当作 release readiness。

## 2. 构建与工具验证

- 聚焦：`pytest tests/harmony_release/test_preflight.py
  tests/harmony_release/test_agc_gap_report.py` → **85 passed**。
- 全套：`pytest tests/harmony_release/` → **799 passed, 1 skipped**
  （较 M14-230H 净增 1 个回归用例；skip 为既有平台符号链接用例）。
- `ruff check --select F,E9`（本切片改动 Python 文件）→ All checks passed；
  `compileall tools/harmony_release` → clean；`git diff --check` → clean。
- 真实 preflight：exit 0 / `blocked_by_external_materials`，
  `signing_configs_count=0`、repo 材料扫描 0 命中。
- 真实 release build：exit 0 / `status=ok`，clean exit 0 且 success marker，
  assemble exit 0 且 success marker；HAP
  `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  为 **236292 bytes**，SHA256
  `E49780730ED666C33BF47E0712EEED108363E1A8F60CD533E682331464489CB0`。

## 3. R3 模拟器安装门证据

- 运行前：`hdc list targets` 仅 `127.0.0.1:15566` 在线；
  `bm dump -n com.ailearningos.app` 报 bundle 不存在，未发现既有安装。
- 安装同一 HAP（size/SHA256 与上节一致）时，`hdc` 进程 exit code 为 0，
  但输出包含 `failed to install bundle. code:9568320` 与
  `no signature file`；post-install ability start 因包不存在失败。这是
  hdc rc 不可信的既有已知形态，R3 harness 原始输出已按语义失败留档。
- 清理与终态：`bm dump` 仍为 bundle 不存在，目标 `127.0.0.1:15566`
  仍在线；未停止/重置模拟器，未触碰无关进程或包。因为安装从未成功，
  force-stop/uninstall 输出均为“包不存在”的确认，不是清理成功假象。
- 该目标在 M14-170B 已有同错控制回归记录；旧 `5555` 模拟器曾允许
  unsigned 安装，但本轮唯一在线目标不是它。设备 UI 验证需要外部提供
  合法签名材料并生成真正 signed HAP，或提供一台允许未签名安装的既有
  测试模拟器；这两者均不在本切片边界内。

## 4. 原始证据

- gitignored R3 原始输出：`.verify/m14-230h-harmony-settings-diag/R3_*`
  （preflight、release build、安装尝试、终态目标在线与清理证据）。
- 关键文件：
  - `R3_release_build.json`：clean/assemble/HAP provenance；
  - `R3_diag_harness_attempt1_fail.json`：安装门失败与无安装清理事实；
  - `R3_preflight_expect_unsigned.json`、`R3_preflight_expect_signed.json`、
    `R3_agc_gap_report.json`：真实 checkout 双向语义；
  - `R3_pre_hdc_list_targets.txt` 与 attempt 内 post-targets：目标在线。
