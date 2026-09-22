# M14-101 监控历史聚合性能修复 + 产物同轮新鲜度（monitoring-history performance & artifact-freshness repair）

- 分支：`ops/m14-101-monitoring-history-performance`（独立 worktree
  `m14-101-monitoring-history-performance`，基于 main
  `a583f918fba4ea6223b08afc275d909fefef152b`（PR #186 merge = M14-98
  合入，精确基点），单 local commit，不 push、不开 PR）。
- 范围：`tools/ops/monitoring_history.py`（算法浪费消除）、
  `tools/ops/monitoring_pipeline.py`（固定名产物同轮新鲜度）、新增
  确定性回归/性能契约测试、三台账同步。**零超时数值变更**（history
  默认 90s 保持——修复针对根因而非放宽上限）。
- 诚实边界（先行声明）：**真实 Windows 计划任务
  `AIOS-Monitoring-Pipeline` 在本切片中未被运行/触碰**；零容器/零
  DB/零 MinIO/零语音/零 secrets/零代理生命周期变更；零生产证据历史
  （`.verify` canonical 工件目录零接触）；全部验证在合成数据 + 注入
  Fake + 临时目录完成。

## 1. 代码级诊断（supervisor 观测 → 根因）

### D1 — history 聚合的逐文件祖先检查重复（90s 超时主因）

supervisor 事实：计划任务 `AIOS-Monitoring-Pipeline` LastTaskResult=1；
monitor 步 ok，history 聚合 ~90.202s 超时；源目录 ~946 份 monitor JSON
（~15.8 MB）。

根因在 `monitoring_history.discover_and_classify`（基线实现）：对**每个
候选文件**调用 `reject_symlinked_path(store, source_dir / name)`，后者对
path 自身 + **全部祖先链**逐个 `exists()` + `is_symlink()`。真实工件目录
深度 d≈8 时每文件 ≈17 次文件系统 stat 调用，其中 ≈16 次对所有候选**完全
相同**（候选文件仅最后一级文件名不同，而文件名不是任何其他候选的祖先）。
946 文件 → **~17,000 次 stat 代理调用，其中 ~89% 纯冗余**；计划任务唤醒
后 OS 文件缓存被换出（冷缓存，每次 stat 毫秒级）时，冗余部分即可达
~90s——与观测的 90.202s 超时定量吻合（见 §4 基线实测）。

次要重复：`load_sample` 对每文件再做 1 次自身 symlink 检查（防御深度，
非热点但同属可去重路径）。

### D2 — pipeline 固定名产物无 per-run 基线（freshness 缺陷）

`monitoring_pipeline.discover_stage_artifacts`（基线实现）对 history /
insights 步只检查两个固定名（`history.jsonl` / `history-summary.md`、
`insights.json` / `insights-summary.md`）是否出现在目录列表中——**无任何
per-run 基线**。history 步超时/失败被杀时，上一轮（任何历史轮）留下的
固定名文件仍被列为该步 `artifacts`（带 SHA-256），报告读者无法证明该
文件是**失败的那一轮**写出的——supervisor 观测到的正是这个歧义：超时
报告引用了旧的固定名 history 工件。monitor 步已有"步骤前后目录差集"
基线（新出现的 `monitor-*.json` 必为本轮写入），语义正确、保持不变。

### D3 — 超时边界（不动的部分）

history 步默认 90s 是 M14-79 由 45s 上调的结果（当时观测 45–47s 刚过界
即杀）；三步硬顶之和 710s < PT12M=720s 执行时限的预算链测试锁定。本切片
**不调任何超时常量**——D1 修复后 90s 预算恢复到远超充裕（合成 1200 文件
全程 <0.2s 热缓存；冷缓存推算见 §4），无需也不应放宽。

## 2. 修复设计

### 2.1 `monitoring_history.py` — run 内路径安全缓存（`PathSafetyCache`）

`run_history` 创建单一 `PathSafetyCache` 贯穿发现与写出（mkdir 前检查）：
「已确认存在且非 symlink」的祖先目录记入缓存；后续 path 的直接父目录已
缓存 ⇒ 其全部现存祖先已随之验证（缓存写入时走的是整条链）——直接短路，
零重复 stat。

**fail-closed 语义不变**（关键设计约束）：

- 缓存只记录**正向结论**（存在且非 symlink）；不存在/未验证的路径**永不
  缓存**，下次仍走完整 `exists`+`is_symlink` 检查。
- 源目录在单次 run 内为只读快照（pipeline.lock 排除管道自身并发 +
  单次 `list_dir` 快照）——与基线实现共享同一 TOCTOU 假设，缓存不扩大
  该窗口（基线实现逐文件重查同样无法感知 stat 之间的文件系统变化）。
- `write_outputs` 的 **mkdir 后复查恒走无缓存**的 `reject_symlinked_path`
  （TOCTOU 窗口防御的复查必须真实再 stat，mkdir 前的缓存结论不可复用）。
- 拒绝词汇（`symlink-target` / `symlink-in-path` / `symlink-source`）与
  检查层次（discover 层 + `load_sample` 防御层）逐项保留，既有 symlink
  契约测试全数通过。

每文件必经 I/O 收敛到下界：1 次内容 `read_bytes` + 2 次自身 symlink 检查
（discover 层 + load_sample 层）；祖先检查摊还 O(1)。`list_dir` 保持单次
有序扫描（无重复 walk）。

### 2.2 `monitoring_pipeline.py` — 固定名产物同轮新鲜度（per-run 指纹基线）

history / insights 步在**步骤执行前**对每固定名采集 `(size, mtime_ns)`
指纹基线（`fixed_name_fingerprints`，经 `Fs` 注入点新增
`stat_fingerprint`）；步骤执行后按基线判定：

| 步骤后状态 | 判定 | 报告条目 |
|---|---|---|
| 基线不存在 → 存在 | 本轮新建 | SHA-256 + bytes + `created-this-run` |
| 指纹变化 | 本轮重写（原子替换必然推进 mtime） | SHA-256 + bytes |
| 指纹未变 | **非本轮写入（旧文件）** | `sha256: null` + `stale-preexisting-not-cited` |
| 基线在场 → 步骤后消失 | 本轮被移除 | `removed-this-run` |
| 步骤后为 symlink | 拒绝读取/哈希 | `symlink-not-hashed` |
| 基线不可读（stat OSError） | 同轮产出不可证 | 全部零哈希引用 + `baseline-unreadable-not-cited` |

要点：

- **mtime 指纹优于内容哈希**：history 输出零墙钟、逐字节可复现——若本轮
  重写产出与上轮**完全相同的字节**（源无变化时合法发生），内容对比无法
  区分「重写了相同内容」与「没写」，会把真实本轮产物误判为 stale；
  `(size, mtime_ns)` 指纹可以（`write_atomic` 的 `os.replace` 必然推进
  mtime，NTFS 精度 100ns）。
- **超时中途半写**（被杀前已原子落盘一个文件）：刷新者带哈希引用、未刷
  新者记 stale——如实区分，与"固定名不可全有或全无"的真实执行面一致
  （每个文件独立 tmp+fsync+replace 原子落盘）。
- 固定词汇 note（报告 schema 稳定性）：`stale-preexisting-not-cited` /
  `created-this-run` / `removed-this-run` / `symlink-not-hashed` /
  `baseline-unreadable-not-cited` / `unreadable-not-hashed`；
  `PIPELINE_BOUNDARIES` 新增同轮 provenance 边界（读者可见）。
- monitor 步差集基线语义不变；超时/失败步的退出码与类别事实照常如实
  入档（不遮蔽），仅产物引用收紧。

## 3. 确定性回归/性能契约测试

新增 `services/api/tests/test_m14_101_monitoring_history_performance.py`
（16 测试）：零子进程（pipeline 侧 FakeRunner 注入）、零网络、零 env
读取/变更、零墙钟（不 sleep、不依赖时间推进；规模乱序用固定种子、时间戳
为合成常量）。

- **合成规模（真实临时文件面，1000+ 文件）**：1200 文件端到端全部入档 +
  严格升序 + 摘要计数；1200 合法 + 1 malformed 混入 → fail-closed 输出
  零写入；1000 唯一 + 200 同内容副本 → 去重 200 显式计数；同
  (project, collected_at) 不同哈希 → conflicting-duplicate 拒绝。
- **有界操作计数（计数 FakeStore，纯内存）**：N=300 与 N=900 两组
  `exists` 调用次数**完全相等**（祖先检查不随候选数重复——与 N 无关）
  且 ≤32 上界；`PathSafetyCache.ancestor_checks` 两组相等；每文件
  `is_symlink` 增量 ≤2 + 常数。
- **fail-closed symlink 语义保持**：祖先 symlink → `symlink-in-path`；
  源目录 symlink → `symlink-target`；候选文件 symlink 双层
  （discover 层 `symlink-target` + `load_sample` 直调层 `symlink-source`）。
- **同轮产物 provenance（FakeRunner + 真实临时目录）**：超时 + 预存旧
  产物 → 双 entry `stale-preexisting-not-cited` 零哈希（旧文件本体不改
  动）；ok 轮重写 → SHA-256 引用；超时半写 → 刷新/未刷新如实分列；失败
  轮（rc=2）+ 预存 → 零引用；insights 同款；步中移除 → `removed-this-run`；
  基线不可读 → 全部 `baseline-unreadable-not-cited`；symlink 产物 →
  不读不哈希。
- **结构 pin**：`PIPELINE_BOUNDARIES` 声明同轮 provenance 边界；note 固定
  词汇表。

## 4. 合成规模验证与复杂度/操作计数证据

全部在本机（Windows 11，canonical `.venv` CPython 3.11.15）合成数据上
实测；热缓存计时仅作数量级参考（冷缓存差异由操作计数推算，见下）。

| 场景（N 文件） | 基线（a583f918 实现） | 修复后 | 变化 |
|---|---|---|---|
| N=946（≈生产观测规模）stat 代理调用（exists+is_symlink 计数） | **17,044**（exists 7,576 + is_symlink 9,468） | ≈**1,900**（ancestor_checks=8 + 每文件自身 ~2） | **−89%** |
| N=946 发现+分类耗时（热缓存） | 0.618s | 0.139s | 4.4× |
| N=1200 全程（发现→校验→去重→留存→摘要→写出） | —（未测） | 0.172s | — |
| 祖先检查总次数（`PathSafetyCache.ancestor_checks`） | ~946×链深（每文件重复） | **8**（N=946 与 N=1200 相同，与 N 无关） | O(N·d)→O(d) |

冷缓存推算：基线冗余 ~15,000 次 stat × 毫秒级冷 stat ≈ 90s 量级——与
生产观测的 **90.202s 超时定量吻合**；修复后冷缓存 stat 总量 ~1,900 次
（−89%），同口径推算 ~10s 量级以内（含每文件 read 的真实 I/O 下界），
90s 预算恢复充裕。**注意：这是合成数据推算，非生产计划任务实测**（真实
计划任务在本切片中未被运行，见 §6）。

复杂度：发现阶段路径安全检查由 O(N·d)（N=候选数，d=祖先链深度）降为
O(N + d)（每文件 O(1) 自身检查 + 一次链验证）；排序/去重/留存保持原有
O(N log N)/O(N)（无 FS walk）。

## 5. 验证矩阵（本切片实际执行）

- 新增契约测试：`test_m14_101_monitoring_history_performance.py`
  **16 passed**（2.5–2.9s）。
- 聚焦回归（受影响模块全量）：
  `test_monitoring_history.py` + `test_monitoring_pipeline.py` +
  `test_monitoring_insights.py` + `test_monitoring_pipeline_task.py` +
  `test_m14_101_...py` + `test_production_monitor.py` **652 passed**
  （10.15s；含既有 symlink fail-closed、产物发现、超时预算链
  710s<720s、M14-79 的 90s 默认 pin 等全部契约）。
- `ruff check`（三改动/新增文件）全绿；`py_compile` 通过；
  `git diff --check` 干净。

## 6. 诚实边界

- **真实 Windows 计划任务 `AIOS-Monitoring-Pipeline` 未被运行、查询或
  修改**（本切片修复代码与测试，不触发真实调度面）；`LastTaskResult=1`
  的生产现场未复现——冷缓存推算（§4）是合成证据，量级论证而非生产实测。
- 零生产触碰：无容器/计划任务/DB/MinIO/语音/secrets/日志/代理生命周期
  变更；canonical `.verify/artifacts/`（真实证据历史）零读取零写入；全部
  测试 I/O 在 pytest 临时目录。
- 修复后工具在下一次真实计划任务窗口的表现由后续运维回合观测；超时
  常量（90s 默认）未动，若冷缓存下仍超限则按事实再评估（而非预设放宽）。
- `release_ready` / `production_ready` 宣称不在本切片范围；单次管道成功
  ≠ production readiness（既有边界不变）。
- 本 README 为本切片唯一入库证据文件；性能证据为合成数据实测+操作计数
  断言（测试锁定），无生产工件入档。

## 7. 后续（留 supervisor 决策）

- 下一次获准窗口让真实计划任务自然运行一轮，观测 history 步时长是否
  回到秒级（M14-79 观测的正常轮 0.3–7.2s）。
- 若需要更强的同轮 provenance（跨进程可验证），可考虑 history 写侧附
  run ID；本切片的指纹基线方案零 schema 变更、零下游破坏，已消除
  「超时报告引用旧产物」歧义。
