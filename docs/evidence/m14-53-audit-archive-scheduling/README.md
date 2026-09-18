# M14-53 审计归档调度(调度面 readiness)——开发切片证据

- 分支/worktree：`ops/m14-53-audit-archive-scheduling`，基于
  `main@92c8abcaf7c9270c46a202a02a9da81149cc9e07`（PR #135 merge）。
- 单 local commit，不 push、不建 PR（remote 发布/CI/合并由 Codex/supervisor 负责）。
- 交付物：`tools/ops/audit_archive_task.py`（Windows Task Scheduler readiness
  管理器，纯标准库）、`tools/ops/run_audit_archive_readiness_silent.vbs`
  （隐藏调度 wrapper）、`services/api/tests/test_audit_archive_task.py`
  （聚焦契约测试，FakeSchtasks 注入，零真实 schtasks）。
- 监督修正（Codex Review）四项全部落实：① VBS canonical 工件目录
  **逐级**创建（`.verify` → `.verify\artifacts` → 叶子目录,干净 checkout
  下 CreateFolder 非递归会失败——现有专项契约测试锁定链条形态与专用退出码 5）；
  ② 新测试文件去除 Windows 绝对路径字面量（fixture 相对值,拒绝形态不变）；
  ③ exact-owned 校验结构收紧——Task 根元素（命名空间 + 标签名）必须精确,
  且恰好一个 `Actions/Exec`、恰好一个 `TimeTrigger`、恰好一个
  `Principal`（多余条目可夹带第二动作/另一套调度/另一身份,一律 malformed
  拒绝;专项测试覆盖）;④ 补齐文档并重跑全部验证。
- 真实 supervisor cscript 干净仓库语法探针（BLOCKER 回合）两项修正落实：
  ⑤a VBS 内容/注释**全 ASCII 重写**——原 UTF-8 非 ASCII 注释在 cscript
  默认代码页下编译失败（missing statement,任何预检前即失败）;ASCII + LF
  行尾在该探针下解析安全,新增聚焦契约锁定 `isascii()`。⑤b `artifactsDir`
  由多余一层嵌套 `BuildPath`（运行时报 invalid BuildPath arguments）改为
  **恰好三级 stepwise 变量构建**（repoRoot+`.verify` → +`artifacts` →
  +`m14-53-audit-archive-readiness`,每级恰一次 `BuildPath`）,专项契约
  锁定精确构建形态。

## 调度契约要点

- 任务名 `AIOS-Audit-Archive-Readiness`；URI
  `urn:aios:m14-53:audit-archive-readiness`（归一化 `\<任务名>` 形态条件认可）；
  Description 为唯一精确归属标记（点名 M14-53 与管理文件）。
- 每日 TimeTrigger 重复间隔 `P1D`（无 Duration = 无限期）；`Hidden=true`；
  `InteractiveToken` + `LeastPrivilege`；`IgnoreNew`；`StartWhenAvailable=true`；
  `ExecutionTimeLimit=PT30M`；电池不禁启不停。
- Action `wscript.exe //B //Nologo "<repo>\tools\ops\run_audit_archive_readiness_silent.vbs"`；
  WorkingDirectory = 仓库根。
- XML 生成/导出/安装临时件均为 UTF-16 with BOM 字节（与声明一致），落盘后
  回读原始字节复核；解析前拒绝 DOCTYPE/ENTITY；路径经 XML 转义；
  `/XML` 解码按四字节形态严格判定（LE BOM / BE BOM / UTF-16LE 无 BOM /
  ASCII-UTF-8 prolog），之外按 unknown fail-closed。
- 状态五态 `installed/missing/foreign/malformed/unknown`；绝不覆盖同名任务；
  uninstall 仅删 exact-owned（foreign/missing/malformed/unknown 零删除）；
  install/uninstall 各需 `--confirm "EXECUTE AUDIT ARCHIVE READINESS SCHEDULER CHANGE"`
  一字不差,否则零 schtasks 调用。
- VBS wrapper：仓库根自脚本位置推导（无盘符硬编码）；隐藏窗口等待并透传
  退出码；恒调用 `<repo>\.venv\Scripts\python.exe` + readiness CLI +
  canonical gitignored state/policy/output
  （`.verify\artifacts\m14-53-audit-archive-readiness\`）；不传 `--now`;
  预检专用退出码 2/3/4/5（venv python / readiness CLI / repo 根 / 工件目录链
  创建失败）。

## 验证（canonical venv 执行;全部通过）

```text
.venv\Scripts\python.exe -m pytest services/api/tests/test_audit_archive_task.py -q
  → 53 passed（含监督修正新增:结构收紧 2 项 + VBS 目录链 1 项
    + 真实 cscript 探针修正后 ASCII 契约 1 项）
.venv\Scripts\python.exe -m pytest services/api/tests/test_audit_archive_scheduler.py \
    services/api/tests/test_audit_archive_readiness.py -q
  → 78 passed（零回归）
.venv\Scripts\python.exe -m ruff check services/api tools/ops/audit_archive_task.py
  → All checks passed!
.venv\Scripts\python.exe -m py_compile tools/ops/audit_archive_task.py \
    tools/ops/audit_archive_readiness.py
  → 通过（零输出）
git diff --check → 干净
```

监督者独立复跑记录（修正前基线）：focused 49 passed、regression 78 passed、
ruff 与 py_compile 通过——本回合在其上落实四项修正后重跑为上述最终数字。

## 诚实边界

- 本切片只交付**调度面 readiness 管理**（plan/generate/status/install/uninstall
  的工具与契约），不实现、不执行审计归档本身。
- 开发全程**零真实 schtasks 读/写**——所有调度器交互经注入 FakeSchtasks；
  未运行任何真实 readiness、归档、WORM/离线/S3/provider/Docker/生产操作；
  未安装/启动/停止任何进程或服务。
- 实际注册计划任务为 supervisor-only（获准窗口 + 提升令牌）;本开发回合
  零安装、零卸载、零注册。
- 无 secret/绝对本地路径/原始子进程输出进入提交的代码或文档
  （generate/install 工件与 canonical 运行工件均在 gitignored `.verify/` 下,
  不入库）。
- `production_ready=false` 不变。
