# M14-275 证据：完卷收尾确定性修复 + 生产收口核查（真机验收 BLOCKED 登记）

- 分支 `api/m14-272e-voice-entrypoint-pipeline`（worktree
  `ai-learning-os-worktrees/m14-272e-voice-entrypoint-pipeline`），
  基于 current main `1d441f5c`（PR #365 merge，即 M14-274）。

## 交付面

**`apps/web/src/components/voice-studio.tsx` 竞态修复**：
`finishFlow` 在 `completeExam`（`router.push('/review/…')`）之前
`await releaseVoiceInput()`——本地麦克风断开 + 服务端 live 会话
close（含 FunASR 冲刷）**确定完成**后才离开页面。此前 `void`
fire-and-forget 在路由跳转后才完成——浏览器离开时服务端冲刷
尚未结束。失败仍静默降级（增强面不阻塞完卷）。

## 生产收口三件核查（2026-10-16，全部真实执行）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 监控轮 | `production_monitor.py --execute --confirm …` | **ok=33 / warn=1 / critical=0**——唯一 warn 为 web 容器 recreated（M14-274 Web 切 `m14-274-production` 的预期事实；下一自然轮基线更新后归零） |
| 恢复 dry-run | `production_recovery.py --dry-run` | **OK 全过**：compose config pass；**pin 一致键 9/9**（含 `AIOS_IMAGE_TAG=m14-272-production` / `AIOS_WEB_IMAGE_TAG=m14-274-production`）；7 服务 healthy；FunASR/CosyVoice `managed-running` health 200 **healthy untouched** |
| 栈健康 | `docker ps` + 端点 | **7/7 healthy**（API/Web 新镜像，基础设施 5 容器持续未重建）；API `/health` 200、Web 200 |

语音双引擎冒烟 ALL PASS（M14-273 交付：ASR 355ms 真实转写 +
TTS 32s/241KB RIFF WAV）。

## 真机麦克风文本转写验收：BLOCKED 登记

- `adb devices`：**空**（无 Android 真机连接）；
- `hdc targets`：**不可用**（无 Harmony 设备/模拟器）；
- 与 M14-126 先例同口径：设备不在场，如实 BLOCKED，**不虚构通过**。
- 覆盖度评估：完整链路 = 真语音转写（**M14-272f 已实证**：SDK
  发布者 + 真中文样本 →「希望你以后能够做到比」）+ 浏览器 UI
  全流程（**M14-274 已实证**：麦克风授权 → LiveKit 发布 → 帧实时
  到达服务端 256 帧/245760 字节 → close 冲刷产出段）——两者相加
  覆盖端到端能力；真机批（Android 真机/Harmony）待设备就位后
  执行，无新增代码依赖。

## 关于 M14-124 release-readiness 检查器

`app/ops/release_readiness.py` 的 `run_release_readiness(evidence_dir)`
是 M14-124 human-only 发布审批的 gitignored canonical 证据树配套
检查器——不适用本批（M14-272 系列）入库式 evidence README。本批
生产收口以上表三件（健康/监控/恢复）为准；`release_ready=false`
不变（release approval 仍 human-only）。

## 验证

| 命令 | 结果 |
| --- | --- |
| `npx vitest run`（Web 全量） | **334 passed**（19 files） |
| `npm run typecheck` / `npm run lint` | 全过 |

## 非声明

- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。
- 不声明公网语音/TURN/TLS 就绪；真机批待设备。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-275（M14-274 转
  前一任务快照，含 PR #365 合并 + 生产 Web 切换结果），里程碑段
  追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-275 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-275 条目（含非声明）。
