# M14-33 真实本地语音 ASR/TTS 端到端冒烟验收（恢复后真实 adapter 链路，一次性执行）

- 验收窗口：2026-09-15 23:51:34 – 23:52:05（+08:00），**一次性执行（无重试），通过即停**
- 仓库基点：`main@7eb7404480f17d9d8014f62abd21012154664a14`（PR #110 merge，本地 git 可验证；任务前后同一 HEAD，porcelain_count=0，tracked 全程 clean）
- 执行方式：仓库既有 `tools/voice/smoke_local_voice.py`（主仓 canonical venv），走主 API 同款 adapter 链路（`app.voice.providers` 的 `LocalFunAsrAsrProvider` / `LocalCosyVoiceTtsProvider`），真实 HTTP、无 mock；样例为既有 `artifacts/voice/smoke/asr_sample_zh.wav`（未下载）
- 原始证据：主仓库 gitignored `.verify/m14-33-real-voice-e2e-smoke/`（ACCEPTANCE-REPORT.md、EXECUTION-LOG.md 与 `raw/` 13 个文件）——不入 git，本 README 仅摘录判定事实
- 结论：**PASS（5/5，本任务验收口径）**；判定依据见下表，每一项均有 raw 原始证据支撑

## PASS 判定表

| # | 验收项 | 要求 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | 脚本终句 + rc | stdout 末行 `ALL LOCAL VOICE SMOKE CHECKS PASSED` 且 rc=0 | 末行即该句；`smoke-rc.txt`=0；stderr 空（0 字节） | ✅ |
| 2 | ASR 真实转写 | 非空真实中文转写，provider=local-funasr | provider=`local-funasr`，latency 2900ms，transcript `'希望你以后能够做的比我还好哟'`（42 字节 UTF-8，非空，与官方样例已知内容一致）；confidence=0.000 如实记录——本地 bridge 不返回置信度，脚本判据不含该项，判定依据是非空真实转写而非置信度 | ✅ |
| 3 | TTS 真实音频 | 非空 RIFF/WAV，provider=local-cosyvoice | provider=`local-cosyvoice`，latency 27066ms（CPU 本轮观测值），audio_bytes=241964，`riff_wav=yes` | ✅ |
| 4 | 服务 PID/健康不变 | 三进程 PID 与 started_at 前后不变、健康探针 200 | FunASR PID 3445（started_at 22:56:56+08 不变）、CosyVoice PID 3851（22:59:24+08 不变）、sidecar PID 701（16:50:38+08 不变）；执行前后（23:50:41 与 23:52:27）5/5 探针 200；无进程重启，状态仍 managed-running / running-healthy | ✅ |
| 5 | tracked clean | 前后 HEAD 一致、porcelain=0 | 前后均 `7eb7404480f17d9d8014f62abd21012154664a14`，porcelain_count=0 | ✅ |

## 执行事实

- 命令：主仓 canonical venv `.venv/Scripts/python.exe tools/voice/smoke_local_voice.py`；端点用脚本默认 `http://127.0.0.1:8010/v1` / `http://127.0.0.1:8011/v1`；env 仅显式设置既有样例 WAV 路径（`ASR_SMOKE_AUDIO`）与子进程作用域 NO_PROXY（127.0.0.1/localhost，防 loopback 走代理）——未设置任何 ASR/TTS API key、未设置期望文本 `ASR_SMOKE_EXPECTED_TEXT`
- 执行窗口：2026-09-15T23:51:34+0800 → 23:52:05+0800（约 31 秒）；rc=0、stderr 空
- ASR：health 200；provider=`local-funasr`；latency 2900ms；transcript `'希望你以后能够做的比我还好哟'`
- TTS：health 200；provider=`local-cosyvoice`；latency 27066ms（CPU 首次合成本轮观测值，不代表稳态延迟）；241964 bytes；RIFF/WAV 判定 yes

## 样例音频指纹（执行前后一致，未下载）

- 路径：`artifacts/voice/smoke/asr_sample_zh.wav`（既有文件；脚本经 `ASR_SMOKE_AUDIO` 显式指定，未走下载分支）
- 字节数：334138
- SHA256：`c7b31d6dbe7cc6a716dded00550db5b50940bf209e424e4ad207b12e657c8ff6`（执行前 23:50:48 与执行后复核 23:52:27 两次一致）
- 头 12 字节 hex `524946463219050057415645` = `RIFF`…`WAVE`（有效 RIFF/WAV）

## 健康探针与 sidecar 实际 bind

- direct：`http://127.0.0.1:8010/health`（funasr，`{"status":"ok","device":"cpu","models_loaded":["sensevoice"]}`）与 `http://127.0.0.1:8011/health`（cosyvoice，`{"status":"ok","model":"Fun-CosyVoice3-0.5B-2512"}`）均 200（curl 零代理）
- sidecar 按 canonical manifest **实际 bind `172.25.7.64`**（非 127.0.0.1）：`18010/health`、`18011/health`、`18011/health/live` 均 200（末者体 `{"status":"ok","liveness":"alive","readiness":"ready"}`）
- 以上 5 探针执行前后全部 200

## 前置条件：自然监控连胜（非本任务动作）

- 冒烟前置要求自然监控连续 ≥3 轮 ok：history.jsonl 尾部连续 4 轮 `overall=ok`（34/0/0）——本地 23:05/23:15/23:30/23:45（= 15:05:22Z/15:15:01Z/15:30:12Z/15:45:01Z）
- 此前 14:45:01Z、15:00:01Z 两轮 critical 为 M14-31 恢复窗口内的历史事实
- **边界**：以上全部为计划任务自然轮，**非 M14-33 触发**；M14-33 未执行 monitoring_pipeline、未触发计划任务、未修改任何 canonical 监控工件（仅只读快照 `monitoring-winstreak.txt`）

## Codex 独立复核

Codex 独立验收亦 PASS：对验收报告、原始 rc/stdout、样例哈希、三进程 PID/started_at、五个实时健康端点、secret 扫描均复核通过。证据秘密扫描 13 个 raw 文件 **0 命中**（模式含 sk-/ghp_/gho_/AKIA/PRIVATE KEY 与 api_key/password/secret/token/authorization/bearer 赋值形态；仅计数，未打印匹配内容）。

## 诚实边界（未覆盖与不宣称）

1. 本验收为**单次、单样例、单文本**的真实 adapter 端到端冒烟（主 API 同款 provider 链路、真实 HTTP）——**不覆盖**：真实客户端/LiveKit 会话链路、移动端录音与播放、长稳（soak）、并发负载、真实客户端负载形态
2. 单次冒烟 ≠ 长期稳定性，**不构成全局 `production_ready=true` 的依据**；全局 `production_ready=false` 不变
3. ASR confidence=0.000（bridge 不返回置信度）——如实记录，判据是非空真实转写而非置信度
4. TTS latency 27066ms 为 CPU 上本轮观测值，不代表稳态延迟
5. 过程披露：port-guard 钩子两次提示 8010/8011 被 Windows 侧转发进程（PID 35952）占用——命令本身为对既有服务的健康探测/调用（不监听端口），按钩子自带指引忽略，未做任何处置
6. M14-33 全程零生产触碰：未 start/stop/restart FunASR/CosyVoice/sidecar/Docker/WSL/wslrelay/CC Switch/本地代理/计划任务；未修改任何 tracked 文件与 canonical 监控工件；未 commit/push/建 PR（remote 收口由 M14-34 回填执行，本 README 即其交付物）
