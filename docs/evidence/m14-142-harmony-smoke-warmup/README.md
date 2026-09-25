# M14-142 冒烟预热加固（Settings 收敛 + 结构化输入选择）

切片：R4 收尾（基于本 worktree 已有未提交实质补丁继续，不重设计、不整
体回退）。分支 `harmony/m14-142-smoke-warmup`，基
`6508619ebbb75487e0854bb7fd884b7a29c752bf`（origin/main，M14-145 合入后 R6 rebase
收口；原基于 `e8a6285`），单 local commit，不 push、
不开 PR。

## 缺陷（Stage 2 首跑实录）

冷启动竞态：第一次点击「设置」tab 后应用仍在首页，但
`configure_settings_ui` 只在尝试 1 点击 tab；后续尝试把首页
`服务地址: http://…` 标签当作设置输入，产出 `settings_input_mismatch`
与 `settings_input_not_found`。fail-closed 与清理行为本身正确——缺陷
是 UI 状态收敛与输入节点选择。

## 修复（tools/harmony_release/backend_smoke.py）

1. **收敛加固**：`_drive_settings_url` 在捕获布局没有 TextInput（仍在
   首页）时，在**当前有界尝试内**重试「设置」tab 点击并重抓布局，绝不
   假设尝试 1 的点击已生效。非收敛尝试记为 retry note（结果新增
   `convergence_retries`），仅当全部尝试耗尽才判 failure。
2. **输入选择纯结构化**：新增 `layout_typed_nodes` /
   `find_input_node` / `is_settings_layout`——仅接受布局契约的
   `TextInput` 节点（M14-84 真实 dump：URL 输入框是 Settings 唯一的
   TextInput）；删除按 URL 文本猜测的旧 `find_input_field`。首页
   `服务地址: http://…` 与 Settings 说明文字都是 `Text`，无论文本内容
   都不可能被选为输入。
3. **小修**：`UiDriver.dump` 空文件判定前移（先空文件检查再 JSON 解
   析）；返回 `(saved, failures, retry_notes)` 三元组。
4. **保持**：有界尝试（3 次）、settle 常量、fail-stop 步骤序、卸载清
   理、退出码与全部 CLI 契约不变。

## 测试（tests/harmony_release/test_backend_smoke.py）

聚焦假布局升级为**真实结构形态**（每节点带 `type`：Home 服务地址行与
Settings 说明文字均 `Text`，唯一可编辑节点 `TextInput`）。新增/改写回
归：

- `TestSettingsConvergence.test_attempt1_home_attempt2_settings_succeeds`：
  尝试 1 停留首页 → 尝试 2 重试 tab 后成功（两次「设置」tab 点击、
  `convergence_retries == 1`、输入点击命中 TextInput 中心、永不命中服
  务地址标签中心、永不点击「首页」tab）。
- `test_never_converges_fails_closed_and_retries_every_attempt`：布局永
  无 TextInput → failure（`settings_input_not_found`），3 个有界尝试
  每个都重试 tab，零输入点击，清理仍执行。
- `TestStructuralInputSelection`：首页结构布局 `find_input_node` 为
  None；URL 文本节点不算输入；Settings 结构被检出（返回 TextInput 中
  心与文本）；边界不可解析 fail-closed；首个结构性输入优先于任何标
  签。
- 既有 ok 路径、说明文字（含「保存」）规避、Home 断言失败用结构假布
  局重写。

## 验证（canonical venv：仓库根目录 `.venv`，无盘符绝对路径）

| 命令 | 结果 |
| --- | --- |
| `pytest tests\harmony_release\test_backend_smoke.py -q` | **29 passed** |
| `pytest tests\harmony_release\test_backend_smoke_repeat.py -q` | **38 passed** |
| `pytest tests\harmony_release -q` | **569 passed, 1 skipped** |
| `ruff check --select F,E9 tools/harmony_release/backend_smoke.py tests/harmony_release/test_backend_smoke.py` | **0 命中** |
| `py_compile`（backend_smoke.py、test_backend_smoke.py） | **通过** |
| `git diff --check` | **干净** |
| 新增行秘密值与本地绝对路径扫描（R6 rebase 后相对基线 `6508619`/origin/main 的全部新增行；R5 时基线为 `e8a6285`） | **0 命中**（R6 复扫确认） |

R5 修正记录：R4 的「扫描 0 命中」声明当时不实——文档新增行确含 canonical
venv 盘符绝对路径（本文件与 PROJECT_STATUS），且 ROADMAP M14-142 标题/
正文存在两处 U+FFFD 乱码；R5 已全部清除并把扫描口径改为相对基线的全部
新增行（含测试合成 loopback URL 属预期非命中）。

## 诚实边界

- **本切片未运行、不声明任何真机/模拟器 Stage 2**：未启动或停止任何
  模拟器、后端或用户进程；全部验证为注入式 fake 单元测试与静态检查。
- 收敛修复的真机实证（首跑竞态在第二次尝试内恢复）仍是 **Stage 2 边
  界**，留给后续切片。
- 不触碰生产服务与 secret；`production_ready=false` 不变。
