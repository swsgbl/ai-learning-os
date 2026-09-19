# M14-65 云端 TTS 音色配置证据（开发切片）

- **切片**：M14-65，开发切片（代码 + 测试 + compose + 冒烟脚本）。零生产触碰、零真实 provider 调用、不 push、不建 PR、不合并不触其他 worktree。
- **分支/worktree**：`feature/m14-65-cloud-tts-voice`（worktree `D:\AI Learning OS\ai-learning-os-worktrees\m14-65-cloud-tts-voice`），基于 `origin/main@cdd27ae90414ea2639086c9a8d111b9081d5bf40`，单 local commit。
- **本 README 为本切片唯一入库证据文件**。

## 1. 功能上下文与官方事实口径

M4-02 引入的云端 TTS 槽位（OpenAI 兼容 `POST {endpoint}/audio/speech`）此前请求体只带
`model` / `input` / `response_format`——不含 `voice` 字段，BigModel 官方端点无法按预置音色
发音。本切片闭合该缺口：新增端到端 `AIOS_TTS_CLOUD_VOICE` 设置（默认 `tongtong`）并透传进
云端 TTS 请求体。

官方事实（开发前 web 检索核实，BigModel 官方文档口径）：

| 项 | 事实 | 来源 |
|---|---|---|
| 端点 | OpenAI 兼容 `POST {endpoint}/audio/speech` | docs.bigmodel.cn（BigModel 语音合成 API 文档） |
| 请求体 | `{"model": ..., "input": ..., "voice": ..., "response_format": ...}` | 同上 |
| 模型 | `glm-tts` | 同上 |
| voice 字段 | 必填；默认 `tongtong`（彤彤）；预置音色枚举 `tongtong` / `chuichui` / `xiaochen` / `jam` / `kazi` / `douji` / `luodo` | 同上；deepchat.dev BigModel TTS 佐证 |
| input 上限 | 最长 1024 字符 | 同上 |
| 音频格式 | `wav`（`response_format=wav` 受支持） | 同上 |

仓库既有 `TTS_CLOUD_MODEL` 默认 `tts-1`（OpenAI 侧口径）不在本切片改动范围——按部署
runbook，模型名由部署变量显式注入（BigModel 部署设 `AIOS_TTS_CLOUD_MODEL=glm-tts`）。

## 2. 实现面（8 个文件）

### 源/配置（5）

1. **`services/api/app/core/config.py`**：Settings 新增 `tts_cloud_voice: str = "tongtong"`
   （注释锚定官方事实与作用域：仅 cloud-openai-tts 消费）；新增
   `strip_tts_cloud_voice` field_validator（剥空白，与 `auth_cookie_name` 同款归一化；
   空白串归一为空串——这是**合法值**：请求不带 `voice` 字段，由端点侧默认音色决定）。
2. **`services/api/app/voice/providers.py`**：共用实现 `_openai_compatible_synthesize` 加
   可选参 `voice: str | None = None`——非空才写入请求体（`if voice: payload["voice"] =
   voice`），空/None 完全省略键；`CloudOpenAiTtsProvider.__init__` 加第 4 位参数
   `voice: str | None = None`（构造时 `(voice or "").strip()`），`synthesize` 透传
   `voice=self._voice`。默认值 `tongtong` **只存在于 Settings**（单一来源），provider 纯
   透传。失败文案与 WAV 校验（`RIFF`/`WAVE` 头）不变。
3. **`services/api/app/api/routes/voice.py`**：`_build_tts` 云端分支构造
   `CloudOpenAiTtsProvider(..., settings.tts_cloud_voice)`。
4. **`infra/docker-compose.yml`**：api 服务 environment 新增
   `TTS_CLOUD_VOICE: ${AIOS_TTS_CLOUD_VOICE:-tongtong}`（`:-` 语义与
   `TTS_CLOUD_MODEL` 一致：未设或置空均回落默认）。
5. **`infra/smoke_voice_cloud.sh`**：可选覆盖清单从两个扩为三个——新增
   `TTS_CLOUD_VOICE`（默认 `tongtong`，置空回落该默认，不回显）；TTS 探针构造 provider
   时以 `voice=(os.environ.get("TTS_CLOUD_VOICE") or "").strip() or "tongtong"` 注入。
   脱敏口径不变（不打印 endpoint/key/鉴权头）。

### 测试（3）

6. **`services/api/tests/test_voice_providers.py`**：新增 M14-65 节 5 个测试（默认值与
   归一化、请求体带 voice、None/空/纯空白省略 voice 键、本地 CosyVoice 请求体不含
   voice、`_build_tts` 布线含本地分支无音色槽位断言）。
7. **`services/api/tests/test_smoke_voice_cloud_script.py`**：`SMOKE_ENV_KEYS` 追加
   `TTS_CLOUD_VOICE`（追加在 index ≥ 7 之后，`REQUIRED_ENV_KEYS = SMOKE_ENV_KEYS[:7]`
   七必填切片不变）；STUB_ENV 加占位值；文本契约断言脚本含 `TTS_CLOUD_VOICE` 与
   `tongtong`。
8. **`services/api/tests/test_compose_profiles.py`**：`PROVIDER_PASSTHROUGH_ENV_KEYS`
   追加 `AIOS_TTS_CLOUD_VOICE`；synthetic 注入断言逐项透传；默认渲染断言
   `TTS_CLOUD_VOICE == "tongtong"`（与 Settings 应用默认一致）；新增置空回落测试
   （`:-` 插值语义锁定——容器形态无「不带 voice」的空值形态）。

### 关键设计决策

- **本地 TTS 逐字节不变**：`LocalCosyVoiceTtsProvider` 构造与调用零改动（签名
  `(endpoint, api_key="", model=..., timeout=120.0, transport=None)` 原样），共用实现
  `voice` 默认 None → 请求体省略键——本地 CosyVoice 请求与改动前逐字节相同，云端音色
  绝不发给本地 provider（测试 `test_local_tts_request_body_does_not_include_voice`
  锁定）。
- **空值语义分层如实**：Settings 显式置空 → 请求不带 `voice`（端点侧默认音色）；compose
  `${VAR:-tongtong}` 置空 → 渲染为 `tongtong`（容器形态**没有**省略 voice 的表示形态）；
  smoke 脚本置空 → 回落 `tongtong`。
- **默认单一来源**：`tongtong` 只在 `config.py` Settings 默认值出现（compose/smoke 的
  `:-tongtong` 是各层不引入第四开关的窄口径回显，测试锚定与应用默认一致）。

## 3. 测试证据（主仓 canonical venv）

| 套件 | 命令（cwd=worktree 根） | 结果 |
|---|---|---|
| provider 聚焦 | `pytest services/api/tests/test_voice_providers.py -q` | **35 passed**（30 基线 + 5 新增） |
| smoke 脚本契约 + 隐私守卫 | `pytest services/api/tests/test_smoke_voice_cloud_script.py services/api/tests/test_config_privacy.py services/api/tests/test_privacy_disclosure.py -q` | **13 passed** |
| compose 渲染 | `pytest services/api/tests/test_compose_profiles.py -q` | **8 passed, 1 skipped**（skip = `AIOS_COMPOSE_SMOKE=1` 门控真启动冒烟，环境性跳过） |
| 全量 services/api | `pytest services/api/tests -q` | **3660 passed, 33 skipped**（247.01s，exit 0） |
| lint | `ruff check services/api/app services/api/tests` | **All checks passed!** |
| diff 卫生 | `git diff --check` | 无输出（干净） |

## 4. 诚实边界

- **本开发切片零真实 provider 调用**：全部云端请求经 `httpx.MockTransport` 伪造端点断言
  请求体；真实 BigModel 端点（`glm-tts` + `tongtong`）冒烟留给运维显式执行
  `bash infra/smoke_voice_cloud.sh`（脚本已支持 `TTS_CLOUD_VOICE` 覆盖）。不声称真实
  端点已验证。
- **compose 门控真启动冒烟未跑**（`AIOS_COMPOSE_SMOKE=1` 全栈 build+healthy 冒烟为环境
  门控跳过项，非本切片引入）。
- **PRIVACY_RELEVANT 不变**：`voice` 是端点侧发音参数、不改变数据流向（仍只有文本出站
  到云端 TTS），非隐私边界，故未加入 `test_privacy_disclosure.py` 的隐私披露白名单，
  `docs/PRIVACY.md` 不涉及。
- **`license_report.py` 未动**：许可证清单工具与音色配置无关，不在任务书范围。
- **`TTS_CLOUD_MODEL` 默认值不改**：仍是 `tts-1`（OpenAI 侧口径）；BigModel 部署按
  runbook 显式注入 `AIOS_TTS_CLOUD_MODEL=glm-tts`。改默认模型超出本切片范围。

## 5. 验证执行记录（commit 前）

主仓 canonical venv：`D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe`，cwd=worktree 根。

- 聚焦三组：35 passed（provider，2.51s）/ 13 passed（smoke 契约 + config 隐私 + 隐私披露，1.75s）/ 8 passed + 1 skipped（compose 渲染，2.56s，docker compose CLI v5.5.0 可用）。
- 全量 `services/api/tests`：**3660 passed, 33 skipped in 247.01s**（exit 0；skip 均为环境门控项——compose 真启动、PG 隔离库等）。
- `ruff check services/api/app services/api/tests`：All checks passed。
- `git diff --check`：无输出（无尾随空白/冲突标记）。
