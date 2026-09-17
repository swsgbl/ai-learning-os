# M14-43 审计锚点 WORM 归档工具（开发切片证据）

> 后续回填（M14-44）：真实 MinIO Object Lock 归档执行已闭环并 verify
> pass；下文「零真实 WORM 归档执行」描述的是 M14-43 开发时点历史事实，
> 不覆盖 M14-44 结论。详见
> `docs/evidence/m14-44-audit-worm-execution/README.md`。

## 任务性质

- 开发切片（实现 + 契约测试）：交付 `tools/ops/audit_anchor_archive.py` + `services/api/tests/test_audit_anchor_archive.py`，本 Claude 开发回合独占 worktree 单 local commit 不 push
- 分支/worktree `ops/m14-43-audit-worm-archive` 基于 `35c0875`（M14-42 docs 回填 commit，本地 git 可验证）
- **零真实 WORM 归档执行**：全部验证为注入 Fake 的零网络契约测试——未连接任何真实 S3/MinIO、未创建/写入任何桶或对象、未触碰生产/凭据
- 关闭 M14-42 留下的可执行缺口：锚文件「另行归档到 WORM/对象锁/离线介质」此前只有运维口头动作，现在有 fail-closed 工具承载（真实执行仍由 supervisor 在获准窗口进行）

## 工具契约（`tools/ops/audit_anchor_archive.py`，单文件纯标准库）

- 三命令，退出码统一 `0` 成功 / `2` 一切拒绝（fail-closed，绝不静默降级）：
  - `preflight`：本地锚链完整校验（字段集/类型/canonical JSON/`anchor_hash` 重算/sequence 非负且严格递增——与 M14-42 生产端同契约，允许跳号不要求 +1 连续/`anchored_at` 必须可按 ISO-8601 解析/previous 链接/sequence 0 head 必须是 genesis 常量/symlink 源拒绝）+ endpoint 策略（公网必须 HTTPS，loopback/RFC1918 可 HTTP，userinfo 内嵌凭据拒绝）+ 凭据只认环境变量 + bucket 存在/versioning `Enabled`/Object Lock enabled；全程零写操作
  - `archive`：先跑全部 preflight 检查；确认短语一字不差（`EXECUTE AUDIT ANCHOR WORM ARCHIVE`）+ `--retention-mode COMPLIANCE`（不实现可被绕过的 GOVERNANCE）+ tz-aware 严格未来 `--retain-until` 三道参数门（不满足 → 零执行零报告；fractional 秒合法——报告 canonical UTC 表示保留非零微秒，archive→verify 精确往返）；内容寻址 key `audit-anchor/<sha256>/audit-anchor.jsonl`；已存在：字节不符 fail-closed、字节相符核验 retention 事实后绝不覆盖；新对象恰好一次 `put_object`（`application/x-ndjson` + SHA-256 checksum + COMPLIANCE + retain-until + 显式 `if_none_match="*"` 条件创建——经注入协议传递、仅在真实适配器边界映射为 PutObject `IfNoneMatch="*"`：head 判定不存在后被并发抢占时服务端拒绝，fail-closed 绝不覆盖竞态写入），put 后重读字节与元数据，任何漂移 fail-closed
  - `verify`：本地校验 + bucket WORM preflight + 归档报告 sidecar 哈希核验（空/非 UTF-8/畸形 → exit 2 报告 problem 绝不抛异常）+ 报告绑定当前调用事实（跨 bucket/endpoint 或失败归档报告一律拒绝）+ head/get 按报告记录 version 定向 + 逐字节 SHA-256、version ID、COMPLIANCE/retain-until/content-type/size 精确核验
- 安全纪律：S3 凭据只认 `AIOS_AUDIT_ARCHIVE_ACCESS_KEY` / `AIOS_AUDIT_ARCHIVE_SECRET_KEY` 环境变量——绝不接受 CLI 值、绝不记录其值；S3Client/FS/Clock/env/报告名随机后缀生成器全注入（开发回合零网络），boto3 仅在真实执行适配器工厂函数体内懒导入，hex SHA-256 → base64 只在 boto3 边界转换（AWS `ChecksumSHA256`）；报告原子写 gitignored `.verify/artifacts/m14-43-audit-worm-archive/`（JSON + Markdown，archive 另附 `.json.sha256` sidecar；字节模式写盘不受 Windows `\n`→`\r\n` 翻译破坏）；**报告名防碰撞（Round 3 起 `<command>-<stamp>-<随机后缀>`）**——每份报告名带 `secrets.token_hex(16)` CSPRNG 随机后缀（32 位小写 hex chars = 128 bits）：仅靠顺序探测防不了并发（两个进程可在各自探测-写入窗口内同时观察到同一候选名不存在而双双选中、事后互相覆盖证据），随机后缀把同 command 同秒并发撞名概率压到约 2**-128；存在性探测循环仍是 fail-closed 兜底——候选名全部工件后缀（`.json`/`.json.sha256`/`.md`，含残留孤儿）任一已存在即递增 `-2`/`-3` 换名，绝不覆盖既有报告证据。残余假设（如实声明）：探测与写入之间存在非原子窗口、无全局互斥——随机后缀只把窗口内撞名压到密码学随机概率，不承诺为零；endpoint 只记 host，绝不含凭据/完整 endpoint/原始异常；`redact_secrets` 终防线折叠 key=value / Bearer 形态疑似秘密
- **绝不删除、绝不覆盖任何已归档对象**：允许的 S3 操作只有 read/head/put（`head_bucket` / `get_bucket_versioning` / `get_object_lock_configuration` / `head_object` / `get_object` / `put_object`），无 delete/copy/create-bucket/put-bucket-config 任何 API（源码契约测试锁定）

## 开发验证事实（全部零网络）

- TDD：先写聚焦失败测试（RED 实证 39 failures），实现后 GREEN；supervisor Review Round 1 提出的 11 项修正全部落实（含 verify 判定语义、version 定向核验、报告绑定当前调用事实、Windows 字节模式写盘、sidecar fail-closed、base64 边界转换、content-type/size 核验、锚链契约对齐 M14-42 生产端等）
- supervisor Review Round 2 提出的 3 项修正全部落实并各有聚焦回归测试：
  1. `_utc_z` 保留非零微秒（fractional-second `--retain-until` 的 archive 报告 canonical 表示不再截断，verify 复算精确一致——`test_archive_then_verify_with_fractional_retain_until`）；
  2. 报告名防碰撞初版（顺序探测 + `-2`/`-3` 递增、孤儿工件也算碰撞、首份证据链保留仍可 verify——同注入时钟测试；Round 3 指出其单独不足以防并发，已被下方随机后缀机制取代）；
  3. create-only 语义强化（显式 `if_none_match="*"` 经注入协议传递、仅在 boto3 适配器边界映射为 PutObject `IfNoneMatch`；FakeS3 拒绝 key 已存在时的条件创建；竞态 fail-closed 测试证明被拒 put 后竞态写入者字节原样保留——允许的 S3 API 表面保持六类不变）
- supervisor Review Round 3 提出的报告名防碰撞修正已落实：原顺序探测策略（`-2`/`-3` 递增）单独不防并发——两个进程可在各自探测-写入窗口内同时观察到同一候选名不存在而双双选中、事后互相覆盖证据。现每份报告名带 `secrets.token_hex(16)` CSPRNG 随机后缀（`<command>-<stamp>-<随机后缀>`，32 位小写 hex chars = 128 bits，并发撞名约 2**-128；随机后缀生成器经 `main(suffix_gen=…)` 全注入，测试不依赖真随机）；存在性探测循环（含孤儿工件）保留为 fail-closed 兜底。聚焦测试证明：①生成名仍含 command 与 stamp；②同注入时钟重复执行零复用零覆盖（不同随机后缀各自成档，首份证据链仍可 verify）；③后缀生成可注入（注入替身决定名字；默认实现只断言形态——32 位小写 hex——与采样互异）；④孤儿工件触发换名绝不覆盖；⑤最坏情况（后缀恒同的确定性生成器）探测兜底递增换名
- 聚焦套件 `services/api/tests/test_audit_anchor_archive.py`：**151 passed**（Round 3 后：141 基线 + Round 2 增 8 + Round 3 净增 2——3 个旧顺序探测测试改写为 5 个随机后缀聚焦测试；canonical venv，0.27s）
- 邻居回归 `test_audit_chain_anchor.py` + `test_release_readiness.py`：**90 passed / 3 skipped**（4.72s，零回归）
- `ruff check` 两文件：All checks passed（含显式 `--select RUF059,UP012,BLE001` 复核；唯一广义 `except Exception` 为 CLI 终防线 fail-closed，按仓库既有惯例标注 `# noqa: BLE001` + 中文理由）
- `py_compile` 两文件 / `git diff --check`：通过
- diff secret 模式扫描：0 命中；S3 API 静态扫描：仅上述六类只读/写入白名单方法

## 边界

- **开发验证 ≠ 真实执行**：本切片零真实 WORM 归档执行——未连接任何真实 S3 兼容端点、未对 M14-42 创世锚（anchor_hash `59c672b9…7c02`）做任何归档动作；真实 preflight/archive/verify 由 supervisor 在获准窗口进行
- 契约测试用注入 Fake 覆盖（含桶缺失/未开版本化/未开对象锁/字节不符/retention 漂移/version 不符/报告跨桶/秘密形态等负例），不能替代真实 boto3 + 真实 S3 行为（如 Object Lock 的服务端语义、retention 下限策略）
- `pass` 不等于 production ready：全局 `production_ready=false` 不变
- 离线介质复制（对象锁桶之外的第二副本）与定期归档调度仍属运维 runbook 动作，本工具不代管
- provider 冒烟与发布审批仍未完成（M14-42 已记，本切片不改变）
