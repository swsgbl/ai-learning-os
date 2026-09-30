# M14-191：Harmony 公网默认服务地址切换（current-main rebase 复验）

## 结论

| 项 | 结果 |
|---|---|
| 分支 / 基点 | `harmony/m14-191-public-default-url`，rebase 到 `origin/main` `ff25550d854d53a7c9f67020e4e1f77ad8d70faa`；最终保持单 local commit，不 push、不开 PR |
| 产品行为 | `DEFAULT_API_BASE_URL` 从 `http://127.0.0.1:8000` 改为 `https://ndtool.cn/aios/`；本地或 LAN 地址仍可在 Settings 中校验、保存并持久化 |
| Settings 默认态 | 真实模拟器布局中唯一服务地址 `TextInput` 的 `text` / `originalText` / `hint` 均为 `https://ndtool.cn/aios/` |
| 公网首启 | Home 显示 `服务地址: https://ndtool.cn/aios/`；`/health` 渲染 `status: ok`、`service: ai-learning-os-api`，版本 `0.1.0`，受保护端点如实 `HTTP 401` |
| DownloadPane | 公网默认时显示 `https://ndtool.cn/aios/download`；保存 loopback 后显示 `http://127.0.0.1:8000/download`，入口由当前 base 推导而非硬编码 |
| 覆盖持久化 | 真实 Settings UI 分段输入并保存 `http://127.0.0.1:8000/` 成功；force-stop + 重启后 Home 与 Settings 均恢复该值，placeholder 仍为公网默认 |
| 新源码契约 | `test_harmony_default_url.py`：**6 passed** |
| 新契约 + backend smoke | **77 passed** |
| 完整 Harmony release 套件 | **689 passed / 1 skipped** |
| Ruff / compileall | 新测试全规则通过；两个修改测试按 `E4,E7,E9,F` 通过；`compileall` 通过 |
| clean release build | 成功；unsigned HAP size **235176**，SHA256 **ADB44A1CF8550784A7C8A9609A49FB0039A33D7D7C3854A74E7BB176FA7DC985** |
| 清理 | 应用 force-stop + uninstall；`bm dump -n com.ailearningos.app` 报不存在；全量 bundle 列表 `com.ailearningos.app` **0 匹配** |

## 实现边界

1. `apps/harmony/entry/src/main/ets/components/SettingsStore.ets` 只替换默认值与注释；偏好键、校验、`put` / `flush` 覆盖路径不变。
2. 新增 `tests/harmony_release/test_harmony_default_url.py` 以源码契约钉住：
   - fresh default 必须是 `https://ndtool.cn/aios/`，且 `SettingsStore.ets` 不再残留 loopback 默认值；
   - Settings 初始值和 placeholder 必须使用 `DEFAULT_API_BASE_URL`；
   - URL policy 继续同时接受 `http` 本地/LAN 与 `https` 公网，并保留反斜杠、userinfo、query/fragment fail-closed 检查；
   - DownloadPane 必须从 active base 推导 `download` 入口，不得硬编码公网链接。
3. `tests/harmony_release/test_backend_smoke.py` 仅同步 smoke fixture 的默认输入和结构化 TextInput 断言；`PUBLIC_BASE` 常量上移到文件公共区，供默认 fixture 与既有 public HTTPS integration 复用。

## 模拟器证据

所有设备变更命令都显式指定 `-t 127.0.0.1:5555`；未对 `127.0.0.1:15566` 发起操作。执行前先 force-stop + uninstall，随后 fresh install 本轮 clean build 的 `entry-default-unsigned.hap` 并启动 `com.ailearningos.app`。

原始目录：`.verify/m14-191-harmony-public-default-url/rebase-ff25550/`。采信链如下：

- `01_install.txt`、`01_bundle_after_install.txt`：安装与 bundle 存在证明。
- `02_home.json` / `02_home.jpeg`：公网默认、health、服务标识、版本与匿名 `HTTP 401`。
- `03_settings.json` / `03_settings.jpeg`：TextInput 默认值与 hint。
- `04_settings_scrolled.json` / `04_settings_scrolled.jpeg`：`https://ndtool.cn/aios/download`。
- `06_text_menu.json`、`07_selected.json`：系统文本菜单全选；`08_click_cut.txt`、`10_typed_loopback.json`：剪切清空后分段 `uiInput text` 输入。
- `11_loopback_saved.json` / `.jpeg`：保存确认与精确 loopback 输入；`11_loopback_download.json` / `.jpeg`：下载入口同步为 `http://127.0.0.1:8000/download`。
- `12_cold_home.json` / `.jpeg`、`13_cold_settings.json` / `.jpeg`：force-stop + 重启后持久化仍生效；Settings placeholder 保持公网默认。
- `14_cleanup_force_stop.txt`、`14_cleanup_uninstall.txt`、`14_bm_dump_after_uninstall.txt`、`14_bm_dump_all_after_uninstall.txt`：卸载与不存在证明。
- `simulator_summary.txt`：12 项断言全部 `True`；`run_rebase_simulator.ps1` 是可回放脚本。

坐标式 `uitest uiInput inputText` 在该模拟器会得到 `/http://...`，本轮没有再采用；输入路径为 longClick → 全选 → 剪切 → 聚焦后分段 `uiInput text`，dump 核对成功。

## 验证与构建

- 新源码契约：`6 passed`。
- `test_harmony_default_url.py + test_backend_smoke.py`：`77 passed`。
- `python -m pytest tests/harmony_release -q`：`689 passed / 1 skipped`。
- Ruff：新测试全规则通过；两个修改测试按 `E4,E7,E9,F` 通过。
- `python -m compileall -q tests/harmony_release` 通过。
- clean release build：DevEco `hvigorw.bat clean --no-daemon` 与 `assembleHap --mode module -p product=default -p buildMode=release --no-daemon` 均 exit 0；`No signingConfig found for product default` 是刻意保留的 unsigned 边界。

## 诚实边界

- HAP 仍为 unsigned，`apps/harmony/build-profile.json5` 的 `signingConfigs: []` 不变；不声明 AGC、签名或生产分发就绪。
- 验证口径为单时点 HarmonyOS 模拟器 + 只读公网请求；未操作生产容器、部署、DB、MinIO、voice、secrets 或签名材料，`production_ready=false` 语义不变。
- loopback 覆盖只验证 URL 校验、保存和冷重启持久化；不要求 `127.0.0.1:8000` 上有可用服务。
- 原始工件位于 gitignored `.verify/`，本 README 是唯一入库证据文件。
