# M14-24 语音健康端点延迟/劣化生产阻塞修复（voice health latency）

分支 `fix/m14-24-voice-health-latency`（基于 canonical `main@c4bda69`，即 PR #101
merge commit，本地 git 可验证）；本 Claude 开发回合独占 worktree，仅做**一个本地
commit**，supervisor 审查与 remote 发布（push/PR/合并）在其后进行。全程零生产
触碰：不停/启/重启任何服务、容器或 voice 进程，零 Docker 变更，零计划任务变更，
零监控管道执行；canonical 监控工件仅只读核对；不输出任何密钥或 env 值；测试全部
合成/fake。`production_ready=false` 不变。

## 触发事实（自然监控只读工件，2026-09-13 UTC）

| 监控轮 | overall | funasr-health (8010) | cosyvoice-health (8011) | web/api 端点 |
|---|---|---|---|---|
| 13:30:02 / 13:45:01 | ok | 200，3.0–3.6ms | 200，3.8–13.9ms | 全部 200，1–15ms |
| 14:00:02 | **warn** | 200，**3619.495ms** | 200，12.719ms | 全部 200，1.6–4.9ms |
| 14:15:01 / 14:30:02 | **incomplete（partial）** | **timeout（TimeoutError，>5s）** | **timeout（TimeoutError，>5s）** | 全部 200，2–15ms |
| 14:45:02 | **warn** | 200，**4813.322ms** | 200，**1001.190ms** | 全部 200，2–189ms |

六受管容器全程 healthy/running、restart 增量 0（容器面与本次劣化无关）；监控
端点 GET 默认超时 5s（`DEFAULT_REQUEST_TIMEOUT_SECONDS`）——14:15/14:30 两轮
两个 voice 端点双双超时 → 采集器 failed → `partial=true` → `incomplete`。

## 根因（代码级，两引擎）

### funasr-health（8010，上游包——本仓不修改第三方文件）

上游 `funasr==1.4.15`（bootstrap 固定版本）`funasr/bin/_server_app.py`：

- `/v1/audio/transcriptions` 与 `/asr` 均为 `async def` 处理器，但推理是**同步
  CPU 阻段**（`_process_fallback` → `model.generate(...)`，SenseVoice `--device
  cpu`）**内联跑在 uvicorn 单 worker 的事件循环线程上**；
- `/health` 同为 `async def`、同一事件循环——任一转写（或惰性模型加载，如首
  个 `spk=true` 请求触发 `_load_spk_model`）进行中，事件循环整体被阻塞，健康
  请求无法被接收/应答，直到推理完成；
- 空闲轮 3ms ↔ 负载轮 3.6s/4.8s/>5s 的形态与该结构完全吻合（多请求在单循环
  上串行排队，健康排在队尾）。

**处置边界**：这是第三方包内部设计，本仓纪律不修改第三方包文件（M14-03 Round 2
同款边界）；在不改变 8010 部署拓扑（引入 facade/sidecar）的前提下无窄修复。
本切片**不改 funasr 数据面**（避免给生产 ASR 主路径引入新故障域），将其记为
**残余风险 + 后续切片建议**（见下）。

### cosyvoice-health（8011，本仓 `tools/voice/cosyvoice_openai_bridge.py`）

- `/health` 本身只读 `engine.phase`（零锁、不碰模型）——设计正确，保留原样
  （503/200 既有契约逐字节不变）；
- 真实劣化源：合成路径把「`torch.cat` + **整张量单次 `.tolist()``** 放在推理锁
  内执行——`tolist` 是**单次非抢占 C 调用**，长音频（≈2s @24kHz ≈ 5×10⁴ 样本，
  长文本合成远超）一次转换可长时间独占 GIL，健康线程（sync 处理器跑在线程池）
  被饿死——观测到合成负载下 1001ms；WSL2 VM 级 CPU 争抢（ASR+TTS 同 VM）时
  进一步越过 5s 超时；
- 缺陷二：进程缺**与模型状态无关的轻量 liveness 端点**——调用方无法区分
  「进程死」与「模型忙/未就绪」，与「昂贵模型能力」混同在一个信号上。

## 修复（仅本仓自有代码，窄作用域）

`tools/voice/cosyvoice_openai_bridge.py` 三处改动：

1. **liveness/readiness 分离**：新增 `GET /health/live`——进程在服务即 200
   `{"status":"ok","liveness":"alive","readiness":"<loading|ready|failed>"}`；
   恒不取 `engine.lock`、不触发任何模型工作；readiness 如实透出（诚实降级：
   加载失败时 liveness 仍 200、readiness=failed 可见）。
2. **readiness 向后兼容**：`/health` 状态码与 JSON 体逐字节保持 M14-01 既有
   形状（loading→503 `{"detail":"cosyvoice model is loading"}`、failed→503
   `{"detail":"cosyvoice model unavailable"}`、ready→200 `{"status":"ok",
   "model":...}`）——既有消费方（监控、voice_service_control 的
   `models_loaded`/`model` 解析、冒烟脚本）零改动。
3. **有界分块转换**：新增 `_tensor_to_samples(flat, *, chunk_samples,
   yield_fn=time.sleep)`（duck-typed，torch 张量与测试 fake 同形）+ 常量
   `SAMPLE_CHUNK_SIZE = 50_000`（≈2s @24kHz，单块转换毫秒级）；`synthesize`
   重构——推理锁**只覆盖模型前向**（GPU/kv 不并发语义边界），`torch.cat`/
   展平/样本转换移出锁外并走分块路径，**块间显式 `yield_fn(0)`（释放 GIL
   一拍）**：长合成不再以单次非抢占 C 调用独占解释器，健康线程可被调度；
   `chunk_samples ≤ 0` 一律 ValueError（fail-closed，绝不静默退回整段转换）。

不修改：监控工具与阈值（不遮蔽告警——warn 语义照旧，只是健康端点恢复应答
速度的事实）、funasr 数据面、compose 栈、bootstrap 脚本、任何第三方包。

## 测试（合成/fake，TDD RED→GREEN）

新增 `services/api/tests/test_voice_health_bridge.py`（14 项，零网络、零 SDK、
零生产触碰；TestClient 不进入 lifespan，ready/failed 态直接驱动
`app.state.engine.phase`，fake 张量 duck-typed）：

- **liveness/冷启动**：loading/failed/ready 三态 `/health/live` 恒 200、体固定；
- **向后兼容**：`/health` 三态状态码与体逐字节回归锁（不加键不改值）；
- **并发探测/超时**：测试线程显式持有 `engine.lock`（模拟合成进行中）时
  `/health` 与 `/health/live` 均即刻应答（同进程共享锁，确定性；计时上界对齐
  监控 5s 超时口径）；8 路并发 `/health/live` 全 200 且体一致；
- **有界分块**：250/100 → 恰 3 块、顺序保持、恰 2 次块间 yield(0)（末块后
  不 yield）；整除边界（40/10 → 4 块 3 yield）；空/短输入零 yield；
  `chunk_samples ≤ 0` ValueError；`SAMPLE_CHUNK_SIZE` 有界性（1000–100_000）
  与合成路径接线（文本契约）；
- **文本契约**：bridge 源码含 `/health/live`/`_tensor_to_samples`/
  `SAMPLE_CHUNK_SIZE` 锚点，无密钥形态。

RED 实证（实现前）：11 项失败且失败原因均为功能缺失——`/health/live` 404、
`_tensor_to_samples`/`SAMPLE_CHUNK_SIZE` AttributeError、文本锚点缺失；3 项
（`/health` 向后兼容回归锁）实现前即通过——**设计如此**（锁的是「不变的
契约」而非新行为）。GREEN：全数通过。

## 验证（本回合实际执行）

- 聚焦：`services/api/tests/test_voice_health_bridge.py` **14 passed**；
- 邻居回归：`services/api/tests/test_voice_local_scripts.py` **40 passed /
  1 skipped**（既有 bridge/脚本契约零回归）；合计 54 passed；
- `ruff check`（bridge + 新测试）与 `py_compile`（bridge）通过；
- `git diff --check` 干净；worktree 相对 `main@c4bda69` 恰一个本地 commit。

## 边界与残余风险（诚实口径）

1. **funasr-health 未修**（上游 `funasr==1.4.15` 事件循环内联阻塞，第三方包）：
   合并后 ASR 负载期间 funasr-health 仍可能再现秒级延迟乃至 5s 超时/incomplete
   ——**这不是本切片能消除的**。后续切片建议（需 supervisor 授权与专门验收）：
   ①升级/跟踪上游修复（uvicorn handler 线程池化）；②或在 8010 前引入本仓自有
   轻量 health facade（缓存式 readiness + 透明代理），并同步
   `voice_service_control` 生命周期契约——两者都超出本切片「窄/生产安全」边界。
2. **cosyvoice 残余争抢**：CosyVoice 第三方内部（frontend FST 文本正则、torch
   算子间 Python 循环）仍可能在极端 CPU 饱和下持有 GIL——本修复消除的是**本仓
   自有的**非抢占窗口（整张量 tolist/cat + 锁内转换），降低而非根除争抢。
3. **生效时机**：本回合零生产重启；bridge 新代码随 supervisor 在获准窗口的下
   一次 voice 引擎重启/部署生效。生效前的自然监控仍按旧代码形态记录。
4. **监控预期（生效后）**：cosyvoice-health 在合成负载期间应回到毫秒级基线
   （不再出现 ~1s 级延迟）；冷启动/加载失败期间 `/health` 仍诚实 503（不变）；
   funasr-health 行为不变（见 1）。监控阈值/语义零改动——任何 warn/incomplete
   仍如实入档。
5. 本切片不构成对语音链路生产就绪的宣称；`production_ready=false` 不变。
