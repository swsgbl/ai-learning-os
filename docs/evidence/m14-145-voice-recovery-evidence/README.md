# M14-145 语音生产恢复证据切片(docs-only:FunASR/CosyVoice 受控恢复 + 双回合恢复编排 + 本地真实语音冒烟)

- 切片性质:**docs-only 证据记录**——只新增本 README 并最小更新两处账本(`docs/ROADMAP.md`、`docs/PROJECT_STATUS.md`),零代码、零测试、零运行时触碰
- 仓库基点:worktree `m14-145-voice-recovery-evidence`,分支 `docs/m14-145-voice-recovery-evidence`,基于 `15bfab9`(PR #231 合入 = M14-144 落线),单 local commit、不 push、不开 PR
- 原始证据源(均在主 checkout 的 gitignored 区,**不入 git**,本 README 仅摘录判定事实):
  - `.verify/artifacts/m14-145-voice-recovery-verification/local-voice-smoke.json`(308 bytes)
  - `artifacts/recovery/recovery-20260926-002818.log`(enforce 回合,1550 bytes)
  - `artifacts/recovery/recovery-20260926-003322.log`(follow-up dry-run 回合,1233 bytes)
- 结论:任务书给定验收口径下应记录的事实**全部如实落档**(见下);本切片本身不做 PASS/FAIL 运行时判定——所有运行时结论均来自已发生的验证回合,本切片仅转录与绑定哈希

## 一、enforce 恢复回合(2026-09-26 00:28 +08,`recovery-20260926-002818.log`)

执行主体:`tools/ops/production_recovery.py` enforce(project `aios-m14-03-production-rehearsal`,profile `local`,env `env.production-recovery`)。日志逐行事实:

| # | 事实 | 日志原文依据 |
|---|---|---|
| 1 | docker engine 就绪(server 29.7.2) | `docker engine: 就绪(server 29.7.2)` |
| 2 | compose config 静态校验通过 | `compose config: OK(--quiet 静态校验通过)` |
| 3 | compose 服务(`--profile local`)恰 6 项:api、livekit、minio、postgres、redis、web | `compose services(--profile local): api, livekit, minio, postgres, redis, web` |
| 4 | 生产 pin 一致键 **9/9**(值不回显;比对对象 api/web/livekit 在线容器,逐键一致) | `pin: 一致键 9/9(值不回显)` / `pin: OK(九键齐全:env 无缺键/占位,且与在线容器逐键一致)` |
| 5 | core compose 栈 **6/6 服务 healthy/running——健康栈无需 up(enforce 与 dry-run 同语义跳过)**,即**未执行 up、未 recreate 任何容器** | `stack health: 6/6 服务 healthy/running——健康栈无需 up(dry-run 与 enforce 同语义跳过)` |
| 6 | FunASR 回合前 `state=stopped health=None`(无 manifest、无监听)→ **仅经受控语音工具 start**(rc=0)→ 复查 `managed-starting` → 决策 leave(本轮已 start,bootstrap 进行中,预期无需干预) | `funasr: state=stopped health=None → start(stopped(无 manifest 无监听)——经受控工具 start)` 等 4 行 |
| 7 | CosyVoice 同路径:`stopped` → **仅经受控语音工具 start**(rc=0)→ `managed-starting` → leave | `cosyvoice: …` 对称 4 行 |
| 8 | 回合结果 **OK(恢复完成/无需恢复,全部核查通过)** | 末行 `=== 结果: OK(…)==` |

两引擎的启动**只**发生在受控托管路径(恢复编排调用受控 start;manifest 由工具写入),未出现任何手动/裸进程启动路径。

## 二、follow-up dry-run 回合(2026-09-26 00:33 +08,`recovery-20260926-003322.log`)

同一编排的只读 dry-run(全程只读):

| # | 事实 | 日志原文依据 |
|---|---|---|
| 1 | pin 一致键 9/9(值不回显),与在线容器逐键一致 | 同上口径 |
| 2 | 只读健康快照:api、livekit、minio、postgres、redis、searxng、web **全部 healthy** | `stack health 快照(只读): {"api": "healthy", …, "searxng": "healthy", "web": "healthy"}` |
| 3 | stack health **6/6 服务 healthy/running**——健康栈无需 up | 同 enforce 语义 |
| 4 | **FunASR `state=managed-running health=200` → leave(managed-running 且 /health 200——不触碰(healthy untouched))** | 第 10 行原文 |
| 5 | **CosyVoice `state=managed-running health=200` → leave(healthy untouched)** | 第 11 行原文 |
| 6 | 回合结果 **OK** | 末行原文 |

如实记录:dry-run 健康快照含 `searxng: healthy`,该服务**不在**本恢复编排 `--profile local` 的 6 服务列表内(6/6 判定只对列表内服务);快照原文如此,本切片不做扩展解读。

## 三、验证回合运行时状态(任务书给定的操作者执行摘要)

以下运行时探测值来自任务书给定的 M14-145 验证回合执行摘要——**本 docs-only 切片未重放任何探测**(重放即触碰运行时,超出切片边界):

- API `/health` 返回 **200**;Web `/` 返回 **200**
- sidecar watchdog(voice health sidecar)`running-healthy`,其 **18010 与 18011 health 端点均返回 200**

## 四、本地真实语音冒烟(`local-voice-smoke.json`,本切片亲自读取并核验)

JSON 原文字段(schema `provider-smoke-evidence-v1`,step `local-voice-smoke`):

| 字段 | 值 |
|---|---|
| `executed` / `result` / `exit_code` | `true` / `pass` / `0` |
| `started_at` | `2026-09-25T16:34:50.110619+00:00`(= 2026-09-26 00:34:50 +08) |
| `completed_at` | `2026-09-25T16:35:07.964286+00:00`(= 2026-09-26 00:35:07 +08) |
| `duration_ms` | **17853** |

- **文件 SHA-256(本切片 sha256sum 实测):`11c8d9d3ca7cc0e16c32401d51ff955328d7d0d18ee07fe9cdfb4d4291e6f9dc`**(308 bytes 文件;与任务书给定大写形式 `11C8D9D3CA7CC0E16C32401D51FF955328D7D0D18EE07FE9CDFB4D4291E6F9DC` 逐字符一致,仅大小写归一)
- 执行摘要细分(任务书给定):**ASR 1473ms;TTS 15921ms,产出 241964 bytes RIFF WAV**。算术自洽:1473 + 15921 = 17394 ≤ 17853(差额 459ms 为健康探测与样例读取等步内开销)
- 工具契约佐证(源码 `tools/voice/smoke_local_voice.py`,本切片只读核对):冒烟走主 API 同款 provider adapter 真实 HTTP、**无任何 mock**;ASR 要求非空转写文本,TTS 要求响应带 `RIFF/WAV` 头(`response_format=wav`);任一探针 FAIL 即 exit 1。因此 JSON `result=pass` + `exit_code=0` 蕴含上述检查全部通过
- 时序链自洽:enforce 00:28 受控启动 → 引擎模型加载 → dry-run 00:33 双引擎已 `managed-running` /health 200 → 冒烟 00:34:50–00:35:07 全绿(三个工件的时间戳与文件 mtime 互相吻合)

## 五、命令与证据 provenance

| 证据 | 产生命令(已发生的验证回合,非本切片执行) | 落盘位置(主 checkout gitignored 区) |
|---|---|---|
| enforce 恢复日志 | `tools/ops/production_recovery.py` enforce 回合 | `artifacts/recovery/recovery-20260926-002818.log` |
| follow-up dry-run 日志 | `tools/ops/production_recovery.py --dry-run` 回合 | `artifacts/recovery/recovery-20260926-003322.log` |
| 本地语音冒烟 JSON | `python tools/voice/smoke_local_voice.py`(经 provider-smoke-evidence 流程落盘) | `.verify/artifacts/m14-145-voice-recovery-verification/local-voice-smoke.json` |
| SHA-256 | `sha256sum`(本切片实测,见第四节) | 记录于本 README |

provenance 分层:第一节、第二节、第四节 JSON 字段与哈希 = 本切片**亲自读取工件核验**;第三节与第四节 ASR/TTS 细分数字 = **任务书给定的操作者执行摘要**(工具 stdout 字段契约与 `smoke_local_voice.py` 源码输出格式一一对应:`latency_ms` / `audio_bytes` / `riff_wav`)。

## 六、诚实边界(本切片明确不声称)

1. **不声称长期稳定性**:一次受控恢复 + 一轮 dry-run 全绿 + 一轮冒烟通过,**不证明**引擎/栈的长期稳定运行,不构成任何 soaken/stability 声明
2. **不声称公网就绪**:全部探测为本机/内网口径,公网可达性与 TLS/外网暴露状态未被本证据覆盖
3. **不声称 provider 完整性**:仅本地 FunASR ASR 与本地 CosyVoice TTS 两条链路被冒烟;云端/其他 provider 不在本证据范围
4. **不声称 release approval**:release approval 恒 human-only,从未发生;全局 `production_ready=false` 不变
5. **不声称 M14-141 alert scheduler 已安装**:drift-watch alert scheduler 的安装与 webhook 送达仍阻塞于操作者 secret 文件,M14-144 之后状态不变,本切片未对其做任何动作或声明
6. 第三/四节的执行摘要值非本切片重放验证——如需复核须另行授权的运行时验证回合

## 七、边界遵守声明

本切片 docs-only:未启动、停止或重启任何服务、容器、代理、模拟器、引擎或调度器;未执行任何 Docker/compose/Task Scheduler 命令;未读取或打印任何含密钥的 env 文件(pin 一致性 9/9 的"值不回显"是恢复工具自身日志语义,两份日志原文即如此);未改动 `production_ready` 或任何全局状态;全部证据源均为只读访问,证据本体不入 git(仅本 README 摘录)。
