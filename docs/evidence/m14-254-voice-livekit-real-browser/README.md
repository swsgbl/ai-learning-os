# M14-254 证据：VoiceStudio LiveKit 输入真实浏览器验收 harness（交付 harness 与 mock 契约验证；真实栈运行保留给 supervisor）

- 分支 `web/m14-254-voice-livekit-real-browser`（worktree
  `ai-learning-os-worktrees/m14-252-voice-livekit-input`，历史目录
  名复用），基于 current main `47c39043`（PR #339 merge，即
  M14-252；main CI run 38103803209 5/5 绿）。执行前 porcelain
  干净（基点本地 git 可验证）。
- 动因：M14-252 把 voice-studio 语音输入升级为 LiveKit 管理，但
  其证据明确说「真实 LiveKit 服务 + 真实浏览器麦克风端到端冒烟
  仍缺位（仓库测试口径：vitest node 环境 stub + 注入点行为
  断言）」。本切片交付可重复的 supervisor 运维 harness 与其零
  网络契约测试，补上「能验证」的能力——**不预声明「已验证」**。

## 验证路径（harness 将对既有 live 栈实证的精确链条）

1. 通过真实 Web UI 登录（`/login` 表单 → HttpOnly cookie）；
2. `GET /api/v1/papers`（Bearer）确定性选卷 → 打开
   `/voice/<paperId>` 真实 VoiceStudio 会话；
3. 等待 `data-voice-session-id` 非空（权威 session 就绪）与
   「语音作答」按钮出现；
4. 点击语音作答 → 断言 `data-mic-phase="recording"`——在 M14-252
   语义下，recording 只能在 `token → Room.connect → 麦克风轨 →
   publish` 全链成功后到达（懒连接控制器语义）；
5. 持续 ~1.5s 后点击「停止录音并提交识别」→ 断言 mic phase 回
   `idle`（utterance 停止 → WAV 编码 → /transcribe → 提交链完成）；
6. 网络事实硬断言（fail-closed）：
   - token 请求**恰一次**且请求体 room === `voice-<session_id>`
     （session_id 取自 DOM `data-voice-session-id`）；
   - `/api/v1/voice/transcribe` **恰一次**、HTTP 200、multipart
     顶层类型 + 体内 `audio/wav` part 与 `filename="answer.wav"`
     （内存子串断言布尔化）+ 字节数 > 0；
   - 验证窗（进入 /voice 起）console error / pageerror 为 0
     （登录窗预期 auth 噪声单独记录不掩蔽）；
   - DOM 与序列化报告零 JWT 三段形态（递归脱敏 + 落盘前自检，
     命中即改判 failed）；
7. 空 transcript 或诚实 UI clarify/error **可接受**——仅当
   transcribe HTTP 链成功；不构成流式 ASR 成功声明；
8. 三张截图（initial-session / recording / post-transcribe）与
   `results.json`（verdict / checks / 净化 network / browser /
   livekit_probe 段，无凭据字段）落 gitignored `.verify/`。

## supervisor 真实运行方式（保留动作，本提交未执行）

```powershell
# 前置：live API(:8000) 与 LiveKit 已在运行（本 harness 只读探测，
# 绝不启停服务）；canonical venv 已装 playwright + chromium
"D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe" `
    infra/verify_voice_studio_livekit_input.py
# 可选环境变量（全部与 verify_web_livekit_client.py 同口径）：
#   AIOS_VOICE_PAPER_ID=<paper_id>   显式指定试卷（缺省确定性取第一份）
#   AIOS_LIVEKIT_BROWSER_LOOPBACK=1  受控 loopback 拓扑（默认 default）
#   AIOS_API_BASE / AIOS_WEB_PORT / AIOS_SKIP_BUILD / AIOS_OUT
# 退出码：0=passed；1=failed（硬断言失败，fail-closed 不伪造）；
#         2=ENV-BLOCKED（API/LiveKit 不可达或配置非法）
```

运行结束检查 `.verify/m14-254-voice-livekit-real-browser/`
（`results.json` + `screenshots/`）；该目录被 `.gitignore` 的
`.verify/` 规则覆盖，原始证据不入库——supervisor 引用时摘录
verdict/checks/网络事实即可。

## 交付面

1. **`apps/web/src/components/voice-studio.tsx`（DOM 契约）**：根
   容器新增 `data-voice-studio` / `data-mic-phase={micPhase}` /
   `data-voice-session-id={view.session.session_id}` 三个非敏感
   属性——零视觉/行为变更，只绑定 micPhase 四态与服务端权威
   session UUID（非凭据）；token/ws_url JWT/identity/控制器内部
   绝不经由属性暴露（hygiene 新断言钉住：data-* 绑定不得引用
   token/jwt/identity）。
2. **`infra/verify_voice_studio_livekit_input.py`（新增 harness）**：
   经 importlib 复用 `verify_web_livekit_client.py` 的全部加固
   模式（不复制易碎逻辑）：API 只读 preflight + 契约派生 LiveKit
   信令探测（M14-38）、既有验收用户 login-only 认证（只调业务
   登录端点，零注册零播种——access token 只进内存请求头）、真实 node 直启 next start + 精确 PID 树回收
   （M14-36：taskkill /PID /T /F 或进程组 SIGTERM→SIGKILL，绝不
   按端口/进程名扫杀）、Chromium fake 麦克风 + 免授权 UI +
   microphone permission、`AIOS_LIVEKIT_BROWSER_LOOPBACK` 严格
   开关（M14-37）、JWT 正则脱敏与登录/验证分窗 console 断言
   （M14-35 R1）。harness 独有：选卷 fail-closed 语义、网络监听
   （requestfinished，先于 /voice 导航安装）、净化元数据白名单、
   multipart 体内子串断言、phase 等待、落盘防线（先对未脱敏报告
   JWT 自检——命中改判 failed 且不可能以 passed 落盘，后递归
   脱敏）。autoplay flag（harness 侧浏览器配置差异）显式入报告。
3. **`services/api/tests/test_verify_voice_studio_livekit_input.py`
   （新增 44 项零网络契约测试，含 supervisor 两项修正随测）**：见下。
4. **`apps/web/src/lib/react-hooks-hygiene.test.ts`（+1 断言项）**：
   M14-254 DOM 契约属性钉住（三属性存在 + data-* 绑定零凭据），
   既有 M14-249/250/251/252 权威链断言全部保持。

## 净化与凭据边界（harness 的核心纪律）

- **LiveKit JWT**：token 响应体从不读取（源码零 `.body()`/
  `.text()` 调用，契约测试锁定）；请求体只解析出 `room` 后丢弃；
  access token 只存在于 harness 内存的请求头。
- **原始音频字节**：multipart 体只用于长度计数与子串断言
  （audio/wav part / answer.wav 文件名 → 布尔），字节绝不进入
  任何保留结构或报告。
- **报告字段白名单**：network 段只含 method/path/status/room/
  byte_count/content_type/布尔；无 authorization/cookie/body/
  token 键（契约测试断言键集）。
- **落盘双防线（修正 2 后顺序）**：`finalize_results` **先**对
  未脱敏报告 `report_has_jwt` 自检——命中即改判 failed、退出码 1
  并记非 secret `jwt_leak_self_check` 标记；**后**递归
  `sanitize_tree`（JWT 正则替换，不改写原值）。检测先于脱敏：
  真实泄漏不可能被脱敏遮蔽后以 passed 落盘。
- **测试凭据**：契约测试中的 JWT 形态均为合成假值（签名段为
  `sig-sig-sig` 占位），零真实凭据入库。

## mock 契约测试覆盖（44 项，零网络/零浏览器/零运行时服务）

- select_paper 五路径：override 精确命中 / 确定性首卷（重复调用
  同结果）/ 空列表 ENV-BLOCKED / 空 override ENV-BLOCKED /
  override id 不存在 ENV-BLOCKED（绝不静默改选）。
- token 房间与摘要：expected_token_room = `voice-<id>`；
  token_request_summary 白名单键集（kind/method/path/status/
  room——结构上无 token/jwt/authorization/cookie/ws_url/body）。
- transcribe 事实净化：计数/布尔/顶层 content-type 保留，原始
  字节不返回（序列化结果不含 RIFF/multipart 边界串）；非
  multipart 与缺 content-type 正确标记 False。
- verify_network_facts fail-closed 十路径：happy path 通过并
  返回净化事实；token 错 room/缺 room/零次/两次、transcribe
  非 200/缺 wav part 与 answer.wav/空体/缺失/非 multipart 各自
  以精确错误拒绝。
- JWT 防线：find_jwt 检出真形态且放过 session-id 形态；
  report_has_jwt 递归检出/干净通过/不可序列化保守判脏；
  sanitize_tree 脱敏且不改写输入原值。
- phase 契约：MIC_PHASES 恰四态；phase_valid 拒绝空串/大小写
  变体/None 等未知值。
- DOM 契约：voice-studio.tsx 三属性存在；所有 data-* 绑定零
  token/jwt/identity 引用。
- 进程回收：复用邻居 VWLC.stop_process_tree（源码锚点）；
  harness 源码零 /IM/pkill/killall/netstat/Get-Process/wmic/
  tasklist 扫杀模式；web 启动同样复用 VWLC.start_web。
- 文本契约：网络监听注册先于 /voice 导航；console 分窗恰一次
  clear；token 响应体零读取；主流程落盘经 finalize_results；autoplay
  flag 入报告；硬断言锚点（data-mic-phase 等待/语音作答/停止
  录音并提交识别/verify_network_facts/find_jwt）齐全。
- **login-only 认证边界（supervisor 修正 1）**：
  login_verification_learner 只调 `POST /api/v1/auth/login`（成功
  返回内存 token；401/无 token/5xx 全部 ENV-BLOCKED fail-closed，
  mock 实证）；harness 源码零 `ensure_user` 复用、零注册端点调用
  （文本契约锁定——零播种边界）。
- **落盘防线顺序（supervisor 修正 2）**：finalize_results **先对
  未脱敏报告做 JWT 自检**（脏报告必须以 failed + 退出码 1 + 自检
  标记收场、原始 JWT 不落盘——真实泄漏不可能以 passed 落盘）、
  **后**递归脱敏；干净 passed 报告不受影响；函数体内检测先于
  脱敏的顺序由文本契约锁定。

## 验证（2026-10-11，worktree 内真实执行；全部零网络/零运行时服务）

| 命令 | 结果 |
| --- | --- |
| `"D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe" -m pytest services/api/tests/test_verify_voice_studio_livekit_input.py -q` | **44 passed**（修正后复跑） |
| `... -m pytest services/api/tests/test_verify_voice_studio_livekit_input.py services/api/tests/test_verify_web_livekit_client.py services/api/tests/test_livekit_lan_cutover.py -q` | **145 passed**（新 44 + 邻居 harness 套件零回归；修正后复跑） |
| `npm run test --workspace apps/web` | **18 files / 326 tests passed**（hygiene +1，M14-252 契约零回归） |
| `npm run typecheck --workspace apps/web` | exit 0，0 error |
| `npm run lint --workspace apps/web` | exit 0，0 error / 0 warning |
| `npm run build --workspace apps/web` | ✓ Compiled successfully，路由条目与基点一致（零 app/ 路由改动） |
| `"D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe" -m ruff check infra/verify_voice_studio_livekit_input.py services/api/tests/test_verify_voice_studio_livekit_input.py` | All checks passed |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描（全部 staged `+` 行）：secret 形态
（api_key/secret/password/apikey/bearer 赋值形态）0 命中；JWT
`eyJ` 形态命中均为契约测试的合成假值（FAKE_JWT 断言样本与其
检测用例，签名段占位，非真凭据）；Windows 盘符/私有绝对路径
0 命中；U+FFFD 0 命中；`production_ready`/`release_ready`/
`public_ready` 置 true 的过度声明 0 命中。

依赖安装：worktree `npm install`（本地 registry 镜像，未走代理；
unrs-resolver postinstall 按 allowScripts 策略被阻，不影响验证）。

## 非声明（explicit non-claims）

- **不声明服务端订阅**、**流式 ASR**、**VAD**、**barge-in**、
  **pause/resume**——harness 验证的仍是 M14-252 整段 utterance
  录制 → /transcribe 上送路径，未新增任何语音能力。
- **不声明远程设备/跨 NAT/长时浸泡**：harness 在本机 Chromium
  （fake 麦克风 + 受控/默认 loopback 开关）上运行，单次有界。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- **不预声明任何真实运行结果**：本提交只交付 harness 与零网络
  契约验证；真实栈运行（live API/LiveKit + 真实浏览器）由
  supervisor 显式执行后另行记录——在那一歩完成前，「真实 E2E
  已验证」不是本仓库的既成事实。
- `production_ready` / `release_ready` / `public_ready` 均 false
  不变；本切片未启停任何运行时服务（未运行 harness 主流程）、
  未触碰 DB/LiveKit 服务端/凭据。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-254（M14-252 转前
  一任务快照），里程碑段追加 M14-254 注记（harness 交付口径，
  计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-254 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-254 条目（含非声明）。
