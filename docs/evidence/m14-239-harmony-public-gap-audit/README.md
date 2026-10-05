# M14-239：Harmony 公开发布缺口证据回填（docs-only）

## 0. 结论与边界

- 切片：worktree `ai-learning-os-worktrees/m14-239-harmony-public-gap-evidence`，
  分支 `harmony/m14-239-harmony-public-gap-evidence`，基于 current main
  `a0b5c6803105224f521c4c2d2929b0839c1cfe74`（PR #325 merge，即 M14-238）。
  交付为 1 个本地 commit，不 push、不开 PR、不合并。
- 性质：**纯文档证据回填（evidence backfill only）**。本切片把 2026-10-05
  M14-238H Harmony 公开发布缺口审计（基础提交 `d41c9bf`）产出的权威
  HMHarness 审计事实落入仓库证据目录。**这不是生产就绪声明**：
  `production_ready=false`（reason `gap_report_is_not_release_readiness`）
  原样保持；本切片不授权任何签名、上传、分发或发布动作。
- 零触碰边界（全程遵守）：不改任何 tracked 生产代码、工具、测试、CI、
  secret、Docker、模拟器、设备；不触碰其他 worktree；本轮未构建、未签名、
  未安装、未验证任何应用。对源材料的唯一操作是**只读**读取与哈希重算。
- 变更面恰 4 个 Markdown 文件：本 README + `docs/PROJECT_STATUS.md` /
  `docs/ROADMAP.md` / `docs/CHANGELOG.md` 三台账。

## 1. 缺口分级（原样保持审计分类与边界，不得弱化）

审计对 `d41c9bf` 基础上的 Harmony 公开发布链路给出三级缺口：

| 优先级 | 缺口 | 审计依据（对照源工件逐项核对） |
| --- | --- | --- |
| **P0** | AGC 证书 / profile / 密钥库缺失，无已签名 HAP，`signing_configs_count=0` | 三个材料环境变量全部 `not_set`（`AIOS_HARMONY_CERT_PATH` 期望 `.cer`、`AIOS_HARMONY_KEYSTORE_PATH` 期望 `.p12`、`AIOS_HARMONY_PROFILE_PATH` 期望 `.p7b`）；`apps/harmony/build-profile.json5` 存在、可解析，`signing_configs_count: 0`、`unsigned_boundary: true`；`agc_gap.json`：`signed_hap_generated=false`、`signing_performed=false`、`agc_access=false`、`entry-default-signed.hap` 不存在、签名验证证据缺席；凭据 env（`AIOS_HARMONY_KEYSTORE_PASSWORD` / `AIOS_HARMONY_KEY_ALIAS` / `AIOS_HARMONY_KEY_PASSWORD`）同样全部缺席（仅公开契约变量名，审计中无任何值） |
| **P1** | Harmony HAP 公共分发 / AGC 通道未验证 | `agc_distribution: blocked`——AGC 上传未尝试、release manifest 未下载、公共分发通道未演练；`public_channel: blocked`——公共通道明确要求已签名 release HAP |
| **P2** | 真实设备与公共分发证据缺失 | 审计为只读（`mode: read_only_report`）：无设备冒烟、无签名验证、无公开发布证据采集。先前只读检查曾观察到 Harmony 模拟器目标 `127.0.0.1:15566` 在线——该观察仅作上下文；**本轮（证据回填）未安装、未验证任何应用** |

`signing_structure` 维在审计中为 `failure`（failure_codes：
`signing_configs_empty` + `product_binding_missing`），即 P0 的结构面证据。
`build-profile` 保持无签名边界是诚实状态，但距可发布仍差外部签名材料与
AGC 通道验证。

## 2. preflight 与 gap report 事实（对照源 JSON）

- 默认 preflight（`preflight_default.json`）：`exit_code=0`、
  `status=blocked_by_external_materials`、三材料 env 全 `not_set`、
  `failures`/`warnings` 空、`repo_materials.count=0`（仓库内无签名材料）、
  `hap=null`。
- `--require-materials` preflight（`preflight_req.json`）：`exit_code=2`、
  同样 `blocked_by_external_materials`、同样三 env `not_set`。
- AGC 缺口报告（`agc_gap.json`，`mode=read_only_report`）：`exit_code=1`、
  `status=failure`（工具层"未达成"分类）；五维中 `signing_structure=failure`，
  `external_inputs` / `agc_distribution` / `public_channel` /
  `signature_evidence` 四维全 `blocked`；`production_ready=false`。

## 3. 源工件锚点表（只读引用，不入仓库）

审计证据保留在审计 worktree（`m14-238h-harmony-public-gap-audit`）的
gitignored `.verify/m14-238h/` 目录。本切片未拷贝、未修改任何源文件，
仅读取并独立重算字节数与 SHA-256，重算值与源工件一致（下表路径相对
该证据目录）：

| 源文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `REPORT.md` | 5289 | `6EE4F7E627D727323945DC2F78C279CBD916765663C9F405E275AFFB738487AB` |
| `summary.json` | 3919 | `F0DED6601FDDAB4AC5DE40E818B98D02A34F02163C1071F9DB15D7E2506C5CDD` |
| `preflight_default.json` | 1160 | `5BC283143DB715C6587B5E43370E1084FA4D5BC7E8CF109408727A30EB1D3BFE` |
| `preflight_req.json` | 1159 | `E3E1F4F3B16DF24583997BF5E2B473FE4339C931DB66117D4E5836C39A4E8A2A` |
| `agc_gap.json` | 8756 | `04A9BC600B0C825C8CAE174C681D611781D8769DD617A59310D354EB4E050EC6` |

脱敏说明：本文档与台账只含上述相对路径、字节数与哈希；不含任何本地
绝对路径、Windows 用户名或凭据。文中出现的环境变量均为公开契约名，
其值在审计时本就全部缺席（`not_set` / `present=false`）。

## 4. M14-237 门禁不可互替

M14-237（public mobile release gate）的门禁与验证面向 **Android APK**，
不能替代 Harmony HAP 发布链路：Android 侧即使全绿，也不能证明 Harmony
侧可发布。Harmony 专属的 preflight、签名与 AGC 证据必须独立推进（审计
`cross_gate_note` 原文明确此点）。

## 5. 审计基与本切片基的关系

- 审计基：`d41c9bf`（PR #324 merge，即 M14-237）。
- 本切片基：`a0b5c6803105224f521c4c2d2929b0839c1cfe74`（PR #325 merge，
  即 M14-238）。
- `d41c9bf..a0b5c68` 差异面仅为 M14-238 计划器
  （`tools/android_release/public_mobile_evidence_plan.py` + 聚焦测试 +
  4 个 docs 文件），**未触碰 `apps/harmony` 或 `tools/harmony_release`**，
  故审计的签名边界事实未失效；本切片时点另做只读复核：
  `apps/harmony/build-profile.json5` 的 `signingConfigs` 仍为空数组。
  该复核是结构事实核对，不构成对审计全部结论在新基础上的全量重跑。

## 6. 下一步（摘自 summary.json；均需运维显式授权，agent 不代行）

1. P0：提供外部签名材料与凭据 → `run_sign_hap_with_external_materials`
   → `run_signature_verification`（并在 build-profile 配置 release 签名
   绑定）。
2. P1：以真实凭据演练 AGC 公共分发通道（upload / release manifest /
   渠道验证）。
3. P2：采集真实设备冒烟与公共分发证据。
4. 在上述完成之前，不得声称 Harmony 生产就绪；`production_ready=false`
   不变。

## 7. 本切片验证（docs-only）

- 五份源工件 SHA-256 与字节数独立重算并逐项核对（见第 3 节）。
- 引用的审计事实逐项对照 `REPORT.md` / `summary.json` 与三份源 JSON。
- `git diff --check` 干净。
- 变更行扫描：零 secret/凭据值、零本地绝对路径、零 Windows 用户名、
  零 U+FFFD。
- 仓库无既有 docs/markdown 校验命令（根 `package.json` scripts 仅
  apps/web 的 dev/build/lint/typecheck 与 pytest，无 markdownlint 配置
  或依赖），该项校验如实注明未运行；docs-only 切片不跑 pytest。
