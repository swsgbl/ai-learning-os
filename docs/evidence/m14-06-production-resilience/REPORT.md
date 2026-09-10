# M14-06 Round 1 证据报告：生产恢复编排（compose restart 策略 + 恢复脚本 + env 护栏）

> **Round 1.1 修正（supervisor 评审，2026-09-11 03:10）**：`check_pins()` 修复
> fail-open 缺陷——在线容器存在但部分 PIN_KEY 事实缺失时曾被静默跳过（`ok`
> 可为 True）；现「缺事实」与「值不等」同权重拒绝（键名-only 报告），并新增
> 模板占位 secret（`<...>` 包裹/模板原文）恒拒绝（含栈未起、无在线容器可比
> 对的原 fail-open 关口）。回归测试 +8（部分在线事实×5、占位×3）；全套件
> **150 passed, 2 skipped**；ruff/py_compile/compose config 复验通过；真实
> 只读 dry-run（占位模板副本 `05-*.log`）与漂移复验（`06-*.txt`）均可见拒绝、
> exit 1、零 up。六容器 ID 与基线逐一相同、8010/8011 未触碰（仍为外部故障
> 停机态）。详见下文「Round 1.1 修正记录」。

- 分支/工作树：`feature/m14-06-production-resilience` @ `D:\AI Learning OS\ai-learning-os-worktrees\m14-06-production-resilience`（基线 6319eb0，M14-04）
- 日期：2026-09-11（01:25–03:05 GMT+8；Round 1.1 修正 03:06–03:10）
- 原始日志：`.verify/m14-06-production-resilience/`（gitignored，本机留存）；编排运行日志另见 `artifacts/recovery/`
- 边界遵守：**未停止/重启/重置任何容器、Docker Desktop、8010/8011、模拟器或代理**；未注册计划任务；无弹窗；无 secret 入库/入日志。

## 变更文件

| 文件 | 变更 |
|---|---|
| `infra/docker-compose.yml` | 六个长驻服务（postgres/redis/minio/api/livekit/web）新增 `restart: unless-stopped` + 注释 |
| `infra/env.production-recovery.example` | 新增：部署 pin env 模板（五必需键 + 纪律说明；占位值非真实 secret） |
| `.gitignore` | 新增 `infra/env.production-recovery`（真实 env 永不入库） |
| `tools/ops/production_recovery.py` | 新增：恢复编排（等引擎→校验→pin check→幂等 up -d --no-build→六服务健康→语音受控调和）；纯标准库；Runner/VoiceGateway 注入；secret 防御性 redact；Windows `CREATE_NO_WINDOW` |
| `tools/ops/README.md` | 新增：用法/安全性质/监督者交接 |
| `services/api/tests/test_production_recovery.py` | 新增：44 项契约测试（决策矩阵/状态空间交叉契约/pin 拒绝与不泄漏/compose 命令纪律/端到端全 fake） |
| `services/api/tests/test_compose_restart_policy.py` | 新增：restart 策略渲染（四 profile）+ PyYAML 静态回退 + env 模板/gitignore/索引护栏 |
| 本报告 | `docs/evidence/m14-06-production-resilience/REPORT.md` |

## 验证命令与结果（canonical venv：`D:\AI Learning OS\ai-learning-os\.venv`）

1. **基线快照（只读）**：`docker inspect` 六容器 id/started/restart/status/health
   → 全部 `running/healthy`、`restart=no`（运行中容器尚未获得新策略——预期，
   应用属 supervisor 后续 enforce 步骤）。证据 `00-baseline-restart-policy.txt`。
2. **compose 校验（只读）**：`docker compose config --quiet` × {无 profile,
   local, hybrid, cloud} → 4×PASS；`--profile local config --format json` 渲染
   → 六服务 `restart='unless-stopped'`。证据 `01-compose-config-validation.txt`。
3. **聚焦测试**：
   `python -m pytest services/api/tests/test_production_recovery.py services/api/tests/test_compose_restart_policy.py -q`
   → **43 passed, 1 skipped**（skip = 真实 env 文件尚未创建的磁盘护栏，按设计跳过）。
4. **回归（相邻套件）**：`test_voice_service_control.py + test_voice_local_scripts.py
   + test_compose_profiles.py` 连同新套件 → **142 passed, 2 skipped**，零回归。
5. **静态门禁**：`ruff check`（编排 + 两个测试文件）→ All checks passed；
   `py_compile` → OK。
6. **恢复 dry-run（真实栈，全程只读）**：
   - A（env 缺失）：`python tools/ops/production_recovery.py --dry-run`
     → 引擎就绪、config OK、六服务 healthy 快照、pin 缺失→「enforce 将拒绝」、
     exit **1**（镜像 enforce 拒绝）。证据 `02-recovery-dry-run-noenv.log`。
   - B（漂移 env，伪值）：`--dry-run --env-file .verify/.../drift.env`
     → 「一致键 2/5；不一致键: AIOS_IMAGE_TAG, AIOS_AUTH_SECRET,
     AIOS_LIVEKIT_API_SECRET（值不回显）」+ 拒绝 up，exit **1**；
     日志全程无 secret 值。证据 `03-recovery-dry-run-drift.log`。
7. **事后核查（只读）**：六容器 id/started-at 与基线**逐一相同**（零重建零触碰）；
   `docker compose up --dry-run` 旗标可用（supervisor 绿灯路径）。证据
   `04-post-verification.txt`。

## 发现（如实报告）

**语音引擎 8010/8011 在本会话中途自行退出**：01:30 基线两者 `/health` HTTP 200；
03:00 复查双双无监听（HTTP 000、netstat 无记录）。期间本会话全部操作为只读
（docker inspect / compose config 渲染 / 基于 fake 的 pytest / 文件编辑），
无任何停止动作——退出属外部因素（疑似 WSL VM 回收/宿主休眠），**恰为 M14-06
要自愈的故障形态**：dry-run 如实检出 `stopped → start`（受控，未执行——
Round 1 边界禁触碰 8010/8011）。Docker 六容器不受影响，全程 healthy。

## Round 1.1 修正记录（fail-open 缺陷）

**缺陷（supervisor 评审发现）**：`check_pins()` 中 `collect_live_pins()` 在 api
容器存在但只回出部分 PIN_KEYS 事实时（如 `docker port` 空输出、镜像 inspect
失败、容器 env 缺行），缺失键既不计 mismatch 也不计 matched——若其余键一致，
`report.ok=True`，违反「五键在线一致性」承诺（fail-open）。另一关口：栈未起
（无在线容器）路径从不校验值本身——照抄模板的占位 secret 可通过并用于 `up`
创建容器。

**修复（`tools/ops/production_recovery.py`）**：
- 新增 `missing_live_keys` 检测与报告：在线容器存在时任一 PIN_KEY 事实缺失 →
  `ok=False`（「inspect/port 探测不完整时不得按跳过放行」）；`PinReport` 增
  `missing_live_keys`/`placeholder_keys` 字段，与 `mismatched_keys` 分类分离。
- 新增 `placeholder_pin_keys()`：`<...>` 包裹值或模板原文
  （`TEMPLATE_PLACEHOLDER_VALUES`）→ 键名-only 拒绝；**在两条路径（有/无在线
  容器）均生效**——占位 secret 在任何情况下不得进入 compose up。
- ok 语义：`env 五键齐全 ∧ 无占位 ∧ (无在线容器 ∨ (在线五键齐全 ∧ 逐键相等))`。
- dry-run 退出语义与 enforce 对齐不变：pin 未就绪 → dry-run 记
  `pin-not-ready` 失败并 exit 1（镜像 enforce 拒绝）。

**回归测试（`test_production_recovery.py`，+8）**：部分在线事实（缺 web 端口
/ 缺镜像事实 / 缺 secret env 行 / 多键同缺 + 分类断言）×4 + 端到端 enforce
零 up ×1；占位（纯函数 / 在线栈存在照抄模板 / 栈未起照抄模板→原 fail-open
关口）×3。全部断言键名-only（占位文本与 secret 值均不回显）。

**复验（2026-09-11 03:06–03:10）**：
- 缺陷复现脚本（修复前 `ok=True` → 修复后 `ok=False`,
  `missing_live_keys=('AIOS_IMAGE_TAG','AIOS_WEB_PORT')`）。
- `pytest` 五套件（新 2 + 相邻 3）→ **150 passed, 2 skipped**（逐文件
  43/8+1s/64/30/5+1s）。
- `ruff check` / `py_compile` → OK；`docker compose config --quiet` ×4 → PASS。
- 真实只读 dry-run：占位模板副本（`05-recovery-dry-run-placeholder.log`）→
  占位键 + 不一致键双拒绝、零 up、exit 1；漂移复验 exit 1（`06-*.txt`）。
- 事后核查：六容器 ID 与基线逐一相同（零重建零触碰）；8010/8011 只读探测
  未触碰（仍为外部故障停机态）。

## Supervisor 交接（后续轮）

1. 创建 pin env：`Copy-Item infra\env.production-recovery.example
   infra\env.production-recovery` 并填入部署真实值（AIOS_IMAGE_TAG=
   m14-03-prod-rehearsal、AIOS_APP_ENV=production、AIOS_WEB_PORT=3011、两个
   secret 取自部署事实）。
2. 绿灯预检：`python tools/ops/production_recovery.py --dry-run` → 应见
   `pin: OK` + `up --dry-run` 计划（首跑会标注六容器重建一次以获得
   restart 策略）。
3. 获准窗口执行 enforce `python tools/ops/production_recovery.py`（一次性重建
   六容器 + 自动受控拉起两语音引擎——engine start 首次含依赖检查，模型缓存
   复用不重下，见 M14-02/03 证据）。
4. Round 2：隐藏登录自启任务安装器（Task Scheduler + 静默 wrapper +
   dry-run/uninstall）——本轮按边界未注册任何任务。
