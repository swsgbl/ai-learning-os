# M14-50 Harmony 模拟器 Stage B 证据回填（docs-only）

## 结论

supervisor 已于 2026-09-18（11:54:05–12:01:53 GMT+8）在验证 worktree
`m14-50-harmony-current-smoke`（detached `main@b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`，
即 **PR #129 merge**，起始 tracked-clean）完成 Harmony **Stage B 模拟器
交互验证**，判定 **PASS**：预构建未签名 HAP 直接 `hdc install` 成功（无任何
签名/绕行手段）、应用启动、六个 tab 全部真实点击且布局转储互异、治理页
「重试」触发真实网络请求、崩溃指标全 0、卸载后 bundle 不存在、模拟器保持
运行。

本回合（worktree `m14-50-harmony-stage-b-backfill`，分支
`docs/m14-50-harmony-stage-b-backfill`，基于 `main@d613667a500d22bef912229f40c7704223c35d2e`）
为 docs-only 回填：**零设备运行、零生产触碰、零代码改动、不 push、不建
PR**；只读取源 worktree gitignored `.verify/m14-50-harmony-emulator-stage-b/`
证据，独立复核后入库。源 REPORT（10,443 bytes）为权威事实来源，本 README
为唯一入库证据文件。

## 双基线与 docs-only 区间

| 基线 | 角色 |
|------|------|
| `b9e8cc8ec7a5e158f1e99468b60b24a27a648c19`（PR #129 merge） | Stage B **验证基线**（验证 worktree detached 于该 commit，起始 clean） |
| `d613667a500d22bef912229f40c7704223c35d2e`（PR #131 merge） | 本 docs 回填分支基点（`main@d613667`） |

`b9e8cc8..d613667` 共 6 个提交（`d613667` merge PR #131 移动端冒烟回填、
`761f3fd` merge PR #132 归档调度器、`298807a` docs mobile smoke、`f9c2c84`
ops audit archive scheduler、`15580f2` merge PR #130、`849f49f` docs audit
worm offline），diff 只触 `docs/`（CHANGELOG/ROADMAP/PROJECT_STATUS + 两份
evidence README）、`services/api/tests/test_audit_archive_scheduler.py` 与
`tools/ops/audit_archive_scheduler.py`；对 `apps/harmony`、
`tools/harmony_release` 的区间 diff 为**空**——Stage B 验证所用移动端源码
与本回填基线完全一致，无源码漂移。

## 回填前只读复核（本 docs 回填回合，零设备/零生产）

- HAP 独立重哈希（certutil，只读）：
  `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  → SHA-256
  `9d1b9609017f2b10e675c280f2ec04ef003b7b17b3e9591c4d57207e37534acb`、
  **188,984 bytes**——与源 REPORT 及 M14-50 移动端冒烟回填已锚定值一致
  （同一 HAP，Stage A device-smoke 与 Stage B 交互验证共用）。
- 源证据目录全量清单重derive（`sha256sum` + `wc -c`，只读）：**44 文件 /
  4,190,967 bytes**，逐文件哈希见下节；确定性聚合清单 SHA-256 =
  `3ff6f7b5ac951fc4baf9f1a2ec2942afdc71524717fccdd52d2aac52d01a9f09`。
- 关键原始产物抽查全部与 REPORT 一致：install-unsigned.txt（"install
  bundle successfully" + EXIT=0）；uninstall.txt（"uninstall bundle
  successfully"）；postuninstall-bmdump.txt 与 preinstall-bmdump.txt
  **字节相同**（同哈希 `eb38add3…`，均为 "failed to get information"
  错误 → 卸载后不存在 = 安装前不存在）；postlaunch-ps.txt 第 174 行与
  logs/final-ps.txt 第 172 行均含
  `20020069  3198  139 1 11:54:24 … com.ailearningos.app`（PID 全程未变）；
  logs/postuninstall-ps.txt 0 条 ailearningos 匹配；crash-scan-hilog.txt
  中 `cppcrash`/`jscrash`/`appfreeze`/`FaultLog` 计数均 0；
  postinstall-bmdump.txt 含 `com.ailearningos.app:` 条目（bundle 已确认）；
  timestamps.txt 时间线 11:54:05.59（start）→ 12:01:53.32（CLEANUP-VERIFIED）。
- git 区间核验：`git log --oneline b9e8cc8..d613667` 共 6 提交；
  `git diff --stat b9e8cc8 d613667 -- apps/harmony tools/harmony_release`
  输出为空。
- 源 `.verify/` 目录本回合只读未写（`.gitignore:16 .verify/`，两 worktree
  同规则，原始产物不入 git）。

## Stage B 验证事实（源：gitignored `.verify/m14-50-harmony-emulator-stage-b/`）

- **设备目标**：`hdc list targets` 仅 `127.0.0.1:5555`，所有 hdc 调用均带
  `-t 127.0.0.1:5555`；`param get bootevent.boot.completed` → `true`；
  全程未停止/重置模拟器，未触碰任何其他设备。
- **未签名安装成功**：对预构建 Stage A 产物 `entry-default-unsigned.hap`
  执行 `hdc install -r` → "install bundle successfully"，EXIT=0；无构建、
  无签名、无 debug identity、无 AGC 访问。`bm dump -n` 确认 bundle
  `com.ailearningos.app` v1.0.0（label 砚席，module entry，ability
  EntryAbility，installTime 1789703665502）。此前失败轮曾试图绕行未签名
  安装；本轮证明该模拟器目标上**普通安装即成功**，未使用任何绕行。
- **启动与进程稳定性**：11:54:24 `aa start` → "start ability
  successfully"；PID **3198**（uid 20020069）单进程、从启动到卸载前最终
  检查无重启、无新 PID。`aa dump -l` → Mission #43
  `#com.ailearningos.app:entry:EntryAbility` focused。hilog 生命周期链：
  `EntryAbility onCreate` → `onWindowStageCreate` → `onForeground` →
  `WMSFocus isFocused:1` → `NotifyCompleteFirstFrameDrawing id:43` →
  `Initialize: pages/Index`（首页路由确认）。
- **六个 tab 真实点击且转储互异**：逐 tab 以 `uinput -T -c <x> 2664` 注入
  真实触摸（首页110/学习330/搜索550/语音770/设置990/治理1210），每 tab
  采 `uitest dumpLayout` + 截屏。七份 dump（tab0-before + 六 tab）哈希/
  尺寸互异（tab0/tab1 同 55,535 B 但哈希不同；tab2–tab5 尺寸各异；
  tab6 56,849 B）——底栏各 tab 确实切换。首次点击误用 `uinput -T -g`
  （错误子命令，报 "argc:5 wrong number of parameters"，未注入任何触摸），
  立即更正为 `-c`；该误试未保留误导性证据（被更正轮转储覆盖）。
- **治理「重试」触发真实网络请求**：12:00:29 `uinput -T -c 196 930`
  点击治理页「重试」→ hilog 于同毫秒窗记录同屏输入消费
  （`AceInputTracking Consumed id:32` + `InputKeyFlow
  ConsumePointerEventInner … wid:43`）+ PID 3198 的
  `BUSSINESS_ISSUE_HTTP`：`effective_method:"GET"`、`curl_code:7`、
  `os_errno:111`，NETSTACK 层 `errCode:2300007 … method:GET, osErr:111`
  ——一次**全新 GET /api/v1/version 请求**。
  - 日志呈现注意：Harmony netstack CHR 隐私脱敏将 host_name 逐字符隔星
    掩码，明文 `api/v1` 在 hilog 中出现 0 次；line 5329 掩码骨架
    `h*t*:*/*2*.*.*.*:*0*0*/*p*/*1*v*r*i*n` 逐位还原即
    `http://127.0.0.1:8000/api/v1/version`，与点击时间戳（12:00:28.94 标记
    → 12:00:29.040 日志）、PID 3198、GET 方法、curl_code 7 互相印证。
- **预期连接拒绝，非缺陷**：应用默认服务地址 `http://127.0.0.1:8000` 在
  模拟器内解析到模拟器自身（host 侧 8000 属生产 docker 栈，本轮任何 host
  命令从未接触）→ 各 tab 呈只读失败态 `网络请求失败: 2300007 Failed to
  connect to the server` + 「重试」。这是正确的只读失败展示；本轮**未改
  任何 API base URL**。
- **崩溃/故障证据**：hilog 全缓冲扫描 0 行匹配 `cppcrash`/`jscrash`/
  `appfreeze`/`FaultLog`；`/data/log/faultlog/faultlogger/` 为空目录；
  `/data/log/faultlog/freeze/` shell 无权限列出（如实记录边界，hilog
  appfreeze 零匹配为补偿证据）。
- **卸载与收尾**：12:01:46 `hdc uninstall com.ailearningos.app` →
  "uninstall bundle successfully"；卸载后 `bm dump` 错误输出与安装前
  **逐字节相同**（bundle 不存在验证）；卸载后 `ps -ef` 无 ailearningos
  进程；**模拟器保持运行**（未停止/未重置）。

## 证据文件与清单锚点

- 源证据（gitignored，不入库）：
  `D:\AI Learning OS\ai-learning-os-worktrees\m14-50-harmony-current-smoke\.verify\m14-50-harmony-emulator-stage-b\`
  —— **44 文件 / 4,190,967 bytes**：REPORT.md（10,443 B，权威报告）、
  timestamps.txt、install-unsigned.txt、pre/postinstall-bmdump.txt、
  launch.txt、focus-aadump.txt、pre/postlaunch-ps.txt、
  postuninstall-{bmdump,ps}.txt、uninstall.txt、dumps/ 18 条（7 份
  dumpLayout JSON + 文本抽取 + helper 脚本 extract-texts.cjs）、tabs/
  7 张截屏 jpeg（1320x2856）、logs/ 8 条（lifecycle-launch /
  postinteraction-hilog / crash-scan-hilog / faultlog 三条 / final-ps /
  postuninstall-ps）。
- 原始截图/转储/日志均不入 git；以下逐文件 SHA-256 即完整性锚点
  （本回合从源目录独立重derive）：

```
54eeac44ff65ee7abb78938ca77a8b5a3f45f9b274d74ffaed7349a7ed2618c2     10443  REPORT.md
d4897d91421abae1561068c1b9e4f4291cb4d00783bfa6540c025f8c0162c3cd     55535  dumps/tab0-before.json
6e5b9d920213f821caa29d8bb0ceb9710f14f5630a4d552f990a6c468e9a2e73       587  dumps/tab0-before.json.txt
ece12d1d0de8deffca32b9f476113f6b5dfdcfaa6cfa347cd4854d49dfea7bfd      2091  dumps/tab0-texts.json
2a163d5dbb7f4dce8e1eae4d0e2ed11fcf6ed843cf708710a0d942a477bcb934        24  dumps/tab0-texts.json.txt
46116272ea1d87547eb620613ccb24a0b8c328dcf060c98250d9678ec42f1462     55535  dumps/tab1-shouye.json
6e5b9d920213f821caa29d8bb0ceb9710f14f5630a4d552f990a6c468e9a2e73       587  dumps/tab1-shouye.json.txt
426f3045cc2012591fec03751261d0bbcf7cf2d5ba1bc9361c160d4be37df6c6     50802  dumps/tab2-xuexi.json
aaa1e8a08b32059d4adc7ddab7f73d72c9e7b722047de7563b4ca1004c6738c3       602  dumps/tab2-xuexi.json.txt
8a430baf7ae14138ab25d074fb2bea4fbcf19b0484df7148c2ccd2b3f6648986     43439  dumps/tab3-sousuo.json
f3aa8ad40873b32ce88ed0e85e20123d124561c7bf931a4a0a4d11c0a5a92efb       471  dumps/tab3-sousuo.json.txt
a33761b78d9c80e7e9dc5332e5e532abe17c34dc56d6c9c83d953b8f0736add0     44263  dumps/tab4-yuyin.json
75373a0325438d1387895828c1b054c3870bcab065701187dc20f94c0d4286b0       561  dumps/tab4-yuyin.json.txt
d799d9654a8b09e4023bd2962b817837fec9ebfaecafaba8fee599753f28f4b1     43466  dumps/tab5-shezhi.json
49b715c7e2376004170a34e9058fc9a28fae41fefd6379ade51f388f8fb720b0       472  dumps/tab5-shezhi.json.txt
4e7ffdef20f5ea09281fe8ca89dd1f6d2af6549e2a8d6565cf51477fd0d8d36b     56849  dumps/tab6-zhili-after-retry.json
51706455657eeb5ecd9276e50978a741315ecc537ac846bf4965ce5e623b6744     56849  dumps/tab6-zhili.json
0230169c623998aba96e162b23eda02b08d7cae8c96c3001d69d3ca5bd6ae665       615  dumps/tab6-zhili.json.txt
d0799d34b3bb8f6dc6aca5b95c56949fb718c3acfe282fc28853c809a7607e38       672  extract-texts.cjs
e8261790f7a4ad89cb81089a21f4e3af38946b31bb5a444145d20a178ef9b84e       527  focus-aadump.txt
016c0966b68afdf7132412447a37b7678ab75bc9d232eaf137bdfd0083463040       231  install-unsigned.txt
e41c6d10655ec6d2eb1d91c38517be742c2e9ae33c1f01185adcd33246f4cf2c        39  launch.txt
770d2db7af3f432da44670908976e9de77383e92b26be4bd2661df068af1f59b    862739  logs/crash-scan-hilog.txt
3c8fdbb292ac9587c742ffce3f5a5de044800bf94b30c001ed98b448518bf40a       474  logs/faultlog-dir.txt
1fdfbf5a2a226baa548ce25accf47d1ae0a0368f4b973069a9495e023edec3fb       196  logs/faultlogger-freeze-ls.txt
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855         0  logs/faultlogger-ls.txt
850f6a5c076c618da2c94a29aee02e96d25aec11651f5d1d057285317ffbdb58     13521  logs/final-ps.txt
dd2d142edc9f50b33a421b2470f650d2f1cd655ffca01e8c3982dd6a02dcd3f1    857529  logs/lifecycle-launch.txt
2adbe47a3fb2d36e6984ae63b36b69d2120af829a3da7f7cf774d8a3878125a5    857293  logs/postinteraction-hilog.txt
8f60360bb3e82b2cddc97f6cefbff921e8dcf6b9e5523f3fab13c05c068ddddb     13977  logs/postuninstall-ps.txt
ae1690b34bb990ddc9669f542039b48ecdda265638279b3667dcd5ee7f9c2605     15586  postinstall-bmdump.txt
2f28c3c76310e75351a6f5d63dc51a6c42e522f37711b000f853152017826147     13897  postlaunch-ps.txt
eb38add3caf062fd8dfe5a887ec518de761fd8843396d40690624a6f4326f698        67  postuninstall-bmdump.txt
eb38add3caf062fd8dfe5a887ec518de761fd8843396d40690624a6f4326f698        67  preinstall-bmdump.txt
24d6b1ba521b75303345f3e045632b5e6a4290db747ec0a6c87a9b1b47bb3771     13490  preinstall-ps.txt
2fe6bc8cb102d29eb75c397054dbf7dd1a5208cda9a9b3ef010a831001287a2a    214347  tabs/tab0-before.jpeg
87ca42a6e47a79968cb680cb34f5d3c12eeacde8319682b32913857d6d896e53    214239  tabs/tab1-shouye.jpeg
f23c19687e595e2b931f8fb81c326d09157cbcf6862db0da13447397ac98c8cb    148836  tabs/tab2-xuexi.jpeg
8e16341c524f548ea62b0f8526f2bb5ba6045223d8858a69def7f5ec3d2d2913    111152  tabs/tab3-sousuo.jpeg
fc401520a07fb7f730af84d7e0d9be275e20ed31c5ef66620a39e4cd435f9275    125337  tabs/tab4-yuyin.jpeg
ffb30965d2647e7208d7358005c0ad633efb6ece8735671efecfd2e212c00b87    112710  tabs/tab5-shezhi.jpeg
f1ed370e37f83d384436e6a0bd4573028842cecb9b31ba94cf353e166804f535    190303  tabs/tab6-zhili.jpeg
057fb486d24e61ef64ba1ef65b13ab124c7752d26a5355e7ddb551173693378d       467  timestamps.txt
46d51bec34720ac0e4832c226b307e4366adaca15f2be61ac73ad0ec41d9604c        80  uninstall.txt
```

- 确定性聚合清单：对上表按路径排序的 `"<sha256>  %8d  <path>\n"` 行串接
  取 SHA-256 =
  `3ff6f7b5ac951fc4baf9f1a2ec2942afdc71524717fccdd52d2aac52d01a9f09`
  （可复现复核命令形态：`find . -type f | sort | while read f; do printf
  '%s  %8d  %s\n' "$(sha256sum "$f"|cut -d" " -f1)" "$(wc -c<"$f")" "$f";
  done | sha256sum`）。
- 值得注意的哈希事实：`preinstall-bmdump.txt` ≡ `postuninstall-bmdump.txt`
  （同哈希 `eb38add3…`，安装前/卸载后状态逐字节一致）；`tab6-zhili.json`
  与 `tab6-zhili-after-retry.json` 同 56,849 bytes 但哈希不同（重试后布局
  确有变化）；`logs/faultlogger-ls.txt` 为 0 字节（faultlogger 目录为空）。

## 边界（不声称）

- **不声称**任何成功 API 数据渲染：模拟器内 `127.0.0.1:8000` 解析到模拟器
  自身，全程 `2300007` 连接拒绝（预期行为），六 tab 仅验证只读失败态的
  渲染与交互。
- **不声称** tab 栏动画正确性：未做动画验证。
- **不声称**深层流程：考试查询、设置保存/测试连接等仅验证渲染，未执行。
- **不声称** Harmony 签名/AGC/真机：HAP 未签名、无 AGC 材料、无 release
  签名、仅模拟器 loopback 单目标 `127.0.0.1:5555`；「未签名安装成功」
  仅证明该模拟器目标接受该 HAP，不外推真机或发布通道。
- **不声称**生产就绪：单次交互验证 ≠ 长期稳定/浸泡/跨设备；真实 provider
  smoke、release-readiness/cutover 审批、AGC 签名发布链等仍开放。
- 视觉确认口径：本轮 `see_image` 未返回文字描述，逐 tab 视觉依据为
  `uitest dumpLayout` 文本节点 ground truth + `tabs/` 截屏留档（供人工
  复核）；治理页「审计日志」区块在折叠线下、dump 未直接观测到（pane 级
  标题已验证）。
- 全局判定不变：**`production_ready=false`**。
- 本回填回合零设备运行、零生产触碰、零代码改动、不 push、不建 PR。
