# M14-16 CosyVoice bootstrap 依赖解析降级回归修复 — 交付证据归档

- 日期：2026-09-13（开发切片交付；合并事实与生产只读观测回填同日）
- 分支：`fix/m14-16-cosyvoice-runtime-consistency`（基于 `main@ce10060`，即
  PR #90 merge commit `ce100600c1f13b854129ddaa8d440089a98c3142`，本地 git
  可验证）。本 Claude 开发回合独占 worktree，仅做一个本地 commit——该
  「待 supervisor 发布」表述是开发时点快照，见下方合并收口。
- **合并收口（回填 2026-09-13）**：已随 **PR #91** 合并 main——merged_at
  **2026-09-12T18:28:09Z**，merge commit
  `d6d0635c2c30797195a1f585766a4c5c9b9f09ba`，feature head 即本切片唯一
  commit `f65251faa7242ace8f619e2ff72d509a24beff35`（本地 git 可验证；
  远端 feature 分支截至回填时点仍存在）。PR CI run `34711072494` 与合并后
  main push CI run `34711281622` 均全部 5 job（Web/API/Docker/Android/
  Release tools）SUCCESS。下文引用本目录的 PROJECT_STATUS/ROADMAP/
  CHANGELOG 表述已同步为合并后事实。
- 状态：**可复现 bootstrap 契约修复（代码已合并、CI 全绿）；不构成生产
  运行恢复宣称**。开发回合零生产触碰：未重跑 bootstrap、未改生产 venv/
  进程/模型缓存；生产 venv 实际修复与冷启动复验由 supervisor 合并后
  受控执行（进展见下「生产栈只读观测」与 M14-17/M14-18 条目——后续
  supervisor 受控重跑发现的闭包/清单缺口由 M14-17/M14-18 续修）。
- 入库变更：`tools/voice/bootstrap_cosyvoice_wsl.sh`（终局同源回写 +
  运行期一致性探针）、`services/api/tests/test_voice_local_scripts.py`
  （新增 `test_bootstrap_torch_reconciliation_order_contract`）、
  `tools/voice/README.md`、`docs/CHANGELOG.md`、`docs/ROADMAP.md`、
  `docs/PROJECT_STATUS.md`。

## 问题与根因（真实生产冷启动实证）

canonical bootstrap 在依赖安装后退出：生产 venv 内 torch 2.3.1 /
torchaudio 2.11.0+cu128 / torchcodec 0.11.1+cu128 / lightning 2.2.4 /
pytorch-lightning 2.6.6 混装，`python -m pip check` 报「No broken
requirements found」但 `import cosyvoice` 在 torchaudio `_extension` 崩
`OSError: ... undefined symbol: aoti_torch_abi_version`。

根因（supervisor 核验）：最小运行时清单（`lightning==2.2.4` 官方 pin 链）
按 PyPI 解析把 torch 降级到 2.3.1 而留下预装 cu128 torchaudio——**cu128
轮 METADATA 不声明 torch 约束，`pip check` 对该混合 ABI 不可信**。

## 修法（本切片交付形态）

- **终局同源回写（`bootstrap_cosyvoice_wsl.sh`）**：清单分支（最小/官方
  完整回退）汇合后，从同一 cu128 index 以 `--no-deps` 显式回写本机已验证
  三件套 `torch==2.11.0+cu128` / `torchaudio==2.11.0+cu128` /
  `torchcodec==0.11.1+cu128`——`--no-deps` 不让 PyPI 约束再参与解析、
  无从降级任一轮；精确 pin 已满足时 pip no-op，不做 force-reinstall
  全量重写环境。
- **运行期一致性探针（fail-closed，先于 CosyVoice import 探针）**：
  torch/torchaudio/torchcodec 真实导入 + torch/torchaudio 基础版本一致
  （`+` 本地标签剥离比较）+ 双 `+cu128` 同源 + torchcodec 可导入——任一
  不满足即 FAIL 点名（文案显式说明 `pip check` 对混合 ABI 不可见）。
- **契约测试**：`test_bootstrap_torch_reconciliation_order_contract`——
  顺序锁定（初始 cu128 同命令安装 → 最小/完整清单分支 → 终局回写 →
  一致性探针 → import/WAV 探针）+ 回写内容 + 探针内容；既有
  `test_bootstrap_torchcodec_pinned_on_cu128_install_line` 的「torch
  安装命令唯一」契约经 `--no-deps` 前缀区分保持指初始安装行。

## 验证（开发回合，零生产触碰）

- 聚焦 `test_voice_local_scripts.py` **31 passed**；
  `test_voice_service_control.py` 回归 **64 passed**；ruff、
  `bash -n tools/voice/bootstrap_cosyvoice_wsl.sh`、`git diff --check`
  全过（canonical venv 解释器仅执行，零 canonical 检出改动）。
- 合并后远端 CI：PR run `34711072494` 与 main run `34711281622` 各 5 job
  全 SUCCESS（见上合并收口）。

## 生产栈只读观测（回填时点，2026-09-13T00:17:54Z / 08:17:54+0800）

本回填回合对生产做**只读**观测（零重启、零改动）：Docker compose 项目
`aios-m14-03-production-rehearsal` 6 容器全部 `Up 12 hours (healthy)`
（web/livekit/api/redis/postgres/minio）；API `GET /health`（127.0.0.1:8000）
**200**、FunASR `GET /health`（127.0.0.1:8010）**200**；CosyVoice
`GET /health`（127.0.0.1:8011）**无监听**——WSL 进程 7481 仍在执行
bootstrap 快照脚本（`bootstrap_cosyvoice_wsl.snapshot.sh`，已运行约
2h21m，service.log 显示 ModelScope 模型下载进行中）。**这是进行中的
受控部署验证快照，不是完成宣称**；详见
`docs/evidence/m14-18-cosyvoice-whisper-triton/README.md` 的同点观测与
边界说明。

## 边界（诚实口径）

- 本切片只修复可复现 bootstrap 契约：`pip check` 在 cu128 混合 ABI 状态
  下不构成任何门禁事实（后续 M14-17 恢复其（附加）门禁地位并补齐 CUDA
  闭包契约）。
- 生产 venv 实际修复与冷启动复验 = supervisor 受控执行；后续受控重跑
  暴露的 CUDA 闭包缺口由 **M14-17**（PR #92）与 **M14-18**（PR #93）
  续修——本切片不贪功。
- `production_ready=false` 不变；语音栈真实 ASR+TTS 活体验收仍未完成。
