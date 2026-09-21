# M14-88 证据:本地 provider-smoke 三件套恢复(真实重跑)

- 切片类型:运维恢复切片(诊断 + 真实重跑 + 文档;零应用代码/配置改动)
- worktree:`D:\AI Learning OS\ai-learning-os-worktrees\m14-88-provider-smoke-recovery`
- 分支:`ops/m14-88-provider-smoke-recovery`,基于 main
  `f2a21a6266ffde71d21303dbf357eae324394563`(PR #175 merge,精确基点;
  初始基点 `5ff1e68`/PR #174,独立验收后经受控 rebase 前移——仅解决
  三台账 docs 冲突,零代码变更、canonical raw 证据零改动)
- 交付形态:单 local commit(rebase 后经 amend 保持单提交),不 push、不建 PR
- 本 README 为唯一入库证据文件;原始证据(gitignored)位于
  `.verify/artifacts/m14-88-provider-smoke-recovery/`(12 文件,sha256/bytes
  锚点见 §4)
- 前置事实(诚实声明):**前一任务 M14-87(audit-chain current gate)已随
  PR #175 合并 main `f2a21a6`,本分支经受控 rebase 将其树内变更完整并入**
  (冲突解决保留 M14-87 全部已合并内容:M14-87 台账/路线/CHANGELOG 条目与
  其 evidence README 原样在树);rebase 前本分支曾基于 `5ff1e68`(
  M14-87 尚未并入)为过程事实,当前声明一律以 rebase 后树为准。

## 1. 目标与背景

M14-83 生产只读证据记录 provider-smoke 门为 blocked(search/llm 失败、无
恢复记录)。本切片目标:诊断并恢复本地 provider-smoke 三件套(search /
local-voice / llm),以仓库既有工具真实重跑并产出可审计的**当前**证据;
绝不虚构通过、绝不复用旧证据冒充当前。

## 2. 诊断(root cause,全部本机实证)

### 2.1 search——容器无恙,宿主代理进程掉了

**症状**:生产容器 `aios-m14-03-production-rehearsal-searxng-1`(healthy,
`127.0.0.1:8878->8080`)对
`http://127.0.0.1:8878/search?q=OpenAI&format=json` 返回 HTTP 200 但
`results=0`,`unresponsive_engines` 全军覆没(brave/duckduckgo/google cse/
wikipedia/wikidata 均 `HTTP connection error`)——容器回环健康检查通过、
上游出站全断。

**诊断**:
1. 容器 env 核验(仅过滤非敏感代理变量,未读取/未回显任何 secret):
   `HTTP_PROXY=http://host.docker.internal:7892`、
   `HTTPS_PROXY=http://host.docker.internal:7892`、
   `NO_PROXY=127.0.0.1,…`——**仓库支持的 `AIOS_SEARXNG_HTTP(S)_PROXY`
   机制(M14-66)部署时已正确注入**,容器侧无需任何变更。
2. 宿主 7892 探测:无监听、无 sing-box/xray/mihomo 进程——**代理目标
   `host.docker.internal:7892` 是死的**,容器出站经死代理 → 全部连接错误。
   根因不在容器、不在仓库机制,在宿主代理进程未运行。

**恢复(零容器触碰)**:启动宿主 sing-box(用户级进程)。首次尝试
`vpn-manager.ps1 on` 回退到了 xray(其订阅节点已失效,经 7892 实测
google/bing 均 HTTP 000),遂直接启动本机已安装的
`~/bin/sing-box/sing-box.exe run -c ~/.config/sing-box/config.json`
(version 1.13.21,7892/10808 双端口监听)。经 `curl -x 127.0.0.1:7892`
实测 google HTTP 200 / bing HTTP 302。代理起来后同一端点复测:
**results=28**(openai.com / platform.openai.com 等真实结果),残余
`duckduckgo CAPTCHA`、`wikidata timeout` 属引擎侧正常波动,不构成阻塞。
未 stop/restart/rebuild/delete 任何生产容器,未改任何容器 env,未读取
任何 secret(含 `infra/env.production-recovery*`,从未打开)。

### 2.2 llm——Windows 原生安装未启动;WSL 报告是干扰项

**症状**:Windows `127.0.0.1:11434` 无监听;WSL Ubuntu 侧
`ollama: command not found`、`systemctl is-active ollama` = inactive、
`is-enabled` = not-found。

**诊断(实际安装/启动方式)**:
1. WSL Ubuntu 内**从未安装过 Ollama**(二进制不存在,systemd 单元
   not-found)——WSL 的 inactive/not-found 报告是干扰项,不是故障点。
2. 实际安装是 **Windows 原生**:
   `G:\AI_MIGRATED\C\Users\hongfu\AppData\Local\Programs\Ollama\ollama.exe`
   (client 0.33.2;`--version` 时告警 "could not connect to a running
   Ollama instance" 印证服务未起);用户级 env
   `OLLAMA_MODELS=G:\AI_MIGRATED\D\Ollama\.ollama`,该目录 manifests 下
   已有 `qwen3.5/9b`(base 模型在场,无需拉取)。
3. 当时无 ollama.exe 进程 → 11434 无监听即此因。

**恢复(允许动作:启动缺失服务)**:后台启动
`ollama serve`(此前无任何用户进程,不存在"替换/停止既有进程");
`netstat` 确认 `127.0.0.1:11434 LISTENING`,`/api/version` =
`{"version":"0.33.2"}`。随后运行仓库幂等供给脚本:

```
powershell -NoProfile -ExecutionPolicy Bypass -File infra/provision_ollama_model.ps1
# [provision-ollama] Ollama 可达: version=0.33.2
# [provision-ollama] base 模型存在: qwen3.5:9b
# [provision-ollama] 别名 aios-qwen3.5-9b-4096 已存在且参数一致
#   （num_ctx=4096, base=qwen3.5:9b）——幂等跳过 create
# [provision-ollama] RESULT: PASS
```

固定别名 `aios-qwen3.5-9b-4096` 经脚本独立 show 复核参数一致(M14-71
时代创建的别名仍在且未漂移)。未触碰 CC Switch、代理配置、FunASR、
CosyVoice 或其他 WSL 服务。

### 2.3 local-voice——本就在场,零触碰

FunASR `127.0.0.1:8010/health` = HTTP 200、CosyVoice
`127.0.0.1:8011/health` = HTTP 200(既有 WSL 侧服务,PID 15952)。
**未修改、未重启任何语音进程**——只做真实冒烟(§3)。

## 3. 真实重跑(仓库工具链,全部当前执行)

执行窗口:**2026-09-21T18:40:14Z–2026-09-21T18:41:03Z**(本地
2026-09-22 02:40–02:41 GMT+8)。环境:worktree 从零
`uv venv .venv --python 3.12`(CPython 3.12.14)+
`uv pip install -r services/api/requirements.txt(-dev.txt)`。
调用形态:`services/api` cwd 下
`python -m app.ops.cli provider-smoke-export <provider> --output <canonical>`
+ `provider-smoke-aggregate`;stdout 全程 tee 留存(canonical 目录)。

| 步 | 结果 | 关键事实(工具自身输出) |
|---|---|---|
| search(`provider-smoke-export search`) | **pass**,exit 0,duration 3630ms | `provider=cloud-web results=5 query_len=21`;endpoint=生产 rehearsal SearXNG 宿主 8878(只读使用,零容器触碰);started_at 2026-09-21T18:40:14.568358+00:00 |
| local-voice(`provider-smoke-export local-voice`) | **pass**,exit 0,duration 3663ms | ASR `provider=local-funasr latency_ms=186 transcript_bytes=42`(真实中文转写);TTS `provider=local-cosyvoice latency_ms=3166 audio_bytes=226604 riff_wav=yes`;`asr=PASS tts=PASS` |
| llm(`provider-smoke-export llm`) | **pass**,exit 0,duration 12372ms | 模型 `aios-qwen3.5-9b-4096`(经 `http://127.0.0.1:11434/v1`,本地无鉴权,LLM_API_KEY 为非敏感占位字面量):简单补全 content 12 chars(空 content 守卫通过)+ rubric judge `achieved=[True, True] confidence=1.0` |
| 聚合(`provider-smoke-aggregate --voice-mode local`) | **PASS**,exit 0 | `voice=pass search=pass llm=pass`;providers 三项 executed=true result=pass,evidence_step 对应 local-voice-smoke/search-smoke/llm-smoke;generated_at 2026-09-21T18:41:03.868171+00:00 |

四个证据 JSON 均为仓库工具程序化生成(schema
`provider-smoke-evidence-v1`),无任何手改;ASR 样例音频为官方
CosyVoice 仓库 asset 缓存复用(334,138 bytes,经 `ASR_SMOKE_AUDIO`
显式指定主仓 gitignored 缓存路径,规避在线下载依赖)。

## 4. 证据文件与完整性锚点(canonical 目录 12 文件)

目录:`.verify/artifacts/m14-88-provider-smoke-recovery/`(gitignored;
另含工具生成的 `SHA256SUMS.txt` 索引,自不含自哈希):

```
6a8e7c4e085644537ba2410c5ff4c6c88a12bfb6c9f9efcf56abea7023a87fc8   20  diagnosis-ollama-version.json
35345c1a0ca8eaeab41e2328fbda38c6ad868765b39687e602386f4c3f2fac8e  25889  diagnosis-searxng-after-recovery.json
4e460877e1be913baebddc4d2c532fec2c6806919650a79fc746b5056c85972c   327  diagnosis-searxng-before-recovery.json
afe35709734ce46cbbd568b8cc8fadcfdf5363906e3133978f7f9d558a5c13c1   300  llm-smoke.json
f4a9e47f02e9a6c3c741f7dd1e0d57338af975f68f681556a5a6f4b6091e9e34   307  local-voice-smoke.json
87e7678721e4a3334d6f17e83e5c91ee7e3f4294e10f4f8cbdd3acb27fa7bd1a   564  provider-smoke.json
6aad25503685039ad2fb6e3b30623d960fa82ef8f44ba775b4f46e1df840adf9   302  search-smoke.json
46455e726266f5cd6a50f27f4de5e3c96e0353bb7ad6e0397af82d25369e6263   113  diagnosis-searxng-container-proxy-env.txt
713d213e24da6c885815df65ecafceaa55719a5d1970dc3d083ff6eaf544fc68   865  stdout-aggregate.txt
39976d032548f1a1f2721e3a2c03e9e6940c731b6534c60a85ca9d9775087a95  1273  stdout-llm.txt
e4f62f6f3dba1e503e026c9d258d59a28c29a9590d2c4f20047eb5c41d8dcd09  1603  stdout-local-voice.txt
9cc37cdae0174727ea0e2dd971baa06903f4dd6542055167a68941a152638252  1211  stdout-search.txt
```

## 5. 验证

- 聚焦契约测试(本切片证据链依赖的全部工具面,worktree venv):
  `tests/test_provider_smoke_evidence.py + test_release_readiness.py +
  test_searxng_local_provider.py + test_smoke_search_script.py +
  test_smoke_voice_local_script.py + test_provision_ollama_model_script.py`
  → **235 passed**(3.36s)。
- `ruff check .`(services/api,ruff.toml 口径)→ All checks passed。
- `git diff --check` → 干净。
- 生产容器生命周期零变更:api/web/postgres/redis/minio/livekit/searxng
  全程 Up 14 hours(health 状态未受本切片影响),未发生任何
  stop/restart/rebuild/delete,未改任何生产 secret env。

## 6. 范围边界(不冒充的部分)

1. **宿主代理是易失性用户进程**:生产 searxng 出站依赖
   `host.docker.internal:7892` 有活进程(M14-66 部署设计)。本证据只在
   执行窗口内成立;sing-box 停止则 search 再断(容器与仓库机制无缺陷)。
   长期方案(如代理常驻服务化)超出本切片,留待运维决策。
2. **Ollama 以会话后台进程运行**:`ollama serve` 未注册为 Windows 服务,
   重启/注销后需再启动(命令见 §7);恢复手段可复现、无状态损失(模型
   与别名持久在 `OLLAMA_MODELS` 目录)。
3. **provider-smoke.json 只覆盖 local 拓扑**(voice_mode=local);cloud
   语音轨道未执行,`release-readiness` 聚合未在本切片重跑(任务范围仅
   四命令)。release_ready 相关结论不在本切片宣称范围。
4. **前一任务 M14-87 已随 PR #175 合并 main `f2a21a6`,本分支经受控
   rebase 并入其树内变更**(冲突解决完整保留 M14-87 已合并内容;rebase
   仅涉三台账 docs 冲突,零代码变更、canonical raw 证据零改动,单提交
   经 amend 保持)。
5. vpn-manager `on` 的 xray 回退节点已失效(实测 HTTP 000);本切片实际
   生效的是手动直启的 sing-box 1.13.21。该事实如实记录,不掩盖。
6. 零生产部署/切换;未触碰 soak 历史、release 审批、生产日志;除代理
   变量名外未 inspect 任何容器 env 值,`infra/env.production-recovery*`
   从未打开。

## 7. 恢复手册(可复现)

```bash
# search:确认容器代理 env 已注入(部署事实),再确保宿主代理在跑
docker inspect aios-m14-03-production-rehearsal-searxng-1 --format '{{json .Config.Env}}' | tr ',' '\n' | grep -i proxy
netstat -ano | grep 7892 || ( ~/bin/sing-box/sing-box.exe run -c ~/.config/sing-box/config.json & )
curl -s -m 40 'http://127.0.0.1:8878/search?q=OpenAI&format=json' | head -c 200   # 应见非空 results

# llm:启动 Windows 原生 Ollama(若 11434 无监听),再幂等供给别名
netstat -ano | grep 11434 || ( 'G:/AI_MIGRATED/C/Users/hongfu/AppData/Local/Programs/Ollama/ollama.exe' serve & )
powershell -NoProfile -ExecutionPolicy Bypass -File infra/provision_ollama_model.ps1

# 三件套真实重跑(证据导出到 canonical 目录)
cd services/api
SEARCH_CLOUD_ENDPOINT=http://127.0.0.1:8878 PYTHON=.venv/Scripts/python.exe \
  <venv>/python -m app.ops.cli provider-smoke-export search --output <EV>/search-smoke.json
ASR_SMOKE_AUDIO=<官方样例wav缓存> PYTHON=.venv/Scripts/python.exe \
  <venv>/python -m app.ops.cli provider-smoke-export local-voice --output <EV>/local-voice-smoke.json
LLM_ENDPOINT=http://127.0.0.1:11434/v1 LLM_API_KEY=<非敏感占位> LLM_MODEL=aios-qwen3.5-9b-4096 \
  PYTHON=.venv/Scripts/python.exe <venv>/python -m app.ops.cli provider-smoke-export llm --output <EV>/llm-smoke.json
<venv>/python -m app.ops.cli provider-smoke-aggregate \
  --search <EV>/search-smoke.json --voice <EV>/local-voice-smoke.json --llm <EV>/llm-smoke.json \
  --voice-mode local --output <EV>/provider-smoke.json
```
