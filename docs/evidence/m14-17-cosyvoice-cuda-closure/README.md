# M14-17 CosyVoice bootstrap CUDA 闭包终局修复 — 交付证据归档

- 日期：2026-09-13（开发切片交付；合并事实与生产只读观测回填同日）
- 分支：`fix/m14-17-cosyvoice-cuda-closure`（基于 `main@d6d0635`，即
  PR #91 merge commit `d6d0635c2c30797195a1f585766a4c5c9b9f09ba`，本地
  git 可验证）。本 Claude 开发回合独占 worktree，仅做一个本地 commit——
  该「待 supervisor 发布」表述是开发时点快照，见下方合并收口。
- **合并收口（回填 2026-09-13）**：已随 **PR #92** 合并 main——merged_at
  **2026-09-12T19:12:05Z**，merge commit
  `5fbeb22f23b662db139530fe3e965b27326854f1`，feature head 即本切片唯一
  commit `74d3988223ecd65071824f5a26ea8d25ece67681`（本地 git 可验证；
  远端 feature 分支截至回填时点仍存在）。PR CI run `34713163316` 与合并后
  main push CI run `34713465954` 均全部 5 job（Web/API/Docker/Android/
  Release tools）SUCCESS。下文引用本目录的 PROJECT_STATUS/ROADMAP/
  CHANGELOG 表述已同步为合并后事实。
- 状态：**可复现 bootstrap 契约修复（代码已合并、CI 全绿）；不构成生产
  运行恢复宣称**。开发回合零生产触碰：生产 venv 仅只读取证（`pip check`
  + dist-info METADATA + 探针只读实跑），未重跑 bootstrap、未装任何依赖、
  未启停任何进程/容器。
- 入库变更：`tools/voice/bootstrap_cosyvoice_wsl.sh`（终局完整闭包恢复 +
  `pip check` 附加门禁 + 闭包契约探针）、
  `services/api/tests/test_voice_local_scripts.py`（改写/新增 4 项契约）、
  `tools/voice/README.md`、`docs/CHANGELOG.md`、`docs/ROADMAP.md`、
  `docs/PROJECT_STATUS.md`。

## 问题与根因（真实生产 venv 只读取证，2026-09-13）

M14-16 的终局 `--no-deps` 回写后 torch/torchaudio/torchcodec 均已
`2.11.0+cu128`/`0.11.1+cu128`，但 `pip check` 报 nvidia-cudnn-cu12
8.9.2.26（torch metadata 需 `==9.19.0.56`）、nvidia-nccl-cu12 2.20.5
（需 `==2.28.9`）、triton 2.3.1（需 `==3.6.0`），多数 CUDA runtime 仍是
12.1 系列（清单分支装 torch 2.3.1 时连带降级的闭包），`import torch`
失败缺 `libcudnn.so.9`。根因：`--no-deps` 只回写三个主轮，**不恢复
torch metadata 声明的 Linux CUDA 依赖闭包**。

## 修法（本切片交付形态）

- **终局 CUDA 闭包恢复**：清单分支汇合后以**完整依赖解析**（同一 cu128
  index，无 `--no-deps`）安装三件套精确 pin + torch `2.11.0+cu128`
  真实 METADATA（Linux 段，自生产 venv
  `torch-2.11.0+cu128.dist-info/METADATA` 导出）声明的闭包成员：
  `cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,
  nvjitlink,nvrtc,nvtx]==12.8.1`、`cuda-bindings>=12.9.4,<13`、
  `nvidia-cudnn-cu12==9.19.0.56`、`nvidia-nccl-cu12==2.28.9`、
  `nvidia-cusparselt-cu12==0.7.1`、`nvidia-nvshmem-cu12==3.4.5`、
  `triton==3.6.0`——精确 pin 使 PyPI 清单分支无从再降级；成员 pin 不满足
  即强制解析（可自愈存量破损态），全部满足即 no-op。
- **`pip check` 附加门禁（fail-closed）**：闭包恢复后、一致性探针前；
  失败文案指向「CUDA closure 未恢复」。注释/测试明确它只是附加门禁、
  不能替代真实 import/运行探针——两个实证盲区：(a) 混合 ABI 报「No
  broken requirements found」（M14-16）；(b) extras 门控的 12.8 系列
  nvidia runtime 错配不报。
- **一致性探针扩展（M14-16 探针保留、顺序不变）**：CUDA closure 契约表
  逐项校验 **18 个成员**（`==` pin 精确相等 / cuda-toolkit extras 按
  `.*` 通配前缀段边界匹配 / cuda-bindings 范围校验），任一不符即 FAIL
  点名。探针已在生产 venv **只读实跑**验证：exit 1 且在 torch import
  之前精确点名 `nvidia-cudnn-cu12 8.9.2.26 != 9.19.0.56`。

## 验证（开发回合，零生产触碰；生产 venv 只读取证）

- TDD RED 先行（新契约在旧脚本上恰 2 项失败）后 GREEN：聚焦
  `test_voice_local_scripts.py` + 回归 `test_voice_service_control.py`
  合并 **96 passed（exit 0；32+64）**；ruff、`bash -n`、
  `git diff --check` 全过（canonical venv 解释器仅执行，零 canonical
  检出改动）。
- 合并后远端 CI：PR run `34713163316` 与 main run `34713465954` 各 5 job
  全 SUCCESS（见上合并收口）。
- 生产后续实证（**发生在本切片合并之后的受控重跑**，2026-09-13 生产
  `artifacts/voice/cosyvoice/service/service.log`，M14-18 动机引用）：
  终局闭包恢复已成功——torch `2.11.0+cu128` / triton `3.6.0` 及全部
  闭包成员就位；随后被 `openai-whisper==20231117` 的 `triton<3` 元数据
  冲突卡在 `pip check`（该缺口由 **M14-18**（PR #93）修复）。

## 生产栈只读观测（回填时点，2026-09-13T00:17:54Z / 08:17:54+0800）

本回填回合对生产做**只读**观测（零重启、零改动）：Docker compose 项目
`aios-m14-03-production-rehearsal` 6 容器全部 `Up 12 hours (healthy)`
（web/livekit/api/redis/postgres/minio）；API `GET /health` **200**、
FunASR `GET /health` **200**；CosyVoice `GET /health`（8011）**无监听**——
WSL 进程 7481 仍在执行 bootstrap 快照脚本（已运行约 2h21m，模型下载
进行中）。**这是进行中的受控部署验证快照，不是完成宣称**；详见
`docs/evidence/m14-18-cosyvoice-whisper-triton/README.md`。

## 边界（诚实口径）

- 生产 venv 的实际修复（重跑 bootstrap 走终局闭包恢复，含多 GB nvidia
  轮重装）与冷启动复验（torch import → CosyVoice import → WAV →
  /health 200）由 supervisor 合并后受控执行——合并后首轮受控重跑实证
  闭包恢复成功但暴露 M14-18 缺口；当前轮（M14-18 修复合并后启动）仍在
  下载阶段，见上只读观测。
- 闭包成员在 cu128 index 的可解析性以生产 venv 初始安装实证为据，本
  切片未重新触网解析。
- `production_ready=false` 不变；语音栈真实 ASR+TTS 活体验收仍未完成。
