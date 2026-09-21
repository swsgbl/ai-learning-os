# M14-84 Harmony 模拟器真实 API 后端冒烟（实现/文档切片，未签名、模拟器专用）

- 切片：分支 `harmony/m14-84-emulator-real-api-smoke`（独立 worktree
  `m14-84-harmony-real-api-smoke`，基于 current main
  `ddcaa229d308a8e5a46e46dae3a9d7a10ac6640e`（PR #173 merge，M14-86 合入后），
  rebase 后单 local commit，不 push、不建 PR）。
- 目标：新增设备驱动后端冒烟工具 `tools/harmony_release/backend_smoke.py`，
  在**本地模拟器**上对**本机 loopback 开发后端**（`http://127.0.0.1:8000/`，
  宿主侧 uvicorn 开发面，非生产容器栈）做真实端到端验证（安装 → 设置页输入
  API 地址 → 冷重启回 Home → 断言真实 API 数据渲染 → 清理卸载），并以
  全 fake 注入的单测锁定工具契约。
- **明确边界：未签名 HAP、仅本地模拟器（loopback target 127.0.0.1:5555）、
  仅本机开发后端——本切片不声明 Harmony 生产就绪，不构成任何发布/部署授权；
  零生产容器/DB/MinIO/语音/secret 接触。**

## 1. 交付物（全部真实入库）

| 文件 | 内容 |
|------|------|
| `tools/harmony_release/backend_smoke.py`（1142 行） | 设备驱动冒烟：宿主 API 预检 → `hdc install` → `aa start` → 设置页自动输入 API 地址（uitest 驱动）→ 冷重启（`aa force-stop` + `aa start`）→ Home 布局关键词断言 → 后台化 → 卸载清理。默认 dry-run（仅宿主预检零设备触碰），`--confirm-mutation` 才触设备；`--api-base` 仅接受 loopback 起点 |
| `tests/harmony_release/test_backend_smoke.py`（536 行，22 测试） | 工具契约全 fake 注入（fake hdc / fake API / fake layout），零设备依赖 |
| `apps/harmony/entry/src/main/ets/AiosApi.ets` | 应用侧真实缺陷修复（见 §3） |

## 2. 真实执行结果（全部真实，exit code 可复核）

| 步骤 | 结果 |
|------|------|
| 冒烟确认（confirm，target `127.0.0.1:5555`，unsigned HAP） | **`status=ok`、`exit=0`，7 步全部 ok**：host_preflight / install / start / settings_ui / home_view / background / uninstall；cleanup ok，bundle 已卸载 |
| Home 断言明细（`counts`） | **`"0.1.0": 1`、`"请求失败 (HTTP 401)": 3`**——真实后端的诚实答案：`/api/v1/version` 渲染 0.1.0；auth-gated 端点（audit / ops-snapshot / privacy）在未认证状态如实渲染 401 |
| 冒烟 HAP | `entry-default-unsigned.hap`，452,587 bytes，SHA-256 `88E5427D71AC64DBF7067C40AD6ADE8AE2E34F46075B09C5A89DBE3C1FCD593E`（**未签名**，`signedness_verified=false` 如实） |
| 聚焦测试 | `pytest tests/harmony_release/test_backend_smoke.py`：**22 passed**（2026-09-22 复跑） |
| 全量 harmony_release | **449 passed, 1 skipped**（1 skip 为套件既有条件跳过，口径与 M14-82 一致） |
| ruff | `ruff check --select F,E9,W605` 通过 |
| 空白检查 | `git diff --check` 干净 |

## 3. 冒烟发现并修复的应用侧真实缺陷

首次 confirm 失败（`home_view: failure`，`home_assertion_missed`）暴露
`AiosApi.ets getJson` URL 拼接缺陷：`base`（以 `/` 结尾）+ `endpoint`（以
`/` 开头）产生 `//health` 双斜杠路径 → FastAPI 返回 404（宿主直连验证：
`//health`=404、`/health`=200），Home 全部区域渲染 404。修复：拼接前剥掉
base 尾部 `/`。重建 HAP 后重跑 confirm 全绿。此为冒烟工具真实抓出的
端到端缺陷，证明链路有实际检出能力（非恒真断言）。

## 4. 已知失败模式（工具注释与单测覆盖）

- **IME 遮挡**：设置页输入后输入法面板遮挡底部 tab bar，布局 dump 无 tab
  文本——home_view 因此采用冷重启（force-stop + start）而非点击 tab。
- **home_assertion_missed**：Home 布局缺预期关键词即 fail-closed 报
  `home_assertion_missed`（§3 首次失败即该模式）。

## 5. 原始证据（gitignored `.verify/m14-84-harmony-real-api-smoke/`，本 README 为唯一入库证据文件；不含 secrets，布局原文不入库）

| 文件 | bytes | sha256 |
|------|-------|--------|
| `confirm.json`（最终 confirm，status=ok） | 6451 | `9E443EEEFA900CDBC0DD630B5C271E5697BB689293C88F18F3FCEC6830F8E11F` |
| `backend-smoke-confirm-5555.json`（首次失败 confirm，双斜杠缺陷证据） | 7966 | `9EF6FC73AA97B5D5F07153898FA29DEA9940849B0BBCE18829BA9C8E41398449` |
| `backend-smoke-plan-5555.json`（dry-run 计划，7 步全部 not_run 零设备触碰） | 12577 | `B563EA99590CFF72E47DA1426618DEA1A38C66B52D4239720285D617CD50B027` |
| `release-build.json`（冒烟 HAP 重建，clean/assemble exit 0） | 1077 | `06CCA40E7EBBC423D45497F10B812FA55A4861D05663E21A24B9DEA44F23399C` |
| `backend_smoke_layout_home.json`（Home 布局采样） | 62584 | `67018128B522BA82511C5541BB385833EC5D34C0C2A4F8952703711ACAAA2BFE` |
| `backend_smoke_layout_settings.json`（设置页布局采样） | 44241 | `F27BCD0B68B4B7E8D8C2AA65939C413B8BAF9C3E441AEE6BD50F442F8A983C53` |
| `confirm.stderr.txt` | 247 | `328A427C41DA478314E7C49FA232FA24057CA0122E6A80F0C6B7065787968E4B` |

（同目录其余 probe 脚本与中间布局为过程现场；全量 pytest/ruff 运行日志
在 `.verify/m14-84-r7-*.log`。）

## 6. 诚实边界

- **未签名 / 无 AGC 材料 / 不声明生产就绪**：产物为 unsigned HAP；全程零
  签名材料接触。
- **仅本地模拟器**：loopback target；真机模式未验证（与本仓 device_preflight
  对 loopback 的拒绝语义一致）。
- **后端为本机开发面**（loopback uvicorn，非生产容器栈）；401 渲染是
  未认证状态下的**预期诚实结果**，不是错误态。
- 全量 449 passed / ruff / git diff --check 为 2026-09-21 晚执行窗口的
  记录；2026-09-22 验收轮复跑聚焦 22 passed，全量未重跑（如实）。
- 单 commit 本地提交，未 push；commit hash 以最终 amend 结果为准（rebase
  前的旧 hash 不作为引用锚）。
