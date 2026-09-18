# M13-16 Harmony 发布链五阶段工具——验收证据（M13-16a docs-only 回填）

## 1. 范围与元数据

- **本切片身份**：M13-16a 为 docs-only 证据回填——零生产/测试代码改动、零设备操作、零密钥触碰，只把已合并的 M13-16「Harmony 发布链五阶段 fail-closed 工具」验收事实转为可审计仓库证据；本 README 是本目录唯一入库文件。
- **回填基点**：分支 `docs/m13-16-harmony-release-chain`，worktree `m13-16a-harmony-release-docs`，基于 `main@65129876b6b5131470d76241ebc21df0cfcdd52e`（**PR #127** merge commit）。
- **被回填的功能交付**：M13-16 已随 PR #127 合并 main，feature commits 共 7 个：`a7553f4` preflight、`10b2c97` release build、`379f6c4` signing wrapper、`5ae56e4` signature verification、`9f86926` device smoke、`38dca1a` layout 证据加固、`7c0f7c2` sign 路径收敛安全修正。
- **原始证据（只读，不入库、不复制）**：`D:\AI Learning OS\ai-learning-os-worktrees\m13-16-harmony-release-chain\.verify\m13-16-harmony-release-chain\harmony-emulator-smoke\`（另一验证 worktree 的 gitignored 目录，15 文件，SHA-256 清单见 §4）。签名凭据绝不复制进本仓库或任何文档。

## 2. 实现摘要：五阶段 fail-closed 发布链

工具落点 `tools/harmony_release/`（测试 `tests/harmony_release/`，294 项）。链以 fail-closed 语义串联：任一阶段违规即失败终止，不静默降级、不带病进入下一阶段。

| # | 阶段 | 工具（`python -m`） | commit | 职责 |
|---|------|----------------------|--------|------|
| 1 | preflight | `tools.harmony_release.preflight` | `a7553f4` | 签名前置 fail-closed 门禁（承 M13-10 契约：signingConfigs 空数组 unsigned 边界、仓库内材料扫描、环境变量材料校验、deterministic JSON） |
| 2 | release build | `tools.harmony_release.release_build` | `10b2c97` | `buildMode=release` 构建；AGC 材料缺位时产物即为未签名 HAP |
| 3 | sign | `tools.harmony_release.sign_hap` | `379f6c4`；`7c0f7c2` 路径收敛安全修正 | hap-sign-tool 签名包装；凭据仅环境变量传入、从不序列化 |
| 4 | verify | `tools.harmony_release.verify_signature` | `5ae56e4` | 签名校验；未签名如实报告 unsigned，不冒充已签名 |
| 5 | device smoke | `tools.harmony_release.device_smoke` | `9f86926`；`38dca1a` layout 证据加固 | 仅显式目标设备冒烟；变更 opt-in；layout 解析+哈希；收尾清理卸载 |

## 3. 验收矩阵

| 门禁 | 结果 |
|------|------|
| PR CI run `35295051405`（PR #127） | **5/5 success**：Docker、Release tools、API、Android、Web |
| supervisor 本地门禁（`7c0f7c2` 之后） | `tests/harmony_release` **294 passed**；`compileall` 通过；`ruff`（F、E9）通过；`git diff --check` 干净 |
| 修正版 Harmony 模拟器冒烟（`05_corrected_run.json`） | **status ok、exit 0、mutation performed、6/6 commands、0 failures、cleanup ok、bundle uninstalled** |

修正版冒烟锚定产物（对应 §4 清单）：

- layout `05_corrected_layout/device_smoke_layout.json`：node_count **2708**、**53798 bytes**、SHA-256 `D56825952B806EDDAC50F1AC794CD4C656C3354E60D91012CC5B883A76147E61`
- HAP：**unsigned**、**452446 bytes**、SHA-256 `6389C7DF066635CCF274D27F918FC610963400AFD9871402D7EFE3DDABBC07E4`

## 4. 原始证据清单（只读；不入库）

证据根：`.verify/m13-16-harmony-release-chain/harmony-emulator-smoke/`（根相对路径，位于另一验证 worktree `m13-16-harmony-release-chain` 内）。下表所有路径均相对于该精确根。

| 相对路径 | bytes | SHA-256 |
|----------|-------|---------|
| `01_dryrun_plan.json` | 8290 | `c26cca71d493b2de250ed237e84e39dbc4e2d537a2fd419ce77fff0ab1c23c18` |
| `01_dryrun_summary.txt` | 390 | `ccdad33d06c5a9a045890efde7dbd6889ce163d612ae8da4e50c3f1af76db01a` |
| `02_unsigned_run.json` | 8092 | `7d2de5737b0642d49d8dc801d7958b3c2f0276a7d409ae53b339805037dedb50` |
| `02_unsigned_summary.txt` | 388 | `7492b3b745d04c93263f0bcc8a8f89dabd2e39dd3b3c27cb94787f8bb4c5dede` |
| `03_unsigned_run_dirfix.json` | 8003 | `75520c5af62e98ccff8e69a5d04b3b3b3e39be829e8567d4fdf123f3f58c10ea` |
| `03_unsigned_summary.txt` | 335 | `db363480c5906e00b99d798f63151197b24c094f74ff345381f1156c1c49c677` |
| `04_hilog_tail.txt` | 835056 | `875355d0378e784126b5cbe2c1ae6320cdcd2232a9ab1047a8009798de8af6f4` |
| `04_ps_full.txt` | 14360 | `42103198acfc33992ee0958a474a3d102d3f7e49846d679a25f04a54d76ec1c1` |
| `04_screen.jpeg` | 80154 | `97a1647a0fd670833500735a023503b1516c5dc73aee977b4b3fe2680f0ed141` |
| `04_supplement_cycle.log` | 1051 | `cbbf98abaf8d5512723e84fd244351759a6e8e7c9287c1a73474b63284425749` |
| `05_corrected_layout/device_smoke_layout.json` | 53798 | `d56825952b806eddac50f1ac794cd4c656c3354e60d91012cc5b883a76147e61` |
| `05_corrected_run.json` | 8063 | `1acec9c8ed7bb0f96455494a89a1bce20f14dd4a724ec5def68cf33a66d92845` |
| `analyze_layout.py` | 4364 | `1667e905897a994db7f852fe38dfc9783a31f9f4bde7fdcea120f2f121e1ce63` |
| `layout/device_smoke_layout.json` | 53798 | `b7f9a5eacb1546c5cfc99b1294f7919cf8d56eb51434c3796bce3f1e0c956cdf` |
| `run_supplement.cmd` | 1843 | `265872c5ee1749e03f8b0ea168ab17033098a9b08b6ae94cdbfe2b754b4c8353` |

**两份 layout 快照说明**：`layout/device_smoke_layout.json` 与 `05_corrected_layout/device_smoke_layout.json` 字节数相同（53798）但 SHA-256 不同（`b7f9a5ea…` vs `d5682595…`）——layout 为按次抓取的快照，同规模不同内容属预期。**仅 `05_corrected_layout` 版本与 `05_corrected_run.json` 运行记录及 §3 锚定哈希一致**（即本轮验收判定依据；十六进制大小写不敏感）；`layout/` 目录为早期抓取，仅作过程留存。

## 5. 阶段与边界语义

- **退出码 fail-closed**：任一阶段失败即非零退出并终止链；preflight 沿用 M13-10 契约（`0`=ok/blocked、`1`=failure、`2`=blocked 且给了 `--require-materials`）。
- **无全设备发现**：工具不自动发现/枚举所有连接设备；仅操作显式指定的目标——本轮唯一目标为 Harmony 模拟器 `127.0.0.1:5555`。
- **变更 opt-in**：设备端变更（安装、写入、卸载）须显式授权才执行；本轮 `mutation performed` 为显式授权的一次验收执行。
- **layout 解析与哈希**：冒烟抓取 UI layout 后解析并记录 node_count/字节数/SHA-256（`38dca1a` 加固），作为可复核锚点。
- **清理**：收尾 cleanup ok 且 bundle uninstalled，不留设备残留。
- **未签名诚实**：产物明确 unsigned；verify 不把未签名冒充已签名；本轮不存在任何已签名 HAP。

## 6. 安全与凭据边界

- 签名凭据仅经环境变量传入，**从不序列化**进结果 JSON、日志或任何文档/仓库。
- **诚实限制**：hap-sign-tool 采用 argv 命令行协议——子进程存活期间，传入的值可能对本机进程列表可见。子进程生命周期短，但该暴露面客观存在，如实记录、不作隐瞒。
- 本轮验证未使用任何 AGC 发布材料、未执行真实签名、未触碰任何 Harmony 真机；仓库内无任何签名材料（`.gitignore` 含 `*.p12`/`*.p7b`/`*.cer`/`*.csr`/`*.jks` 防护，承 M13-10）。

## 7. 剩余阻塞与后续生产 backlog

- **直接生产阻塞**（完成前发布链不算生产就绪，`production_ready=false` 语义不变）：① AGC 发布材料创建；② 真实签名接入；③ Harmony 真机验证。
- **其后 backlog**：WORM 离线第二副本、定时归档、provider 冒烟外部配置处理、浸泡/真实负载测试、发布就绪评审与切换。
