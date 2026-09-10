# tools/ops —— 生产恢复编排（M14-06）

本机 Windows 生产彩排栈（Docker Desktop + WSL 语音引擎）的自愈编排。容器面
兜底由 `infra/docker-compose.yml` 的 `restart: unless-stopped`（M14-06）承担；
本目录的 `production_recovery.py` 承担编排面：等引擎 → 校验 → 幂等 up →
健康核查 → 本地语音受控调和。

## production_recovery.py

```
# 仓库根执行（canonical venv 或任意 Python ≥3.11，纯标准库）
python tools/ops/production_recovery.py --dry-run     # 全程只读体检 + 决策预告
python tools/ops/production_recovery.py               # enforce（需 pin env 就绪）
```

流程与安全性质（细节见脚本头注释与
`services/api/tests/test_production_recovery.py` 契约测试）：

1. **等 Docker 引擎**：轮询 `docker version`（默认 300s，`--engine-wait-seconds`
   可调）——登录自愈场景给 Docker Desktop 留启动时间。
2. **compose 静态校验**：`docker compose config --quiet`（零容器改动）。
3. **pin check（fail-closed）**：`infra/env.production-recovery`（gitignored，
   模板 `infra/env.production-recovery.example`）必须存在、五键齐全
   （`AIOS_IMAGE_TAG/AIOS_APP_ENV/AIOS_WEB_PORT/AIOS_AUTH_SECRET/
   AIOS_LIVEKIT_API_SECRET`），且值与在线 api/web 容器一致（仅报键名，值
   绝不回显；子进程输出写日志前经防御性 redact）。任一不满足 → enforce 在
   `up` 之前可见拒绝——防止恢复路径用默认值/漂移值静默重建容器（tag/端口/
   密钥轮换）。
4. **幂等 up**：`docker compose -f infra/docker-compose.yml -p
   aios-m14-03-production-rehearsal --profile local --env-file <pin>
   up -d --no-build`（绝不 `--build`；dry-run 模式附加 compose 原生
   `--dry-run`）。
5. **六服务健康等待**：`compose ps` 轮询至 postgres/redis/minio/api/web/
   livekit 全 healthy（默认 420s）。
6. **本地语音调和（status first）**：经 `tools/voice/voice_service_control.py`
   的只读 inspect 取状态后决策——
   - `unmanaged-running`/`managed-running` 且 `/health` 200 → **不触碰**；
   - `stopped` → 唯一放行动作：经受控 CLI `start --engine <name>`（工具
     自身幂等 + fail-closed 语义不变）；
   - `unknown` / `managed-mismatch`（foreign）/ `port-mismatch` / 既有
     `managed-starting` → **可见失败**：不 spawn、不发信号、不清理 manifest；
   - `unmanaged-running` 非 200 → 不触碰（拒绝误杀边界）+ DEGRADED 退出。

退出码：`0` 完成/无需恢复；`1` 可见失败（含 pin 拒绝、健康未达、语音 fail
状态、DEGRADED）；`2` 参数错误。运行日志落 `artifacts/recovery/`
（gitignored）；Windows 侧子进程恒 `CREATE_NO_WINDOW`。

## 部署 env（pin 事实源）

```powershell
# supervisor 一次性创建（值来自部署事实；文件已 gitignore，绝不提交/回显）
Copy-Item infra\env.production-recovery.example infra\env.production-recovery
# 编辑填入真实值后：
python tools/ops/production_recovery.py --dry-run   # 应见「pin: OK」并给出 up 计划
```

注意：首次 enforce `up -d` 会因新增 `restart: unless-stopped` **一次性重建
六个容器**（compose 对 restart 策略变更的正常反应；之后恢复为 no-op）。该步
骤由 supervisor 在获准窗口执行——编排脚本自身不会在 pin 未就绪时动任何容器。

## 状态（Round 1，2026-09-11）

- 已交付：compose restart 策略、恢复编排 + 契约测试、env 模板/护栏。
- 未启用（待后续轮）：Windows 登录自启任务（Task Scheduler 安装器 + 静默
  wrapper + dry-run/uninstall 路径）——本轮按边界**未注册任何计划任务**。
