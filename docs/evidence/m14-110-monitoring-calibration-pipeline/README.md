# M14-110：监控阈值标定管道集成（实现/测试/文档切片）

- 切片：分支 `ops/m14-110-monitoring-calibration-pipeline`（独立 worktree
  `m14-110-monitoring-calibration-pipeline`，基于 main
  `abdde29c700dd15ea5ad15a703c2009e3962d579`（PR #197 merge = M14-109
  合入，精确基点）），单次 local commit（不推送、不建 PR）。
- 目标：把 M14-109 阈值标定/评估工具集成为既有监控管道
  （`tools/ops/monitoring_pipeline.py`）的**显式、可测试第四步**——
  序列升级为 monitor → history → insights → calibration。
- 零生产触碰（本切片全程）：**零真实管道 execute、零调度注册/改动**
  （`monitoring_pipeline_task.py` / VBS / 计划任务零变更）、零网络、
  零生产容器/DB/MinIO/secrets 接触。全部验证基于注入 fake + 真实临时
  目录 + 真实子进程 CLI 冒烟（合成 fixture 输入）；canonical 仓库与真实
  `.verify` 目录零触碰（supervisor 的 standalone 冒烟只读输入 SHA-256
  前后不变）。

## 1. 管道集成契约（tools/ops/monitoring_pipeline.py）

1. **序列语义**：calibration 仅在 monitor exit 0 ∧ history ok ∧
   insights ok 后运行；任一前置失败/跳过 → calibration
   `skipped` + 固定词汇原因（`insights-status-<status>`，与既有
   `<prev>-status-<status>` 风格同款）；前置步骤事实绝不遮蔽；calibration
   失败不改变前三步事实。
2. **固定命令白名单第四形态**：`<python>
   monitoring_threshold_calibration.py --format json`——**仅形态旗标**
   `--format json`（工具既有输出选项，JSON 产物契约所必需），恒不带
   `--history`/`--samples`/阈值参数（输入面全部经校准工具**既有默认值**
   生效：canonical history.jsonl + 默认窗口 200 + 既有阈值）；管道 CLI
   不暴露任何校准 argv/源/阈值参数（结构性测试锁定）。任何其它 argv
   （含校准源/窗口/阈值注入）在任何执行之前拒绝。
3. **stdout 捕获 + JSON 产物契约校验（唯一 stdout 捕获步）**：其余三步
   stdout 绝不保留；calibration stdout 是被持久化的产物本体，**绝不回显
   控制台**。校验 = 单一 JSON 文档 ∧ 顶层对象 ∧ `schema_version` /
   `tool` 与受支持的校准产物契约**精确匹配**（管道侧常量经回归测试与
   `monitoring_threshold_calibration.CALIBRATION_SCHEMA_VERSION` /
   `TOOL_NAME` 交叉 pin——仅键在场不够，版本不兼容/身份不符同样拒绝）；
   非零退出 / malformed / 契约不匹配 / 写入拒绝均为**可见 calibration
   失败**（类别 `stage-exit-nonzero` / `calibration-output-not-json` /
   `calibration-artifact-write-error`，固定词汇），绝不静默接受。
4. **管道独占持久化**：校准工具自身零文件写入（stdout-only 契约不变，
   history.jsonl 与任何输入工件绝不改动/删除——测试锁定）。管道把
   校验通过的 stdout 经 redact_secrets 终防线 → symlink 拒绝 → 原子写
   （tmp+fsync+os.replace）持久化为管道工件目录内固定名
   `calibration.json`；报告引用带 SHA-256/bytes/note
   （`persisted-from-stage-stdout`），哈希 = 落盘字节。失败/跳过轮绝不
   写、绝不引用旧 calibration.json（与 M14-101 同轮新鲜度语义同款）。
5. **超时预算（supervisor Round 1 边界）**：calibration 1–5s（默认 5s，
   与 insights 同型本地只读 history.jsonl 处理秒级完成）；四步硬顶之和
   540+120+50+5=**715s** < PT12M=720s——**恒留 ≥5s** 给管道自身开销
   （启动、锁、证据报告原子写与调度器余量），不把执行时限用满；三步
   既有硬顶 540+120+50=710s 由既有测试 pin **不变**；默认总和
   480+90+15+5=590s。
6. **报告 schema 保持 v1（add-stage-keep-version）**：与 M14-21 加第三步
   时的先例一致——stages/config 为加键扩展，既有键语义零变化；升级版本
   号反而造成新旧报告硬分叉，无消费者需求驱动。消费面
   `pipeline_incident_review.py` 同步最小扩展：stages 键集加
   calibration（子集校验——旧两步/三步与新四步报告同面可解析）、
   归因循环涵盖第四步、calibration「退出 0 失败」按 stage
   failure_category 原固定词汇引用（`calibration-output-not-json` 等）。
7. **plan 惰性不变**：plan 报告四步全 `planned`、零 Runner 构造、零
   calibration 产物。

## 2. standalone 工具唯一调整（ASCII-safe json）

`monitoring_threshold_calibration.py` 的 json 模式输出改恒 ASCII-safe
（`json.dumps` 默认 `ensure_ascii=True`——非 ASCII 经 `\uXXXX` 转义）：

- **动机（supervisor 冒烟实证的根因）**：真实调度链路（VBS → wscript →
  python → 子进程）在 Windows ACP=cp936 且无 `PYTHONUTF8` 时，子进程
  stdout 以 GBK 编码写含中文（边界注记）的 JSON——管道以 UTF-8 捕获必然
  字节损坏。ASCII-safe 使输出在**任意子进程编码**下字节恒定、捕获零
  损坏。
- **行为不变性**：JSON 文档语义逐键等值（`json.loads` 后完全相同，
  M14-109 既有 57 项测试零改动全过）；summary 人读模式不受影响；零墙钟
  字节可复现性增强为跨编码环境逐字节相同。
- **回归锁定**：新增契约测试 pin json 模式全 ASCII + 语义经 loads 完整
  还原；真实子进程（剥离 PYTHONUTF8）连跑两次 stdout 字节逐位相同、
  UTF-8 解码零损坏。

## 3. 验证（全部基于合成 fixture / 注入 fake）

- 聚焦与家族回归（canonical venv，supervisor 终验口径）：监控家族九套件
  （production_monitor / history / history_query / m14_101_performance /
  insights / pipeline / pipeline_task / incident_review /
  threshold_calibration）全绿——数字以 supervisor 终验记录为准
  （Round 1 验证：聚焦四套件 269 passed、家族扩展 825 passed + 4 failed
  仅因本反馈第 2 条的 stale fake（已修）；本回合终验命令见下）。
- 真实 standalone 冒烟（supervisor 已执行，无 PYTHONUTF8、canonical
  history.jsonl 只读输入）：两轮 exit 0；输入 SHA-256 前后不变
  （`0D684AC6…4F31D`）；输出两轮 SHA-256 相同
  （`FEAFBDE8…494DF`）、9061 字节、**零非 ASCII 字节**。
- `py_compile` 三工具通过；`ruff`（默认规则与 `--select F,E9`）全绿；
  `git diff --check` 干净。
- 本切片**零真实管道 execute**：四步序列的端到端行为全部由注入 fake
  （FakeRunner/FakeClock/RealFs on tmp_path）契约测试锁定；真实调度轮
  （calibration 第四步产出 calibration.json）留待 supervisor 获准窗口。

## 4. 诚实边界

- 第四步接入是**代码/契约口径**——真实计划任务轮次尚未跑过 calibration
  步（M14-22 验收口径是三步时代）；本切片不构成 production readiness
  宣称，不解除 provider-smoke/long-soak/release gates，不授权任何部署。
- 校准产物 `calibration.json` 是标定/评估**证据**，不改变生产阈值——
  任何阈值变更仍走 production_monitor 显式配置 + supervisor 获准窗口。
- `pipeline_incident_review` 的同步为最小消费面扩展，其既有两步/三步
  兼容语义零改动（38 项既有测试零改动全过）。
- `production_ready=false` 不变。
