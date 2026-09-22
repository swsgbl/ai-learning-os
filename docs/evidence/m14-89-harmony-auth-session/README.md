# M14-89 Harmony 认证会话（auth session）证据

切片：`m14-89-harmony-auth-session` worktree（基于 main `5ff1e68`，PR #174 merge）。单 local commit，不 push。
原始证据 gitignored `.verify/m14-89-harmony-auth-session/`（本 README 为唯一入库证据文件）。

## 交付

- `tools/harmony_release/auth_smoke.py`：认证会话 UI 冒烟工具（计划/确认两态，`--confirm-mutation` 才触设备；凭据/令牌永不入档；fail-closed 契约继承 device_smoke/backend_smoke）。
- `tools/harmony_mock/server.py` + `test_contract.py`：mock 认证服务（`--auth` 开启登录/门禁/401 契约；合成固定凭据，非真实 secret）。
- 应用侧 `AuthPane.ets` / `AuthSession.ets` / `TokenVault.ets`（安全存储会话恢复/登出）、`AiosApi.ets`/`Index.ets` 接入。

## 轮次事实（R12/R13/R14b/R14d）

- **R12（auth-off，已接受）**：`auth_smoke_off_r12.json` — exit 0，total=12，ok=7，not_run=5（auth_phase_skip ×5），warnings=0，request_failures=0，mutation/cleanup=true；HAP 570027 bytes SHA256 `6666F3BE…685`。
- **R13（auth-on，已接受）**：`auth_smoke_on_r13.json` — exit 0，total=12，ok=11，failure=0，not_run=1（唯一 auth_off_local/auth_phase_skip），warnings=0，request_failures=0，mutation/cleanup=true；同 R12 签名版 HAP（570027 bytes `6666F3BE…685`）。
- **R14b（重链静态验证，已接受）**：契约 79/79；android mock pytest 36 passed；全量 `tests/harmony_release` 通过；compileall/ruff（F,E9,W605）通过；release 构建 exit 0 → **unsigned HAP 219684 bytes SHA256 `D1FB2C304A2EA60D4D1125CB779CB823A9E81DC348FE580CE0666C250346EFC1`**。旧 mock 68252 安全停止；当前 worktree auth-on mock PID 77624 启动（8765）。R14b/R14c 因宿主/模型中断未出冒烟结论。
- **R14d（本轮，auth-on 新 HAP 真冒烟）**：
  - 预检：PID 77624 = `python tools\harmony_mock\server.py --host 0.0.0.0 --port 8765 --auth`，CreationDate 2026-09-22T10:19:08 与本 worktree `mock_authon_r14b.{out,err}.log` 写入时刻一致；唯一 8765 监听者；`GET /api/v1/auth/status` → 200 `{"auth_enabled":true}`。
  - 冒烟（`run_r14d_smoke.cjs` 包装，target 127.0.0.1:5555，`--confirm-mutation`）：**exit 0；total=12，ok=11，failure=0，not_run=1（唯一 auth_off_local/auth_phase_skip）；warnings=0；request_failures=0；mutation_performed=true；cleanup_attempted=true**；HAP SHA256 与 R14b 产物逐字节一致（219684 `D1FB2C30…EFC1`）。**未签名 HAP 在该模拟器直接安装成功——未触发 sign_hap 回退链，无签名材料接触/暴露/入库**。产物：`r14d_auth_smoke_on.{stdout,stderr,exit}.txt`、`auth_smoke_on_r14d.json`。
  - 清理：仅停止经证明的 PID 77624（`r14d_mock_stop_proof.txt`：停后进程消亡、8765 端口释放）；模拟器（Emulator PID 30000）与 net.uniterm.poc 未触碰。
  - 意外文件 `nol`（SHA256 `8C70A5CB3F1532602E9AEA52EDE373AB33721AF8F7D41899B228AB218CA2EC6B` 校验一致，内容为 node -e TypeError 崩溃输出）按意外 node 错误输出删除。
  - 秘密扫描：新增行 591 条，命中 1 = `test_contract.py` 的 `Bearer not-the-mock-token`（负向测试假令牌，非 secret）；`git diff --check` / `--cached --check` 干净；无 .p12/.p7b/.cer/.csr/.jks 入库。

## 轮次事实（R16/R17/R18 监督者修正）

- **R16（三项修正落地）**（依据 `SUPERVISOR/_FEEDBACK.md`）：
  1. 新增焦点测试 `tests/harmony_release/test_auth_smoke.py`（34 用例：plan-only 无 HTTP/无 hdc/12 步全 not_run、auth-on 脱敏契约、`validate_api_base` 绕过矩阵、mutation/request/toolchain fail-closed 路径）。
  2. `validate_api_base` 弃用前缀匹配，改为真实 URL 解析：仅精确 loopback 主机；userinfo/query/fragment/path 绕过一律拒绝；保留归一化尾斜杠；永不回显原始 URL。
  3. 客户端登录载荷须 `token_type == "bearer"` 才建立任何内存/安全存储会话（`AuthSession.ets`，检查先于 bearer 写入与 vault 保存）；mock 契约新增 wrong/missing token_type 故障覆盖。
- **R16 现场清理**：在 `_host_auth_contract` 内发现一段引用未定义符号的坏代码（上轮中断遗留的未跟踪脚本注入所致，任何 auth-on 契约执行都会 NameError），外科式移除，保留干净的 `token_type` 检查实现。
- **R17 验证（全绿）**：焦点 34/34；mock 契约 81/81（含 2 个新 token_type 故障）；全量 `tests/harmony_release` 483 passed + 1 skipped；py_compile 干净；ruff F,E9,W605 干净；`git diff --check` 干净；clean/assembleHap exit 0。`server.py --fault` CLI choices 同步补齐两个新故障（此前契约测试进程内直设未暴露）。
- **R17 auth-on 模拟器冒烟**（target 127.0.0.1:5555，仅自有 loopback mock，`--confirm-mutation`）：首跑 exit 1（`wrong_password_error_missing`，错误文案渲染时序 flake——R14d/R17 布局节点差恰为 1 个错误文案节点，客户端 401 路径未改动）；立即复跑 **exit 0，total=12，ok=11，failure=0，not_run=1（唯一 auth_off_local/auth_phase_skip），warnings=0，request_failures=0，mutation_performed=true，cleanup_attempted=true**。证据 `auth_smoke_on_r17.json`。
- **R18 清理**：仅停止 R17 冒烟自有 mock 进程并留证（`r18_mock_stop_proof.txt`：进程消亡 + 8765 端口释放）；模拟器与无关进程未触碰；删除全部遗留临时脚本。

## 中断记录

R14b/R14c 两轮宿主/模型层中断致冒烟未出结论；R14d 在同一 worktree 恢复执行（未 reset/revert 任何有意修改），一次通过。R16 轮曾发现上一轮中断遗留脚本注入的坏代码块（已移除，见上）；R17 轮一次宿主中断后 R18 就地恢复（仅补停止证明/文档/收尾，未重跑冒烟——R17 证据有效）。

## 诚实边界

- **未签名 HAP**（emulator 接受；signedness 边界同 M14-82/M14-84：不构成发布/签名授权）；本轮未使用任何 AGC/签名材料。
- **mock 认证边界**：登录/令牌/门禁 401 全部针对本仓库 mock 服务器的合成凭据，非真实生产认证。
- `production_ready=false`；不声明 Harmony 生产就绪；零生产容器/DB/MinIO/语音/secret 接触。
