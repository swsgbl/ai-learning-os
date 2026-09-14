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

生产验收已于 2026-09-14 由 supervisor 在获准窗口执行（offline fast path
重启幂等通过；同轮 12:00 自然监控因 WSL 转发层 failed，如实记录），事实
回填见 §8。

## 8. 生产验收回填（2026-09-14，supervisor 获准窗口；本回填回合零生产操作）

分支 `docs/m14-25-production-acceptance`（基于 canonical `main@d54ad5b`，即
PR #104 merge commit `d54ad5b51ec7653c592786399f37a778f8c07e5d`，本地 git 可验
证）；本回填回合 docs-only、独占 worktree，按任务书在分支上做本地 commit
（R0 回填一个 + R1 追加一个，均 docs-only；不 push / 不建 PR——remote 发布由
supervisor 决策）。本回合零生产触碰：
不启/停/重启任何生产容器或 voice 进程（§8.2 的重启由 supervisor 于获准窗口执
行）、零 Docker/计划任务/服务变更、零网络部署，不修改任何生产代码、测试、
compose 或 `.verify/**`/`artifacts/**`（canonical 证据仅只读核对）；原始 WAV
绝不入库（仅存 gitignored `.verify/artifacts/m14-25-production-acceptance/`，
`.verify/` 在 `.gitignore` 第 16 行）；不输出任何密钥或 env 值；
`production_ready=false` 不变。

背景：M14-25 开发切片（本 README §1–§7）合并 main 后，遗留边界「真实离线
生产重启（生产 WSL/venv/模型、断网形态）需 supervisor 合并后在获准窗口受控
复验」（§6/§7）。本节回填的正是 supervisor 于 2026-09-14 获准窗口完成的
**受控生产重启与验收**事实。

### 8.1 合并与 CI

| 项 | 事实 | 核对口径 |
|---|---|---|
| PR #104（M14-25 修复） | 已合并 main | supervisor 验收事实 |
| feature head | `98a2d773c8e098e4a2ebf4328f82ccdcc25023b6`（"fix(voice): M14-25 CosyVoice bootstrap offline restart idempotency"） | 本地 git 可验证 |
| merge commit | `d54ad5b51ec7653c592786399f37a778f8c07e5d` | 回填回合核对：parents `1f58800`（PR #103 merge）+ `98a2d77`，主题 "Merge pull request #104 from swsgbl/fix/m14-25-cosyvoice-offline-restart"；canonical main == 该提交 |
| PR CI | **5/5 job success** | supervisor 验收事实（本回填回合未发起网络查询） |
| 合并后 main CI run | `34803270943`：首败为 **Docker Hub redis 镜像拉取连接重置**（外部基础设施侧），rerun 后 **5/5 job success** | supervisor 验收事实（同上） |

### 8.2 受控生产重启时间线（supervisor 获准窗口执行；时间口径 +08:00）

重启前基线（2026-09-14 11:49，重启未发生时点）：

- CosyVoice PID **19132**（M14-24 验收轮 2026-09-13T23:51:10+08:00 启动的同一
  进程，跨文档一致）、端口 **8011**、manifest managed-running，`/health` 200
  （model `Fun-CosyVoice3-0.5B-2512`）；
- FunASR PID **867**、端口 **8010**、manifest managed-running，`/health` 200
  （sensevoice）；
- 六容器全部 healthy（`Up 39 hours`，重启前后 `docker ps` 输出逐字节相同）。

| 步骤 | 事实 |
|---|---|
| 第一次 stop 尝试（fail-closed，**未动任何进程**） | WSL `/proc` 探测**瞬时不可用** → voice-ctl 无法核实 PID 19132 归属 → **拒绝发送信号**，restart 中止（stop rc=3，「未启动新实例」）——归属核验 fail-closed 语义在生产按设计工作 |
| 默认环境重试（成功） | PID **19132** 优雅退出（TERM）；新 PID **26008** 于 **2026-09-14T11:52:45+08:00** 启动、端口 **8011**、manifest managed-running |

### 8.3 offline fast path 生产实证（重启偏移后 service.log 硬证据）

以重启前日志偏移（`bytes=1241785 lines=11546`）截取偏移后增量，逐条命中：

- `offline fast path 命中：pip check + CUDA closure 一致性 + import
  cosyvoice.cli.cosyvoice + torchaudio WAV 探针全部通过（本地运行时契约满足）
  ——跳过 pip upgrade/install 与 CUDA 闭包网络恢复（零网络安装命令），直达
  模型检查`——四道门禁全过即跳过整个安装段，与 §2 修法逐字对应；
- `模型载荷已就位，跳过下载`（Fun-CosyVoice3-0.5B payload 齐备）；
- `wetext 离线缓存就绪（payload 齐备，无需下载）`；bridge 侧 `wetext
  offline cache READY … payload complete — binding local-only, zero
  ModelScope traffic` 与 `snapshot_download bound LOCAL-ONLY`——**零
  ModelScope 流量**；
- **禁用模式 grep（restart-only 段）：`FORBIDDEN_INSTALL_DOWNLOAD_PATTERN_MATCHES=0`**——无 pip install/upgrade、无 `download.pytorch.org`、无
  `Cloning into`、无 `FunAudioLLM/CosyVoice.git`、无模型下载（偏移增量中的
  `固定 commit 074ca6d … HEAD is now at` 为既有 pinned 克隆的 checkout 确认，
  非新 clone）；
- readiness 语义与 M14-24 验收一致：模型 loading 期 `/health` 503 → model
  ready 后 200；`/health/live` 全程 200；
- 稳态（11:59:02）：`/health` 200 体 `{"status":"ok","model":"Fun-CosyVoice3-0.5B-2512"}`；
  `/health/live` 200 体 `{"status":"ok","liveness":"alive","readiness":"ready"}`。

### 8.4 TTS 冒烟（新进程 PID 26008、合并后代码）

| 项 | 主 API 同款 provider 调用 | 冒烟脚本 smoke（此前） |
|---|---|---|
| 结论 | **成功** | TTS **成功**；ASR health **未通过**（WSL 转发层超时，见 §8.5/§8.6） |
| provider | `local-cosyvoice` | `local-cosyvoice` |
| 耗时 | **21896 ms** | 41721 ms |
| 输出 | **220844 bytes**，RIFF/WAV | **241964 bytes**，`riff_wav=yes` |
| SHA-256 | `E78CC2FE0AB4D894160033F1B6975F9B802275CCD0ADE990EDAE34C688AA4ABD` | —（工件未含哈希） |

同一时点 Windows 直连 `curl http://127.0.0.1:8011/v1/audio/speech` 超时
（`http_code=000`、`time_total=30.000944s`）——**主 API 同 provider 路由成功
而 Windows 直连超时**，指向 Windows→WSL 转发层而非 bridge 本身（§8.6）。
smoke 轮 `asr=FAIL tts=PASS`：ASR health 不可达同因。

原始 WAV 仅保留于 gitignored
`.verify/artifacts/m14-25-production-acceptance/post-restart-tts.wav`，**不入库**
（`.verify/` 在 `.gitignore` 第 16 行）。回填回合对该文件独立重算字节数与
SHA-256，逐项一致（哈希现场计算输出小写、本文件统一转大写呈现，十六进制
大小写等价）。

### 8.5 重启前后自然监控（计划任务自然轮，非手动触发；工件时间戳 UTC）

| 轮（本地 +08:00） | pipeline | monitor 采集器 | 端点 |
|---|---|---|---|
| 11:45（03:45Z，重启前基线轮） | overall **ok** | 全采集器 ok | 五端点全 200：web-root 4.239ms、web-login 1.579ms、api-health 2.204ms、funasr-health **3.281ms**、cosyvoice-health **4.615ms** |
| 12:00（04:00Z，重启后首轮） | overall **failed**（monitor exit 2；history/insights 按序跳过、skip 原因固定；lock acquired/released=true） | compose_ps/containers/logs 三采集器 ok、**endpoints failed** | **六容器全部 healthy**、Web/API 正常：web-root 200 5.791ms、web-login 200 1.582ms、api-health 200 1.918ms；**funasr-health 与 cosyvoice-health 两 Windows→WSL loopback 端点 5s（`request_timeout_seconds=5.0`）TimeoutError** |
| 12:15/12:30/12:45/13:00/13:15/13:30（R1 追加六轮） | 六轮 overall 全部 **failed**、与 12:00 轮**同构**（monitor exit 2、history skipped（`monitor-status-failed`）、insights skipped（`history-status-skipped`）、lock true/true） | 六轮均 compose_ps/containers/logs ok、**endpoints failed** | 六轮均六容器 6/6 healthy、web-root/web-login/api-health 三端点 200（延迟约 1.3–11.9ms）、funasr-health/cosyvoice-health 均 timeout/TimeoutError |

**R1 追加（2026-09-14 下午，supervisor 验收事实 + 回填回合 canonical 工件
核对）**：12:00–13:30 **共 7 轮生产监控同构失败**（上表两行合计）——每轮
六容器 compose 状态 ok、Web root/login 与 API health 均 200；仅 Windows 侧
访问 FunASR health 与 CosyVoice health 5s 超时；monitor `overall_status=
incomplete` 且 `partial=true`；pipeline `overall_status=failed`；history/
insights 因失败被 skipped。回填核对时点 canonical 监控目录中 13:45 轮
（monitor `20260914-054502` / pipeline `20260914-054514`）已再落一轮**同
签名**失败（第 8 轮，哈希见 §8.7 追加表）。

**不是引擎本体死亡**（supervisor 现场取证）：WSL 内部直连 FunASR `/health`
曾返回 200（服务本体健康）；CosyVoice **PID 26008 持续存活**（manifest/日志
连续）；CosyVoice Windows 侧也曾短暂恢复 200（11:59，§8.3 稳态）。Windows
侧 8010/8011 listener 由 **wslrelay.exe PID 17936** 持有，listener 启动时间
**2026-09-12 20:44:46**（早于 M14-24 与 M14-25 两次故障窗口——同一 listener
实例跨两次窗口在场）；`wsl.exe` 管理面间歇出现 **`WSL/Service/0x8007274c`
（连接超时）或 `TimeoutExpired`**（详见 §8.6）。

### 8.6 WSL localhost 转发层偶发不可用 = 新生产阻塞（非 M14-25 回归）

- 事实链（R1 后完整口径）：11:49 两 voice 端点 200（基线）→ 11:57 smoke ASR
  health 不可达 → 11:59 CosyVoice Windows 侧 200（恢复）→ **12:00–13:30 共
  7 轮双 voice loopback 端点 5s 超时、pipeline overall failed（同构；13:45
  轮工件仍同签名）**；期间 WSL 内部 FunASR `/health` 曾 200、CosyVoice PID
  26008 持续存活（§8.5）——**不是引擎本体死亡**；
- 转发层取证（supervisor 现场事实）：Windows 侧 8010/8011 listener 由
  **wslrelay.exe PID 17936** 持有、listener 启动于 **2026-09-12 20:44:46**
  （同一 listener 实例跨 M14-24 与 M14-25 两次故障窗口在场）；`wsl.exe`
  管理面间歇出现 **`WSL/Service/0x8007274c`（连接超时）或 `TimeoutExpired`**；
- 归因边界：**不能写成 M14-25 回归**——M14-25 范围 = bootstrap 离线重启
  幂等；7 轮失败中 fast path 进程行为全部正确（存活、日志健康、WSL 内部
  探针 200），失败发生在 Windows→WSL loopback 转发层（M14-24 时点
  14:15/14:30 已有同类双端点 5s 超时先例，早于 M14-25 合并）——定性为
  **新的 Windows→WSL loopback/relay 稳定性阻塞**，不宣称全绿；
- 建议下一片 **M14-26** 聚焦三件事：① **relay 稳定性**（wslrelay/WSL
  management 面修复或替代转发路径）；② **FunASR health facade/sidecar**
  （8010 前轻量探针，同时收口 M14-24 残余风险）；③ **避免监控
  history/insights 因 relay 层失败长期 skipped**（现状为固定词表 skip、
  不遮蔽，但 7 轮连跳使 history/insights 停更——下游降级策略需评估）。

### 8.7 证据文件与回填回合只读核对

gitignored canonical 工件（仅安全摘要入库；无绝对路径/容器 ID/密钥）：

| 文件（repo 相对路径） | 字节 | SHA-256 |
|---|---:|---|
| `.verify/artifacts/m14-25-production-acceptance/pre-restart-time.txt` | 35 | `0CE709B28D97B963B9E28DE429D1043A21654FEE790101ACD1923BC3D2029FBA` |
| `.verify/artifacts/m14-25-production-acceptance/pre-restart-manifest.json` | 430 | `E980D8A7A9AA7A0D585C3D5699561A4B4AB491669CD1D691A31F5F63C3B0D626` |
| `.verify/artifacts/m14-25-production-acceptance/pre-restart-status.txt` | 1100 | `1AFE771048272C2386C1D93AC7409962B407D1A4BD6BE4BE72A3D62FC05CDCBC` |
| `.verify/artifacts/m14-25-production-acceptance/pre-restart-docker.txt` | 709 | `D6A0DFFF195D2F762367C4106D7092AC4E07BF55723A8520C20985B20A41CCF3` |
| `.verify/artifacts/m14-25-production-acceptance/pre-restart-log-offset.txt` | 27 | `77AD23328D56D333F0D6C1FC95C621E796829F0261F7D895278F8CC30F9A8499` |
| `.verify/artifacts/m14-25-production-acceptance/pre-restart-manifest-sha256.txt` | 309 | `A8B32E0F172EEBA2EBAFD4659DA7D50CCB9F3C7616ADE82BFB4884D526B1A1A7` |
| `.verify/artifacts/m14-25-production-acceptance/restart-command.txt` | 338 | `D3902C5CF681DF3BF7C7D9DC7664A29899DD184BEAFD90B5D505B7B1BCE7A6F7` |
| `.verify/artifacts/m14-25-production-acceptance/restart-command-retry.txt` | 481 | `825A4609DC64D3F85EAD2C973532051A224EE6A00C4D2439CD6F35018289BAED` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-manifest.json` | 430 | `C074CEBEA1EC24BE63421C609790A2C996C39A6B561011790551C0558AD2A952` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-evidence-time.txt` | 35 | `F225EBAA962D98A64F7421769C1512B233D8EABEC517AB305DA0947BE5D4C757` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-health-body.json` | 50 | `E96232882F2014189A3F67EAB0569DAD88057F3599E5CA6F4CE257349800EEA0` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-health-headers.txt` | 125 | `3DB77F6F569F929C882F82E184E08568AE00BF9B56FFEA924910E5705CFB7F4D` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-live-body.json` | 54 | `AFC31D1E17D507B956677EA87EBA334EC01476B438AFFCA73E3727DB00BCD52D` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-live-headers.txt` | 125 | `6FCC2FEDAA9A8A28FDA4857E966741B453F0D83CD201E494C6A391EEAEBA410D` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-service-delta.log` | 12860 | `5B3FCAB5E1833574A17BFA7FDB992C8CDBD2AC9B035875D5A731D165454C7DD5` |
| `.verify/artifacts/m14-25-production-acceptance/restart-only-service.log` | 5154 | `E08B57ACF80503F55250C006F75D6E72DF4F89EA4BAC8EC84B5D120978C80567` |
| `.verify/artifacts/m14-25-production-acceptance/restart-only-forbidden-patterns.txt` | 46 | `C2D02E8A358EDC183EF85D141CF06AD18D424BFF503D687D02BFF96F2C8638B3` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-docker.txt` | 709 | `D6A0DFFF195D2F762367C4106D7092AC4E07BF55723A8520C20985B20A41CCF3` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-local-voice-smoke.txt` | 407 | `3C780FF73B3EEDCCF696F06B5A618D6378BBD63878A99591EE40C1D6197FC479` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-tts-curl.txt` | 52 | `9FD24FDE206A09C987D41D76DF00A0CA83333C35B4AD5033CB0073E3E4B8E2BC` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-tts-headers.txt` | 0 | `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-tts-validation.txt` | 29 | `49AAF1BB6C49C54DE243F2CAB2108882AD90623F5B43DDE9B65385FD678F3215` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-tts-provider.txt` | 148 | `75772283FFBAE0E0D5D02FF3A515AEEB30BCB928D8A4CDC943F175F63BA1C6E5` |
| `.verify/artifacts/m14-25-production-acceptance/post-restart-tts.wav` | 220844 | `E78CC2FE0AB4D894160033F1B6975F9B802275CCD0ADE990EDAE34C688AA4ABD` |
| `.verify/artifacts/m14-25-production-acceptance/monitoring/pre-restart-monitor-20260914-034502.json` | 15635 | `E1041DC49279C2B44B5A92E46959ADD39A580FBE9D17ED9964AE2CCB8171A0E3` |
| `.verify/artifacts/m14-25-production-acceptance/monitoring/pre-restart-pipeline-20260914-034504.json` | 5244 | `97CB5850C040C3CD166B0C19250AE724560CD1C5A4D8C4FC6F4810AD3D93CAAF` |
| `.verify/artifacts/m14-25-production-acceptance/monitoring/post-restart-monitor-20260914-040002.json` | 15721 | `0787A3C91C74DDAAC79EBDA171F3BA4536A59664822BBFB4F0E930E1638FE960` |
| `.verify/artifacts/m14-25-production-acceptance/monitoring/post-restart-pipeline-20260914-040021.json` | 4565 | `C4262B618B709E3C32589D056C5345CB457A28327EE84BAE4B223A2BDF347928` |
| `.verify/artifacts/m14-25-production-acceptance/monitoring/SHA256SUMS.txt` | 432 | `32EC2FBA9300731A32F63661AF32184B1C996B7D83B5A08BDE502BECCE44B465` |

（哈希为回填回合以本地 `sha256sum` 现场计算——输出小写、本文件统一转大写
呈现，十六进制大小写等价。`pre-restart-manifest-sha256.txt` 为验收时点一次
未成形的 PowerShell 格式化捕获，仅按原样留档、不作为事实依据；空文件
`post-restart-tts-headers.txt`（0 字节）对应 Windows 直连 curl 超时未收到
响应头。）

R1 追加核对（2026-09-14 下午，canonical 监控目录只读）——12:15–13:30 六轮
与 13:45 同签名轮的 monitor/pipeline JSON（12:00 轮哈希已在 §8.7 上表
monitoring/ 条目）：

| 文件（repo 相对路径） | 字节 | SHA-256 |
|---|---:|---|
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260914-041502.json` | 15719 | `83805B285BE017B14806B157B43E332B3B5FB2335430F94FBBFF60ED92343123` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260914-041527.json` | 4565 | `11B71F3792388B2132A0476CFF08F1D7AAE5D79195EE40084D2C7B62A289F5A7` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260914-043003.json` | 15725 | `D5DE761D0BFDBE262EF968E648D563BC7FAD709606214EF7AADB809EE27B471F` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260914-043021.json` | 4565 | `4F10BF6687527AAE0E293243172AE9AA42C043F8182915F7EF72A845F42A4635` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260914-044502.json` | 15721 | `16477793A46F76FE9101F9A99F14518944AA4E47AAF3187C28F23F6D0055682F` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260914-044515.json` | 4565 | `CB4F809E0F15526BB39F5B3951299EE7621BF9896A9148EBDC3F00583EE07374` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260914-050001.json` | 15719 | `E205A3AE1DE422E481B66F7EE66546443BE56520712031309CBF50796D91CF97` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260914-050013.json` | 4565 | `29F5B6EB9185DDF21301DBC75EDCDA9203783236692935A7F8E1DC10C44E0BB6` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260914-051522.json` | 15719 | `E89F8AE1F8F0FF542D1DA179F01EFDE7A1050EDA7F6DD108FC7C64CCCE689287` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260914-051537.json` | 4565 | `22E0817DEFD433C681E5C3C6AD764AABEE9228BE3EB1A617C1BAE5FDF3C2C355` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260914-053003.json` | 15723 | `932B9E2CE806CF2CB171DC1A4FCAD1CC05D7F08A804FEF602ABA7B30BC17DF44` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260914-053033.json` | 4565 | `CF4139312219C824CA06B4C5B91F309AF8C42D5451A2549271D7FB8415378BD2` |
| `.verify/artifacts/m14-12-production-monitoring/monitor-20260914-054502.json` | 15721 | `BAAC3CF5A7EDBA9BFE7F6F9BB50496FD70D622034B87C76B060771F1453C7AF9` |
| `.verify/artifacts/m14-14-monitoring-pipeline/pipeline-20260914-054514.json` | 4565 | `C0FEA5073754F049E2467910FD7DF48979F28463907BC6D78E6E9B18A4E32C11` |

R1 追加核对内容（全部通过）：上表 14 份 JSON 逐份解析——每轮 monitor
`overall_status=incomplete` 且 `partial=true`、compose_ps/containers/logs 三
采集器 ok、compose-service 阈值 6/6 healthy、web-root/web-login/api-health
三端点 200（延迟约 1.3–11.9ms）、funasr-health 与 cosyvoice-health 均
timeout/`TimeoutError`；每轮 pipeline `overall_status=failed`、monitor
exit 2、history skipped（`monitor-status-failed`）、insights skipped
（`history-status-skipped`）、lock acquired/released=true——与 supervisor
验收口径逐项一致。wslrelay.exe PID 17936、listener 启动时间 2026-09-12
20:44:46、`wsl.exe` 管理面 `WSL/Service/0x8007274c`/`TimeoutExpired` 为
supervisor 现场取证事实（本回填回合未执行任何进程/网络查询复验——零生产
触碰约束）。

本回填回合实际执行的只读核对（全部通过）：

- **git 谱系**：canonical main == `d54ad5b`；`git show` 核对 merge commit 完整
  哈希、双亲（`1f58800` + `98a2d77`）与 PR #104 合并主题；feature head 完整
  哈希逐字核对；
- **工件哈希链**：WAV 字节数（220844）与 SHA-256 独立重算，与验收留档
  `post-restart-tts-provider.txt` 内嵌值逐字一致（小写原文
  `e78cc2fe…aa4abd`）；上表 29 项工件哈希全部现场计算；monitoring 四份
  JSON 与 `monitoring/SHA256SUMS.txt` 内嵌值一致；
- **重启工件核对**：前后 manifest（PID 19132→26008、端口 8011、started_at
  11:52:45+08:00）；首次 stop fail-closed 文案（拒绝发送信号、rc=3、未启动
  新实例）与重试文案（TERM 优雅退出、新 PID 启动）逐条在场；
- **日志硬证据**：§8.3 各行在偏移增量（`post-restart-service-delta.log`）
  逐条原文在场；`FORBIDDEN_INSTALL_DOWNLOAD_PATTERN_MATCHES=0` 在场；
- **监控工件解析**：四份 JSON 解析核对 §8.5 各值（11:45 轮五端点 200 与
  延迟、12:00 轮 overall failed/exit 2、三采集器 ok + endpoints failed、
  六容器 healthy、web/api 三端点 200 与 5.791/1.582/1.918ms、两 voice 端点
  TimeoutError 与 5.0s 超时口径、lock acquired/released）；
- **未执行**：任何网络 CI 查询、任何生产服务/容器/voice 进程操作、任何
  计划任务命令（含只读 status——计划任务与 CI 事实为 supervisor 验收时点
  结果）、任何 monitor/history/insights/pipeline 执行面、任何网络部署。

### 8.8 边界（诚实口径）

1. **验收范围仅 offline fast path 重启幂等**：一次受控重启（含一次 fail-closed
   拒绝 + 一次成功重试）+ 偏移日志硬证据 + 禁用模式 grep + 稳态健康 + 一次
   主 API TTS 冒烟（与一次 smoke TTS 成功）——**不构成对语音链路 production
   readiness 的宣称，`production_ready=false` 不变**。
2. **不宣称 WSL localhost 转发长期稳定**：**12:00–13:30 共 7 轮自然监控
   pipeline overall failed 如实在案**（13:45 轮工件仍同签名；两 voice
   loopback 端点 5s 超时、history/insights 连续 skipped）；本验收中 Windows
   直连 curl 30s 超时与 smoke ASR health 未通过均与转发层相关，不得以
   「验收通过」掩盖、**不得宣称全绿**。
3. **Windows→WSL loopback/relay 稳定性是新生产阻塞**（wslrelay.exe PID
   17936 持有 8010/8011 listener、`wsl.exe` 管理面间歇
   `WSL/Service/0x8007274c`/`TimeoutExpired`；建议 M14-26 聚焦 relay 稳定性、
   FunASR health facade/sidecar、避免监控 history/insights 因 relay 层失败
   长期 skipped），**非 M14-25 回归**（归因边界见 §8.6）。
4. 反向用例（人为制造契约失败核对未命中路径）未在生产执行——§7 既定
   supervisor 裁量项。
5. 本回填为 docs-only：不改任何代码/测试/compose/阈值/基础设施，不启停任何
   进程/容器/计划任务，不遮蔽任何告警语义。
