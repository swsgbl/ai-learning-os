# M14-118：post-cutover evidence watch 只读工具与契约测试

## 1. 结论与边界

M14-118 把 M14-117 生产切换后的 canonical 证据完整性检查从人工命令拼装
固化为单一 fail-closed 只读仓库工具 `tools/ops/post_cutover_watch.py`
+ 聚焦契约测试 `services/api/tests/test_post_cutover_watch.py`。目的：
防止后续证据缺失、路径逃逸、哈希漂移或索引篡改被误认为仍可验收/回滚。

必须保留的诚实语义：

- **本切片未执行生产巡检**：工具校验的是 gitignored canonical 证据
  **文件的完整性**（清单 + 哈希），不是生产运行时的健康巡检。未访问
  任何生产端口、容器、DB、MinIO 或语音服务。
- **本切片未验证回滚**：`--execute` 通过（exit 1，status=verified）仅
  表示 **evidence inventory verified**——25 个证据文件与 SHA256SUMS
  索引一致；不表示生产健康、不表示 provider/browser/monitor 窗口外
  仍有效、更不表示回滚路径已演练。
- **本切片未解除 release-approval/human-only**：
  `release_ready=false` / `production_ready=false` 在工具报告与仓库
  契约中恒不变；release-approval 仍是 human-only 门，本工具永不代拟、
  不构成任何 release blocker 的解除。
- 本切片零生产接触、零网络、零子进程、零环境变量读取；未停止或
  重启任何进程；不 push、不合并。

## 2. 工具设计（tools/ops/post_cutover_watch.py）

与 `long_soak_release_window.py` 同款纪律：单文件、纯标准库、一切
I/O 经 `monitoring_history.Store` 注入复用、固定词汇 fail-closed、
报告原子落盘（tmp + fsync + os.replace）到全新目录。

- **双模式**：默认 plan 零副作用（零读取、零写入，stdout 不回显任何
  绝对路径/secret——指向不存在的路径同样 exit 0 且零触碰）；显式
  `--execute` 才读取证据。
- **输入**：`--evidence-root`（默认 canonical gitignored M14-117
  cutover 证据目录内 canonical 子目录）与 `--sha256sums`（默认该目录
  内 SHA256SUMS）；`--output-dir` 必须不存在。
- **SHA256SUMS 严格解析**：仅 UTF-8 文本；每行恰为「64 位小写 hex +
  两空格 + 相对 POSIX 路径」；允许恰一个行尾 `\r`（实测 canonical
  索引即 Windows PowerShell 生成的 CRLF 文本——本切片据此把行尾 CR
  定为容忍项，行中间 CR/控制字符仍拒绝）。空索引（index-empty）、
  坏 hex/单空格分隔/空行（index-line-format）、`..` 穿越/绝对路径/
  反斜杠/盘符/`//` 空段/首尾空白（index-path-escape）、同路径重复
  （index-duplicate-path）、自引用（index-self-reference）一律拒绝。
- **9 项分组/关键文件集合契约**（准确来源：M14-117 证据 README §10 +
  canonical SHA256SUMS，2026-09-24 采集）：7 个分组（provider-smoke 8 /
  monitor 4 / browser 5 / rc-smoke 2 / cutover 3 / endpoints 2 /
  recovery 1）**具名文件集合**精确匹配 + key-files（README §10 关键
  SHA256 表的 8 个文件）+ index-integrity（总条目恰 25 且索引集合
  == 7 分组并集）。
- **文件级校验**：逐条目拒绝 symlink（目标自身与现存祖先组件）、
  缺失、读取失败；哈希复核登记 expected/actual 双指纹。
- **报告**：JSON+MD 双文件；仅相对路径/字节数/SHA-256/固定词汇状态
  与原因——绝无文件内容、env 值、token、password、完整 DB URL、
  容器日志正文。零墙钟：报告不含时间戳字段，同输入两次运行输出
  逐字节相同。
- **退出码**：0 plan / 1 verified（仅证据清单完整）/ 2 可判定失败
  （诚实 failed 报告落盘）/ 3 结构性拒绝（零输出）。

## 3. 真实 canonical 一次端到端校验（只读冒烟）

开发过程中用本工具对 M14-117 worktree 的真实 canonical 证据树执行了
一次 `--execute`（输入只读，报告写本机系统临时目录，不写 canonical、
不写仓库）：

- 结果：exit 1，`status=verified`，25 索引条目，9 项契约检查全 pass，
  25 文件哈希全部复核一致——M14-117 证据在巡检时点完好，无缺失、
  无哈希漂移、无索引篡改。
- 该结果只对本次读取的文件字节成立，是时点事实，不承诺任何窗口外
  状态，也不进入本切片的 canonical 证据归档（临时输出已删除）。
- 契约测试与文档中的全部其他运行均使用合成 fixture（tmp 目录内
  25 文件 + 真实哈希索引），不依赖真实 canonical。

## 4. 验证

- 聚焦契约测试：`services/api/tests/test_post_cutover_watch.py`
  43 项全过（结构契约含源码 token 扫描 + AST import 白名单/禁动态
  执行/禁直接 open + socket/subprocess 双阻断端到端；plan 零副作用；
  happy path；缺文件/哈希漂移/路径逃逸×10/重复行/自引用/空与畸形
  索引×6/CRLF 容忍与行中 CR 拒绝/契约缺条目/多余条目/symlink 证据
  文件/输出目录已存在/索引 symlink/根缺失/非 UTF-8；报告脱敏（标记
  token 不进 JSON/MD/stdout）；stdout 不回显路径；零墙钟确定性）。
- ruff（默认 + `--select F,E9`）对新增工具与测试全绿。
- `py_compile` 两文件通过；`git diff --check` 干净。
- 新增文件秘密模式扫描 0 命中（无 key/token/password/secret/完整
  DB URL 模式）。
- 邻域回归：monitoring_history/long_soak_release_window 等共享
  `monitoring_history` 依赖的套件复跑通过（见交付报告）。

## 5. 入库形态

- 新增：`tools/ops/post_cutover_watch.py`、
  `services/api/tests/test_post_cutover_watch.py`、本 README。
- 更新：`tools/ops/README.md`（标题 + 总述 + 专节）、
  `docs/PROJECT_STATUS.md`（顶部任务，M14-117 移次席）、
  `docs/ROADMAP.md`（M14-118 状态更新）、`docs/CHANGELOG.md`
  （M14-118 条目）。
- 单个 local commit，提交信息以 M14-118 开头；不 push、不合并、
  不修改生产。
