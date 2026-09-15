# M14-26：WSL 语音健康 sidecar（双端口只读窄代理 + 受控生命周期）

- 切片：`feat/m14-26-voice-health-sidecar`，基于
  `070646fcc53e60bfeb5806a3e4fc8ac857121aa8`（本地 git 可验证；谱系 =
  `d54ad5b`（PR #104 merge，canonical main）→ `3e96d66`（M14-25 验收回填
  R0）→ `070646f`（R1 relay 失败事实追加，均 docs-only））。本开发回合独占
  worktree，仅做**一个本地 commit**；supervisor 审查与 remote 发布
  （push/PR/合并）在其后进行。
- 变更面：`tools/voice/voice_health_sidecar.py`（WSL 侧 sidecar，508 行）+
  `tools/voice/voice_health_sidecar_control.py`（Windows 侧编排控制器，
  1084 行）+ `services/api/tests/test_voice_health_sidecar.py`（105 项契约/
  生命周期测试）+ 本 README + `docs/PROJECT_STATUS.md` + `docs/ROADMAP.md`
  + `docs/CHANGELOG.md` + `tools/voice/README.md`。零改动现有引擎、relay、
  compose、监控与 `voice_service_control.py`。
- **零生产触碰（本开发回合）**：**零真实 WSL**（未调用 `wsl.exe`/WSL bash
  transport；测试全部 fake Runner/Transport/Health/Popen + importlib 装载，
  唯一例外是既有邻近套件自身设计内的真实 CLI 子进程测试，见 §6 flake
  记录）；**零真实网络或健康端点访问**（未发任何 HTTP 请求到
  8010/8011/18010/18011 或任何生产端点；上游/健康探测全部 fake）；**未
  inspect/stop/restart 任何生产进程**（FunASR、CosyVoice、wslrelay、Docker、
  WSL、CC Switch、代理、计划任务一律未触碰）。未启动本 sidecar（开发约束）。

## 1. 背景与动机（M14-25 回填实证，2026-09-14）

M14-25 生产验收回填（§8.5–§8.6）固化了新生产阻塞：**Windows→WSL loopback
转发层不稳**——12:00–13:30 共 7 轮自然监控 pipeline overall failed，两 voice
端点（funasr-health/cosyvoice-health）Windows 侧 5s `TimeoutError`，而 WSL
内部 FunASR `/health` 曾 200、CosyVoice PID 26008 持续存活（**不是引擎本体
死亡**）；Windows 侧 8010/8011 listener 由 wslrelay.exe PID 17936 持有，
`wsl.exe` 管理面间歇 `WSL/Service/0x8007274c`/`TimeoutExpired`。M14-25
回填建议 M14-26 聚焦 ① relay 稳定性 ② FunASR health facade/sidecar ③
避免 history/insights 因 relay 层失败长期 skipped。本切片实现其中的
**②：健康路径旁路**——不动 relay 本身（①需另行窗口），为监控提供一条
不经 wslrelay 的直达健康通道；③（监控侧切换端点）留待本切片合并复验后
由监控配置收口。

## 2. 设计与守护（范围 / 安全边界）

**部署形态**：sidecar（纯标准库，仅 WSL/Linux 内运行）绑定 eth0 私网 IPv4，
双端口窄代理：**18010 = FunASR → 恒 `http://127.0.0.1:8010/health`**；
**18011 = CosyVoice → 恒 `http://127.0.0.1:8011/health` 与
`/health/live`**。上游 URL 恒为模块常量 `PORT_ROUTES`，绝不取自请求——
结构上不存在通用 TCP/HTTP 转发能力。Windows 侧控制器以固定 argv 零 shell
编排 start/status/stop。失败可整体回收（stop + 清理 artifacts），部署面
零侵入现有引擎与 relay。

**绑定地址 fail-closed 链**（listen 之前，任何一环失败即退出、绝不退回
`0.0.0.0`）：解析 `/proc/net/route` 取默认路由接口 → UDP connect 探测源地址
（零发包，仅查内核路由表）→ 校验链：探测可用 → IPv4 → 非 0.0.0.0 → 非回环
→ 非链路本地 → RFC1918 私网（10/8、172.16/12、192.168/16）→ 落在默认路由
接口网段内。非法类别机器可读（`BindAddressError.reason`）。

**精确 GET allowlist**：18010 仅放行 `GET /health`；18011 仅放行
`GET /health` 与 `GET /health/live`。查询串/片段→400（防参数走私）、
allowlist 外路径→404、非 GET（POST/PUT/DELETE/PATCH/OPTIONS/HEAD）→405
（带 `Allow: GET`，拒绝后不复用连接）；生僻方法由基类 501 同样不代理。

**状态/manifest 文件安全**：仓库根 containment（`resolve_status_path`）、
`safe_join` 拒绝绝对路径/`..`/分隔符/符号链接越界、原子写（tmp `O_EXCL` +
`os.replace`，无 tmp 残留）、schema 版本化（status 与 manifest 均
`schema_version: 1`）、非普通文件/符号链接目标一律拒绝（悬空符号链接同样
fail-closed）。

## 3. 代理行为（bounded，零代理 opener）

- **零代理 opener**：sidecar 上游探测与控制器健康探测均用
  `urllib.request.build_opener(ProxyHandler({}))`——继承的
  `HTTP_PROXY`/`http_proxy`/系统代理不得劫持 `127.0.0.1` 探测（同
  `voice_service_control` 修正轮 2 实证口径）。
- **有界 I/O**：上游 socket 超时 `UPSTREAM_TIMEOUT_SECONDS=5.0`（对齐监控
  GET 5s 口径）；响应体读取上限 `MAX_UPSTREAM_BODY_BYTES=65536`。
- **响应映射**：上游 2xx→透传（体/Content-Type 原样）；上游非 2xx（含
  503 loading）→**原状态码字节级透传**（上游降级如实可见）；超时→504、
  连接失败→502、未知异常→502 安全降级。错误体为固定文案
  （`{"detail": "upstream timeout"}` 等）——不外泄异常细节、不读生产日志。

## 4. 控制器生命周期行为

- **命令纪律**：仅固定 allowlist 的 `wsl.exe` argv 形态（start/probe/signal
  三类，argv 列表直传、零 shell——AST 测试扫描两文件全部 subprocess 调用
  无 `shell=`）；信号名 Python 侧白名单（SIGTERM/SIGKILL）；Windows 上
  `CREATE_NO_WINDOW`。
- **start 幂等**：manifest 归属成立（PID 活 + 身份标记 + 双端口健康 200）
  →跳过；存活但健康未全 200（启动中/上游降级）→同样跳过；PID 已死/被无关
  进程复用/manifest 损坏→清理文件（绝不发信号）后全新启动。spawn 后等待
  status 落档（20s 上限）→ status 事实核验（PID/双端口/schema）→ 身份
  核验（cmdline 双标记）→ manifest 原子落档。
- **stop 终止序列**：TERM → 有界宽限（10s 内 `/proc` 消失即优雅退出）→
  单次 KILL 兜底 → 有界确认（5s）；TERM/KILL 送达失败或 KILL 后仍存活→
  manifest 保留以便重试。绝不 pkill/killall/fuser/按端口杀。
- **生产保护硬边界（先于一切探测，零信号）**：`PROTECTED_PIDS =
  {867, 26008}`（FunASR 867 / CosyVoice 26008，M14-25 验收时点 PIDs）；
  `PROTECTED_MARKERS`（wslrelay/wsl.exe/wslservice/vmmem/docker/funasr/
  cosyvoice/bootstrap 脚本/systemd）命中即拒绝；身份不符（PID 被无关进程
  复用）同样拒绝发信号。kill 目标恒为 manifest 核验过的单个 PID 或本次
  spawn 的 wsl.exe 句柄。
- **Windows 句柄回收 + Linux 侧有界补回收**：启动失败路径 `kill_spawned`
  只回收**本次 spawn 的** wsl.exe 句柄；因 `Popen.kill()` 不保证 WSL 内
  子进程终止，仅当「本次落档 PID + 探活 + 无保护标记 + 身份标记全过」才
  补发**单次 SIGTERM**（零宽限、零 KILL、零重试）；核验不可用（WSL 管理面
  已失败）/保护 PID/身份不符一律放弃回收并记录——盲发信号比泄漏更危险。
- **WSL 管理面失败统一分类**：`wsl-management-unavailable`，单次尝试不
  重试，rc 3 拒绝。并发 start/stop 经 `ControlLock`（O_EXCL）串行化，
  崩溃残留锁 TTL 600s 后回收。status 恒只读（不写不杀不清理，含残留
  status 文件/损坏 manifest 只报告）。
- **退出码**：0 成功/幂等无操作；1 操作失败；2 参数错误；3 安全拒绝
  （保护目标/身份不符/并发锁/WSL 管理不可用）。

## 5. 测试覆盖（105 项，三片；全部 fake/临时文件/importlib）

| 片 | 锁定 |
|----|------|
| 契约（常量+HTTP 面+绑定地址+文件安全+固定 argv） | `PORT_ROUTES` 精确形状与上游恒 127.0.0.1；控制器 `EXPECTED_PORTS`/`PROTECTED_PIDS`/`IDENTITY_MARKERS`/`PROTECTED_MARKERS` 同源事实；GET 成功透传/503 字节级透传/超时 504/连接失败 502/未知异常 502 不泄密/查询串 400/未知路径 404/非 GET 405+Allow 头；`/proc/net/route` 样本解析（表头/垃圾行/metric 优先级）与 8 类不安全绑定地址逐一拒绝；status 文件 schema/原子替换/目录与符号链接目标拒绝/路径穿越拒绝；三类 wsl.exe argv 逐元素锁定；AST 扫描零 `shell=`；`CREATE_NO_WINDOW`；身份/保护标记谓词；`evaluate_target` 六类分类（own/reused/protected/dead/runner-error × 含 PID 867/26008 硬保护优先于身份标记）；`safe_join` 越界与符号链接拒绝；`atomic_write_json` 原子性与目标拒绝；Manifest roundtrip |
| 生命周期（fake Runner/Health/Popen） | cmd_start：健康/降级幂等零 spawn、stale/reused/损坏清理零信号、保护 PID（867/26008）零探测零信号零 spawn、全新 spawn 成功落 manifest 释锁、status 超时只回收本次句柄、status 事实异常（含落档 PID=867/26008）回收、身份核验失败回收零信号、RunnerError 三阶段分类 rc 3；cmd_stop：TERM 优雅退出、TERM 超时单次 KILL、KILL 后仍存活保 manifest、TERM/KILL 送达失败 rc 3、保护 PID/保护标记/身份不符拒绝零信号、stale 清理、无 manifest 幂等；cmd_status：只读零清理（stopped/stale/pid-reused/protected-target/unknown/损坏/unsafe/running-healthy/running-degraded）；ControlLock 并发拒绝零副作用 + 陈旧锁回收 |
| 生产修复（红转绿） | status 等待中 RunnerError 分类回收（rc 3，句柄不裸穿）；Linux 子进程有界补回收：身份核验通过才单次 TERM、核验不可用放弃并记录、保护 PID 零信号、manifest 写入被拒分支同样有界补回收、TERM 送达失败不改退出码不升级 KILL |

测试纪律：`importlib` 装载（先注册 `sys.modules` 再 exec，dataclasses 字符串
注解可解析）；`object.__new__` 直构 handler（零 socket）；时延常量
monkeypatch 至毫秒级（`fast_timing` fixture）保持快速；全部临时文件
（`tmp_path`）；符号链接测试平台不支持时 skip。

## 6. 验证命令与结果（2026-09-15，开发回合实测）

解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`
（Python 3.11，pytest 9.1.1，ruff 0.16.5）。工作目录：本 worktree 仓库根。

| 命令 | 结果 |
|------|------|
| `python -m pytest services/api/tests/test_voice_health_sidecar.py` | **105 passed in 0.73s**（初跑） |
| `python -m pytest services/api/tests/test_voice_health_bridge.py services/api/tests/test_voice_service_control.py` | 第 1 轮：**1 failed, 77 passed in 37.38s**（见下方 flake 记录）；复跑：**78 passed in 17.73s** |
| `python -m ruff check tools/voice/voice_health_sidecar.py tools/voice/voice_health_sidecar_control.py services/api/tests/test_voice_health_sidecar.py` | 第 1 轮：**10 errors（exit 1）**；修复后复跑：**All checks passed!（exit 0）**；再复跑 sidecar 套件 **105 passed in 0.69s** |
| `python -m py_compile tools/voice/voice_health_sidecar.py tools/voice/voice_health_sidecar_control.py services/api/tests/test_voice_health_sidecar.py` | **exit 0**（修复前后各一次） |
| `git diff --check` | **exit 0**（无空白错误；修复前后各一次） |
| `python -m ruff check tools/voice/voice_service_control.py`（基线邻居口径核对） | **All checks passed!**——证明本仓库 tools/voice 的 ruff 门槛是默认规则全过 |

### 6.1 邻近套件环境 flake 记录（既有测试，非 M14-26 引入）

`test_cli_status_stopped_subprocess`（`test_voice_service_control.py`，既有
CLI 子进程测试——该测试自身设计即启动真实 `voice_service_control.py` 子进程，
其 status 路径经真实 `BashOps` 调 `wsl.exe bash -c ss` 做只读端口探测）：

1. 第 1 轮全套：失败——`assert 1 == 0`，stdout 含
   `bash transport 不可用: TimeoutExpired`（真实 WSL bash transport 超过
   其 60s 超时；与本机 M14-25 实证的 wslrelay/WSL 管理面不稳同源环境现象）；
2. 同会话单测复跑：再次失败（32.32s）；
3. **新会话单测复跑：1 passed in 12.96s**——判定 flaky/环境性；
4. 全套复跑：**78 passed in 17.73s**。

归因：失败测试文件与 M14-26 三个文件零交集（M14-26 不 import
`voice_service_control.py`，亦未改动它），失败由真实 WSL transport 慢导致、
复跑通过——**非 M14-26 引入，未做任何代码修正**。按硬边界约束，未为使该
测试通过而触碰任何 WSL/生产进程（探测是测试自身设计内的只读行为，本回合
仅按用户指令重跑该测试）。

### 6.2 ruff 修复记录（10 项，全部行为保持等价的窄修正）

仓库无 ruff 配置文件（默认规则集，ruff 0.16.5）；基线邻居
`voice_service_control.py` 默认规则全过即本仓门槛。10 项：

- **FLY002 ×3**（测试文件）：`"\n".join([...]) + "\n"` → 拼接字符串字面量
  （路由表样本常量，内容逐字节相同）；
- **UP035 ×1**（sidecar）：`from typing import Sequence` →
  `from collections.abc import Sequence`；
- **FURB161 ×1**（sidecar）：`bin(int(row.mask)).count("1")` →
  `int(row.mask).bit_count()`（Python 3.11 等价）；
- **UP041 ×1**（sidecar）：`except (socket.timeout, TimeoutError)` →
  `except TimeoutError`（`socket.timeout` 是其别名）；
- **F541 ×4**（控制器）：无占位符的 f-string 去掉 f 前缀（字面量不变）。

每项修正后复跑：ruff 全过、sidecar 套件 105 passed、py_compile exit 0、
`git diff --check` exit 0。另注：本回合曾对不存在的
`tools/voice/voice_health_bridge.py`（该名是测试文件而非工具）误跑 ruff 得
E902，与上述五项门槛无关，据 `ls tools/voice/` 更正命令后无影响。

## 7. 边界（诚实口径）

- **开发验证是本地/契约验证**：全部行为经 fake Runner/Transport/Health/
  Popen 与样本数据锁定。**零生产变更/启停/重启、零真实网络或健康端点访问、
  未 inspect/stop/restart 任何生产进程**；M14-26 专属验证零真实 WSL——
  开发回合整体口径的唯一例外是既有邻近套件自身设计内的只读 wsl.exe bash
  端口探测（一次环境性超时、复跑通过，见 §6.1）；本 sidecar 在开发期
  **从未启动**。
  真实部署与端到端验证（WSL 内实跑、eth0 实际绑定、监控端点切换、wsl.exe
  编排实路径）**需 supervisor 合并后在获准窗口受控复验**。
- PROTECTED_PIDS（867/26008）是 M14-25 验收时点的生产 PIDs 硬编码——
  引擎重启后 PID 会漂移，届时保护主要依赖 PROTECTED_MARKERS 与 manifest
  归属核验；复验窗口应核对当前生产 PIDs 并按需更新常量（契约测试同源锁定）。
- 本切片不修 relay 本身（M14-25 §8.6 建议 ①），不改变监控当前端点配置
  （建议 ③）——两者另行收口；不宣称 WSL localhost 转发长期稳定。
- 8010/8011 若上游不可达，sidecar 如实返回 502/504（有界、固定文案）——
  区分「上游引擎降级」与「转发层故障」正是本切片的观测目的。
- `production_ready=false`；不构成语音链路 production readiness 宣称。

## 8. 复验指引（supervisor 合并后，需获准窗口）

1. `python tools/voice/voice_health_sidecar_control.py status`（只读）核对
   初始 stopped；2. `start` 后 WSL 内核对 sidecar 进程、eth0 绑定与 18010/
   18011 监听、status/manifest 落档；3. Windows 侧直连
   `http://<bind>:18010/health` 与 `:18011/health`（经 sidecar，不经
   wslrelay）核对 FunASR/CosyVoice 状态透传（200/503 如实）；4. `start`
   幂等复跑、`stop` 后端口释放与 artifacts 清理；5. 反向：伪造 manifest
   指向保护 PID 核对拒绝。全程不得以任何手段绕过生产保护边界。
