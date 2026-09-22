# M14-95 Harmony 真实认证冒烟（real auth smoke）证据

切片：`m14-95-harmony-real-auth-smoke` worktree（分支 `harmony/m14-95-real-auth-smoke`）。真实执行/代码基点 = main `001af38e8786c9578076143351978b96d0332874`（PR #181 merge = M14-89 认证会话合入）；单 local commit 后受控 rebase 至 current origin/main `c0610e9e2ed13864a0bd00df2e213ab13b4830d8`（PR #184 merge = M14-97 合入）——rebase 仅解决三台账（CHANGELOG/PROJECT_STATUS/ROADMAP）docs 冲突（编号倒序插入与前一任务结构），零代码变更、零验证重跑声明之外的重跑。不 push、不开 PR。原始证据 gitignored `.verify/`（本 README 为唯一入库证据文件；根目录 `auth_smoke_server.log` 与 `services/api/m14-95-auth-smoke.db` 等 B3 漂移遗留均不入库）。

## 交付（代码）

- `services/api/app/ops/auth_smoke_server.py`：一次性**真实** FastAPI 认证后端 `AuthSmokeServer`——直接复用仓库 `create_app()`（含 M9-01/M9-04/M9-06 门禁与 M10-03 cookie 行为）+ 隔离 SQLite 文件库（不碰主库）+ 任务自有 loopback 空闲端口（`bind(0)` OS 分配，绝不写死、不跨任务复用）+ 种子一个合成用户走**真实 bcrypt 登录校验路径** + 受保护 `GET /api/v1/smoke/protected`（证明业务门禁真实生效）；AUTH_SECRET 必须显式提供且 ≥32 字节（M9-04 同款语义）否则拒绝启动；全部状态/日志文本对 secret/口令/token 脱敏（永不回显）；冷重启 = 同库文件 + 同 secret 新进程（会话持久化语义）。
- `tools/harmony_release/auth_smoke_launcher.py`：任务自有 launcher——默认 **plan-only 零副作用**（不执行时 server 工厂从不调用）；`--execute` 才启动任务自有后端并把动态 base 注入 M14-89 冒烟工具（`expect_auth=on`）；`finally` 只停自有后端（stop-on-smoke-failure）；报告脱敏（`db_path`→`<task-work-dir>`、`auth_secret`→`<configured>`、`seed_password`→`[REDACTED]`，永不携带令牌/口令/绝对私有路径）；直接脚本导入回退（`python tools/harmony_release/auth_smoke_launcher.py` 可用）；`--help` 零副作用。
- `tools/harmony_release/auth_smoke.py`（M14-89 工具增强）：新增 `device_api_base()`——从**已验证**的 loopback `--api-base`（动态端口）派生设备侧 URL（127.0.0.1/localhost→10.0.2.2 同端口，scheme 与尾斜杠保持），fail-closed 拒绝一切未验证形态（非 http/非 loopback 主机/userinfo/query/fragment/带 path）；Settings 输入与断言改用派生值（替代写死的 8765），非 loopback base 时设备 URL 置 None 并如实记录。
- 测试：`services/api/tests/test_m14_95_auth_smoke_server.py`（24 项，全部真端口/真 socket，不靠 TestClient）；`tests/harmony_release/test_auth_smoke_launcher.py`（20 项）；`tests/harmony_release/test_auth_smoke.py` 新增 6 个测试函数（34→49 项，动态端口派生矩阵/未验证拒绝面/报告脱敏/非 loopback 置 None）。

## 轮次事实（B3→B7，全部真实执行）

- **B3（失败）**：相对 `db_path` 在宿主种子 cwd 与 uvicorn 子进程 cwd（`services/api`）之间**漂移**——宿主把库种到一个位置、子进程打开另一个位置，登录 401。漂移遗留物 `services/api/m14-95-auth-smoke.db` 为 gitignored 未跟踪文件（未入库、保留现场不清理不隐瞒）。
- **B4（修复）**：`AuthSmokeServer` 把相对 `db_path` 一律解析到 work_dir 之下（构造器 + `start()` 双保险；绝对路径保持原语义），保证宿主种子与子进程看到同一个库文件。
- **B5（复跑仍失败）**：`work_dir` 本身仍可能是**相对路径**——launcher 传相对 work_dir 时，`work_dir/db` 组合出的"绝对"路径随解析 cwd 漂移，后端启动 fail-closed。证据 `.verify/m14-95-b5-launcher-out.json` / `.verify/m14-95-b5-evidence/auth_smoke_launcher.json`：`status=executed`、`reasons=[server_start_failed/RuntimeError]`、smoke 未运行。
- **B6（修复，随后复跑通过）**：① `work_dir` 构造时 `Path.resolve()` 绝对化（无论宿主/子进程在哪个 cwd 打开都指向同一文件）；② sqlite URL 保持 `Path.as_posix()`（POSIX 分隔符形式，绝不让 URL 出现 Windows 反斜杠）；③ launcher 增加直接脚本导入回退（`try ImportError → sys.path` 注入同目录，`python tools/harmony_release/auth_smoke_launcher.py` 可用）；④ launcher `--help` 零副作用（不触工厂、不触 sys.path、不起子进程）。
- **B7（真实 Harmony 模拟器复跑，通过）**：见下节。

## B7 真实模拟器复跑结果（canonical `.verify/m14-95-b7-evidence/`，SHA256 锚定）

- **exit 0；12 步 = 11 ok + 1 not_run**（唯一 `auth_off_local`/`auth_phase_skip`——auth-on 语义下的预期跳过，与 M14-89 R13/R14d/R17 同构）；`warnings=0`、`request_failures=0`、`mutation_performed=true`、`cleanup_attempted=true`。
- 步骤明细（全部 ok，除注明外）：`host_auth_contract`（read_only）、`install`、`start`、`settings_ui`、~~`auth_off_local`（not_run，auth_phase_skip）~~、`home_401_unauth`、`wrong_password`、`login_and_refresh`、`cold_restart`、`logout`、`background`、`uninstall`（cleanup）——即 install/start/settings/home/wrong-password/login/cold-restart/logout/background/uninstall 全部 ok。
- 宿主认证契约 **7/7 matched**：`auth_status`（200 auth_enabled=True）、`gated_privacy_401`、`wrong_password_401`、**`login_200_token`（200 usable_token=True token_type_bearer=True）**、`me_200_with_token`、`gated_privacy_with_token`（200）、`gated_privacy_wrong_token`（401）。
- 后端：真实 FastAPI（仓库 `create_app`）绑定 `127.0.0.1:52439`（OS 分配动态端口，子进程 pid 36340），设备侧派生 URL `http://10.0.2.2:52439/`；种子用户 `aiosstudent`（= `auth_smoke.MOCK_LOGIN_USER` 合成凭据，口令/令牌不入档），真实 bcrypt 登录路径。hdc 工具链 `path_lookup` 解析成功，`commands_executed=4`。
- **HAP**：`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`，**533946 bytes，SHA256 `A8348FDEF303A63F95F4C380F32F016B6F6F4E047624A3FB5736BBB5893F78BB`**（未签名，`signedness_verified=false`，诚实边界同 M14-82/M14-84/M14-89）。
- **布局证据 = 摘要哈希 + 节点数**（`content_recorded=false`，布局内容/截图一律不落档、本 README 亦不虚构）：`wrong_password` 24 节点（SHA256 `EE0256D366735B7EEAB754A183AA3FA0AF27487D992F9A4060268983D5A5F8D0`）、`login_and_refresh` 24 节点（`D5F70B535CB0F9F04D4CFA26DD5937EAA16A1D4F88AFA58D98AA3D7D117991E3`）、`cold_restart` 24 节点（`2F48BA52F09E080E726F128F945297F1E54F6BEEE61D826F990D5D831A397D38`）、`logout` 22 节点（`650928E7BFBD893D91CEDEF746269FDB4E95B691D4F82337D5D22177DC68653E`）、`post_logout_home` 35 节点（`C3353974F2A5A9DEC26A580FDBEDBEB91F0A6DD1679948FA389937529F3FA5EC`）。
- **server log 为 0 字节**（uvicorn `log_level=error`，整个冒烟期间无任何错误输出；空文件如实记录，不虚构日志内容）。

## 证据 SHA256（canonical `.verify/m14-95-b7-evidence/`）

| 文件 | SHA256 | 大小 |
|---|---|---|
| `auth_smoke_launcher.json` | `6E9B300FC688D7649643155ABB4ED14ADFABB8C1CB65823122572F6C34F1B166` | 5658 bytes |
| `auth_smoke_on.json` | `1E87FA99AAA08BFDBEF155570F9013629CAE0B1B449B0461A8B8E1E814E22818` | 4573 bytes |

## 突变与影响边界

- 设备突变**仅限目标 HAP**（bundle `com.ailearningos.app`）于本地模拟器 `127.0.0.1:5555`：install/start/交互/uninstall，**收尾已卸载**（`uninstall` ok、`cleanup_attempted=true`）。
- **Android 真机 EYFBB22923201473 未触碰**；模拟器本体与生产服务（容器/DB/MinIO/语音/secrets）**未重启、未停止、未触碰**——后端为任务自有一次性进程，`finally` 停止且端口为 OS 动态分配。
- 不入库：原始 `.verify/` 证据（本表哈希锚定）、根目录 `auth_smoke_server.log`（0 字节）、`services/api/m14-95-auth-smoke.db`（B3 漂移遗留）。

## 诚实边界

- **真实后端 ≠ 生产后端**：真实 FastAPI 仓库实现 + 真实 bcrypt 登录 + 真实门禁 401/200 语义，但跑在本机 loopback 一次性隔离 SQLite 上，凭据/secret 均为合成值（非生产认证、非生产库）。
- **未签名 HAP**（模拟器直接安装；不构成发布/签名授权，signedness 边界同 M14-82/M14-84/M14-89）；本轮零 AGC/签名材料接触。
- `production_ready=false` 不变；不声明 Harmony 生产就绪；零生产容器/DB/MinIO/语音/secret 接触。

## 验证（canonical venv `ai-learning-os\.venv\Scripts\python.exe`，rebase 前后各跑一轮）

- API 聚焦 `services/api/tests/test_m14_95_auth_smoke_server.py`：**24 passed**（32.64s）。
- launcher 聚焦 `tests/harmony_release/test_auth_smoke_launcher.py`：**20 passed**（0.18s）。
- `tests/harmony_release/test_auth_smoke.py`：**49 passed**（0.12s；M14-89 基线 34 + 本切片 15 个新参数化项）。
- `py_compile` 六个改动 Python 文件通过；`ruff check --select F,E9,W605` 全绿；`git diff --check` 干净。
