# M14-24 生产验收回填（production acceptance backfill）

分支 `docs/m14-24-production-acceptance`（基于 canonical `main@165ca5c`，即 PR #102
merge commit `165ca5caff9b48e8514a741e77fe3420b12b5fee`，本地 git 可验证）；本回填
回合 docs-only、独占 worktree，**按任务书不做本地 commit/push/PR**——变更以未提交
工作树交付，由 supervisor 审查后处理。本回合零生产触碰：不启/停/重启任何生产容器
或 voice 进程，零 Docker/计划任务/服务变更，不修改任何生产代码、测试、`.verify/**`
与 `artifacts/**`（canonical 证据仅只读核对）；原始 WAV 绝不入库（仅存在于
gitignored `.verify/artifacts/m14-24-production-acceptance/`）；不输出任何密钥或
env 值；`production_ready=false` 不变。

背景：M14-24 开发切片（`fix/m14-24-voice-health-latency`，见
`docs/evidence/m14-24-voice-health-latency/README.md`）修复 cosyvoice bridge 的
liveness/readiness 分层与合成路径 GIL 有界化，合并时遗留边界「生效时机：本回合零
重启，bridge 新代码随 supervisor 获准窗口的下一次 voice 引擎重启生效」。本文回填
的正是 supervisor 于获准窗口完成的**受控生产重启与验收**事实（2026-09-13）。

## 1. 合并与 CI

| 项 | 事实 | 核对口径 |
|---|---|---|
| PR #102（M14-24 修复） | 已合并 main | supervisor 验收事实 |
| merge commit | `165ca5caff9b48e8514a741e77fe3420b12b5fee` | 本地 git 可验证（回填回合核对：parents `c4bda69`（PR #101 merge）+ feature head `91da769`，主题 "Merge pull request #102 from swsgbl/fix/m14-24-voice-health-latency"；origin/main == 该提交） |
| PR CI | **5/5 job success** | supervisor 验收事实（本回填回合未发起网络查询） |
| 合并后 main CI run | `34766519950`，**5/5 job success** | supervisor 验收事实（同上） |

## 2. 受控生产重启时间线（supervisor 获准窗口执行；本回填回合零生产操作）

时间口径 +08:00（监控工件命名与其内时间戳为 UTC，换算见 §6）：

| 步骤 | 事实 |
|---|---|
| 旧进程退出 | 旧 CosyVoice PID **7481** 优雅退出（即 PROJECT_STATUS M14-19 条目留档的 2026-09-13 05:54+0800 spawn 的 WSL 进程，跨文档一致） |
| 第一次新启动（失败） | PID **18447** 于 bootstrap 阶段中止——**PyPI 网络不可达**；此时模型/依赖缓存已完整，bootstrap 仍强制 pip 联网 → 暴露 **M14-25 阻塞点：离线重启不幂等（缓存完整仍强制 pip 联网）** |
| 最终启动（成功） | PID **19132** 于 **2026-09-13T23:51:10+08:00** 启动成功；端口 **8011**；service manifest **managed-running**；运行**合并后 `main@165ca5c` 的新 bridge**（含 `/health/live` 与有界分块转换） |

## 3. readiness / lifecycle 实测语义（新进程启动过程）

- bridge **端口先监听**（进程在服务）；
- 模型 **loading 阶段 `/health` 返回 503**（诚实降级，不假装就绪）；
- 模型加载完成后 **`/health` 返回 200 ready**（体 `{"status":"ok","model":"Fun-CosyVoice3-0.5B-2512"}`）；
- **`/health/live` 全程 200**，体 `{"status":"ok","liveness":"alive","readiness":"ready"}`（见 §5 探针实测）。

口径：**liveness ≠ readiness**——`/health/live` 200 只证明进程活着（恒 200、零锁、
不碰模型），readiness 以 `/health`（或 live 体中的 `readiness` 字段）为准；不得把
liveness 说成 readiness。

## 4. 真实 TTS 冒烟（新进程、合并后代码）

| 项 | 值 |
|---|---|
| 耗时 | **18467 ms** |
| 输出大小 | **405164 bytes** |
| SHA-256 | `C54E871D6FD12022085713D2A52DD3ED0483FF4C3A30A643D23958EC42E33DE3` |
| 文件头 | `RIFF…WAVE`（WAV 容器） |

原始 WAV 仅保留于 gitignored `.verify/artifacts/m14-24-production-acceptance/
tts-production.wav`，**不入库**（`.verify/` 在 `.gitignore` 第 16 行）。回填回合
对该文件独立重算字节数与 SHA-256，逐项一致。

## 5. 合成期间并发健康探针（与一次合成并发执行）

- 探针总数 **28**（`/health/live` ×14 + `/health` ×14），**非 200 计数 = 0**；
- `/health/live`：**max 409.981 ms**，mean ≈ **38.35 ms**；
- `/health`：**max 9.708 ms**，mean ≈ **4.61 ms**；
- 全部样本低于监控 GET 超时 5s（`DEFAULT_REQUEST_TIMEOUT_SECONDS`）口径；
- 并发期间的合成输出：**585644 bytes**，SHA-256
  `92E2A5FADD4DCEF4ACE0D700123A8B3F5EF7373D0E05C2F0B74797865E8DD798`
  （`tts-concurrent.wav`，同样仅存 gitignored `.verify/`，不入库）。

对照修复前形态（合成负载下 cosyvoice-health 1001ms 乃至 >5s 超时，见
m14-24-voice-health-latency 事件表）：本轮合成负载下 `/health` 回到个位数毫秒，
`/health` 503/200 契约未变——与开发切片的监控预期一致（见 §8 边界，单轮口径）。

## 6. 新进程后自然监控观察（00:00 与 00:15 两轮，非手动触发）

计划任务 `AIOS-Monitoring-Pipeline` 在新进程 ready 后的**自然轮**（工件名与内嵌
时间戳为 UTC：00:00 轮 `2026-09-13T16:00:23Z` 起 = 本地 2026-09-14
00:00:23+08:00，晚于 23:51:10+08:00 进程启动约 9 分钟，时序吻合；00:15 轮
`2026-09-13T16:15:57Z` 起 = 本地 00:15:57+08:00，supervisor R1 修正补充）。
**两轮自然观察均正常**：

| 工件 | 轮次（+08:00） | 结论 |
|---|---|---|
| `pipeline-20260913-160025`（monitor→history→insights） | 00:00 | overall_status=**ok**，三步全 ok、exit 0，lock acquired/released=true |
| `monitor-20260913-160023` | 00:00 | **34 检查 ok=34 / warn=0 / critical=0**；六容器全部 healthy；**restart 增量全 0**（M14-23 增量语义，基线 `monitor-20260913-154501`；api 累计 restart_count=1 不变、当轮 delta=0）；端点全 200：web-root 4.618ms、web-login 1.659ms、api-health 11.709ms、**funasr-health 3.346ms**、**cosyvoice-health 12.417ms** |
| `pipeline-20260913-161559`（monitor→history→insights） | 00:15 | overall_status=**ok**，三步全 ok、exit 0，lock acquired/released=true |
| `monitor-20260913-161557` | 00:15 | **34 检查 ok=34 / warn=0 / critical=0**；六容器全部 healthy；**restart 增量全 0**（滚动基线 `monitor-20260913-160023`，api 累计 restart_count=1 不变、当轮 delta=0）；端点全 200：web-root 7.731ms、web-login 12.052ms、api-health 12.362ms、**funasr-health 13.161ms**、**cosyvoice-health 13.243ms** |

口径：这是**新进程 ready 后 00:00 与 00:15 两轮自然观察**，证明合并后代码在
生产自然调度下连续两轮整体 ok——**两轮自然观察仍不能证明长期稳定性**（见 §8）。

## 7. 证据文件与回填回合只读核对

gitignored canonical 工件（仅安全摘要入库；无绝对路径/容器 ID/密钥）：

| 文件（repo 相对路径） | 字节 | SHA-256 |
|---|---:|---|
| `.verify/artifacts/m14-24-production-acceptance/tts-production.wav` | 405164 | `C54E871D6FD12022085713D2A52DD3ED0483FF4C3A30A643D23958EC42E33DE3` |
| `.verify/artifacts/m14-24-production-acceptance/tts-concurrent.wav` | 585644 | `92E2A5FADD4DCEF4ACE0D700123A8B3F5EF7373D0E05C2F0B74797865E8DD798` |
| `.verify/artifacts/m14-24-production-acceptance/health-probes.json` | 4529 | `2D9AACFF9FC6057FEC8C757BBEABD24B3EA864B7DA7D8686E1BF0D62F1B2AB3A` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260913-160023.json` | 15639 | `4D6086524F0B86ED28D332BFB0C0D59AA639455328A0EC7C73F169057C7D6C46` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260913-160023.md` | 4031 | `C117E657E38CAB1FBC4FEF2A9F727EA96B0356FC4A99679D8CEDB5A4F7A2EA0` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260913-160025.json` | 5243 | `1A858FFB820C517350C81ADEFF7E2DA695780306ACFBC65C3D114A010F970FFF` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260913-160025.md` | 2141 | `FDDB7A81693F5FC4A948BF2B4F33A3131DA2440AE7F5DFFB89BFAF6390CCBB64` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260913-161557.json` | 15643 | `42419EDC92D612FC37D81FDD22E57D552CA425AC96D5C5F77670F77ADEBD60FC` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260913-161557.md` | 4033 | `50BD90287DF7F4D94C87C47E917317D25909F0A6944C8B6116114891952BF2F1` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260913-161559.json` | 5243 | `5F7AD9778782C106CD26F3EC80E1FB27F696B7CA9AC06C46B459B354A6260EF6` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260913-161559.md` | 2141 | `5ED2F13388DF700A7FF9DF631ED8EC748700139AE6416E2E95F5F3D1162AF3E8` |

（哈希为回填回合以本地 `sha256sum` 现场计算——输出小写、此处统一转大写呈现，
十六进制大小写等价。）

本回填回合实际执行的只读核对（全部通过）：

- **git 谱系**：origin/main == `165ca5c`；`git show` 核对 merge commit 完整哈希、
  双亲（`c4bda69` + `91da769`）与 PR #102 合并主题；
- **工件哈希链**：两个 WAV 的字节数、SHA-256、RIFF/WAVE 文件头独立重算，与
  supervisor 留档值逐项一致；上表 5 份辅助工件哈希为回填回合现场计算；
- **探针统计复算**：`health-probes.json` 28 样本逐条清点（live 14 + health 14、
  非 200 为 0）；live 求和 536.875/14 = ≈38.35ms、health 求和 64.504/14 =
  ≈4.61ms、max 409.981/9.708ms——与验收口径逐项吻合；`/health/live` 体为
  alive+ready（§3 语义依据）；
- **监控工件核对**：00:00 与 00:15 两轮 monitor/pipeline 共四份 `.md`（及对应
  JSON）逐项核对 overall、34 检查计数、六容器 healthy、restart delta、两端点
  延迟（3.346/12.417ms 与 13.161/13.243ms；00:15 轮滚动基线
  `monitor-20260913-160023`）；
- **未执行**：任何网络 CI 查询、任何生产服务/容器/voice 进程操作、任何计划任务
  命令（含只读 status——计划任务与 CI 事实为 supervisor 验收时点结果）、任何
  monitor/history/insights/pipeline 执行面。

## 8. 边界（诚实口径）

1. **单机、单轮冒烟 + 两轮自然观察**：一次真实 TTS 冒烟 + 一次合成期并发探针 +
   新进程后 00:00 与 00:15 两轮自然观察——**两轮自然观察仍不能证明长期稳定
   性**，也不是高精度 benchmark；不构成对语音链路 production readiness 的
   宣称，`production_ready=false` 不变。
2. **funasr-health 上游事件循环阻塞仍未修复**（M14-24 已确认的残余风险，第三方
   包 `funasr==1.4.15`）：ASR 负载期间 funasr-health 仍可能出现秒级延迟乃至
   warn/incomplete——本轮 3.346ms 是空闲/轻载时点事实，**属预期行为、非回归**。
3. **CosyVoice bootstrap 离线重启不幂等是下一个功能切片（M14-25）**：PID 18447
   中止实证「模型/依赖缓存完整时 bootstrap 仍强制 pip 联网」——PyPI 不可达时新
   进程无法启动；需 supervisor 授权后修复（缓存完整时跳过联网安装路径）。
4. 本回填为 docs-only：不改任何代码/测试/阈值/基础设施，不遮蔽任何告警语义；
   变更未提交，待 supervisor 审查后处理。
