# M14-194 Harmony AGC signing-input preflight

## 结论

| 项 | 结果 |
|---|---|
| 分支 / 基点 | `harmony/m14-194-agc-signing-preflight`，基于本地 `main` / `origin/main` 同指的 `50bd66a8c5695bae76917cc3fc2f686f72133687`；不声称远端实时刷新，前一轮 fetch 超时 |
| 交付 | 新增只读 AGC signing-input 校验器、value-free 声明文件与 36 项聚焦测试 |
| fake 结构校验 | `ok` / exit `0`，且 `signing_performed=false`、`signed_hap_generated=false`、`agc_access=false`、`production_ready=false` |
| fake 缺席输入 | `blocked_by_external_inputs` / exit `2` |
| fake present-but-invalid 输入 | `failure` / exit `1` |
| 当前 checkout AGC 结构 | `failure` / exit `1`：`signingConfigs: []` 且 default product 无 release 绑定（`signing_configs_empty` + `product_binding_missing`） |
| 既有 unsigned gate | 真实 checkout 冒烟仍为 `blocked_by_external_materials` / exit `0`，默认诚实阻断语义不变 |
| 提交边界 | 单 local commit；**未 push、未开 PR** |

## 实现与契约

新增文件：

- `tools/harmony_release/agc_signing_preflight.py`
- `tools/harmony_release/agc_signing_inputs.json`
- `tests/harmony_release/test_agc_signing_preflight.py`

CLI：

```bash
python -m tools.harmony_release.agc_signing_preflight \
  --repo-root .
```

`--repo-root` 是包含 `apps/harmony/` 与 `tools/` 的 Git/仓库根；工具内部固定读取 `apps/harmony/build-profile.json5` 与 `apps/harmony/AppScope/app.json5`。在仓库根运行时可省略该参数；`--declaration` 也可省略，默认读取 tracked canonical 声明。

声明文件是 value-free 的固定 schema，必须与 canonical 常量全等：

- `schema_version: 1`
- `bundle_name: com.ailearningos.app`
- `product: default`
- `signing_config: release`
- 三个材料输入：`AIOS_HARMONY_CERT_PATH` `.cer`、`AIOS_HARMONY_PROFILE_PATH` `.p7b`、`AIOS_HARMONY_KEYSTORE_PATH` `.p12`
- 三个凭据输入：`AIOS_HARMONY_KEY_ALIAS`、`AIOS_HARMONY_KEYSTORE_PASSWORD`、`AIOS_HARMONY_KEY_PASSWORD`

声明解析严格拒绝重复 JSON key、非标准 JSON 常量、未知形状、任何常量漂移、非常规文件、不可读、超限和 symlink/reparse point。结构检查要求 `build-profile.json5` 中恰有一个 `release` signing config、恰有一个 `default` product 且 product 绑定 release；`AppScope/app.json5` 必须含匹配的 bundleName。这两个结构输入同样通过 lstat 要求为常规文件，并拒绝 symlink/reparse point（fail-closed 失败码为 `build_profile_rejected` / `app_manifest_rejected`）。材料路径复用既有 preflight 分类器：仓库外、常规文件、正确后缀；从不读取材料字节。凭据只做非空存在性检查，值不进入结果。

退出码矩阵：

| exit | 状态 / 语义 |
|---:|---|
| `0` | 本地结构与全部已提供外部输入均有效；仍不构成生产就绪 |
| `1` | 声明/工程结构畸形或漂移，或 present-but-invalid 输入 |
| `2` | 本地结构有效，但有必需材料或凭据缺席 |

## Fake 冒烟与输出卫生

原始 fake-only 工件在 gitignored `.verify/m14-194-agc-signing-preflight/`。fixture 只包含临时目录里的占位字节 `placeholder-not-a-real-signing-material`、fake bundle/product/config 结构和公开环境变量名；未使用、创建或读取真实 AGC 材料或凭据。

fake 脚本四条结果与当前 checkout 的独立实测：

| case | status | exit |
|---|---|---:|
| AGC fake 结构成功 | `ok` | `0` |
| AGC fake 缺全部外部输入 | `blocked_by_external_inputs` | `2` |
| AGC fake 材料后缀错误 | `failure` | `1` |
| AGC preflight（当前 checkout，独立实测） | `failure`；`product_binding_missing` + `signing_configs_empty` | `1` |
| 既有 unsigned preflight（真实 checkout） | `blocked_by_external_materials` | `0` |

对所有生成 JSON 的泄露扫描（盘符、工作区路径、用户名、占位密码、token/secret/private key 标记）为 0 命中。输出为确定性 JSON：无时间戳、无绝对路径、无环境变量值、无材料文件名或内容；凭据只出现变量名和 present/error。

## 验证记录

均在本 worktree 本地执行：

| 检查 | 命令 | 结果 |
|---|---|---|
| TDD 红 | 先添加测试、无实现 | **30 failed**（缺模块，预期红） |
| 聚焦测试 | `python -m pytest tests/harmony_release/test_agc_signing_preflight.py -q` | **36 passed in 0.31s** |
| Harmony release 全量 | `python -m pytest tests/harmony_release -q` | **725 passed, 1 skipped in 22.04s** |
| Ruff 全规则 | `ruff check tools/harmony_release/agc_signing_preflight.py tests/harmony_release/test_agc_signing_preflight.py` | 通过 |
| Ruff 关键规则 | `ruff check --select F,E9 tools/harmony_release/agc_signing_preflight.py tests/harmony_release/test_agc_signing_preflight.py` | 通过 |
| 编译 | `python -m compileall -q tools/harmony_release/agc_signing_preflight.py tests/harmony_release/test_agc_signing_preflight.py` | 通过 |
| CLI 冒烟 | `python .verify/m14-194-agc-signing-preflight/run_preflight_smoke.py`；另实测 `python -m tools.harmony_release.agc_signing_preflight --repo-root .` | fake 脚本四 case 如上且 stderr 均空；当前 checkout 为 exit `1` 与上表失败码一致 |
| 空白检查 | `git diff --check` | 通过 |
| 新增行扫描 | 最终 staged diff（相对基点）1164 条新增行；secret 值 / 本地绝对路径 / U+FFFD | **0 命中**（宽口径关键词仅公开变量名与测试 `fake-*` 合成标记） |

文档写入后已复跑聚焦、全量、ruff、compileall、CLI 冒烟、diff check 与新增行扫描；结果如上表。

## 不得声称的事项

1. 未访问 AGC，未调用 Java、DevEco signing tool、hvigor、网络或设备。
2. 未读取或生成任何真实签名材料，未生成、修改或签名 HAP；`signed_hap_generated=false`。
3. 结构通过只表示声明与工程形状一致，不验证材料内容、凭据正确性、签名结果或 AGC 授权。
4. `production_ready=false` 固定不变；本切片不是 production readiness 声明。
5. 未 push、未开 PR、未触发远端 CI；前一轮 `git fetch origin main` 超时，远端新鲜度未重验。
