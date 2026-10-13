# M14-259 证据：FastAPI lifespan 拥有 VoiceRuntimeManager（fake-only 接线）

- 分支 `api/m14-259-voice-runtime-lifespan`（worktree
  `ai-learning-os-worktrees/m14-259-voice-runtime-lifespan`），基于
  current main `981c40c8cfcaad4608b35438baefbfcc7fbf500a`（PR #345
  merge，即 M14-258）。执行前 porcelain 干净（基点本地 git 可验证）。
- 动因：M14-258 交付了管理器的安全关闭/排空边界，但尚无所有者把
  它接进应用生命周期。本切片把 `VoiceRuntimeManager` 接入既有
  FastAPI lifespan——显式所有权、`app.state` 暴露、确定性关停，
  即使随后进行正常的数据库清理。**仍为 fake-only 接线**：不注册
  生产订阅者工厂、不连 LiveKit、不加 HTTP 路由、不改 readiness
  语义、不做流式 ASR/VAD/barge-in/pause-resume。

## 非声明（explicit non-claims）

- **无生产订阅者工厂注册**：lifespan 只构造管理器并接线
  `app.state`，`start(key, factory)` 面向后续切片，本切片零调用
  （测试中仅合成 fake）。
- **无 voice HTTP 路由新增/改动**：voice_subscription/
  voice_runtime/voice_tokens/voice.py 零触碰；
  `/readyz` 检查与 readiness 语义零变化。
- **无真实 LiveKit 连接、无流式 ASR / VAD / barge-in /
  pause/resume**；零真实网络、零运行时服务启停、零真实 DB 凭据。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。
- settings、容器、compose 文件、部署配置零改动。

## 交付面

**`services/api/app/main.py`（保守修改，+14/−1）**：

- 导入 `VoiceRuntimeManager`（既有域模块，零行为改动）。
- 既有 `lifespan` 内、启动依赖装配完成后、`yield` 之前：构造
  **恰一个** `VoiceRuntimeManager` 并赋值
  `app.state.voice_runtime_manager`。快照状态经管理器自身
  `snapshot()` 透出——**不加包装状态**。
- **确定性关停**：`yield` 包进 `try/finally`，`finally` 中
  `await voice_runtime_manager.close()` 在 lifespan 关停时**总是**
  执行（应用阶段异常路径同样执行）。关停顺序——**先关语音
  管理器**（M14-258 close 即排空），**再**停 parse worker、释放
  数据库引擎；既有数据库清理语义保持。**绝不**在 close 之前另调
  `stop_all()`（那会是双排空接口）。
- **所有权显式**：每 `create_app()`/lifespan 调用恰一实例；无
  模块级实例、无全局单例、无线程/嵌套事件循环/`asyncio.run`/
  进程池/调度器/后台任务。

**`services/api/tests/test_voice_runtime_lifespan.py`（新增，6 项
fake-only 契约测试）**：全合成 fake，不连真实 LiveKit 房间/语音
引擎/DB 凭据/外部网络。

1. **进入前未接线**：lifespan 进入前 `app.state` 上无
   `voice_runtime_manager`。
2. **活跃期 open / 退出后 closed（同一实例）**：内存模式
   TestClient 活跃 lifespan 期间管理器存在且 `snapshot()["state"]`
   == "open"（并打真实 `/health` 200 证明应用阶段可用）；退出后
   **同一实例**报 "closed"（身份一致，非新建）。
3. **SQLite 启动模式**：同样接线（open）并在退出时关闭
   （closed）。
4. **实例独立**：两个独立 `create_app()` 得到不同管理器实例，
   各自 closed。
5. **订阅者恰停一次**：活跃期间经管理器 `start(key, factory)`
   注册的 fake 订阅者，应用关停时 `stop_calls` 恰为 1，条目清零。
6. **关停幂等**：lifespan 退出后对已关闭管理器再次 `close()`
   不重复停止该订阅者（stop_calls 仍 1），返回记录的排空结果
   `{stopped: 1, failed: 0}`。
7. **源码卫生**：`main.py` 无模块级/全局 `VoiceRuntimeManager`
   （唯一构造点缩进在 lifespan 闭包内，恰一处）；文本序断言
   close 先于 `worker.stop()` 先于 `engine.dispose()`；无
   `stop_all(` 调用形态（杜绝双排空）。

（第 5、6 项合并于同一测试函数，合计 6 个测试函数覆盖 8 项
要求面。）

## 验证（2026-10-13，worktree 内真实执行；全部零网络/零运行时服务）

| 命令 | 结果 |
| --- | --- |
| `... -m pytest services/api/tests/test_voice_runtime_lifespan.py -q` | **6 passed** |
| `... -m pytest services/api/tests/test_voice_runtime_lifespan.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_api_observability.py services/api/tests/test_migrations.py -q` | **75 passed**（6+44+21+4；M14-258 管理器基线 44 零回归 + lifespan 邻居零回归） |
| `... -m ruff check services/api/app/main.py services/api/app/domain/voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py services/api/tests/test_voice_runtime_manager.py` | All checks passed |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生：secret 形态 0 命中；JWT `eyJ` 命中均为合成假值；
私有绝对路径 0；U+FFFD 0；readiness 置 true 0。

## 后续空间（不在本切片）

- 生产订阅者工厂注册（受控运行时切片）；帧消费者；VAD/
  barge-in/pause-resume；真实 LiveKit 服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-259（M14-258 转前一
  任务快照），里程碑段追加 M14-259 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-259 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-259 条目（含非声明）。
