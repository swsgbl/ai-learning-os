# M14-139 Harmony current-main 生产栈只读后端冒烟（证据刷新，未签名、模拟器专用）

- 切片：分支 `harmony/m14-139-production-readonly-smoke`（独立 worktree
  `m14-139-harmony-production-readonly-smoke`，冒烟实际执行于 current main
  `f530ac385ca04d515bd40e903afa4814776a67a7`（PR #225 merge，M14-138 合入后），其后 rebase
  至 `8563717cb4dec2b8f7569c3565034d9cb4da15f5`（PR #226 merge；基座增量仅 M14-137 ops
  工具/测试/文档，零 Harmony 生产代码改动；
  `.verify` 原始证据与哈希逐字节不变），
  单 local commit，不 push、不建 PR）。
- 目标：与 M14-84 的旧 dev-backend 证据区分——用**current main** 的既有
  `tools/harmony_release/backend_smoke.py`（零工具改动，原样复用）对
  **当前正在运行的本地生产 API 容器栈**（host loopback
  `http://127.0.0.1:8000/`）做一次 current-main 生产栈只读证据刷新。
  **这不是 Harmony 生产发布声明。**
- **明确边界：未签名 HAP、仅本地模拟器（127.0.0.1:5555）、宿主侧仅只读
  GET；零生产容器/DB/MinIO/语音/调度器/secret/Android USB/CC Switch 变更。**

## 1. 生产前置条件（只读核实，digest 全部匹配才执行）

| 项 | 值 |
|----|----|
| API 容器 | `aios-m14-03-production-rehearsal-api-1`，image `aios/api:m14-124-production`，digest `sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f` |
| Web 容器 | `aios-m14-03-production-rehearsal-web-1`，image `aios/web:m14-124-production`，digest `sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b` |
| digest 核对 | 与预期 M14-124 digest **完全一致**（无漂移，未触发 BLOCKED） |
| `GET /health` | `{"status":"ok","service":"ai-learning-os-api"}`（HTTP 200） |
| 容器状态 | Up 20 hours (healthy)（全部 aios-m14-03-production-rehearsal-* 容器；只读观察，未触碰） |
| 模拟器 | 既有本地实例（loopback target `127.0.0.1:5555`），`param get bootevent.boot.completed` = true |

宿主侧匿名只读 GET 预检（curl，工具前置同口径）：
`/health`=200、`/api/v1/auth/status`=200、`/api/v1/version`=200（0.1.0）、
`/api/v1/system/privacy`=401、`/api/v1/system/ops-snapshot`=401、
`/api/v1/audit?limit=100`=401。

## 2. current-main unsigned HAP

| 项 | 值 |
|----|----|
| 构建 | `harmony_build`（hvigor assembleHap，`BUILD SUCCESSFUL in 7s`，34 tasks） |
| 产物 | `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap` |
| 大小 | 533,946 bytes |
| SHA-256 | `21BF5950547381DB5BF0EF3D5347FB640B12418F7C30114B34379F447A970BB0`（Get-FileHash 独立复核一致） |
| 签名边界 | `build-profile.json5` `signingConfigs: []`——**unsigned**，`signedness_verified=false` 如实 |

## 3. 冒烟执行（现有工具，零改动，一次通过）

命令（worktree 根）：

```
python tools/harmony_release/backend_smoke.py \
  --target 127.0.0.1:5555 \
  --hap apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap \
  --confirm-mutation \
  --evidence-dir .verify/m14-139-harmony-production-readonly-smoke
```

结果：**`status=ok`、`exit=0`、`preflight=True`、`cleanup=ok`** ——
7 步全部 ok：host_preflight / install / start / settings_ui / home_view /
background / uninstall；49 条设备命令；bundle `com.ailearningos.app`
已卸载（`device_side_state.bundle_uninstalled=true`）。

Home 断言明细（`counts`，未认证状态的真实生产 API 诚实答案）：

- `"0.1.0": 1` —— 生产 `/api/v1/version` 渲染版本数据；
- `"请求失败 (HTTP 401)": 3` —— privacy / ops-snapshot / audit 三个
  auth-gated 区域如实渲染 HTTP 401。

设置页确认：`saved_confirmed=true`（已保存 `http://10.0.2.2:8000/`，
41 次 UI 动作）。宿主前置 6/6 matched（200/200/200 + 401/401/401）。
无任何意外失败（`failures=[]`）。

## 4. 测试与静态检查（全部真实执行）

| 检查 | 结果 |
|------|------|
| `pytest tests/harmony_release/test_backend_smoke.py` | **22 passed** |
| `pytest tests/harmony_release`（全量） | **524 passed, 1 skipped**（1 skip 为 Windows FIFO 既有条件跳过，与 M14-126/M14-138 基线一致） |
| `uvx ruff check --select F,E9,W605 tools/harmony_release tests/harmony_release` | **All checks passed!** |
| `python -m py_compile tools/harmony_release/backend_smoke.py` | 通过 |
| `git diff --check` | 干净 |

本切片零 Python 源码改动（ruff/py_compile 为合同要求的一致性复核）。

## 5. 原始证据（gitignored `.verify/m14-139-harmony-production-readonly-smoke/` 等，本 README 为唯一入库证据文件；不含 secrets，布局原文不入库）

| 文件 | bytes | sha256 |
|------|-------|--------|
| `.verify/m14-139-confirm.json`（confirm，status=ok） | 6451 | `0A2505F7EAA36F1EE82D584617B0217A9C70801C52DF7875EC7C0DC11B0FE052` |
| `.verify/m14-139-confirm.stderr.txt` | 247 | `0B51BE84C5607420D56ED99F762BB9087E73551AFC51048919C358C21160C709` |
| `.verify/m14-139-harmony-production-readonly-smoke/backend_smoke_layout_home.json` | 62419 | `657EECD7BF3919192C88DFFC15FF124516ABE782E4D19ECF37F81D1CBA3EA5C4` |
| `.verify/m14-139-harmony-production-readonly-smoke/backend_smoke_layout_settings.json` | 66965 | `EE140F9E2358080FA7D9A928782B253B550AF153844A933E4C30D97D18185A0F` |

## 6. 诚实边界

- **未签名 / 无 AGC 材料 / 不声明签名或真机结果**：产物为 unsigned HAP
  （533,946 bytes），全程零签名材料接触。
- **仅本地模拟器**：loopback target 127.0.0.1:5555；真机模式未验证。
- **后端为本地生产容器栈的 loopback 面**（aios-m14-03-production-rehearsal
  API，M14-124 digest 零漂移）；设备侧经模拟器 loopback 别名
  `10.0.2.2:8000` 访问同一 API。401 渲染是未认证状态下的**预期诚实结果**。
- 本切片是 current-main 生产栈只读证据刷新，**不构成 Harmony 生产发布
  声明**；M14-138 结论保持不变；M14-137 已随 PR #226 合入 main（冒烟
  执行时原基座上仍为 pending，本切片对其内容零改动）。
- 单 commit 本地提交，未 push。
