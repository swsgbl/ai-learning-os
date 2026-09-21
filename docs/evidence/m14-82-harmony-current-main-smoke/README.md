# M14-82 Harmony current-main 模拟器未签名发布链验证

- 日期：2026-09-21（本地执行窗口；原始证据落盘于当日 23:02 本地时间，以文件
  系统时间戳为凭，不声称更精确的探测时刻）
- Worktree：`m14-82-harmony-current-main-smoke`，分支 `harmony/m14-82-current-main-smoke`
- 基线：current main `e1f128be80fee736d5326272bcecebc1c729f0fb`（PR #168 merge）
- 执行身份：hmharness implementation worker（verify-only，零生产触碰）
- 原始证据：gitignored `.verify/m14-82-harmony-current-main-smoke/`（16 文件，
  SHA256SUMS.txt 锚定字节数与哈希，见文末）

## 目的

在 PR #167（M14-80 sign_hap fail-closed 收口）与 PR #168（M14-79 soak gates）
合并后，真实重跑模拟器面未签名发布链，刷新 current-main 证据并入库切片。
本切片**不声明生产就绪、不涉及真实签名**。

## 链路步骤与结果（全部真实执行）

| 步骤 | 命令 | exit | 结果 |
|---|---|---|---|
| 1 模拟器状态 | `hdc list targets` + `param get` 探测 | — | 两个 loopback target；选定 `127.0.0.1:5555`（`const.product.name=emulator`，API 24）；boot 佐证 `bootevent.boot.completed=true` + foundation/appspawn 进程运行（`step1_simulator_state.md`） |
| 2 preflight | `preflight.py --repo-root . --expect-unsigned` | 0 | `status=blocked_by_external_materials`（诚实未签名边界：signing_configs_count=0，三材料 env 均未设置）；`--require-materials` 变体 exit 2（`step2_preflight*.json`） |
| 3 release_build | `release_build.py --repo-root .` | 0 | clean exit 0 / assemble exit 0；HAP `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`，188,984 bytes，SHA-256 `584E5D47EE98261E9E029CA71378B908E86A44A36B881B8668AD958AEDAFB341`（`step3_release_build.json`） |
| 4a device_smoke plan | `device_smoke.py --target 127.0.0.1:5555 --hap <unsigned> --layout-dir ...` | 0 | 5 个 mutation step 全部 `not_run`（plan-only，零设备变更）（`step4_device_smoke_plan.json`） |
| 4b device_smoke confirm | 同上 + `--confirm-mutation` | 0 | `status=ok`，mutation_performed=true，**6/6 命令执行**（install / aa start / dumpLayout / file recv / force-stop / uninstall），0 failures；cleanup ok，bundle 已卸载；layout 落盘（`step4_device_smoke_confirm.json`） |
| 5 device_preflight plan | `device_preflight.py plan --target 127.0.0.1:5555 --repo-root .` | 0 | `mode=plan, status=planned`；loopback 目标记录 `loopback_rejected_in_true_device_mode=true`（check 模式拒绝 loopback），2 命令规划 0 执行——按任务约束**未跑 true-device 模式**（`step5_device_preflight_plan.json`） |
| 6 pytest | 主仓 venv `python -m pytest tests/harmony_release -q` | 0 | **427 passed, 1 skipped**（`step6_pytest_harmony_release.txt`） |
| 6b mock contract | `tools/harmony_mock/test_contract.py` | 0 | **54/54 passed**（installed UI 网络行为契约：非 GET / 未知路径 / 不支持 method fail-closed 404/501 语义保持）（`step6b_mock_contract.txt`） |

## HAP 产物

- 路径：`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
- 188,984 bytes，SHA-256 `584E5D47EE98261E9E029CA71378B908E86A44A36B881B8668AD958AEDAFB341`
- **未签名**（unsigned release 构建，无 AGC 材料、无签名配置）

## 布局证据

- `layout/device_smoke_layout.json`：53,798 bytes，SHA-256 `f85eda4577eea3a182094471a048543952df48adc3bdfddfa22af80cdcf429db`
- bundle 名 `com.ailearningos.app` 命中 layout 原始字节（属性字段）；工具报告
  node_count 2708（`uitest dumpLayout` 原始树口径）；本次原始 JSON 树遍历为
  82 个节点（81 含 attributes）——两口径均为真实采样，差异是计数范围定义不同
  （工具统计全树属性节点，README 口径统计顶层 JSON 遍历单元）。
- 首页关键区关键词命中：AIOS、服务地址、整体刷新、服务健康、认证状态、
  隐私模式、服务版本、运维快照、审计日志、com.ailearningos.app、网络请求失败
  （预期降级态文案，与 M14-76 记录一致——模拟器无后端时 App 呈网络错误态属预期）。

## 与 M14-76 / M14-80 对比（诚实漂移声明）

| 维度 | M14-76 | M14-80 | **M14-82（本次）** |
|---|---|---|---|
| 基线 main | `2bbeb07…`（PR #163 前） | `5670a04…`（PR #167） | `e1f128b`（PR #168） |
| pytest | 417 passed, 1 skipped | 427 passed, 1 skipped | **427 passed, 1 skipped**（与 M14-80 持平，零漂移） |
| HAP bytes | 188,984 | —（未重建 HAP，纯工具加固） | **188,984**（大小与 M14-76 相同） |
| HAP SHA-256 | `1908C160…0547` | — | **`584E5D47…B341`**（大小相同、内容哈希不同——与 HAP zip 归档时间戳的已知非确定性一致，与 M13-10 记录的同类现象相符；不声称字节级内容一致） |
| layout | 49,948 B / 76 attr 节点 | — | **53,798 B / 82 节点（81 attr）**（模拟器 UI 采样时点状态差异：健康区文案/时间戳等动态内容导致大小漂移；两任务 layout 均 parseable 且关键词命中） |
| smoke | 6/6 ok | — | **6/6 ok，cleanup+uninstall 成功** |
| mock contract | — | — | **54/54 passed** |
| device_preflight | plan exit 0 | — | **plan exit 0**（loopback 拒绝语义保持） |

## 诚实边界

- **未签名 / 无 AGC / 无已签名 HAP / 不声明生产就绪**：本切片全程未接触任何
  AGC/签名材料（未设置 AIOS_HARMONY_* 材料环境变量），preflight 如实返回
  `blocked_by_external_materials`，产物为 unsigned HAP。
- 未验证真机（device_preflight 未跑 true-device 模式——模拟器按设计被拒绝）。
- 模拟器 UI 网络降级态为预期（无后端），不构成错误态。
- pytest 的 1 skipped 为套件既有的条件跳过（与 M14-76/M14-80 相同口径）。
- 本切片零生产触碰：未动容器、调度任务、共享运行时服务；未读取任何 secret。
- 未对任何发布链工具做修改（现有工具全部按预期工作，verify-only 完成）。

## 原始证据清单（.verify/m14-82-harmony-current-main-smoke/，gitignored）

```
f85eda4577eea3a182094471a048543952df48adc3bdfddfa22af80cdcf429db    53798  layout/device_smoke_layout.json
6e5cc362851c70a2d4e00eff7993ee1ea20ceb350ac7f57c8ecc0c5e4bcfb0f1     2035  step1_simulator_state.md
5fffbe40da63c4a1b57a15930dbdef32c18419cac384ebb1f39ea9173b2b6c5d     1119  step2_preflight.json
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855        0  step2_preflight.stderr.txt
3aade09377b2d06f41a53a80940b37ab896f4a32eb59f16846dce11ac147c5fd     1118  step2_preflight_require_materials.json
76655eea07872e584fe97875bad2c798f99bbdf5c7a256c529f5c740fa08cf9b     1117  step2_preflight_require_materials_strict.json
9ac865f912a752f14ced3ec71dbf01bb8c45382cc7955029b02ffccd6073a8d9     2156  step3_release_build.json
3fd58bbd34e289025e9ce96465319c34f06fc79523db51b912e05dccf46df748     1136  step3_release_build.stderr.txt
7287ba2e994508b4fe6c646ff74b91619463b383fe4a0f62a5798f7a0c5a06e3     8063  step4_device_smoke_confirm.json
d49ad9f07421056a562f7b6d3d11bf03b8b0a3fcb7cea0fd6b59ca5eacee2ee6      702  step4_device_smoke_confirm.stderr.txt
96b1b6e05af1159b1b824773a93d8ad80a4dad0bbf7c457c76779cf802fcb2c1     8347  step4_device_smoke_plan.json
508384b851460a7577d08e88182dd4560e002e0d74ebf4dbf7a844f7383ccb12      759  step4_device_smoke_plan.stderr.txt
12bd5454f1407e85b2ed348275822df6a5655415bf5f0328c86594eb6118b496     5067  step5_device_preflight_plan.json
ef3ac4c52bc1ba9c0fa53062e9dc09825b52237eb5a4c75cdac83b22ee07c5ca      553  step5_device_preflight_plan.stderr.txt
72fa16d4815f984909bee3c9c7c05a3a4133245b86681db6742303bd623c51b8      519  step6_pytest_harmony_release.txt
db5b1dc66d3262671b3f00f0de589286518193f127c62eac0f4d05a4f0c598d3     3250  step6b_mock_contract.txt
```
