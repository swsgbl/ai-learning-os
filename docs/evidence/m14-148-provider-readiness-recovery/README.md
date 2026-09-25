# M14-148 证据:provider readiness recovery Round 1(实现切片)

- 切片类型:**实现切片**——最小仓库变更 + 幂等恢复 helper + 离线契约测试
  + 文档;**Round 1 零生产变更**(live 生产未被触碰,provider 门仍 blocked)
- worktree:`m14-148-provider-readiness-recovery`,分支
  `ops/m14-148-provider-readiness-recovery`,基于 main
  `306f72a471aec2492a10140e5808c292d3363c5e`(树等价于当前远端 main
  `a9b414bdc6ee759060924aadac435e1a732c8185`),单 local commit、
  不 push、不开 PR
- 本 README 为唯一入库证据文件;本切片的运行原始输出(gitignored)位于
  `.verify/m14-148-provider-readiness-recovery/`(2 个 dry-run stdout 存档,
  各含末尾 `[exit-code]` 行——修正轮补记,退出码不再靠转述)
- 结论先行:**Round 1 交付的是"可恢复的生产默认"的仓库事实与工具路径,
  不是恢复本身**——SearXNG 直连出网已成为 env 模板/compose/恢复 helper
  三层锁定的 canonical 默认;对 live 部署事实的 enforce + 容器 recreate
  留待 Round 2 由 supervisor 获准后执行(§6)。

## 1. 背景:M14-147 blocked 证据与本切片诊断基线

M14-147(2026-09-25 21:20 UTC)preflight 证据(gitignored,
SHA256 `55cd957f…` 已在 M14-147 归档):voice ready / search
`not_ready(upstream_failure)` / llm `not_ready(model_absent)`,
overall **blocked**。supervisor 诊断(本切片任务书给定,作为既定事实):

1. **本地语音就绪**(ASR/TTS /health 200);
2. **SearXNG 容器 healthy 但六上游引擎全数失联**——容器出站被部署 env
   的代理槽位固定在一条**当前已坏**的 SOCKS 路径;宿主直连 egress
   (example.com / api.github.com / bing)正常。根因不在容器、不在仓库
   机制(M14-66 语义完好),在部署 env 事实;
3. **Ollama 模型 `aios-qwen3.5-9b-4096` 已安装但未驻留** `/api/ps`
   (M14-117 同款形态,operator 加载即解,不在本切片范围)。

关键仓库事实(既有,非本切片变更):compose
`HTTP_PROXY: ${AIOS_SEARXNG_HTTP_PROXY:-}` 三槽位在键缺省/空时插值为空
= urllib getproxies 忽略空值 = 直连(M14-66 修正轮 live 实证;M14-114
已合入 China Bing `cn.bing.com` 直连引擎)。**缺的是恢复路径**:部署
env(gitignored `infra/env.production-recovery`)持非空坏代理值时,把
部署事实恢复到直连默认是纯手工、不可审计的 env 文件编辑,且模板
`.example` 完全没有这三槽位的文档。

## 2. 实现面(6 文件:配置/工具 3 + 测试 2 扩展 + 文档)

1. **`infra/env.production-recovery.example`**——模板新增 SearXNG 出站
   代理三槽位文档(注释形态 = 直连是 **canonical 模板默认**):头部说明
   区声明三槽位**非 PIN_KEYS**(不参与 pin 比对,可随网络状况增删而不
   触碰九键)、缺省/空/注释 = 直连出网(可恢复的生产默认,M14-148)、
   损坏恢复动作指向新 helper;尾部三槽位以注释形态在档(显式 opt-in
   才取消注释填值)。
2. **`tools/ops/searxng_egress_recovery.py`**(新增,纯标准库单文件,
   与 tools/ops 家族同款纪律)——幂等、fail-closed、**零子进程**(源码
   契约测试锁定:无 subprocess import / 无进程执行属性 / 无 docker、
   compose 命令字面量构造——AST 面):
   - **validate key names only**:经 importlib 复用
     `production_recovery.PIN_KEYS`(单一事实源,零副本;契约测试逐键
     相等)——九键必须全部以激活形态在位,缺任一键 fail-closed 拒绝
     (exit 1,仅报键名);代理槽位值只做存在/非空布尔判定,**绝不回显**;
   - **disable broken proxy slots**:激活且非空的
     `AIOS_SEARXNG_HTTP_PROXY` / `AIOS_SEARXNG_HTTPS_PROXY` 行 →
     固定标记注释形态(`# [disabled-by-searxng-egress-recovery]
     KEY=…`,原值保留在 gitignored 文件内供日后 opt-in 取消注释);
     `AIOS_SEARXNG_NO_PROXY` 刻意不在禁用集(直连下无效但无害,最小
     变更);**幂等**:标记形态/空值/缺省 → no-op;其余行逐字节原样
     写回,写后立即重验九键齐全 + 无残留激活槽位,失败 → 可见 exit 1;
   - **严格 UTF-8 fail-closed(supervisor 修正轮)**:env 读取恒严格
     解码——非 UTF-8 字节在**任何写入之前**可见拒绝(exit 1),不回显
     解码内容或替换字符;dry-run 与 enforce 两路径下文件字节均逐字节
     不变(本工具不做有损解码/改写;写后重验读取同样严格);
   - **原子替换写 + 失败保真(supervisor 修正轮)**:enforce 恒经
     `_atomic_write_text` 同目录临时文件原子替换(mkstemp → 严格
     UTF-8 写 + flush + fsync → 复制原文件权限位(平台支持时)→
     `os.replace`);任一步失败 → 临时文件清理、**原文件逐字节
     不变**、可见 exit 1——secret 文件绝不承受半写状态;成功路径
     亦零 `.tmp` 残留;
   - **dry-run plan**:`--dry-run` 全程只读零写入,输出待禁用键名
     (含行号)+ 渲染语义预告 + Round 2 后续(仅文字,不执行);
   - **Round 1 硬边界**:本工具无任何服务生命周期能力——enforce 也
     仅编辑 env 文件,使新 egress 事实生效所需的容器 recreate 是
     Round 2 获准后的受控动作。
3. **`infra/docker-compose.yml`**——searxng 服务代理注释区补三行
   M14-148 恢复口径(直连 = 可恢复的生产默认;指向 helper;与模板
   双载防漂移,测试锚点锁定)。零行为变更(纯注释)。
4. **`services/api/tests/test_searxng_egress_recovery.py`**(新增,
   28 项(Round 1 23 项 + 修正轮 5 项),全离线:tmp env 文件 +
   importlib,零 Docker/网络/真实 env 读取)——PIN_KEYS 单一事实源
   (逐键相等 + 槽位键不与九键相交);
   文件缺失/九键缺一 fail-closed(仅键名,伪 secret 标记值零出现);
   enforce 禁用形态 + 其余行逐字节不变 + 九键伪值逐键保留;幂等二跑
   no-op;空值槽位/无槽位 no-op;dry-run 零写入 + 计划含键名行号;
   伪代理值标记(`socks5h://ZX-markerproxy-…`)绝不出现在任何输出;
   **修正轮 5 项**:非 UTF-8 fail-closed 两路径(dry-run/enforce 均
   exit 1,文件字节逐字节不变,输出零 U+FFFD/零内容探针回显)、
   诱导 `os.replace` 失败(原文件逐字节不变 + 零 `.tmp` 残留 +
   exit 1 可见失败)、成功 enforce 零 `.tmp` 残留、原子替换后
   权限位保留(where supported);
   行分类五形态矩阵(active/active-empty/disabled/commented/absent);
   NO_PROXY 永不禁用;源码 AST 契约(零子进程/零服务命令);模板三
   槽位注释形态在档且无激活代理行;compose 注释与模板双载恢复口径。
5. **`services/api/tests/test_compose_profiles.py`**(扩展 2 项渲染
   回归,`docker compose config` 只渲染不启动容器)——**env-file 通道**
   (production_recovery 同款 `--env-file` 注入面):槽位标记禁用/注释/
   置空 → searxng 三槽位渲染恒空 = 直连,且九键插值照常(AIOS_WEB_PORT
   → web 端口锚点生效,pin 面与出站开关面互不干扰);对照面:同一通道
   激活槽位原样透传(取消注释即恢复 opt-in,恢复语义真实成立)。
   `_render` helper 增加 `env_file` 参数(unset 宿主侧三槽位保证
   确定性)。

## 3. 真实 dry-run 证据(只读,零生产变更)

对 live 部署事实做了两次只读验证(gitignored 存档
`.verify/m14-148-provider-readiness-recovery/`):

1. **worktree 内**(env 文件不存在)→ 正确 fail-closed 拒绝
   (`拒绝: env 文件缺失`,**exit 1**)。**修正轮更正:Round 1 原文
   误记为 exit 0("计划完成/拒绝均已如实报告"的转述不成立)——
   fail-closed 拒绝的真实退出码是 `EXIT_ERROR = 1`,与 helper
   docstring 退出码契约(env 缺失 → 1)一致;修正轮已重跑并存档
   末尾补记 `[exit-code] 1` 行,退出码不再靠转述**。工具对自身
   默认路径的缺失诚实可见(拒绝即非零退出,CI/脚本可依赖);
2. **主 checkout 真实 `infra/env.production-recovery`**(只读
   `--dry-run`,`--env-file` 显式指向;修正轮重跑 exit 0,存档补记
   `[exit-code] 0`)→ **九键齐全(键名验证)**;
   `AIOS_SEARXNG_HTTP_PROXY`(第 12 行)与 `AIOS_SEARXNG_HTTPS_PROXY`
   (第 13 行)均非空——与 supervisor"容器 egress 被固定在坏 SOCKS
   路径"诊断一致;计划 = 禁用两槽位 → compose 渲染空 = 直连。输出
   全程零值回显(仅键名/行号/固定词汇)。

**Round 1 未执行 enforce**:真实 env 文件未被写入(零字节变更),
在线容器未被触碰——live 生产在本切片内未被修改。

## 4. 验证(Round 1 计数如实;**修正轮全部重跑**,命令与计数如下)

- 聚焦新套件:`pytest services/api/tests/test_searxng_egress_recovery.py
  --basetemp <任务书指定目录>` → Round 1 **23 passed** → 修正轮重跑
  **28 passed**(23 + 修正轮 5 项新增;exit 0);
- 渲染面:`pytest services/api/tests/test_compose_profiles.py` →
  **16 passed, 1 skipped**(skip = AIOS_COMPOSE_SMOKE 门控真启动,
  预期跳过;修正轮重跑一致,exit 0);
- 回归(修正轮 7 套件):`pytest test_searxng_local_provider.py
  test_production_recovery.py test_compose_restart_policy.py
  test_minio_selfbuild.py test_smoke_search_script.py
  test_searxng_egress_recovery.py test_compose_profiles.py` →
  **165 passed, 2 skipped**(六套件口径对账:Round 1 144 passed/
  1 skipped + 修正轮 5 项新增 = 149 passed/1 skipped,+ compose
  16 passed/1 skipped = 165/2;PIN_KEYS 面/compose 服务集/smoke
  脚本面零回归,exit 0);
- `ruff check`(三个触碰的 Python 文件,经 `uvx ruff`)→ All checks
  passed(exit 0);
- `py_compile`(helper + 两个测试文件)→ OK(exit 0);
- compose 渲染(docker compose config --format json,零容器启动)→
  env-file 禁用/空/激活三形态断言全过(§2.5,测试套件内);
- `git diff --check` → 干净;新增行扫描(修正轮,对基 main 全部
  1154 条新增行):真实凭据模式(sk-/ghp_/github_pat_/AKIA/PEM 私钥/
  Bearer + password/secret/token/api_key 赋值形态)与本地绝对路径 →
  **真实凭据 0 命中**(secret 赋值形态命中的 3 行均为测试源码离线
  **伪标记 fixture**——`ZX-marker*` 约定值/`whatever` 占位,Round 1
  既有测试伪值约定,非真实凭据)、本地路径 **0 命中**;U+FFFD →
  文档/工具行 0 命中,仅测试源码 2 行**有意探针字面量**
  (`assert "…" not in out` 断言常量,验证拒绝面不回显替换字符——
  非编码损坏,如实留档)。
- CI(首次 PR,事后如实记录):**4/5 绿**;API job 仅
  `test_rc_smoke_rehearsal.py::test_production_files_byte_equivalent_to_base
  [infra/docker-compose.yml]` 1 例失败——M14-100 口径 compose 字节 pin
  陈旧(本任务有意改写 compose 注释,pin 未同步);该次 CI API 全量
  **5106 passed / 10 skipped**(仅此单失败)。修正:pin 显式更新为
  `ee84bc33…`——仅注释变更,生产语义零漂移(compose diff 实录见上);
  本地重跑整套 smoke rehearsal → **59 passed**(exit 0,含新 pin 三文件
  字节等价;完整输出在 .verify 任务日志)。

## 5. 诚实边界

- **live 生产在 Round 1 未被修改**:零容器 stop/restart/recreate、零
  在线容器 env 变更、零真实 env 文件写入、零模型加载、零代理/CC
  Switch/Xray/Docker/API/DB/MinIO/语音/Ollama/scheduler/模拟器/用户
  进程生命周期触碰;
- **provider 门仍 blocked**:search `upstream_failure`(容器 egress
  事实未变)、llm `model_absent`(模型驻留未变)——本切片不声称任何
  provider 就绪;
- `release_ready=false` / `production_ready=false` 恒不变;不运行
  provider-smoke export/aggregate、不改任何 gate JSON、不复用历史
  证据;
- helper 的 enforce 能力在 Round 1 仅经离线测试验证(修正轮后
  **28 项**,含严格 UTF-8 fail-closed 与原子替换写/失败保真),对真实
  env 文件的 enforce 是 Round 2 获准动作;dry-run 对真实文件只读
  验证已如实留档(§3,含 exit-code 行);
- **修正轮(amend 进同一 commit)如实声明**:①§3 worktree dry-run
  退出码由误记的 exit 0 更正为实测 exit 1;②严格 UTF-8 fail-closed、
  原子替换写、失败保真三个写纪律行为系 supervisor 修正要求,与
  Round 1 其余交付一并入档(§2);③全部验证套件按修正轮重跑计数
  (§4)。

## 6. 建议 Round 2 live 恢复序列(待 supervisor 获准,本切片不执行)

1. supervisor 获准后,对主 checkout 真实 env 文件执行
   `python tools/ops/searxng_egress_recovery.py --env-file
   infra/env.production-recovery`(enforce,幂等;先 `--dry-run`
   复核计划);
2. 获准窗口内 recreate searxng 容器使新 egress 事实生效(如
   `docker compose -f infra/docker-compose.yml --env-file
   infra/env.production-recovery --profile search up -d searxng`
   ——以 production_recovery 既有 up 口径为准;此为唯一的容器生命
   周期动作,需显式获准);
3. 复核:宿主 `curl 'http://127.0.0.1:8878/search?q=<probe>&format=json'`
   非空结果(China Bing 直连引擎应聚合返回);
4. `provider-smoke-preflight` 复跑确认 search 槽位 ready(llm 槽位另需
   operator 加载 `aios-qwen3.5-9b-4096` 驻留,M14-117 同款动作);
5. 全槽位 ready 后按 M14-147 任务书的 §3 注入配方执行
   provider-smoke-export(search/local-voice/llm)+
   aggregate(`--voice-mode local`),产出当前证据。
