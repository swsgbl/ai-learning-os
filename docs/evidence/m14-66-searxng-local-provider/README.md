# M14-66 证据：集成生产安全的本地 SearXNG provider 栈

- 切片类型：开发切片（infra + 测试 + 文档；无应用运行时代码改动）
- worktree：`D:\AI Learning OS\ai-learning-os-worktrees\m14-66-searxng-local-provider`
- 分支：`feature/m14-66-searxng-local-provider`，基于 `origin/main@13fdcbe`
- 交付形态：单 local commit，不 push、不建 PR
- 监督修正轮（2026-09-19 晚）：**出站代理显式透传产品化**——compose 新增
  `AIOS_SEARXNG_HTTP_PROXY / AIOS_SEARXNG_HTTPS_PROXY / AIOS_SEARXNG_NO_PROXY`
  三槽位（默认恒空 = 直连出站），并补 compose 形态 live 验证；修正轮原始
  证据在 gitignored
  `.verify/m14-66-searxng-local-provider-compose-live/README.md`
- 本 README 为唯一入库证据文件；初始轮 live 原始证据在 gitignored
  `.verify/m14-66-searxng-local-provider-live/README.md`（本机复核用，不入库）

## 1. 功能上下文与官方事实口径

目标：给 CloudWebProvider（M10-12 已交付的 SearXNG-compatible JSON 消费方）补一个
生产安全的本地 provider 栈——生产本地版一条 `--profile search` 拉起自有 SearXNG，
云端搜索能力不再依赖外部公共服务。

| 项 | 事实 | 来源 |
|---|---|---|
| 官方镜像 | `docker.io/searxng/searxng@sha256:6869f20676fd91e3f856bcaefc510bc363fdd126f7bd860f49f2ffcb3b305da0`（digest 精确 pin，本地已拉取核验） | supervisor 核验 |
| 容器端口 | SearXNG 监听容器 8080 | 官方镜像 |
| JSON API | 默认 `search.formats` 只有 html；JSON API 需 formats 含 json，未启用即 403 | 官方文档 |
| 挂载点 | 配置 `/etc/searxng`；缓存 `/var/cache/searxng` | 官方文档 |
| 配置基线 | `use_default_settings: true`（继承上游默认 + 最小覆盖） | 官方推荐 |
| secret | `server.secret_key` 可经 `SEARXNG_SECRET` env 覆盖（镜像加载期） | 官方镜像机制 |
| 私有实例语义 | limiter / public_instance 应为 false（bot 防护面向公网部署） | 官方语义 |
| 镜像工具 | 官方镜像含 `/usr/sbin/wget`（healthcheck 可用） | supervisor 核验 |
| 宿主 8080 | 被无关进程占用（tailscaled，PID 9832）——绝不映射、绝不触碰 | 本机实证 |
| 宿主映射 | 唯一核验空闲：`127.0.0.1:8878:8080`（恒 loopback，绝不超本机） | 本机实证 |
| 网络内端点 | compose 网络内 API 侧端点 `http://searxng:8080`（不经宿主端口） | supervisor 核验 |
| 既有消费方 | CloudWebProvider 在 JSON 启用后对该服务可用（M10-12 契约不动） | 既有实现 + live 验证 |
| 冒烟脚本缺陷 | `smoke_search.sh` 在 WSL 继承代理下 127.0.0.1 被发给代理而必然失败；`NO_PROXY=127.0.0.1,localhost` 可修复 | 本机实证 |
| 出站代理 env 读取 | SearXNG 出站栈 httpx 经 urllib `getproxies` **大小写不敏感**读取代理 env；**空值被忽略**（空默认 = 直连，不产生代理行为） | Python/urllib 语义 + live 实证 |
| 健康检查代理免疫 | 官方镜像 busybox wget **只读小写** `http_proxy`——容器仅设大写单形即回环健康检查不经代理 | live 实证 |

## 2. 实现面（8 文件：源/配置 3 + 测试 5）

1. `infra/docker-compose.yml`——新增 `searxng` 服务（挂独立 `search` profile）：
   digest 精确 pin（常量 `SEARXNG_IMAGE`）、
   `SEARXNG_SECRET: ${AIOS_SEARXNG_SECRET:-${SEARXNG_SECRET:-<占位>}}` 嵌套插值、
   仓库配置只读挂载 `./searxng/settings.yml:/etc/searxng/settings.yml:ro`、
   命名缓存卷 `searxng-cache:/var/cache/searxng`、healthcheck（wget 探官方
   `/healthz`，interval 10s / timeout 5s / retries 5）、宿主暴露恒
   `127.0.0.1:8878:8080`、`restart: unless-stopped`；顶层 volumes 同步声明；
   **出站代理显式控制三槽位（修正轮）**：`HTTP_PROXY/HTTPS_PROXY/NO_PROXY`
   仅以 `${AIOS_SEARXNG_*:-}` 单链插值（默认恒空 = 直连出站；无通用回落，
   绝不隐式继承宿主 shell 代理 env；仅大写单形——httpx 大小写不敏感读取
   即全量生效，且 busybox wget 只读小写故回环健康检查恒不经代理）；
   compose 不硬编码任何代理地址/端口（静态 + 渲染测试双锁）；
   api 服务 `SEARCH_*` 槽位默认恒空，注释含接线配方
   （`AIOS_SEARCH_MODE=cloud` + `AIOS_SEARCH_CLOUD_ENDPOINT=http://searxng:8080`）。
2. `infra/searxng/settings.yml`（新增）——`use_default_settings: true` + 最小覆盖：
   `search.formats: [html, json]`（JSON API 是 CloudWebProvider 可用性硬前提）、
   `server.limiter: false`、`server.public_instance: false`、`server.secret_key`
   占位（与 compose 插值默认同一字面量，生产经 env 覆盖）。
3. `infra/smoke_search.sh`——新增 `_ensure_loopback_no_proxy`：对 NO_PROXY 与
   no_proxy 双变量幂等追加 127.0.0.1、localhost（case 匹配防重复；仅追加不删改
   既有条目——非回环 endpoint 的代理行为不变；不触碰 HTTP_PROXY 代理变量本体）。
4. `services/api/tests/test_searxng_local_provider.py`（新增，16 项静态契约，
   PyYAML 直解不触网）——digest pin、恒 loopback 8878 唯一映射、`:ro` 挂载、
   命名卷（服务 + 顶层）、healthcheck 六字段、profile/restart、secret 插值
   字面量、settings 三断言、跨文件漂移锁（compose 插值默认 == settings
   secret_key == 常量）、api 槽位默认空、接线配方文本在档、两文件零 secret 形态、
   代理三槽位 AIOS 单链空默认插值形态（无小写镜像）、searxng 服务面零硬编码
   代理地址（无 host.docker.internal / 无 7892，全文件无 `:7892` 端口形态）。
5. `services/api/tests/test_compose_profiles.py`（扩展 6 项渲染面，
   `docker compose config` 不启动容器）——`--profile search` 渲染 6 服务集
   （searxng 无 livekit，端口 dict 断言 host_ip/published/target）；无 profile
   与三个语音 profile（local/hybrid/cloud）均不含 searxng；secret 注入链四形态
   （AIOS 优先 → 通用回落 → 双未设回落占位 → 置空同回落）；接线配方注入透传 +
   默认恒空；代理默认渲染三槽位恒空 + api 不沾代理变量；代理显式注入（合成值）
   逐槽透传 + 无小写镜像 + 槽位独立（仅 HTTPS 注入形态）。
6. `services/api/tests/test_compose_restart_policy.py`（扩展）——长驻服务全集加
   searxng（七个）；渲染参数化加 `search` 形态（unless-stopped + 服务集断言）。
7. `services/api/tests/test_minio_selfbuild.py`（扩展）——compose 服务集静态锁
   加 searxng + profile membership 断言（`profiles == ["search"]`）。
8. `services/api/tests/test_smoke_search_script.py`（扩展 3 行为测试 + 文本契约）
   ——env-dump 桩捕获脚本导出值：空起点补齐 `NO_PROXY/no_proxy=127.0.0.1,localhost`、
   既有条目保留（`corp.example,10.0.0.0/8,127.0.0.1,localhost`）、幂等 + 大小写
   同步（不同客户端读取大小写不一，缺一即失效）；文本契约加双变量/回环条目
   在源码中、`HTTP_PROXY/http_proxy` 不在源码中；`SMOKE_ENV_KEYS` 纳入双变量
   保证各用例起点环境确定性。

关键设计决策：

- **独立 `search` profile（显式部署控制）**：searxng 只随 `--profile search`
  渲染/启动，与语音 profile（local/hybrid/cloud）相互独立——不挂搜索不拉起、
  不挂语音也不隐含搜索；默认渲染零变化（渲染测试四形态锁定）。
- **fail-closed 不动**：`providers.py` 三门判定（`PRIVACY_SEND_CONTEXT_TO_CLOUD`
  + `SEARCH_MODE == cloud` + endpoint 合法 http/https）原样保留；api
  `SEARCH_CLOUD_ENDPOINT` 默认恒空——普通非搜索路径零隐式出站，接线是部署侧
  显式注入行为，不是代码路径的隐式启用。
- **零真实 secret 入库**：compose 插值默认与 settings.yml secret_key 是同一
  dev 占位字面量（跨文件漂移锁强制成对修改）；生产 secret 只经
  `AIOS_SEARXNG_SECRET`/`SEARXNG_SECRET` 注入（AIOS 前缀优先，`:-` 嵌套插值
  置空回落语义）。
- **宿主暴露恒 loopback:8878**：8080 被本机 tailscaled 占用（绝不映射）；compose
  网络内 API 走 `http://searxng:8080`，宿主端口只服务宿主侧冒烟/调试。
- **出站代理显式部署控制（修正轮核心）**：受限/受管网络下容器直连上游引擎
  会超时（初始轮实证），但代理启用必须是**部署显式行为**——`AIOS_SEARXNG_*`
  三槽位单链插值、默认恒空（= 直连出站，空值被 urllib 忽略）、无通用
  `HTTP_PROXY` 回落（宿主 shell 代理绝不隐式进容器）、仅大写单形（httpx
  大小写不敏感读取即全量生效；busybox wget 只读小写故回环健康检查恒不经
  代理）。compose 不硬编码任何代理值；真实代理地址只放部署 env/secret。

## 3. 测试证据（canonical venv `D:/AI Learning OS/ai-learning-os/.venv`）

| 套件 | 命令 | 结果 |
|---|---|---|
| 聚焦六件套 | `python -m pytest services/api/tests/test_searxng_local_provider.py services/api/tests/test_compose_profiles.py services/api/tests/test_compose_restart_policy.py services/api/tests/test_minio_selfbuild.py services/api/tests/test_smoke_search_script.py services/api/tests/test_search.py -q` | **96 passed, 2 skipped**（skip 为 AIOS_COMPOSE_SMOKE 门控真启动冒烟 + 真实 env 文件不存在，均预期；修正轮 +4：静态 2 + 渲染 2） |
| lint | `ruff check services/api/app services/api/tests` | 全过 |
| git | `git diff --check` | 干净 |
| compose 校验 | `docker compose -f infra/docker-compose.yml config`（默认直连 / 显式代理合成值 / 无 profile 多形态） | 渲染合法 |

## 4. 诚实边界

- **出站网络边界随本机网络姿态漂移（如实记录）**：初始轮（docker-run 形态）
  本机直连出站时上游引擎全部 engine timeout，代理出站后正常；修正轮（compose
  形态）同机直连却已可通（全新查询 results=19）——本机边界随系统级代理/网络
  姿态漂移。正因如此，compose 以 `AIOS_SEARXNG_HTTP_PROXY/HTTPS_PROXY/
  NO_PROXY` 三槽位提供**显式部署控制**（默认恒空 = 直连；无通用回落不隐式
  继承宿主 shell；仅大写单形）；**本机部署若需代理，具体地址/端口只放部署
  env/secret，绝不入库、不在文档回显**（两轮 live 全程未输出真实代理值）。
  DEVELOPMENT.md runbook 已记注入配方与边界。
- **零真实 secret 入库**：两文件仅含 dev 占位（测试锁定零 secret 形态）；真实
  凭据只放部署 secret/.env。
- **live 验证口径**：任务自有隔离 `docker run` 容器（仓库 settings.yml 只读
  挂载 + 同 digest + 同 loopback 端口/命名卷形态），非 compose 全栈真启动；
  compose 全栈冒烟是 `AIOS_COMPOSE_SMOKE=1` 门控测试，本环境跳过（如实记录，
  不虚报）。
- 宿主 8080 全程未触碰（tailscaled 占用）；live 清理仅任务自有容器/卷，
  零残留核验（`docker ps -a` / `volume ls --filter name=m14-66` 均空）。
- 不构成 `production_ready=true` 依据，全局 `production_ready=false` 不变。

## 5. 验证执行记录（live，2026-09-19；原始证据 gitignored `.verify/`）

### 5.1 修正轮：compose 形态（2026-09-19 晚）

任务专属隔离 compose 项目 `aios-m14-66-searxng-compose-verify`
（`docker compose -p <项目> -f infra/docker-compose.yml --profile search up -d
searxng`，只拉起 searxng 单服务；开始前核验 8878 空闲 + 项目名零残留；
真实代理值全程未回显，仅记录长度/形态）：

1. **阶段 (a) 默认直连渲染**：healthy@~20s；容器内 `HTTP_PROXY/HTTPS_PROXY/
   NO_PROXY/http_proxy` 全空；宿主侧 `/healthz` HTTP 200；全新唯一查询（防缓存）
   results=19（本机当前直连可通——与初始轮边界相反，网络姿态漂移如实记录）。
2. **阶段 (b) 显式代理注入**（部署 env `AIOS_SEARXNG_HTTP_PROXY/HTTPS_PROXY`）：
   容器内两槽位透传（长度 32，`http://host.docker.internal:<端口>` 形态）、无
   小写镜像变量；healthy@~20s（busybox wget 只读小写——大写单形下回环健康
   检查不经代理的实证）；healthz 200；全新唯一查询 results=20。
3. **NO_PROXY 槽位 live 实证**：注入 `AIOS_SEARXNG_NO_PROXY=127.0.0.1,localhost`
   重建 → 容器内原样呈现、healthy、healthz 200。
4. **真实 CloudWebProvider 端到端**（代理形态容器上）：`SEARCH_CLOUD_ENDPOINT=
   http://127.0.0.1:8878 bash infra/smoke_search.sh`（canonical venv python）
   → `provider=cloud-web results=5 / ALL SEARCH SMOKE CHECKS PASSED / EXIT=0`
   （脚本回环旁路原样未改）。
5. **清理与零残留**：`docker compose -p <项目> --profile search down -v
   --remove-orphans`；残留核验 containers=0 / volumes=0 / networks=0；
   8878 释放；生产容器（aios-m14-03-production-rehearsal-* 等）全程未动；
   含端口的临时探测文件已删除。

### 5.2 初始轮：docker-run 形态（2026-09-19 早，原始证据
`.verify/m14-66-searxng-local-provider-live/`）

任务自有隔离容器 `aios-m14-66-searxng-verify`（docker inspect 证明 digest 与
supervisor 核验逐字节一致、唯一端口映射 `127.0.0.1:8878→8080`、仓库
settings.yml 只读挂载、命名缓存卷 `aios-m14-66-searxng-cache-verify`）：

1. `/healthz` → **HTTP 200**（一次通过，宿主侧 loopback 直探）。
2. **JSON API 未 403**：`GET /search?q=test&format=json` 返回 JSON——官方默认
   formats 只有 html 时该请求 403，此处 200 即 `formats: [html, json]` 生效实证。
3. 真实 CloudWebProvider JSON 搜索冒烟，两段过程（如实记录）：第一段容器直连
   出站 → 上游引擎全部 engine timeout，`results=0 / EXIT=1`（本机网络边界事实，
   非回归）；第二段以宿主代理（`--add-host=host.docker.internal:host-gateway` +
   代理 env，地址/端口略——不入库、不回显）重建 → `results=5 / ALL SEARCH
   SMOKE CHECKS PASSED / EXIT=0`。
4. **对抗性复跑**：假代理（`http://127.0.0.1:9` 四变量全设）+ `unset
   NO_PROXY no_proxy` 敌意环境下重跑冒烟仍 PASS——若回环绕过未生效，loopback
   请求必被交给死端口 9 并连接拒绝；仍 PASS 即 `smoke_search.sh` 双变量修复的
   对抗性证明（与 env-dump 桩测试互补：桩锁行为，此跑锁真实效果）。
5. 清理：`docker rm -fv aios-m14-66-searxng-verify`（`-v` 连带匿名卷）+
   `docker volume rm aios-m14-66-searxng-cache-verify`；残留核验零输出；
   无关容器抽查未动。
