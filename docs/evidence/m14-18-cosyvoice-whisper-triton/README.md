# M14-18 CosyVoice 最小运行时 openai-whisper triton 元数据冲突修复 — 交付证据归档

- 日期：2026-09-13（开发切片交付；合并事实与生产只读观测回填同日）
- 分支：`fix/m14-18-cosyvoice-whisper-triton`（基于 `main@5fbeb22`，即
  PR #92 merge commit `5fbeb22f23b662db139530fe3e965b27326854f1`，本地
  git 可验证）。本 Claude 开发回合独占 worktree，仅做一个本地 commit——
  该「待 supervisor 发布」表述是开发时点快照，见下方合并收口。
- **合并收口（回填 2026-09-13）**：已随 **PR #93** 合并 main——merged_at
  **2026-09-12T21:53:44Z**，merge commit
  `cc782b0d4908d9dac38cf984a94fa95a0efa5968`，feature head 即本切片唯一
  commit `2185bdc721241675065d72e90f3fcf35424b8379`（本地 git 可验证；
  远端 feature 分支已在合并后删除）。PR CI run `34721050144` 与合并后
  main push CI run `34721233549` 均全部 5 job（Web/API/Docker/Android/
  Release tools）SUCCESS。**此后 PR #73（M13-11 governance read-only
  pane）再合并产生 main `42653b7900e574fc8ede577320fc19a735276ba9`，
  merge-main CI run `34726676218` 同样 5/5 SUCCESS**——M14-14…M14-18 五
  个切片全部在 main 历史内且当前 main HEAD CI 绿。下文引用本目录的
  PROJECT_STATUS/ROADMAP/CHANGELOG 表述已同步为合并后事实。
- 状态：**可复现清单/bootstrap 契约修复（代码已合并、CI 全绿）；不构成
  生产运行恢复宣称**。开发回合零生产触碰：生产 venv 仅只读探针（import
  + 签名），未装任何依赖、未启停任何进程/容器。
- 入库变更：`tools/voice/cosyvoice-runtime-requirements.txt`
  （`openai-whisper==20231117` → `==20250625`）、
  `tools/voice/bootstrap_cosyvoice_wsl.sh`（头部注释 + 版本留档 grep）、
  `services/api/tests/test_voice_local_scripts.py`（新增 3 项契约）、
  `tools/voice/README.md`、`docs/CHANGELOG.md`、`docs/ROADMAP.md`、
  `docs/PROJECT_STATUS.md`。

## 问题与修法（生产 bootstrap 实证）

- **冲突证据（2026-09-13 生产 `artifacts/voice/cosyvoice/service/service.log`）**：
  M14-17 终局 CUDA 闭包恢复已成功（torch `2.11.0+cu128` / triton
  `3.6.0` 及全部闭包成员就位），但最小运行时清单的
  `openai-whisper==20231117` METADATA 声明 `triton<3,>=2.0.0`（无环境
  标记）与闭包 `triton==3.6.0` 冲突——`pip check` 附加门禁被「openai-
  whisper 20231117 has requirement triton<3,>=2.0.0, but you have
  triton 3.6.0」卡死 FAIL，bootstrap 退出；且清单分支装 20231117 时该
  约束触发 pip 回溯把 torch 一路降级（实证 2.14.0→…→2.3.1）。
- **修法 = 升级 pin 而非绕过门禁**：`openai-whisper==20250625`（METADATA
  `triton>=2`，x86_64/linux 环境标记、无上界）与 cu128 闭包共存；运行时
  依赖集合与 20231117 完全一致（不替换 torch/CUDA 包）。所有绕过路径
  （`--no-deps` 装清单 / 强制降级 triton / 改写 dist-info / 弱化 pip
  check）被排除且被契约测试锁定；M14-17 门禁/闭包契约/一致性探针一字
  不动。CosyVoice 固定 commit `074ca6d` 用到的两个 API 经 20231117 vs
  20250625 逐字 diff **源码级不变** + 生产 venv 只读签名探针实跑通过。
  官方 requirements 固定 commit 仍 pin 20231117——有意偏离并留档。

## 验证（开发回合，零生产触碰；生产 venv 仅只读探针）

- 聚焦 `test_voice_local_scripts.py` **34 passed + 1 skipped（签名回归
  按设计 skip）**；回归 `test_voice_service_control.py` **64 passed**；
  ruff、`bash -n`、`git diff --check` 全过。
- 合并后远端 CI：PR run `34721050144` 与 main run `34721233549` 各 5 job
  全 SUCCESS（见上合并收口）。

## 生产栈只读观测（回填时点，2026-09-13T00:17:54Z / 08:17:54+0800）

本回填回合对生产做**只读**观测（零重启、零改动、零 env 读取），三类
事实严格分开：

1. **代码合并且 CI 绿（ durable 事实）**：M14-14…M14-18 五切片经
   PR #89–#93 全部合并 main，各 PR run 与 merge-main run 均 5/5 SUCCESS
   （逐 PR 的 merge commit/run ID 见上文与 PROJECT_STATUS/CHANGELOG 各
   条目）。
2. **生产栈只读观测（易变快照）**：Docker compose 项目
   `aios-m14-03-production-rehearsal` **6 容器全部 `Up 12 hours
   (healthy)`**（web/livekit/api/redis/postgres/minio，`docker ps` 只读
   列出）；API `GET /health`（127.0.0.1:8000）**HTTP 200**、FunASR
   `GET /health`（127.0.0.1:8010）**HTTP 200**（只读 GET）。
3. **仍待完成（不宣称）**：CosyVoice `GET /health`（127.0.0.1:8011）
   **无监听**——WSL 进程 **7481** 仍在执行 bootstrap 快照脚本
   （`bootstrap_cosyvoice_wsl.snapshot.sh`，spawn 工件时间戳 2026-09-13
   05:54+0800 ≈ PR #93 合并后约 1 分钟启动；观测时点已运行约 2h21m；
   service.log 显示 ModelScope 模型下载进行中——CosyVoice-BlankEN/
   model.safetensors 942M 已完成，speech_tokenizer_v3.batch.onnx 925M
   进行中）。**bootstrap 仍在下载/未起服务 = 进行中状态，不是完成
   宣称**；`openai-whisper==20250625` 的实装与冷启动复验（pip check
   过 → torch import → CosyVoice import → WAV → /health 200）以该轮
   受控 bootstrap 的最终日志为准。

## 边界（诚实口径）

- 本切片只修复可复现清单/bootstrap 契约与文档：未重跑 bootstrap、未向
  任何 venv 装依赖、未启停任何进程/容器；生产实际升级与冷启动复验由
  supervisor 合并后受控执行（当前轮进行中，见上观测）。
- 语音栈真实 ASR+TTS 活体验收（端到端语音会话）、监控计划任务的实际
  注册与持续运行（M14-14 readiness 之后续）、外部告警接入均**未完成**。
- 无任何模拟器/真机验证或外部生产流量宣称（本仓库切片从未做过，也
  不由本回填发明）。
- `production_ready=false` 不变。
