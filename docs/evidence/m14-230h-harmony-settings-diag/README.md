# M14-230H：Harmony Settings 一次性连接诊断（源码契约切片）

## 0. 结论与边界

- 切片：worktree `m14-230h-harmony-release-provenance`，分支
  `harmony/m14-230h-release-provenance`，基 current main `7ef3d51c`
  （PR #316 merge）。交付为 1 个本地 commit，不 push、不开 PR、
  不合并；零设备/模拟器/Docker/WSL/生产/网络接触。
- 交付物（2 类文件）：
  1. `apps/harmony/entry/src/main/ets/components/SettingsPane.ets` ——
     「测试连接」从单端点 `/health` 文案直显改造为一次性双端点连接
     诊断：每次测试恰好一次 `getHealth` + 一次 `getAuthStatus`
     （各受 AiosApi 既有 10s 超时约束，无重试/轮询），结果映射
     四值闭集（ok / http_path_mismatch / network_unreachable /
     mixed）并以固定中文闭集文案展示（仅 http_path_mismatch 可
     附加数字 HTTP 状态码）。
  2. `tests/harmony_release/test_harmony_settings_diag.py`（新增，
     21 用例）—— 源码契约测试（ArkTS 扫描不执行，与仓库既有
     Harmony 测试边界一致），锁定显式触发门、端点使用、闭集映射、
     无重试/轮询/自动运行、禁用态与泄漏边界。

## 1. 契约细节

- 显式动作门：诊断仅由「测试连接」按钮 onClick 触发；
  aboutToAppear / doSave / onChange / emitter 均不接线；
  `testing` 布尔防重入。
- 端点使用：每端点单次 GET（`usingCache: false`，connect/read
  各 10s，无 retry/poll/setTimeout/Promise.all）；不触及其他
  任何端点；诊断不读取/写入/保存基地址。
- 判决与映射：单端点三值判决 `classifyEndpoint`（good = 2xx 且
  响应形状合法；http_error = 真实非 2xx 应答；no_conn = 传输
  失败/URL 校验失败/2xx 形状非法），`combineVerdicts` 按约定表
  映射四值闭集；URL 白名单校验失败直接归入 network_unreachable
  （闭集文案，不发任何网络请求）。
- 展示边界：文案只允许闭集中文标签（+数字 HTTP 状态码）；
  不渲染响应体、异常对象、令牌、堆栈、内部路径；无 console/
  hilog 输出。运行期间「保存」「测试连接」两按钮均禁用，
  结束后恢复；旧「重试」按钮移除（重入由按钮自身承担）。
- 不变量：`tests/harmony_release/test_harmony_default_url.py`
  的 SettingsPane 契约（默认 URL 初始值/placeholder）零破坏。

## 2. 验证（父仓 venv `D:\AI Learning OS\ai-learning-os\.venv`）

- 聚焦：`pytest tests/harmony_release/test_harmony_settings_diag.py`
  → **21 passed**（7 类：显式门 3 / 端点使用 4 / 结果映射 6 /
  无重试轮询 3 / 禁用态 2 / 泄漏边界 3）。
- 邻居：`pytest tests/harmony_release/test_harmony_default_url.py`
  → **6 passed**（同文件既有契约零回归）。
- 全套：`pytest tests/harmony_release/` → **798 passed, 1 skipped**
  （较 M14-228 的 777 passed 净增 21 = 本次新用例；skip 为既有
  平台符号链接用例）。
- `ruff check`（新测试文件，默认规则与 `--select F,E9` 双口径）→
  All checks passed；`py_compile` 通过。
- `git diff --check` → 干净；新增/改动文件 secret/本地绝对路径/
  U+FFFD 扫描 → 0 命中。

## 3. 诚实边界

- 本切片为源码契约级改造，未执行模拟器/真机运行验证（无设备
  操作）；HAP 构建/签名/AGC 状态不变：仍无 signed HAP，
  `production_ready=false` 不变。
- 诊断行为受 AiosApi 既有层约束（超时/错误文案收敛在 API 层），
  本切片未改动 `AiosApi.ets` / `UrlPolicy.ets` / `SettingsStore.ets`。
