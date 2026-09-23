# M14-99 Harmony Post-Login Regression — Evidence

## 1. 执行环境

| 项目 | 值 |
|------|-----|
| 目标设备 | HarmonyOS 模拟器 `127.0.0.1:5555` |
| 执行方式 | `tools/harmony_release/auth_smoke_launcher.py` |
| Launcher 状态 | `executed` |
| HAP 大小 | `533946` bytes |
| HAP SHA256 | `91596B12DA6871C41B8581B7B3D6D51549B352585B701DFB9C99FFA67701C21F`（未签名） |
| 宿主后端 | OS 动态分配 loopback 端口 `57250`（事实，非秘密） |
| 设备侧 URL | `http://10.0.2.2:57250/` |

## 2. 冒烟结果摘要

```
total=12  ok=11  failure=0  not_run=1
```

| 步骤 | 结果 |
|------|------|
| install | ok |
| start | ok |
| settings_ui | ok |
| home_view | ok |
| wrong_password | ok |
| login | ok |
| cold_restart | ok |
| logout | ok |
| background | ok |
| uninstall (cleanup) | ok |
| auth_off_local / auth_phase_skip | **not_run**（唯一跳过项） |

- `warnings=0`
- `request_failures=0`
- `mutation_performed=true`
- `cleanup_attempted=true`

## 3. 轮次史

| 轮次 | 结果 |
|------|------|
| 首次 launcher 尝试 | `server_start_failed`（auth_smoke_server 启动失败） |
| 重跑 | 成功（11/12 ok） |

## 4. 原始证据文件（gitignored，SHA256 锚定）

| 文件 | SHA256 | 大小 |
|------|--------|------|
| `.verify/m14-99-harmony-post-login-regression/evidence/auth_smoke_launcher.json` | `801f62ec9e3035aadca3058a6e35d6e8b3bc33b65a3d6f53404096476902d518` | 6065 bytes |
| `.verify/m14-99-harmony-post-login-regression/evidence/auth_smoke_on.json` | `4ed6c7248fdb5555a6df8def1235c738269ac565dc269adcad8092d444e1ad0c` | 5294 bytes |

> 注：原始 JSON 文件中**无 `exit_code` 字段**，不应虚构该字段。

## 5. 诚实边界

- 未签名 HAP（未触发 sign_hap，零签名材料接触）
- Harmony 模拟器 `127.0.0.1:5555`（非真机，未触碰 Android 物理设备）
- 零生产容器 / 零生产 DB / 零 MinIO 接触
- 宿主后端为一次性 loopback 动态端口隔离 SQLite，非生产后端
- `production_ready=false` 不变

## 6. 验证记录

| 检查项 | 结果 |
|--------|------|
| `pytest tests/harmony_release/test_auth_smoke.py -q` | **55 passed** |
| `pytest tests/harmony_release/test_auth_smoke_launcher.py -q` | **20 passed** |
| `py_compile tools/harmony_release/auth_smoke.py` | 通过 |
| `py_compile tools/harmony_release/auth_smoke_launcher.py` | 通过 |
| `ruff --select F,E9,W605`（auth_smoke.py / auth_smoke_launcher.py / test_auth_smoke.py） | 通过 |
| `git diff --check` | 干净 |
