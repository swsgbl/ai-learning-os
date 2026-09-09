# M13-10 第一切片「Harmony release/AGC 签名 preflight 固化」— 验收证据归档

- 日期：2026-09-09（hmharness 验证轮在先；本切片实现随后，独立会话）
- 分支：`feature/m13-10-harmony-agc-signing-readiness`（基于 `main@4572551`，即 M13-09 合并提交，本地 git 可验证）
- 状态：本地实现完成，本地提交完成；**未 push、未开 PR——PR/CI/合并状态刻意不在此预写，待后续状态切片回填**（本切片按要求未改动 ROADMAP/PROJECT_STATUS/DEVELOPMENT 的 PR 状态）
- 原始证据路径：`.verify/m13-10-harmony-agc-signing-readiness/`（gitignored，不入库，不改名不移动；本 README 不复制任何原始工件全文）
- 范围：**仅非 Harmony 工程实现**（`tools/`、`tests/`、`.gitignore`、本 README）；`apps/harmony/**` 零改动（Harmony 配置后续只能由 hmharness 处理）

## 一、hmharness 已验证事实（本切片的上游输入，归档口径）

以下事实由 hmharness 验证轮产生（报告 `.verify/m13-10-harmony-agc-signing-readiness/HMHARNESS_REPORT.md`，2026-09-09，基线 `origin/main@4572551`）：

| # | 项 | 已验证事实 |
|---|---|---|
| 1 | release 构建可执行性 | `hvigorw.bat assembleHap --mode module -p product=default -p buildMode=release --no-daemon`（`apps/harmony` 目录、`DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`）→ **exit 0**，BUILD SUCCESSFUL in 6s347ms |
| 2 | release 产物 | `apps\harmony\entry\build\default\outputs\default\entry-default-unsigned.hap`，**198569 bytes**，SHA256 **`21CF87CA17BF2FBEAED9591BDE619B7627303DA0DDDF08216F0B65832C476D3B`**（HAP 为 zip 打包产物，同源重建哈希可能不同，以该留存产物为准） |
| 3 | 产物签名形态 | 文件名后缀 `-unsigned.hap`——release 模式可构建 ≠ AGC 已签名，两者不可混同 |
| 4 | 构建警告清单 | **仅 2 条 WARN、无 ERROR**：①release 混淆开关提示（`ruleOptions.enable=false` 为既有配置）；②`No signingConfig found for product default`（诚实未签名边界） |
| 5 | signingConfigs 边界 | `apps/harmony/build-profile.json5` 中 `signingConfigs: []`（HEAD 4572551 原样），与 M13-09 证据一致 |
| 6 | AGC 签名前置 | **blocked_by_external_materials**——仓库内零材料（无 *.p12/*.cer/*.p7b/*.csr/*.jks）、零约定变量名；本机仅有 DevEco 本地调试身份（非 AGC 发布材料）；按任务定义不算失败 |
| 7 | 设备状态 | `hdc list targets` → `127.0.0.1:5557`（1 个本地模拟器**在线**）；**未安装产物、未做任何运行时验证**——模拟器在线 ≠ 真机验证 ≠ 运行时行为验证 |

## 二、本切片实现（非 Harmony 工程面）

| 文件 | 内容 |
|---|---|
| `tools/harmony_release/preflight.py`（新增，标准库实现，300 行） | 签名前置 fail-closed 门禁 CLI：①校验 `build-profile.json5` `signingConfigs` 仍为空数组并把 unsigned 边界写进结果；②仓库内扫描 `.p12/.p7b/.cer/.csr/.jks`（排除 .git/.verify/build/node_modules 等目录），存在即失败并报告扩展名+相对路径（只看文件名，绝不读取内容）；③仅检查 `AIOS_HARMONY_CERT_PATH` / `AIOS_HARMONY_PROFILE_PATH` / `AIOS_HARMONY_KEYSTORE_PATH` 三个变量名的存在性，存在时校验路径存在、是常规文件、扩展名分别为 `.cer/.p7b/.p12`、resolved 路径在仓库外，任何违规失败；**绝不打印环境变量值/路径值/文件内容**；④`--hap` 只记录存在/字节数/SHA256/文件名是否含 unsigned，不推断已签名；⑤`--strict` 把 warning 升级为 failure；缺材料不是 warning，而是明确的 `blocked_by_external_materials` 状态；输出 deterministic JSON（排序键、无时间戳、无绝对路径） |
| `tools/harmony_release/json5lite.py`（新增） | preflight 的最小 JSON5 兼容加载器（纯 JSON 优先，失败后退到注释/尾逗号剥离重试，字符串感知；不支持非引号键，不可解析返回 None 由调用方 fail-closed）；也支持 `python -m tools.harmony_release.preflight` 与直接脚本两种入口 |
| `tests/harmony_release/test_preflight.py`（新增） | 35 项针对性测试，全部使用临时目录+占位字节（`placeholder-not-a-real-certificate`），不生成真实证书；覆盖任务要求的全部场景（见「四、验证记录」） |
| `.gitignore`（追加） | 签名材料扩展名防护：`*.p12` `*.p7b` `*.cer` `*.csr` `*.jks` + 说明注释（仓库内本无此类 tracked 文件，纯防御未来误提交，不影响现有 ignore 语义，`git ls-files` 本地可验证） |
| 本 README | 唯一入库文档证据 |

`apps/harmony/**` 零改动；未引入任何真实签名材料；未输出任何 token/key/password/secret；未访问 AGC。

## 三、preflight 工具用法

```bash
# 默认（当前仓库）：缺外部材料 → status=blocked_by_external_materials，exit 0
python tools/harmony_release/preflight.py

# CI/发布流水线口径：材料必须就位，缺材料 → exit 2
python tools/harmony_release/preflight.py --require-materials

# 严格模式：warning 一并升级为 failure（exit 1）
python tools/harmony_release/preflight.py --strict

# 记录 HAP 产物事实（只记录，不推断签名）
python tools/harmony_release/preflight.py --hap apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap

# 指定仓库根（测试/异构 checkout）
python tools/harmony_release/preflight.py --repo-root <path>
```

退出码契约：`0` = ok 或（默认模式下）blocked；`1` = failure（fail-closed 违规，或 --strict 升级）；`2` = blocked 且给了 `--require-materials`。

外部材料接入方式（唯一约定）：三个环境变量 `AIOS_HARMONY_CERT_PATH`（.cer，发布证书）、`AIOS_HARMONY_PROFILE_PATH`（.p7b，发布 Profile）、`AIOS_HARMONY_KEYSTORE_PATH`（.p12，发布密钥库），**材料文件必须放在仓库外**（工具对指向仓库内的路径 fail-closed）。JSON 输出对每个变量只含：变量名 / present / valid / expected_extension / error 类别——不含任何路径值。

失败类别（`failures[].code`）：`build_profile_missing`、`build_profile_unparseable`、`signing_configs_missing`、`signing_configs_invalid`、`signing_configs_not_empty`、`repo_signing_material_present`、`external_material_invalid`、（--strict 下）`hap_path_missing` / `hap_filename_not_unsigned`。

JSON5 兼容说明：`build-profile.json5` 解析先按纯 JSON，失败后退到最小化注释/尾逗号剥离重试（不支持非引号键）；任何不可解析情形均 fail-closed 为 `build_profile_unparseable`，绝不放行。

## 四、验证记录（本切片实现会话，本地口径）

| 项目 | 结果 |
|---|---|
| 针对性测试 | `python -m pytest tests/harmony_release -q`（canonical venv `D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`，Python 3.11）→ **35 passed in 0.51s**。覆盖：干净仓库 blocked/exit 0；`--require-materials` 缺材料 exit 2；仓库内材料 fail（含大小写扩展名、排除目录不误报）；外部正确材料通过（含 ok 状态下仍记录 unsigned 边界）；扩展名错误/路径不存在/非常规文件/仓库内路径/空值 各自 fail-closed 类别；JSON 不含环境变量值/绝对路径（正向与违规场景双向断言）；真实仓库 build-profile signingConfigs 读取（空数组）+ 非空/缺失/非列表/不可解析/json5 注释/字符串含括号 各分支；--hap 记录与 --strict 升级；CLI 子进程 exit 码与默认仓库根 |
| 回归 | `python -m pytest tests/android_smoke -q` → **295 passed / 1 skipped**（与 M13-09 基线完全一致，skip 为既有 Windows symlink 特权测试） |
| 编译检查 | `python -m compileall tools/harmony_release` 通过 |
| 真实仓库冒烟 | 本 checkout 上运行 preflight → `status=blocked_by_external_materials`、`repo_materials.count=0`、`build_profile.unsigned_boundary=true`、exit 0——与 hmharness 验证轮结论一致 |
| git 检查 | `git diff --check` 干净；`git status --short` 仅预期文件 |

## 五、不得声称的事项

1. **不得声称 AGC 已签名 / 可发布**：`release` 构建成功（exit 0、198569 bytes）仅证明 `buildMode=release` 可执行；产物名仍为 `entry-default-unsigned.hap`、`No signingConfig found` 警告仍在、`signingConfigs: []` 未变——与「已签名/可发布」之间没有任何已验证的等价关系。
2. **不得声称签名材料已就位或已创建**：仓库与本机均无 AGC 发布材料；本切片刻意未创建、未修改、未读取任何签名材料（preflight 对仓库内材料只看文件名，对外部材料只做 stat/扩展名检查）。
3. **不得声称真机验证**：仅 hmharness 记录到本地模拟器 `127.0.0.1:5557` 在线；未做任何真机连接、安装或运行验证。
4. **不得声称运行时行为验证**：本切片不含安装/启动/运行时验收；release 产物未在任何设备上安装，其运行时正确性未验证。
5. **不得声称 HAP 哈希跨构建稳定**：HAP 为 zip 打包产物，同源重建哈希可能不同；SHA256 `21CF87...76D3B` 以 hmharness 当时留存产物为准。
6. **不得声称混淆已启用**：release 日志的混淆提示 WARN 为既有 `ruleOptions.enable=false` 配置的通用提示，本切片未启用/验证任何混淆。
7. **不得声称 CI/远端验证**：CI 无 HarmonyOS job，本切片全部验证为本地口径；后续 PR 的 API/Android/Docker/Web CI 结果不能扩大为 HarmonyOS 远端验证。
8. **不得声称 preflight 通过等价于签名配置正确**：preflight 只做存在性/类型/路径边界检查；材料与 bundleName `com.ailearningos.app` 的匹配、signingConfigs 接入方式（本地路径 vs 环境变量 vs CI secret）属后续 hmharness 实现切片，均未验证。
9. **不得预写 PR/CI 状态**：本 README 时点本地提交完成、未 push、未开 PR；ROADMAP/PROJECT_STATUS/DEVELOPMENT 的 PR 状态未改动、待回填。

## 六、已知边界与下一步

- **B-1（外部材料，沿用 hmharness blockers）**：AGC 发布签名材料（发布证书 .cer / 发布 Profile .p7b / 发布密钥库 .p12）未就位 → preflight 在真实仓库上的结论是 `blocked_by_external_materials`。下一步（需运维授权）：AGC 后台创建发布证书与 Profile，材料放仓库外，经三个 `AIOS_HARMONY_*_PATH` 变量引用。
- **B-2（签名配置接入）**：材料就位后 `build-profile.json5` signingConfigs 的接入由 hmharness 后续切片处理；届时 preflight 的 `signing_configs_not_empty` fail-closed 契约需同步修订（当前契约 = 仓库必须保持诚实未签名边界）。
- 观察项（hmharness O-1）已由本切片关闭：`.gitignore` 显式 `*.p12/*.p7b/*.cer/*.csr/*.jks` 防护已补。
- CI 未纳入 `tests/harmony_release`（CI 仅跑 `services/api`，与 `tests/android_smoke` 同为本地/canonical venv 口径）；如需纳入 CI 属后续治理决策。

## 七、合并后状态回填（2026-09-09，本节由状态回填切片追加）

上文一～六节为合并前本地切片时点的历史记录，**原样保留**（含当时「本地提交完成、未 push、未开 PR」的仓库状态描述与「不得预写 PR/CI 状态」约束——那是文档时点事实，不代表当前状态）。已验证事实如下（注意时点：hmharness 复检一条为 PR #69 合并**前**、基于 feature commit 的事实，其余为合并后观察）：

- **PR #69 已合并 main**：https://github.com/swsgbl/ai-learning-os/pull/69「M13-10: Harmony AGC signing preflight」；feature commit `7342c519111893763d199c104aa3d22c403389d7`（chore: add harmony signing preflight，7 files +889、零删除——即第二节所列全部文件，`apps/harmony/**` 零改动）。
- **PR CI run `34296903848`** 四项 job（API/Android/Docker/Web）全部 success（Web 1m25s、Docker 2m28s、API 3m49s、Android 3m36s）。
- **merge commit `bc41d6cf083191958ca9710ae5b71ba31e056aa5`**（本地 git 可验证：parents 为 PR #68 merge commit `2fded00` 与 feature commit `7342c519`；merge 与 feature 的差异仅为 PR #68 的四份 M13-09 状态回填文档——本切片文件全部按 feature commit 原样入库）。
- **merge 后 main CI run `34297190550`** 四项 job（API/Android/Docker/Web）全部 success（Web 1m26s、Docker 2m36s、API 3m45s、Android 3m25s）。
- **远端功能分支 `feature/m13-10-harmony-agc-signing-readiness` 已在合并后删除**。
- **hmharness 复检（合并前时点，非合并后）**：clean/release 两次构建均基于 feature commit `7342c519111893763d199c104aa3d22c403389d7`、完成于 PR #69 合并之前，时点 worktree 相对远程跟踪分支 ahead 1 / behind 2；clean 与 release 构建均成功，0 ERROR、2 条预期 WARN（与第一节清单一致：release 混淆开关提示 + `No signingConfig found`）；未签名 HAP **198569 bytes、SHA256 `D67FDA0B46018B30CD5F28A6D64BB320D592CE90246053E5102ABABF777490FD`**——与第一节留存产物（198569 bytes、`21CF87CA…76D3B`）同字节数、不同哈希，符合第五节第 5 条「HAP 为 zip 打包产物、同源重建哈希可能不同」的已知边界；两次 preflight 调用均 exit 0 且 `status=blocked_by_external_materials`；tracked 文件未变。

第五节「不得声称的事项」在合并后**全部继续有效**，特别重申：AGC 发布材料仍缺位（仓库与本机均无发布签名材料，preflight 结论仍为 `blocked_by_external_materials`）；不存在任何已签名 HAP（release 构建成功 ≠ 已签名 ≠ 可发布）；未做真机验证与任何运行时验证；CI 无 HarmonyOS job（PR run `34296903848` 与 main run `34297190550` 四项 success 均不扩大为 HarmonyOS 远端验证）；preflight 通过 ≠ 签名配置正确；`production_ready=false` 语义不变。ROADMAP/PROJECT_STATUS/DEVELOPMENT 的 M13-10 条目已由本回填切片同步补记（实现切片当时按约刻意未预写，本 README 第一节「状态」行为历史时点记录）。
