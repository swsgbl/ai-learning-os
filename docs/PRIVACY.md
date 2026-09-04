# 隐私披露（Privacy Disclosure）

> 面向学生与家长。本文件说明 AI Learning OS 把哪些数据保存在哪、什么内容会发送到云端、以及对应的开关。
> 每一条声明都绑定真实的配置项或自动化测试——声明与实现的一致性由 `tests/test_privacy_disclosure.py` 持续锁定（映射见文末「声明-证据」表）。
> 部署形态前提：生产本地版把全部服务（数据库 / 对象存储 / API / 网页）跑在你自己的电脑上（见 `docs/ONBOARDING.md` 第 1 步）。

## 一、你的数据存在哪（本地保存边界）

| 数据 | 存在哪 | 什么时候离开本机 |
|---|---|---|
| 课程内容、试卷、答题记录、考试成绩 | 本机 PostgreSQL 数据库 | 从不（单机部署，无出站通道） |
| 上传的文件（讲义 / 试卷等） | 本机对象存储，按内容哈希命名（不含学生信息） | 从不 |
| 语音转写文本 | 本机数据库 `voice_transcripts` 表 | 见第二节语音链路 |
| 原始语音录音 | 默认不保存——转写完成后立即丢弃，并在接口响应里留 `audio_stored=false` 痕迹 | 从不 |
| 原始语音录音（显式开启时） | 本机对象存储，按内容哈希命名 | 从不 |
| 搜索的查询词与结果 | 本机数据库 `search_queries` 表（可审计） | 见第二节检索链路 |
| 评阅判定与规则版本 | 本机数据库（判分记录） | 从不 |

开启保存原始录音的开关：`PRIVACY_STORE_AUDIO`（默认 false）。开启后录音按内容哈希写入本机对象存储，与数据库记录互相可回查。

## 二、什么内容会发送到云端（云端处理边界）

路由由三个隐私模式开关控制（local / hybrid / cloud）：

| 开关 | 控制链路 | local | hybrid | cloud |
|---|---|---|---|---|
| `VOICE_MODE` | 语音（听写 + 朗读） | 全本地 | 听写本地、朗读云端 | 全云端 |
| `SEARCH_MODE` | 检索 | 本地语料检索 | 同 local（检索暂无混合形态） | 云端 web 检索 |
| `MODEL_ROUTE` | 模型路由（预留） | 全本地 | 部分云端 | 全云端 |

语音链路出站内容逐模式说明（hybrid 语义：语音原文不出本机，仅朗读文本出站）：

| 模式 | 听写（语音→文本） | 朗读（文本→语音） | 出站内容 |
|---|---|---|---|
| local | 本地 | 本地 | 无 |
| hybrid | 本地 | 云端 | 朗读文本（不含语音原文） |
| cloud | 云端 | 云端 | 语音音频 + 朗读文本 |

- 显式 `ASR_PROVIDER` / `TTS_PROVIDER` 覆盖模式默认选择；想用云端但未配置云端端点时，系统降级本地并在接口响应里透出 `fallback` 标记——不虚报实际链路。
- 云端端点配置项：`ASR_CLOUD_ENDPOINT`、`ASR_CLOUD_API_KEY`、`ASR_CLOUD_MODEL`、`TTS_CLOUD_ENDPOINT`、`TTS_CLOUD_API_KEY`、`TTS_CLOUD_MODEL`。真实密钥规则：只放部署 secret 或本机 `.env`，不入库不入码。云端语音链路的真实端点冒烟（`bash infra/smoke_voice_cloud.sh`，需要部署 key 与一段真实短语音 WAV）是**显式运维动作**——会把该音频与 TTS 合成文本发送到所配置的云端端点（与 cloud 模式出站内容一致），输出只含 provider/长度/字节数等脱敏摘要（不打印 endpoint、key、音频路径或转写正文）。
- 检索出站：`SEARCH_MODE=cloud` 且 `SEARCH_CLOUD_ENDPOINT` 配置了合法的 http/https 端点时启用云端 web 检索（SearXNG-compatible JSON API——查询词会发送到该端点）；`SEARCH_CLOUD_API_KEY` 可选（无鉴权 SearXNG 不需要，设置时经鉴权头出示）。`SEARCH_MODE=local/hybrid`（检索路由本地，暂无混合形态）或 `PRIVACY_SEND_CONTEXT_TO_CLOUD=false`（隐私总闸）时，即使 endpoint/key 配齐也禁用 cloud-web，视图如实显示原因（不虚报可用）；本地语料检索恒可用且不出站。查询词、结果与弃用原因都落在本机库，可事后审计。
- 主观题判分：`RUBRIC_JUDGE` 默认 `keyword`——内置确定性判分，不出站；设为空时主观题全部进入人工复核（不判分、不出站）。
- 上下文出站总闸：`PRIVACY_SEND_CONTEXT_TO_CLOUD`（默认 true）控制是否允许把学习上下文发送到云端模型链路与云端 web 检索（检索查询词出站同受此闸）；当前状态在语音 providers 视图中透出，学生与家长可直接核对。
- 抓取频率上限：`FETCH_RATE_LIMIT_PER_MINUTE`（默认 30/分钟，per-IP 固定窗口），用于抓取外部网页前限流。

## 三、语音数据的具体策略

1. 转写文本恒存本机数据库，学生可回查（`GET /api/v1/voice/transcripts`）。
2. 原始音频默认不保存：接口响应 `audio_stored=false`（留痕，不静默）。
3. `PRIVACY_STORE_AUDIO=true` 时按内容哈希存本机对象存储，可回读，与转写记录互相可回查。
4. 语音请求体上限 20MB（超过拒绝，错误码 413）。
5. hybrid 模式下听写全程本地——语音原文（音频字节）不经过网络出站。
6. 语音耗时观测带服务端/客户端来源标记，观测数据不改变任何业务状态。

## 四、怎么自己核实（透明度）

核实的入口是两个 provider 视图端点（响应里没有任何密钥形态，密钥泄露回归在安全套件）：

```bash
# 语音链路：当前模式、实际 provider、fallback 标记、两个隐私开关状态
curl http://127.0.0.1:8000/api/v1/voice/providers

# 检索链路：各搜索源可用性与未启用原因
curl http://127.0.0.1:8000/api/v1/search/providers
```

逐项核对：

1. `voice_mode` 应等于你设置的 `VOICE_MODE`；
2. `asr` / `tts` 的 `provider` 与 `fallback` 如实反映实际链路；
3. `privacy_store_audio` / `privacy_send_context_to_cloud` 与 `.env` 一致；
4. transcribe 响应的 `audio_stored` 逐条可核（默认 false）；
5. search 视图里 cloud-web 未启用时 `enabled=false` 且 `unavailable_reason` 说明原因（本地路由 / 隐私总闸 / 端点未配置或非法）。

## 五、声明-证据映射（每条声明都有支撑）

| 声明 | 支撑 |
|---|---|
| 转写文本恒存本机库、可回查 | 追加式 repository + `tests/test_voice_providers.py` |
| 原始音频默认不保存（audio_stored=false 留痕） | `PRIVACY_STORE_AUDIO` 默认 false + `tests/test_voice_providers.py` |
| 开启保存时内容哈希寻址、可回读 | SHA-256 内容寻址 + `tests/test_voice_providers.py` |
| hybrid 听写本地、朗读云端、fallback 透出 | `app/voice/routing.py` + `tests/test_voice_providers.py` |
| cloud-web 未启用（本地路由/隐私总闸/未配置）不虚报可用；查询落库可审计 | `tests/test_search.py` |
| 视图无密钥形态 | `tests/test_security_suite.py` |
| 非法隐私模式拒绝启动 | `tests/test_config_privacy.py` |
| 端到端核实路径可用 | `tests/test_privacy_disclosure.py`（本守卫测试） |
