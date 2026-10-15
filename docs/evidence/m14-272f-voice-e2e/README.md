# M14-272f 证据：服务端语音链路真实端到端验收（LiveKit+FunASR 实证）

- 分支 `api/m14-272e-voice-entrypoint-pipeline`（worktree
  `ai-learning-os-worktrees/m14-272e-voice-entrypoint-pipeline`），
  基于 current main `4a1f6db3`（PR #363 merge，即 M14-272e）。
- **结论：两轮端到端 verdict=PASS**——M14-267~272e 交付的服务端
  语音链路在真实 LiveKit + 真实 FunASR 上完整打通，并在验收中
  发现/修复三处 fake-only 盲区缺陷。

## 验收拓扑（一次性，全部本地）

```
发布者（LiveKit Python SDK，24 kHz 中文 WAV 样本）
  → 一次性 dev LiveKit 容器（livekit/livekit-server --dev，127.0.0.1:17880）
  → 服务端订阅者（voice_entrypoint 管线，uvicorn 127.0.0.1:18000，
     env：LIVEKIT_SERVER_URL/KEY(devkey)/SECRET(dev dev 模式) +
     VOICE_ASR_ENDPOINT=http://127.0.0.1:8010/v1）
     ├─ SplitVoiceSink（计量 + 有界通道 256 帧/512 KiB）
     └─ FunAsrBatchAdapter（close 冲刷 → WAV 包装 → 真实 HTTP）
  → FunASR（funasr-server SenseVoiceSmall，本机 8010 受控启动）
  → 服务端权威 transcript
```

样本：`artifacts/voice/smoke/asr_sample_zh.wav`（既有真实中文语音
样本——M4-02 冒烟遗留，float32/24 kHz，harness 内 float32→int16
转换）。

## 验收结果（2026-10-16 04:49 +049 04:53 +08 两轮一致）

```json
{
  "api_health": 200,
  "open": {"status": 201, "identity": "server-sub-f621bc62"},
  "token": {"status": 200},
  "publish": {"published": true, "bytes_sent": 288000, "sample_rate": 24000},
  "state_before_close": {"status": 200, "buffered_frames": 256, "buffered_bytes": 245760, "fsm_state": "SESSION_READY"},
  "close": {"status": 200},
  "transcript": {"segment_count": 1, "texts": ["希望你以后能够做到比"]},
  "verdict": "PASS"
}
```

LiveKit 服务器侧佐证（容器日志，验收时刻）：订阅者
`server-sub-aec41898` 真实订阅学生轨 `TR_AMmBUnjc588XKG`，downstream
RTP `packetsSeenPrimary=287 / bytes=70194 / packetsLost=0`——媒体
真实转发零丢包。

## 验收中发现并修复的三处 fake-only 盲区缺陷

1. **AudioFrameEvent 解包**（`voice_runtime.py`）：livekit>=1.1 的
   `AudioStream` 迭代产出 `AudioFrameEvent`（帧在 `.frame`）——
   M14-256 适配层直转导致下游 `frame.data` AttributeError、帧全部
   被 M14-269 通道计为无效（服务端零帧入界；LiveKit 侧却有完整
   RTP 流——正是端到端验收的价值）。修复：`_consume` 解包
   `getattr(item, "frame", item)`（+回归测试：新形态解包/帧对象
   直转不复制；旧形态测试原样通过——两形态兼容）。
2. **int16 memoryview 计账**（`voice_frame_channel.py`）：SDK 帧
   常为 int16 memoryview——`len()` 是样本数、`bytes()` 归一后翻倍，
   入账（样本数）/出账（字节数）不对称出现**负缓冲字节账目**
   （实测 -33600）。修复：`on_frame` 计账恒用归一后字节数
   （+回归测试：int16 memoryview 480 样本 → 960 字节、drain 后
   账目对称归零）。
3. **WAV 容器包装**（`voice_pipeline_entry.py`）：通道交付裸 PCM
   而转写端点按 wav 解析——`sync_funasr_transcriber` 发送前按
   `sample_rate`/`channels`（参数可注入，默认 48k mono 16-bit）
   包装 RIFF 容器头。

## 契约一致性修正

- live 路由前缀统一 `/api/v1/voice/live-sessions`（验收实测发现
  与主 voice 路由 `/api/v1/voice` 不同层——统一后三端对齐面一致）。

## 新增工具

- `tools/voice/e2e_voice_pipeline_probe.py`：可复现验收 harness
  （鲁棒 RIFF 解析含 float32→int16；open/token/publish/close/
  transcript 全链断言；JSON 证据输出；`--api/--livekit/--sample`
  参数化）。

## 验证（2026-10-16，worktree 内真实执行）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| e2e probe（两轮） | **verdict=PASS ×2**（结果一致） |
| `-m pytest`（十九文件语音回归）`-q` | **494 passed**（M14-272e 基线 492 + 2 真实形态回归，零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（触及文件） | All checks passed |
| `-m compileall`（触及文件） | OK |
| `git diff --check` | 干净 |

## 环境清理（验收后恢复原状）

- 一次性 dev LiveKit 容器 `aios-e2e-livekit` 已删除；
- FunASR 经 `voice_service_control.py stop` 受控停止（manifest
  清理）；
- 验收 API 进程已停止；
- **生产演练栈 8 容器零触碰**（验收前后 `/health` 200 一致）。

## 非声明（explicit non-claims）

- **服务端链路验收 ≠ 浏览器端验收**：发布者用 SDK 代浏览器
  麦克风——真实浏览器（Web UI 对齐 live 契约 + getUserMedia）与
  Android 真机/Harmony 分批属后续验收批。
- transcript 单段批式（close 冲刷路径）——实时流式 pump 调度需要
  时另切片。
- 验收环境 devkey/dev 容器（LiveKit --dev）——零真实凭据零生产
  凭据读取。
- 不声明公网语音/TURN/TLS 就绪；`production_ready=false` /
  `release_ready=false` / `public_ready=false` 不变；计数不变
  implemented 60 / partial 3。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-272f（M14-272e 转
  前一任务快照，含 PR #363 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-272f 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-272f 条目（含非声明）。
