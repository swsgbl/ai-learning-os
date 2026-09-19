# M14-67 Harmony release-chain 执行证据（verify-only）

## 结论

在基线 `a813b3b` 的独立 worktree（分支 `verify/m14-67-harmony-release-readiness`）
上，用**现有** `tools/harmony_release` 管线完成了一次完整的模拟器口径
release-chain 实跑：preflight → release_build（unsigned HAP + 哈希）→
device_smoke plan → device_smoke confirm-mutation（安装/启动/layout/后台/卸载
+ 清理）→ device_preflight plan-only。零代码改动，零共享 main 触碰。

## 命令与退出码

| 步骤 | 命令（要点） | exit | 关键结果 |
| --- | --- | --- | --- |
| preflight | `python tools/harmony_release/preflight.py --repo-root .` | 0 | `blocked_by_external_materials`；signingConfigs=[]（unsigned 边界成立）；repo 材料扫描 0 命中；无 AIOS_HARMONY_* 环境 |
| release_build | `python tools/harmony_release/release_build.py --repo-root .`（真实 hvigor clean + assembleHap release） | 0 | `ok`；clean=0、assemble=0（均含 BUILD SUCCESSFUL marker） |
| device_smoke plan | `device_smoke.py --target 127.0.0.1:5555 --hap <unsigned.hap> --device-id 127.0.0.1:5555` | 0 | `planned`；6 planned / 0 attempted / 0 executed；hardware_touched=false |
| device_smoke confirm | 同上 + `--confirm-mutation --layout-dir .verify/.../layout` | 0 | `ok`；6/6 executed；mutation_performed=true；cleanup ok |
| device_preflight plan | `device_preflight.py plan --target 127.0.0.1:5555 --bundle com.ailearningos.app` | 0 | `planned`；3 planned / 0 executed；0 设备命令 |
| pytest | `pytest tests/harmony_release -q` | 0 | 380 passed |
| ruff（canonical） | `ruff check services/api/app services/api/tests` | 0 | All checks passed |
| ruff（tools/harmony_release） | `ruff check tools/harmony_release` | 1 | 278 个 UP006/UP045 存量（main@a813b3b 同样输出，非本分支引入；本分支零代码改动） |

## HAP 工件与哈希

- `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
- size 188,984 bytes
- SHA-256 `FF3D88B73F961FA6F82B08AA517D343893F54E7DD3304D6D4B4A3642C60958AF`
- 交叉验证：certutil 独立计算 = 同值；device_smoke plan/confirm JSON 内记录 = 同值。
- `filename_has_unsigned=true`；**签名性未验证、不声称**（无 AGC/签名材料）。

## 设备冒烟（confirm-mutation）证据

目标 `127.0.0.1:5555`（target_hash `6460677A198B`，loopback，exact match）；
bundle `com.ailearningos.app`（AppScope/app.json5 解析）；ability `EntryAbility`。

- install → start → layout → background → uninstall **全部 ok**，6/6 命令执行。
- 布局证据（`uitest dumpLayout` 拉取）：
  `layout/device_smoke_layout.json`，55,535 bytes，
  SHA-256 `60A7374195D6CE393E9ADE57ACC884CE3B9DA4D5AC42E34996C3EAD554411603`，
  2,774 节点，parseable=true。
  可见文本含 `AIOS 只读面板`、`服务地址: http://127.0.0.1:8000`、`服务健康`、
  `认证状态`、`隐私模式`、`运维快照`、标签栏 `首页/学习/搜索/语音/设置/治理`、
  时钟 `08:24`；bundle 名 `com.ailearningos.app` 出现在树中。
  注：面板显示 `网络请求失败: 2300007`（API 服务未启动时的预期降级态，诚实记录）。
- **清理**：uninstall ok（cleanup.status=ok，bundle_uninstalled=true）；
  设备侧 layout dump 按设计保留（`/data/local/tmp/aios_device_smoke_layout.json`）。
- 模拟器状态：`hdc list targets` = `127.0.0.1:15566` + `127.0.0.1:5555`；
  `param get const.product.name` = `emulator`。

## 原始证据与完整性锚点（gitignored `.verify/m14-67-harmony-release-readiness/`）

```
5fffbe40da63c4a1b57a15930dbdef32c18419cac384ebb1f39ea9173b2b6c5d    1119  preflight.json
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855       0  preflight.stderr.txt
1b4f2cfea68a553cc1856f68f06e3f833ea626a59c7f02c0e81ed8416a7a02e1    1077  release_build.json
6824acdf971c99f9a1c2b19eb3d08bffb5a5d175ca1920ca28d4d0efe8cb23fb     216  release_build.stderr.txt
11b08ebad45d451f90044fc4c0d420c0207cf7794ac59c7541710e29ce779407    8290  device_smoke_plan.json
cb47508956e8a3b64e06241fbd6979aaae9aeeb0ebc215d15a9dc233d3f5a193     390  device_smoke_plan.stderr.txt
3e2e047236fd86b70af5f221f1c9805e8e21f03b5a100c9b644a14fe897533cc    8003  device_smoke_confirm.json
f126f7423ce939b924c13331d773096841ea57c2fade2256de18e2ee3ab371c4     335  device_smoke_confirm.stderr.txt
854ffa389bd0b9a41c145ca81e004812eaa09022e253b2f3634be953132d4a95    5075  device_preflight_plan.json
11c4051bf94b5f107048d3e3bd79e018bd24b3dbad35c0f6143501ed18e6be36     188  device_preflight_plan.stderr.txt
60a7374195d6ce393e9ade57acc884ce3b9da4d5ac42e34996c3ead554411603   55535  layout/device_smoke_layout.json
091c7de819c6d78011d2dca2920a54acc0d2e864465579ed7e06280b195ae0f0      33  hdc-list-targets.txt
e161d286399460c786f3407fca4c2b15d3df181a9ca22d28753fcf9dc12337c9      22  emulator-params.txt
044bc3811df911cc25b0590147bb0a2f3faa49f825db195848fd939d41e48dbb     508  pytest-harmony-release.txt
82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18      19  ruff-canonical.txt
fd8cb17709e7ea16d2c2ebca89fa35f21c7da360715ab8d2cc416649b407a0ac  170152  ruff-tools-harmony-release.txt
```

交叉印证：`device_preflight_plan.json` 与 M14-60 同参数产物
（`plan-127.0.0.1-5555-with-bundle.json`，SHA-256 `854ffa38…4a95`）字节一致
——确定性 JSON 输出的可复现性证据。

## 边界（不声称）

- **无 AGC / 签名 / 签名 HAP**：无签名材料，HAP 为 unsigned，签名链零结论。
- **仅模拟器（loopback）**：无真机；device_preflight 的 check 模式对 loopback
  目标 fail-closed 拒绝（M14-60 已实证），本任务按边界**只跑 plan**。
- **生命周期证据 = 退出码 + 布局**：未读设备日志/截图；runtime 状态未验证。
- **API 服务未启动**：面板健康区显示网络失败 2300007（预期降级态）。
- **零生产访问、零 provider 访问**；共享 main 未触碰。
- `tools/harmony_release` 的 ruff UP 类发现为 main 存量（278 个），本分支未改
  任何工具代码，不在本任务修复。
- 全局判定不变：**`production_ready=false`**。
