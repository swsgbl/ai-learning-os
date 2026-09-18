# M14-50 Harmony 模拟器 Stage C 证据回填（docs-only）

## 结论

supervisor 已于 2026-09-18（时间线产物记录 12:28:28 起，GMT+8）在验证
worktree `m14-50-harmony-current-smoke`（detached
`main@b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，即 **PR #129 merge**，
全程 HEAD 不变）完成 Harmony **Stage C 多阶段功能验证**，判定 **PASS**：
经真实设置 UI 保存服务地址 `http://10.0.2.2:8765/` 并持久化；五个 pane
（Home / Study / Search / Voice / Governance）对 task-owned mock 全部正向
渲染；受控 mock 故障 + 应用内「重试」真实恢复；崩溃指标全 0；卸载后
bundle 不存在；task-owned mock 全清理且模拟器保留运行。

本回合（worktree `m14-52-mobile-harmony-evidence-backfill`，分支
`docs/m14-52-mobile-harmony-evidence-backfill`，基于 `main@fba3bb1`，
PR #134 merge）为 docs-only 回填：**零设备运行、零生产触碰、零代码
改动、不 push、不建 PR**；只读取验证 worktree gitignored
`.verify/m14-50-harmony-emulator-stage-c/` 证据，独立复核后入库。源
REPORT.md（10,392 bytes）为权威事实来源，本 README 为唯一入库证据文件。

## 双基线与 docs-only 区间

| 基线 | 角色 |
|------|------|
| `b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`（PR #129 merge） | Stage C **验证基线**（验证 worktree detached 于该 commit，三个 phase 全程 HEAD 不变） |
| `fba3bb1`（PR #134 merge） | 本 docs 回填分支基点（`main@fba3bb1`） |

`b9e8cc8..fba3bb1` 共 10 个提交（`fba3bb1` merge PR #134 就绪 CLI、
`74d099d` ops readiness CLI、`965eb91` merge PR #133 Stage B 回填、
`37b5b22` docs Stage B、`d613667` merge PR #131 移动端冒烟回填、
`761f3fd` merge PR #132 归档调度器、`298807a` docs mobile smoke、
`f9c2c84` ops audit archive scheduler、`15580f2` merge PR #130、
`849f49f` docs audit worm offline），全程 12 files / 2488 insertions，
只触 `docs/` 与 `tools/ops/` + `services/api/tests/`（归档调度器/就绪
CLI）；对 `apps/harmony`、`tools/harmony_release` 的区间 diff 为
**空**——Stage C 验证所用移动端源码与本回填基线完全一致，无源码漂移
（区间同样不触 `apps/android`、`tools/android_smoke`、`tests/android_smoke`）。

## 回填前只读复核（本 docs 回填回合，零设备/零生产）

- HAP 独立重哈希（只读）：
  `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  → SHA-256
  `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`、
  **188,984 bytes**——与源 `hap-hash.txt`、源 REPORT（清理时 certutil
  复算同值）及 Stage A 冒烟 / Stage B 交互验证已归档锚定值一致（同一
  HAP，三个验证阶段共用同一产物）。
- 源证据目录全量清单重derive（`sha256sum` + `wc -c`，只读）：**110 文件 /
  5,202,795 bytes**；确定性聚合清单 SHA-256 =
  `ec60539a5ee18b08ed3931046f8f3e38add2faae6f597e722873058cff69b9aa`。
- 源 REPORT.md 独立重哈希：10,392 bytes，SHA-256
  `85b05dbed0e5abd0d77cfba1bce939ec580ed16ea163c1f3f774ada7dd8274cc`；
  三份 phase report（URL / POSITIVE / RECOVERY）verdict 均 **PASS**，
  哈希锚定见下节。
- 故障四项独立重数：`artifacts/cleanup-hilog-dump.txt`（883,213 bytes）
  逐项 `grep -c` 计数 `cppcrash`=0、`jscrash`=0、`appfreeze`=0、
  `FaultLog`=0——与源 REPORT §Fault scan 一致。
- PID 稳定双见证：源 REPORT §App PID stability 声明 **30005** 全程不变
  （uid 20020069，进程 `20020069 30005 139 … com.ailearningos.app` 无
  重启）；phase 产物（`phase-url-pre-pid.txt` 等 PID 见证文件）与
  timestamps.txt 中 12:29:37 起 PID 30005 记录一致。
- 卸载证明独立复核：`artifacts/cleanup-bm-dump.txt`（168 bytes）为
  "failed to get information" 类错误输出（bundle 不存在的权威响应）；
  `artifacts/cleanup-bm-dump-all.txt` 全 bundle 列表 0 处匹配
  `ailearningos`。
- mock 清理独立复核：`artifacts/cleanup-mock-verify.txt`（394 bytes）
  记录 PID `61400`/`86020`/`88404` 均 not found、`:8765` 监听 **0**。
- 时间线复核（`timestamps.txt`，UTF-16LE 编码，37,214 bytes）：12:28:28
  PHASE-A 起 → 12:34:51 记录止。**诚实记录**：首轮（12:28:28）
  install/launch 的 hdc 调用语法错误（报 "Unknown operation command"，
  app PID NOT_FOUND，零副作用）；12:29:30 PHASE-A2 以修正后的 hdc 调用
  重试，12:29:31 "install bundle successfully" + 启动成功、12:29:37 PID
  30005——该首轮失败在源 REPORT 中未述，出自本回填回合对 timestamps.txt
  的独立复核，如实记录于此。
- git 区间核验：`git log --oneline b9e8cc8..fba3bb1` 共 10 提交；
  `git diff --stat b9e8cc8 fba3bb1 -- apps/harmony tools/harmony_release`
  输出为空。
- 源 `.verify/` 目录本回合只读未写（`.gitignore` 含 `.verify/` 规则，
  原始产物不入 git）。

## Stage C 验证事实（源：gitignored `.verify/m14-50-harmony-emulator-stage-c/`）

- **设备与被测物**：模拟器单目标 `127.0.0.1:5555`（全程唯一 hdc 目标）；
  被测 `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  ——**188,984 bytes**，SHA-256
  `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`
  （验证回合在清理时复算哈希一致）。
- **六项 per-phase 判定**（全部 PASS）：

  | 阶段 | 判定 | 报告 |
  |---|---|---|
  | URL 持久化（设置 → 真实 UI 保存 `http://10.0.2.2:8765/`） | **PASS** | PHASE-URL-REPORT.md |
  | 五 pane 正向（Home / Study / Search / Voice / Governance） | **PASS**（五 pane 全部） | PHASE-POSITIVE-REPORT.md |
  | 受控 mock 故障 + 应用内重试恢复 | **PASS** | PHASE-RECOVERY-REPORT.md |
  | 稳定性 / 故障扫描 | **PASS**（四项全 0） | REPORT §Fault scan |
  | 卸载 + 证明 | **PASS** | REPORT §Uninstall |
  | task-owned mock 清理 | **PASS** | REPORT §Mock cleanup |

- **URL 持久化**：经真实设置 UI（longClick → 全选 → 剪切 → `uiInput
  text` → 保存）保存 `http://10.0.2.2:8765/`；保存后 dump 显示
  TextInput 文本 = 该 URL、应用状态行 `已保存: http://10.0.2.2:8765/`
  （`artifacts/phase-url-saved.json` + `.jpeg`）。
- **五 pane 正向**（对 task-owned mock `aios-mock-android-smoke`）：每
  pane 均渲染 `服务地址: http://10.0.2.2:8765/`、无 pane 卡在 loading：
  - **Home**：`status: ok service: aios-mock-android-smoke`、本地模式
    （认证未开启）、privacy model/voice/search 全 `local`、store audio
    `关`、send context `关`、version `0.12.5-mock`、ops `postgresql` /
    papers `34` / search queries `456` / audit total `1024` / worker
    `是`、审计 `#1001 paper.publish` + `#1002 worker.tick`。
  - **Study**：三篇论文标题精确匹配（`Attention Is All You Need` 2017、
    `BERT: Pre-training of Deep Bidirectional Transformers` 2019、
    `ImageNet Classification with Deep Convolutional Neural Networks`
    2012）；paper-001 `来源: NeurIPS` / `学科: Machine Learning` /
    `难度: medium` / `时长: 45 分钟`。
  - **Search**：`local-corpus` `已启用`、`cloud-web` `未启用`、不可用
    原因如实展示。
  - **Voice**：模式 `hybrid`；ASR `Provider: fake` 无回退徽章；TTS
    `Requested: cloud-openai-tts` → `Provider: tone` + `已回退`；
    `存储音频 (privacy_store_audio): 关`、`发送上下文到云端
    (privacy_send_context_to_cloud): 开`；只读边界文本可见。
  - **Governance**：version `0.12.5-mock`、git `m1205mockgit`、Alembic
    current/head `m1205mockrev`、运维字段、审计 `#1001`/`#1002`；任何
    pane 均未渲染 `mock-before-*`/`mock-after-*` 标记串。
- **受控故障/恢复**：mock 停止 → Study 刷新 → 可见错误态
  `网络请求失败: 2300028 Operation timeout` + 真实「重试」控件，
  outage 期间三篇论文标题全部缺席；新 task-owned mock 启动（launcher
  `61400` → child `86020`，health 200）；真实点击「重试」（bounds
  center `(660,550)`）→ ≤ 10 s 三篇论文 + 年份 chips 全部恢复、错误
  文本消失；全程无 app 重启。
- **PID 稳定性**：**30005** 在 URL 阶段（保存前后）、五 pane 逐 pane、
  以及故障/恢复全程（outage 前 / 停止后 / 错误中 / 重试前 / 恢复后 /
  最终）均不变；清理阶段卸载后进程消失为卸载的预期结果，非失败。
- **故障扫描**：`hdc shell hilog -x` dump（883,213 bytes）中
  `cppcrash` / `jscrash` / `appfreeze` / `FaultLog` 匹配均 **0**；
  `ls -R /data/log/faultlog/`：`faultlogger/` 与 `freeze_ext/` 列表为
  空；子目录 `filter`/`freeze`/`temp` 对 shell 用户返回 Permission
  denied（如实记录边界，hilog 零匹配为补偿证据，未发明零值）。
- **卸载证明**：仅卸载 `com.ailearningos.app` → "uninstall bundle
  successfully"；`bm dump -n com.ailearningos.app` → "failed to get
  information and the parameters may be wrong."（权威不存在响应）；
  `bm dump -a` 全 bundle 列表 0 处 `ailearningos`；卸载后 `ps -ef` 无
  该包进程。
- **task-owned mock 清理**：`taskkill /PID 61400 /T /F` 终止 `88404`
  （conhost）/`86020`（server）/`61400`（launcher）整树；复核三 PID
  均 not found、`:8765` 监听 **0**；未触碰其他 Python、代理、Docker、
  模拟器或任何服务进程。
- **环境保留**：模拟器保持运行且连接（报告时复查 `hdc list targets`
  仍仅 `127.0.0.1:5555`，`bootevent.boot.completed=true`）；未停止、
  未重置；host 侧 8000 与生产服务全程未触碰。
- **收尾 tracked-clean**：验证回合报告时 `git status --porcelain` 为空
  （仅 untracked `.verify/` 证据；无 tracked 生产文件被改、无重建）。

## 证据文件与清单锚点

- 源证据（gitignored，不入库）：验证 worktree
  `m14-50-harmony-current-smoke` 的 `.verify/m14-50-harmony-emulator-stage-c/`
  —— **110 文件 / 5,202,795 bytes**：根级 4 份报告（REPORT +
  三份 PHASE report）、时间线/运行日志/辅助脚本、`artifacts/` 全部
  逐阶段产物（dumpLayout JSON + 截屏 jpeg + 文本抽取 + URL/正向/恢复
  阶段见证 + 清理阶段证明）。
- 原始截图/转储/日志均不入 git；以下关键文件 SHA-256 为完整性锚点
  （本回合从源目录独立重derive）：

```
85b05dbed0e5abd0d77cfba1bce939ec580ed16ea163c1f3f774ada7dd8274cc     10392  REPORT.md
988a7e09139e788c3988fde15e95dae6aeb0d469d1482c131177a2ef6e7d9ede      4105  PHASE-URL-REPORT.md
d0111911dd1cf0424a2cd8f21bb4a50b516bfeafee3b276f9a352e35620a1e7e      9282  PHASE-POSITIVE-REPORT.md
2994e2f3c00589fc1281dbf5049688bcfcca941a6c04db4847cf5b999b6a7039      6694  PHASE-RECOVERY-REPORT.md
74c5d6176203a0a758efacfdf810e4739678e4ff0b11dd3467ce607a44363ac2     37214  timestamps.txt
f607d02215185038e4bbce060e1e12af4a21f0ed319f99f2dbe980ef9668cb3f      4177  stage-c-log.txt
9fff847b9bf473b260e863ece7da1b705c3a612db14e7465d246f138b5fc62a3        93  hap-hash.txt
777c04118f1da93108c89a0fc9999691f8c1e62c06d7bb368b6feb77b38d4d79      2262  artifacts/ARTIFACTS-MANIFEST.txt
117192011c6274c6f9352cb229a341acb6b40b700c7af12ec2094718e1fb2eaa    883213  artifacts/cleanup-hilog-dump.txt
ab0fbf3ea995b78b1c5bbf480b3c505c3ea107c03bd589bd3a24e32f816b8f99       597  artifacts/cleanup-fault-scan.txt
d37f4326a831295b1f31e330a4ca4ca9707f58ad8bbb60a90cab16d6455a3520     13965  artifacts/cleanup-uninstall.txt
b62c61d1d7da3eeb5ac7c1f73c7ba5e44906e78c80943a510176d9f8aa71cc3b       168  artifacts/cleanup-bm-dump.txt
449923f90fcec72fa705d92bbd5a027e04bee776ac270831dd2907b0c1503eac       394  artifacts/cleanup-mock-verify.txt
f8aa8cdec7bb37e0e8bc13d5d1a5252e351ef41f106fe2c8a0b051b49a289cb7     59547  artifacts/phase-url-saved.json
ddf9181cae94aadf8e059b2e98afe84fe1127707e78fe745fa242528033f9bcb     79441  artifacts/phase-recovery-recovered.json
```

- 确定性聚合清单：源目录 110 文件按路径排序的 `"<sha256>  %8d  <path>\n"`
  行串接取 SHA-256 =
  `ec60539a5ee18b08ed3931046f8f3e38add2faae6f597e722873058cff69b9aa`
  （可复现复核命令形态：`find . -type f | sort | while read f; do printf
  '%s  %8d  %s\n' "$(sha256sum "$f"|cut -d" " -f1)" "$(wc -c<"$f")" "$f";
  done | sha256sum`）；验证回合自产的机器可读清单
  `artifacts/ARTIFACTS-MANIFEST.txt`（2,262 bytes）亦在锚定表中。
- 本 README 为唯一入库证据文件；以上哈希 + 计数 + 聚合哈希即完整
  性锚点，完整 110 条逐文件清单可按上述命令形态从源目录重derive。

## 边界（不声称）

- **不声称**真实后端验证：全部数据来自 task-owned mock
  `tools/harmony_mock/server.py`（`aios-mock-android-smoke` v0.12.5-mock）；
  无生产 API、无 PostgreSQL、无真实模型 provider 被执行。
- **不声称**签名 / AGC / 发布就绪：被测 HAP 为未签名产物，走本地模拟器
  debug/unsigned 安装路径；无签名、无 AGC（AppGallery Connect）profile、
  无 release 分发就绪——「未签名安装成功」仅证明该模拟器目标接受该
  HAP，不外推真机或发布通道。
- **不声称**真实语音 provider：ASR/TTS 仅对 mock/fake provider
  （`fake`、`tone`）验证，云端路径由 mock 刻意禁用。
- **不声称**生产就绪：单次多阶段验证 ≠ 长期稳定 / 浸泡 / 跨设备；真实
  provider smoke、release-readiness / cutover 审批、AGC 签名发布链等
  仍开放。
- 全局判定不变：**`production_ready=false`**。
- 本回填回合零设备运行、零生产触碰、零代码改动、不 push、不建 PR。
