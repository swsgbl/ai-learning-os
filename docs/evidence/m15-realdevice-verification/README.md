# M15 证据：移动端真机验证（M15-002 注册 + M15-004 首启）+ 生产复核收口

- 基点 current main `b1b1e47c`（#380 后）。**背景**：用户指出
  M15-002/004 此前只有单测 + 编译验证（350 tests / hvigorw 零
  ERROR），注册端点回环从未在设备上完成（M15-003 模拟器被 IME
  拦截 + Docker 宕机挡住 PG）。本切片补齐**真机端到端**并顺带
  完成 Docker 恢复后的生产复核。

## 环境（2026-10-18，全部真实）

- **设备**：Huawei MGA-AL00（serial `EYFBB22923201473`，USB/adb）
- **APK**：current main 移动端内容（`730df6fb` == main 的
  apps/android，#380 为 docs-only）`assembleDebug` → `adb install
  -r` → `pm clear`（保证首启态）
- **验收 API**：uvicorn `0.0.0.0:18000`（worktree 最新代码），
  `DATABASE_URL=…ai_learning_os_test`（`create_pg_test_db.py` 建
  立的白名单隔离库，落在彩排 PG 实例 5433 上——**主库
  ai_learning_os 零接触**；alembic head `0027_audit_chain`，
  30 表）+ 假 AUTH_SECRET（auth_enabled=true）。手机经 LAN
  `http://192.168.8.3:18000` 访问（临时防火墙入站规则用后已删）。
- 服务端探针：`POST /auth/register` 重复用户名 → **409
  `username already exists`**（权威行为先行确认）。

## M15-004 首启连接引导——真机四点全过

| # | 验证点 | 真机结果 |
| --- | --- | --- |
| 1 | 首启（pm clear 后）首页顶部 | **WelcomeBanner 渲染**：「欢迎使用砚席」+ 价值文案 +「连接服务器」按钮 + 下一步提示；下方 API 状态如实报「无法连接」 |
| 2 | 点「连接服务器」 | **跳转设置页**（onOpenSettings 生效） |
| 3 | 输入 `http://192.168.8.3:18000` → 保存 | 客户端校验「地址有效」→ 保存后「**当前生效**」+「已保存，正在用新地址重新探测」 |
| 4 | 返回首页 | **banner 消失**（BANNER_PRESENT=False）+「服务（/health）**正常**」 |

## M15-002 注册功能——真机六点全过

| # | 验证点 | 真机结果 |
| --- | --- | --- |
| 1 | 登录页 | 「登录 / 注册」**FilterChip 双模式**渲染且可切换 |
| 2 | 切注册模式 | 标题「注册」+ 引导文案「第一次使用？创建账号后即可定制课程并开始练习。」 |
| 3 | 提交按钮 | 动态变「**注册并登录**」 |
| 4 | **负例**：注册已存在用户名 `probe_user`（服务端 curl 预建） | **409 → 「用户名已被注册，换一个或切换到登录」**（留在注册页，表单保留） |
| 5 | **正例**：新用户名注册 | **201 → 自动登录 → 首页显示用户名 + 「学习者」角色 + 「退出登录」** |
| 6 | 退出登录 | 回「未登录」+「登录状态已失效，请重新登录」；重启 app 状态一致 |

正例过程注记（诚实记录）：首测时 adb `input text` 向**已保留
表单**（onModeChange 保留表单是 ViewModel 设计）追加了用户名，
得到 `probe_userprobe_user`——实际注册的是这个新用户名并成功
自动登录，恰好完整走了正例路径；非 app 缺陷。

## 发现的待查异常（登记，不阻塞验收）

- **「退出登录」后首页「登录」按钮对 adb tap 无响应**：连续 5
  次（含 accessibility 通道 tap_element，坐标经 dump 核验居中）
  均无效；`am force-stop` 重启后同一按钮**立即**响应。未定位
  根因（疑与退出登录后的某 UI 状态残留相关）。不影响注册/
  首启验收路径（重启可绕过）；建议后续切片用 logcat 复现定位。
- 过程记录：Windows 防火墙默认拦 18000 入站（ping 通但 TCP 不
  通）——临时放行规则验证后**已删除**；华为输入法下 adb ASCII
  注入正常（与鸿蒙模拟器 IME 拦截不同）。

## 生产复核（Docker Desktop 恢复窗口内完成，事实链如实记录）

- Docker Desktop 恢复（engine 29.8.2）→ 彩排栈 restart 策略
  自动拉起：**7/7 healthy**（api/web 镜像核实仍为
  `aios/api:m15-001-production` / `aios/web:m15-001-production`
  ——M15-002/003/004 均移动端/docs 改动，无服务端代码变化，
  不需重建）。
- **recovery dry-run（主检出，env 文件在位）：OK**——pin 通过、
  健康栈跳过 up、funasr/cosyvoice stale manifest 识别并清理后
  受控 start（发现并纠正了 worktree 语境误启动的引擎实例：
  artifacts 路径锚定主检出，停掉 worktree 实例改由主检出拉起）。
- **production monitor 第一轮**：三组告警逐项核实为非代码
  故障——postgres「23 errors」全部为 Docker 重启时段
  `starting up/shutting down` 正常日志；api restart=1 同因；
  funasr/cosyvoice connection-refused 由受控 start 处置。
- **引擎恢复确认**：FunASR `/health` **HTTP 200**
  （sensevoice loaded）；CosyVoice `/health` **HTTP 200**
  （Fun-CosyVoice3-0.5B-2512）——复核完成。
- **后续（如实记录）**：复核完成后 Docker Desktop 引擎再次
  失联且 WSL 语音进程随之停止（第二轮 monitor 全端点 refused
  即此现象）。这是本会话已知的基础设施不稳定问题（Docker
  Desktop 由另一智能体处理，本线按边界不干预），**非本批代码
  引入的回归**——恢复窗口内的复核证据（7/7 healthy +
  dry-run OK + 双引擎 200）已足以确认代码侧无回归；基础设施
  恢复后 `production_recovery.py --dry-run`（enforce 亦然）可
  幂等收敛（其设计目标即为此场景）。

## 非声明

- 验收 API 用后停止；防火墙临时规则删除；手机 `pm clear` 复原；
  隔离测试库 `ai_learning_os_test` 保留（白名单库，CI/本地测试
  共用语义）。三 ready 标志均 false；无真实凭据入库；截图仅本地
  留档（`D:\AI Learning OS\m15-002-negative-409-realdevice.png`）
  不入库。
