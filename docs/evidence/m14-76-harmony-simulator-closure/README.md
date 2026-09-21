# M14-76 Harmony 模拟器收官 release-chain 执行证据（verify-only）

## 结论

在当前 main 基 `2a0e911`（PR #162 合并点，晚于 M14-67 基线 `a813b3b` 两个
PR）的独立 worktree（分支 `verify/m14-76-harmony-simulator-closure`）上，
严格复刻 M14-67 的模拟器口径 release-chain 并全程实跑：
preflight → release_build（unsigned HAP + 哈希）→ device_smoke plan →
device_smoke confirm-mutation（安装/启动/layout/后台/卸载 + 清理）→
device_preflight plan-only → 聚焦 pytest。零代码改动，零共享 main 触碰，
零生产/AGC/真机访问。**smoke 6/6、pytest 417 passed（>380 要求）**。

## 命令与退出码

退出码以各工具 JSON 内自记的 `exit_code` 字段为地面真值（cmd 的
`%errorlevel%` 回显存在解析期展开假值问题，见"陷阱记录"）。

| 步骤 | 命令（要点） | exit | 关键结果 |
| --- | --- | --- | --- |
| preflight | `python -m tools.harmony_release.preflight --repo-root .` | 0 | `blocked_by_external_materials`；signingConfigs=0（unsigned 边界成立）；repo 材料扫描 0 命中；无 AIOS_HARMONY_* 环境 |
| release_build | `python -m tools.harmony_release.release_build --repo-root .`（真实 hvigorw clean + assembleHap release） | 0 | `ok`；clean/assemble 均 exit 0 且含 success marker |
| device_smoke plan | `device_smoke --repo-root . --target 127.0.0.1:5555 --hap <unsigned.hap> --device-id 127.0.0.1:5555` | 0 | 6 planned / 0 executed；hardware_touched=false；plan_only=true |
| device_smoke confirm | 同上 + `--confirm-mutation --layout-dir .verify/m14-76-harmony-simulator-closure/layout` | 0 | `ok`；**6/6 executed**；mutation_performed=true；cleanup.status=ok（uninstall 成功） |
| device_preflight plan | `device_preflight plan --repo-root . --target 127.0.0.1:5555 --bundle com.ailearningos.app` | 0 | `planned`；3 planned / 0 executed；零设备命令 |
| pytest | `python -m pytest tests/harmony_release -q`（主仓 .venv 解释器） | 0 | **417 passed, 1 skipped**（14.49s） |
| ruff（canonical） | `ruff check services/api/app services/api/tests` | 0 | All checks passed |
| ruff（tools/harmony_release） | `ruff check tools/harmony_release` | 1 | 278 个 UP006/UP045/UP035 存量（main 存量，本分支零代码改动不在本任务修复） |

## HAP 工件与哈希

- `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
- size 188,984 bytes
- SHA-256 `1908C160FEA60B6255A8BFEFDAFF447850DC57939C1B38556C09E7E3F4410547`
- 交叉验证（三方一致）：release_build.json 内记录 = device_smoke
  plan/confirm JSON 内记录 = PowerShell `Get-FileHash` 独立复算。
- 与 M14-67（`FF3D88B7…58AF`）size 相同、哈希不同：基线不同（`2a0e911` vs
  `a813b3b`）+ 构建时间戳，如实记录。
- `filename_has_unsigned=true`；**签名性未验证、不声称**（无 AGC/签名材料）。

## 设备冒烟（confirm-mutation）证据

目标 `127.0.0.1:5555`（loopback，exact match）；bundle
`com.ailearningos.app`（AppScope/app.json5 解析）；ability `EntryAbility`。

- install → start → layout → background → uninstall **全部 ok，6/6 命令执行**；
  failures=[]；cleanup ok（bundle_uninstalled）。
- 布局证据（`uitest dumpLayout` 抓取）：`layout/device_smoke_layout.json`，
  49,948 bytes，SHA-256 `DE46F1DFA6AD2EFE797843481471DA4BA0CAD3BB291EBEB7E1C9FD3F76315C83`，
  parseable=true，含 attributes 的节点 76 个。
  可见文本含 `AIOS 只读面板`、`服务地址: http://127.0.0.1:8000`、`整体刷新`、
  `服务健康`、`认证状态`、`隐私模式`、`服务版本`、`运维快照`、
  `审计日志(最近 100 条)`、标签栏 `首页/学习/搜索/语音/设置/治理`。
  bundle 名 `com.ailearningos.app` 存在于 layout 原始字节（属性字段），
  但不在可见 text 中——与 M14-67 "出现在树中"口径一致（属性而非渲染文本）。
  注：面板服务地址指向 `http://127.0.0.1:8000` 且 API 服务未启动，
  健康区为预期降级态（本次 layout 文本中未见 M14-67 记录的
  `网络请求失败: 2300007` 字样，降级渲染存在时序差异，如实记录）。
- **清理**：uninstall ok；设备侧 layout dump 按设计保留
  （`/data/local/tmp/aios_device_smoke_layout.json`）。
- 模拟器状态：`hdc list targets` = `127.0.0.1:15566` + `127.0.0.1:5555`；
  `param get const.product.name` = `emulator`；
  `param get bootevent.boot.completed` = `true`。

## 确定性复现交叉验证（跨任务、跨基线）

`device_preflight_plan.json`（SHA-256 `854FFA38…4A95`）与 M14-67 worktree
同参数产物**字节一致**——尽管两任务基线不同（`2a0e911` vs `a813b3b`）。
plan 模式零读/零 spawn 的纯计划输出可复现。
`device_smoke_plan.json` 与 M14-67 不同（`4A53885B…` vs `11B08EBA…`），
原因是其内嵌本次构建的 HAP 哈希（预期差异，非不确定性）。

## 原始证据与完整性锚点（gitignored `.verify/m14-76-harmony-simulator-closure/`）

`SHA256SUMS.txt` 为目录清单（16 文件，不含自身）：

```
854ffa389bd0b9a41c145ca81e004812eaa09022e253b2f3634be953132d4a95     5075  device_preflight_plan.json
11c4051bf94b5f107048d3e3bd79e018bd24b3dbad35c0f6143501ed18e6be36      188  device_preflight_plan.stderr.txt
e916abd47a5dd6d6ec2711df5e93def8f0d461eb21f994ae2d7343f12022dc82     8003  device_smoke_confirm.json
9374bac52d76f0c0f24d76dd1ee8eda94eedc466bf3e3db8fe390ab72280c4b8      335  device_smoke_confirm.stderr.txt
4a53885b36c8a6d283302eff95fa37d58f0f9b0443fc6d183628e8a5824178c0     8290  device_smoke_plan.json
444f0b658dfcd5dbd6f82eb7b862f83916b82895d7e45d12c7c16eadce5ee67d      390  device_smoke_plan.stderr.txt
e9c66640fdc99b596ae0a236cd5098ac5105eedc207376b26ffcdfbd4d46ba5c       18  emulator-params.txt
091c7de819c6d78011d2dca2920a54acc0d2e864465579ed7e06280b195ae0f0       33  hdc-list-targets.txt
5fffbe40da63c4a1b57a15930dbdef32c18419cac384ebb1f39ea9173b2b6c5d     1119  preflight.json
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855        0  preflight.stderr.txt
8778698b02ef505053e7fd1760c2662f318957d61e5384fe2014cae6fd36a5c8      519  pytest-harmony-release.txt
b572d3496b123b65163720ce66117a5a7fd3e184996fe9b5e11095cecb5ce6d6     1077  release_build.json
5d863a3bb1e93a1b6a0b20d9c52459085e0cbbee00d8daeb33e4093208f1fff9      216  release_build.stderr.txt
82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18       19  ruff-canonical.txt
16af4bc26cefe8a7f944d8c3fc4e26abebf0f9d6bdb7cd599e66f520251dd7fc   161498  ruff-tools-harmony-release.txt
de46f1dfa6ad2efe797843481471da4ba0cad3bb291ebeb7e1c9fd3f76315c83    49948  layout/device_smoke_layout.json
```

## 与 M14-67 的差异（如实记录）

| 项 | M14-67 | M14-76 | 说明 |
| --- | --- | --- | --- |
| 基线 | `a813b3b` | `2a0e911` | 本任务为 current-main 口径（新 2 个 PR） |
| HAP SHA-256 | `FF3D88B7…58AF` | `1908C160…0547` | 基线+构建时间戳差异 |
| layout 大小/节点 | 55,535 B / 2,774 节点 | 49,948 B / 76 attributes 节点 | 采样时 UI 状态与节点计数口径差异；两任务均 parseable 且关键词命中 |
| pytest | 380 passed | 417 passed, 1 skipped | 基线新增测试 |
| ruff tools 存量 | 278 | 278 | 完全一致 |

## 陷阱记录（cmd / 证据纪律）

- `& echo ===EXIT %errorlevel%` 在 cmd 单行内属解析期展开，可能回显前一
  命令的退出码（本次 ruff tools 步骤回显 0 而真实 exit 1）。本任务所有
  链条步骤的真实退出码均取自工具自身落盘 JSON 的 `exit_code` 字段，不依赖
  shell 回显。
- 多行 Python 内联脚本在 cmd 中会被截断（SyntaxError）；布局/清单探针均
  落为 `.verify` 下临时脚本执行（`_layout_probe.py`、`_manifest_probe.py`，
  不入清单）。

## 边界（不声称）

- **无 AGC / 签名 / 签名 HAP**：无签名材料，HAP 为 unsigned，签名链零结论。
- **仅模拟器（loopback）**：无真机；device_preflight 的 check 模式对
  loopback 目标 fail-closed 拒绝（M14-60 实证，plan JSON 中
  `loopback_rejected_in_true_device_mode=true`），本任务按边界**只跑 plan**。
- **生命周期证据 = 退出码 + 布局**：未读设备日志/截图；runtime 状态未验证
  （`evidence_boundaries.lifecycle_evidence=exit_codes_only`）。
- **API 服务未启动**：面板服务地址指向 127.0.0.1:8000，健康区为降级态。
- **零生产访问、零 provider 访问、零主仓 main 触碰**；本分支零代码改动。
- `tools/harmony_release` 的 ruff UP 类发现为 main 存量（278 个），不在本
  任务修复。
- 全局判定不变：**`production_ready=false`**。
