# M14-06 Round 1 证据报告：生产恢复编排（compose restart 策略 + 恢复脚本 + env 护栏）

- 分支/工作树：`feature/m14-06-production-resilience` @ `D:\AI Learning OS\ai-learning-os-worktrees\m14-06-production-resilience`（基线 6319eb0，M14-04）
- 日期：2026-09-11（01:25–03:05 GMT+8）
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
