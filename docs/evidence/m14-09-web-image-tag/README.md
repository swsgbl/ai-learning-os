# M14-09 Web 镜像 tag 独立发布/回滚锚点 — 验收证据归档

- 日期：2026-09-11
- 分支：`fix/m14-09-web-image-tag`（基于本地 `main@539dbe1`（PR #76 树），worktree
  `D:\AI Learning OS\ai-learning-os-worktrees\m14-09-web-image-tag`，本地 git 可验证）
- 状态：本地实现与验证完成；本 README 记录提交前本地验收快照，远端 PR/CI/合并
  状态以 PROJECT_STATUS 后续回填为准（回填 2026-09-11：已随 **PR #81** 合并
  main（feature `321fb50`、merge `433d018`），PR CI run `34582538887` 为外部
  0-step 形态失败；合并后生产 env 补键与首次 Web-only 升级已完成，见
  `docs/evidence/m14-10-production-web-upgrade/`——本 README 其余内容维持
  提交前快照原文，不改写合并前口径）
- 入库证据：本 README（唯一入库文件）；`.verify/m14-09-web-image-tag/` 为
  gitignored 本机原始输出（如留存）
- 结论：compose web 镜像改读独立 `AIOS_WEB_IMAGE_TAG`，recovery pin 扩六键，
  全部聚焦测试与 compose 渲染验证通过；**不改变当前生产容器，只解锁后续
  Web-only 升级**

## 动机与设计

生产 pin 此前 `AIOS_IMAGE_TAG` 同时绑定 api/web：只升级 Web 时要么破坏
recovery pin（env 值与在线 web 镜像漂移 → enforce 拒绝），要么让同一 tag
混用两代镜像（隐性耦合，`docker compose` 用户无从察觉）。设计取舍：

| 面 | 决策 | 理由 |
|---|---|---|
| compose 插值 | `web: aios/web:${AIOS_WEB_IMAGE_TAG:-local}`（api 不变：`${AIOS_IMAGE_TAG:-local}`） | 普通 compose 用法（`up`/`down`/`config`/CI `--build`）零破坏——各锚点独立默认 `local`，渲染结果在 `docker compose config` 可见；**只设 `AIOS_IMAGE_TAG` 不再改变 Web tag**（隐性同 tag 耦合消除）。刻意不用 `${AIOS_WEB_IMAGE_TAG:?}` 必填式：那会让未设变量的 `docker compose down/config/ps`（含 build_release_candidate 的 cleanup 路径）因插值直接报错，破坏普通用法 |
| fail-closed 位置 | recovery pin 扩为**六键**（新增 `AIOS_WEB_IMAGE_TAG`） | 生产面防混用属恢复编排职责：env 缺键/在线 web 镜像事实缺失/与 env 不等 → enforce 在 `up` 之前可见拒绝（键名-only，值绝不回显）；在线事实取自 **web 容器镜像**（`docker inspect web {{.Config.Image}}`），与 api 镜像分别 inspect、互不派生 |
| 发布包 | `build_release_candidate.sh` 同 tag 显式双变量（`AIOS_IMAGE_TAG=$TAG AIOS_WEB_IMAGE_TAG=$TAG`） | 发布包语义不变（同一源码树、同 tag 双镜像、同一归档/manifest），仅启动环境变量从单 tag 变双变量显式 |
| 用法语义 | 同 tag 发布/回滚 = 显式同时设置两个变量；Web-only 升级 = 只设 `AIOS_WEB_IMAGE_TAG` | 回滚 runbook（根 README）与 DEVELOPMENT 同步改写 |

## 变更文件（diff 面）

| 文件 | 变更 |
|---|---|
| `infra/docker-compose.yml` | web `image:` 改 `${AIOS_WEB_IMAGE_TAG:-local}` + 设计注释（api 不变） |
| `tools/ops/production_recovery.py` | `PIN_KEYS` +`AIOS_WEB_IMAGE_TAG`（六键）；`collect_live_pins` 增 web 镜像 inspect；五键→六键文案 |
| `infra/env.production-recovery.example` | 必需键清单 + 正文增 `AIOS_WEB_IMAGE_TAG` 行（模板示例值=当前彩排事实 `m14-03-prod-rehearsal`） |
| `infra/build_release_candidate.sh` | 头注/步骤 5 注释/`say` 文案 + `up` 行双变量；发布包语义不变 |
| `services/api/tests/test_production_recovery.py` | FakeRunner 增 `live_web_image`（web 容器镜像独立回放）；matching env/占位 env/纯函数样本补第六键；**+2 回归**：web 镜像事实独立缺失拒绝、web tag 漂移仅报 `AIOS_WEB_IMAGE_TAG` 不连坐 api |
| `services/api/tests/test_versioning_rollback.py` | 渲染断言改四场景：默认双 local / 仅 `AIOS_IMAGE_TAG`（web 不连坐）/ 双变量同 tag / 仅 `AIOS_WEB_IMAGE_TAG`（api 不动） |
| `services/api/tests/test_compose_restart_policy.py` | `PIN_KEYS` 六键（模板护栏随动）；**+1 静态契约**：api/web 镜像锚点字符串精确断言且 web 不引用 `AIOS_IMAGE_TAG` |
| `services/api/tests/test_release_candidate.py` | compose 启动断言增 `AIOS_WEB_IMAGE_TAG="$TAG"` |
| `README.md` / `docs/DEVELOPMENT.md` / `tools/ops/README.md` | 回滚 runbook 双变量、六键口径、RC 步骤 5 描述同步 |
| `docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` / `docs/CHANGELOG.md` | M14-09 当前任务条目、ROADMAP 勾选项与待选清单更新、CHANGELOG Security 条目 |

**不改**：`infra/env.production-recovery`（真实文件，本切片不触碰）、任何容器/
服务/计划任务、CI workflow（Docker job `--build` 用默认 local/local，两锚点
独立默认渲染，无需改动）、业务代码。

## 验证（2026-09-11，canonical venv 解释器，真实命令与结果）

1. **聚焦测试（五套件）**：`python -m pytest services/api/tests/
   test_production_recovery.py test_versioning_rollback.py
   test_compose_restart_policy.py test_release_candidate.py
   test_release_readiness.py -q --basetemp=<仓库外>` →
   **237 passed, 2 skipped**（skip 为既有 git/真实 env 磁盘护栏，按设计）；
   其中 `test_production_recovery.py` 单独 45 passed（43 + 2 新增）。
2. **compose 静态校验**：`docker compose -f infra/docker-compose.yml
   config --quiet` × {无 profile, local, hybrid, cloud} → **4× exit 0**
   （零容器改动）。
3. **插值语义实证**（`config --format json` 读 services.*.image）：
   - 无变量：`aios/api:local` | `aios/web:local`
   - 仅 `AIOS_IMAGE_TAG=v9`：`aios/api:v9` | **`aios/web:local`**（不连坐）
   - 双变量 `AIOS_IMAGE_TAG=v9 AIOS_WEB_IMAGE_TAG=w9`：`aios/api:v9` |
     `aios/web:w9`
4. **静态门禁**：`ruff check`（5 个变更 Python 文件）→ All checks passed；
   `bash -n infra/build_release_candidate.sh` → OK；
   `py_compile tools/ops/production_recovery.py` → OK；
   `git diff --check` → 干净。

## 运维注意（fail-closed 推论，非缺陷）

- 现有真实 `infra/env.production-recovery` 尚无 `AIOS_WEB_IMAGE_TAG`——
  合并后**下一次 recovery enforce 会可见拒绝**（缺必需键，键名-only）。supervisor
  须按当前在线 web 镜像 tag（彩排栈应为 `m14-03-prod-rehearsal`，以
  `docker inspect <web 容器> --format {{.Config.Image}}` 实测为准）补键。
  （回填 2026-09-11：该补键已随 M14-10 执行——`AIOS_WEB_IMAGE_TAG=m14-05-security`、
  `AIOS_IMAGE_TAG=m14-03-prod-rehearsal` 不变，首次 enforce 即绿灯；后续事实见
  `docs/evidence/m14-10-production-web-upgrade/`）
- 此前以单 `AIOS_IMAGE_TAG` 同滚 api/web 的命令（旧 runbook 形态），合并起
  须显式双变量，否则只影响 api。

## 剩余风险与后续事项

1. compose 层的独立性靠「独立默认值 + 注释 + config 可见」表达；若需更硬的
   失败语义（未设 web tag 即拒绝渲染），需 `${AIOS_WEB_IMAGE_TAG:?}`，代价是
   `down/config` 等普通用法同样报错——本切片按「兼顾普通用法」要求未采纳。
2. 首次 Web-only 升级演练（`AIOS_WEB_IMAGE_TAG` 单独换 tag + recovery 绿灯）
   属合并后的运维步骤，本切片未执行（不碰生产容器）。
3. CI（GitHub Actions）为外部基础设施形态失败（0 step），本 README 不以本地
   通过掩盖 CI 未绿；远端结果见 PR 评论与后续 PROJECT_STATUS 回填。

均不含 token/key/secret/password；本切片未读取/输出任何真实 env 值。
