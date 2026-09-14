# M14-25：CosyVoice bootstrap 离线重启幂等（offline fast path）

- 切片：`fix/m14-25-cosyvoice-offline-restart`，基于 `origin/main@1f58800`
  （PR #103 merge commit，含 M14-24 生产验收回填；本地 git 可验证）。本 Claude
  开发回合独占 worktree，仅做**一个本地 commit**；supervisor 审查与 remote
  发布（push/PR/合并）在其后进行。
- 变更面：`tools/voice/bootstrap_cosyvoice_wsl.sh`（offline fast path + 门禁
  函数化）+ `services/api/tests/test_voice_local_scripts.py`（契约/行为测试
  扩展）+ 本 README + `docs/PROJECT_STATUS.md` + `docs/ROADMAP.md` +
  `docs/CHANGELOG.md` + `tools/voice/README.md`。
- 零生产触碰：不启停/重启 CosyVoice/FunASR/容器/计划任务，不执行 WSL 生产
  bootstrap，不改生产 artifacts/.verify，不读取或输出任何密钥。

## 1. 背景与动机（生产实证）

M14-24 受控生产重启（2026-09-13，supervisor 获准窗口）暴露：旧 CosyVoice
PID 7481 优雅退出后，第一次新启动 **PID 18447 在 bootstrap 阶段中止**——
模型/venv/wetext 缓存完整，但脚本**无条件**执行：

1. `pip install --upgrade pip`（PyPI）；
2. `pip install torch torchaudio torchcodec==0.11.1+cu128 --index-url
   https://download.pytorch.org/whl/cu128`（PyTorch index）；
3. `pip install -r cosyvoice-runtime-requirements.txt`（PyPI）；
4. 终局 CUDA 闭包恢复 `pip install torch==2.11.0+cu128 … --index-url …/cu128`。

PyPI 不可达 → 上述任一命令失败 → `set -euo pipefail` 中止。最终 PID 19132
于 23:51:10+08:00 启动成功只是**网络恢复后的成功**，不是幂等修复（事实见
`docs/evidence/m14-24-production-acceptance/README.md` 时间线）。

## 2. 修法（offline fast path，单一事实源）

四道门禁（pip check / CUDA closure 运行期一致性 / `import
cosyvoice.cli.cosyvoice` / torchaudio WAV 加载探针）**原样函数化**为
`gate_pip_check` / `gate_consistency_probe` / `gate_import_probe` /
`gate_wav_probe`（heredoc 探针逐字搬入，CUDA closure 契约表 18 项不动）。
venv 就绪后先以这四道门禁做 **fast path 判定**：

- **当且仅当四道门禁全部通过**：跳过整个依赖安装段（上述 4 类联网安装命令
  零执行），日志明示 `offline fast path 命中`，直达模型检查/wetext/启动段；
- **任一门禁失败**：日志 `offline fast path 未命中` 点名缺失项（venv 缺失 /
  pip check / CUDA closure 一致性 / import cosyvoice / WAV 探针五类原因），
  进入既有安装/恢复路径，安装后门禁照常 **fail-closed** 执行；
- 判定只依据本地解释器 + 本地 metadata + 本地探针，**绝不先访问网络探测
  可用性再决定 fast path**；判定阶段探针 stderr 透出（点名缺失包），stdout
  成功详情不重复打印。

不引入双事实源：判定与安装后门禁调用**同一组函数**（bash 契约测试锁定每个
门禁函数恰定义一次、两处调用共享）。模型 payload 四文件判定、ModelScope
缓存回落、`COSYVOICE_SKIP_DOWNLOAD=1` 缺模型 fail-closed、wetext payload
判定与 local-only 语义、bridge 启动段全部原样不动（fast path 命中时这些
段照常执行——它们本身已是幂等本地检查）。

## 3. TDD 证据（RED → GREEN，真实运行）

**RED（实现前，2026-09-14 本回合实测）**：先扩展契约测试再实现，5 项新/改
测试在旧脚本上全部失败，失败原因均为缺少 fast path 实现（非 clone/路径/
harness 错误——行为测试 stdout 显示脚本直接 checkout 本地 fake commit、
无任何 `Cloning into`）：

- `test_bootstrap_offline_fast_path_hit_runs_zero_pip_install`（行为）：
  `assert 'offline fast path 命中' in stdout` 失败——fake venv 四探针全过、
  payload 齐备，旧脚本仍无条件走 pip install（fake pip 桩记录到 install
  调用）且无命中日志；
- `test_bootstrap_offline_fast_path_miss_falls_back_to_install`（行为）：
  `assert 'offline fast path 未命中' in stdout` 失败——探针失败时旧脚本直接
  fail-closed（exit 1，门禁语义正确）但无判定块；
- `test_bootstrap_offline_fast_path_contract`（文本契约）：`FAST_PATH_HIT` /
  门禁函数 / 守卫块锚点缺失；
- `test_bootstrap_torch_reconciliation_order_contract`（更新）：门禁函数
  定义锚点缺失；
- `test_openai_whisper_triton_no_bypass_contract`（更新）：
  `AssertionError: pip check 门禁调用应恰一处 (assert 0 == 1)`。

**GREEN（实现后）**：`test_voice_local_scripts.py` 全套 **43 passed /
1 skipped**（skip 为 whisper 未装的既有环境跳过）；加
`test_voice_service_control.py` 合计 **107 passed / 1 skipped**。

**上一回合测试布局教训（R1 返工收口）**：初版行为测试的 fake 布局把
`VOICE_ARTIFACTS_DIR` 少拼了一层 `cosyvoice/`（脚本语义 = artifacts 根，
自行拼 `cosyvoice/`），`.git` 检测落空 → 旧脚本进入**真实 git clone**
（github.com 连接超时后 exit 128）。本轮修正：fake 数据全部落在
`<root>/artifacts/voice/cosyvoice/` 下（`COSYVOICE_DIR`/`VENV_DIR`/
`MODEL_DIR`/wetext 缓存与脚本拼接路径逐层对齐），并在运行前加**防错位
assert**（fake `CosyVoice/.git` 必须恰在脚本将检查的路径上，否则测试立即
失败而不是联网）。修正后 RED 复跑确认零 clone、失败原因纯净。

**R2 返工（Windows MAX_PATH 隔离）**：独立复现实证 fake 仓库放在 pytest
深层编号 tmp 路径下时，git 写 objects 哈希文件报
`Filename too long` / `Error building trees`（commit 失败曾被吞、只在
rev-parse 暴露）。修正：两个行为测试改用
`tempfile.TemporaryDirectory(prefix="m1425-…")` **短根**（不嵌套 pytest
tmp 编号路径，bootstrap 后续 git 操作也在短路径上跑；自动清理、零网络），
fake git 全部命令（init / config / commit / rev-parse）**fail-fast**
（非零退出即断言失败并带 stderr），仓库设 `core.longpaths=true` 加固；
R1 防错位守卫保持原位。

**R3 返工（WSL 启动器 env 透传 + CRLF stub，supervisor PowerShell 复现）**：
R2 结果只在选中 Git Bash/MSYS 时通过——supervisor 在 PowerShell 下
`BASH=C:\Windows\System32\bash.EXE` 复现两个行为测试 180s 超时，进程取证
证明**真实网络 clone**（`git clone --recursive https://github.com/
FunAudioLLM/CosyVoice.git …/repo/artifacts/…`，而 fake `.git` 在布局路径
上；快照 bash 的 `/proc/<pid>/environ` 有 `COSYVOICE_BOOTSTRAP_*` 但**没有**
`VOICE_ARTIFACTS_DIR`/`PIP_CALL_LOG`/`COSYVOICE_COMMIT`/
`COSYVOICE_SKIP_DOWNLOAD`/`FAKE_PYTHON_FAIL`）。两个根因与修法：
1. **WSL 启动器只透传 WSLENV 声明的变量**——测试自有变量全部丢失，脚本
   内 `ARTIFACTS` 回落 `REPO_ROOT` 默认路径 → `.git` 检测落空 → clone。
   修法：布局 env 在 WSL 分支构造 `WSLENV`（保留既有声明、去重追加五个
   测试变量；值保持 POSIX 形态，无需 `/p`）。
2. **fake python/pip stub 曾以 `write_text` 写出（Windows 翻译为 CRLF）**
   ——WSL `/bin/sh` 报 `bad interpreter: /bin/sh^M`，fast path 误判 miss
   进安装路径。修法：stub 改 `write_bytes` 显式 **LF 字节**。
3. **preflight**：启动 bootstrap 前经同一选中的 BASH 执行 `preflight.sh`
   （LF 脚本文件——WSL 启动器对 `bash -c '<复合串>'` 做二次命令行解析会
   字面化内层引号，实证 stderr 出现字面 `'""'`，故与 bootstrap 本体同以
   文件形态执行）：逐项验证 env 透传、`PIP_CALL_LOG` 目录、fake `.git`
   位置、stub 可执行位与首行解释器（CRLF 检测）、fake 仓库可被该 BASH
   的 `git rev-parse` 读取且 commit 与 env 一致——任一不成立立即失败并
   输出完整诊断（不进入 bootstrap，杜绝再次真实 clone）。
4. **post 硬断言**：两个行为测试断言 stdout+stderr 无 `Cloning into` 与
   `FunAudioLLM/CosyVoice.git`。
R3 后双模式实证：Git Bash/MSYS 与 WSL
（`BASH=C:\Windows\System32\bash.EXE`，`uname -r`=
`6.18.33.2-microsoft-standard-WSL2`）下行为测试均通过（见 §5）。

## 4. 测试覆盖（新增/扩展 5 项）

| 测试 | 锁定 |
|------|------|
| `test_bootstrap_offline_fast_path_contract` | 门禁函数单一事实源（恰定义一次、判定与门禁两处调用共享）；判定块零网络（生效行无 `pip" install`/`--index-url`/`snapshot_download`/`git clone`）；五类缺失原因点名；五类联网安装命令全部位于跳过守卫块内（缩进 + 顶层 fi 边界）；门禁 fail-closed 调用在安装后；模型/wetext/bridge 段在守卫块外之后；skip-download 缺模型 fail-closed 语义不动 |
| `test_bootstrap_offline_fast_path_hit_runs_zero_pip_install`（行为，fake venv/git/payload） | 命中：exit 0、命中日志、wetext 就绪日志、安装段 say 不出现、fake pip 桩 argv 全程只有只读 `check`/`freeze`（零 install/upgrade/index-url） |
| `test_bootstrap_offline_fast_path_miss_falls_back_to_install`（行为） | 契约失败：未命中日志点名一致性探针、fake pip 记录到 install + cu128 `--index-url`（安装路径确实执行）、安装后门禁 fail-closed exit 1 + stderr 文案——不绕过、不静默 |
| `test_bootstrap_torch_reconciliation_order_contract`（更新） | 新顺序：门禁函数定义（顺序 = 门禁顺序）→ fast path 判定 → 初始安装 → 清单 → 终局闭包恢复 → pip check 门禁 → 一致性 → import → WAV（调用层）；闭包恢复 pin 全集 / 无 --no-deps / cu128 index / fail-closed 文案逐项保留 |
| `test_openai_whisper_triton_no_bypass_contract`（更新） | pip check 命令恰一处（函数内）+ 门禁调用行 `if !` 形态不可短路；反绕过旗标断言不变 |

行为测试边界：fake venv（python/pip 桩 **LF 字节**，pip 桩逐次记录 argv）、
fake git 仓库（git init + 空 commit，固定 commit 可 checkout；
`core.longpaths=true` + tempfile 短根——Windows MAX_PATH 隔离见 §3 R2）、
fake 模型四载荷 + wetext 四 FST、`COSYVOICE_SKIP_DOWNLOAD=1`、WSL 启动器
下经 **WSLENV 声明**五个测试变量 + 启动前 **preflight.sh**（env 透传/
布局/stub LF/git 可读五重验证，见 §3 R3）+ 事后 **零 clone 硬断言**——
**全程零真实网络、零生产 venv/模型/进程**；git clone/fetch 段以本地仓库
短路（fetch 失败 `|| say` 容忍为既有离线语义）。

## 5. 验证命令与结果（2026-09-14，开发回合；R3 amend 后以 uv 隔离环境双 bash 模式复跑）

| 命令 | 结果 |
|------|------|
| `uv run --python 3.11 --with-requirements services/api/requirements.txt --with-requirements services/api/requirements-dev.txt -- python -m pytest services/api/tests/test_voice_local_scripts.py -vv --tb=short`（选中 Git Bash/MSYS `D:\Git\usr\bin\bash.EXE`） | **43 passed, 1 skipped**（含 bash -n 三脚本语法检查；skip = whisper 未装的既有环境跳过） |
| 同上命令，`PATH` 前置 `C:\Windows\System32` → 选中 **WSL 启动器 `C:\Windows\System32\bash.EXE`**（`uname -r` = `6.18.33.2-microsoft-standard-WSL2`，与 supervisor 复现环境一致） | **43 passed, 1 skipped**（行为测试经 WSLENV 透传 + preflight 通过 + 零 clone 硬断言） |
| `uv run …（同上环境）… pytest services/api/tests/test_voice_service_control.py -q` | **64 passed**（launcher 生命周期零改动回归；两套件合计 107 passed / 1 skipped） |
| `uv run --python 3.11 --with-requirements services/api/requirements-dev.txt -- python -m ruff check services/api` | **All checks passed!** |
| `uv run …（dev 环境）… python -m py_compile services/api/tests/test_voice_local_scripts.py` | 通过 |
| `bash -n tools/voice/bootstrap_cosyvoice_wsl.sh` | 通过（Git Bash 与 WSL bash 各跑一次；并由套件内 `test_bash_scripts_pass_bash_n` 双模式复盖） |
| `git show --check HEAD` | 干净（无空白错误） |

注（环境，非代码）：uv 环境经本地缓存解析（无网络下载）；本机
`pytest-of-<user>/pytest-current` 曾存在损坏递归 symlink（指向 `..`，
删除被系统拒绝），使使用 `tmp_path` 的套件在 tmp factory 初始化时报
`PermissionError: [WinError 5]`——R2 曾以重命名 `pytest-of-hongfu` 旁路
（pytest 自动重建；R3 起不再触碰用户级 pytest 临时目录）。

聚焦套件选择原因：本切片变更面 = 1 个 bash 脚本 + 1 个测试文件；两个聚焦
套件正是该脚本的契约测试与 launcher 生命周期测试的全部消费方（其余套件
零 import 该脚本）。voice_service_control.py 本身零改动（launcher 只透传
环境后 `exec bash bootstrap_cosyvoice_wsl.sh`，fast path 是脚本内部行为）。

## 6. 边界（诚实口径）

- **开发验证是本地/契约验证**：fake venv/fake payload/fake git 仓库 + 文本
  契约锚点。**真实离线生产重启（生产 WSL、生产 venv、生产模型、断网形态）
  需 supervisor 合并后在获准窗口受控复验**——本回合零生产触碰。
- fast path 命中语义 = 「四道门禁全过则跳过安装」；生产 venv 若存在门禁
  探针不可见的损坏（理论上 pip check + import + WAV 探针已覆盖
  M14-16/M14-17/M14-18 全部实证盲区），仍会被门禁拦下走安装路径——
  不弱化任何既有检查。
- 判定阶段四道探针各运行一次（import torch/cosyvoice 需数秒-数十秒），
  命中时安装段的门禁不再重复执行——离线重启总耗时从「pip 全量联网解析
  （网络好时分钟级、网络差时失败）」变为「本地探针一次通过即达启动段」。
- git clone/fetch 段保持既有语义（clone 已存在即跳过；fetch 失败容忍）；
  全新机器（无克隆/无 venv）不受影响——探针不过 → 既有安装路径。
- `production_ready=false` 不变；不构成语音链路 production readiness 宣称。

## 7. 复验指引（supervisor 合并后，需获准窗口）

受控复验 = 在网络受限（或临时断网）形态下重启 CosyVoice voice 进程，核对
service.log 出现 `offline fast path 命中` 且无任何 `pip install`/
`--index-url` 行，进程到达 bridge 启动（端口 8011 监听、`/health` 语义与
M14-24 验收一致）。反向用例（可选）：对生产 venv 人为制造契约失败（如
临时改坏一个闭包成员版本）核对未命中路径点名与安装恢复——由 supervisor
裁量，本切片不要求。
