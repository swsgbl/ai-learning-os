# 实施路线

## M0 工程基线

- [x] monorepo 与 Next.js/FastAPI 分层
- [x] Grok 视觉资产迁移
- [x] 服务端权威考试 API 草案
- [x] Docker Compose 基础设施
- [x] Git 初始化
- [ ] 远程仓库
- [ ] CI
- [ ] PostgreSQL repository

## M1 内容与来源

- [ ] Source Registry
- [ ] License State Machine
- [ ] 文件上传与 hash 去重
- [ ] Docling/MinerU/Marker/olmOCR adapter
- [ ] Evidence 与页码定位

## M2 考试与评分

- [x] 内存版 ExamSession
- [x] 答案事件序列
- [x] 幂等提交
- [ ] PostgreSQL FSM 持久化
- [ ] Redis timeout worker
- [ ] 数学/主观题 grader

## M3 学习模型

- [ ] LearningEvent 标准化
- [ ] Concept DAG
- [ ] StudentConceptState 重放
- [ ] FSRS-like scheduler

## M4 语音

- [x] 浏览器本地语音过渡实现
- [ ] LiveKit server/token
- [ ] FunASR/CosyVoice 本地 adapter
- [ ] 在线 provider fallback
- [ ] VoiceSession FSM 与打断恢复

## M5 检索

- [ ] 多源搜索
- [ ] 去重排序
- [ ] 受控抓取
- [ ] 课程/试卷导入审核

## M12 原生 Android

- [x] M12-01 App Shell + API/Auth 基础（Compose M3 壳、五屏、登录/会话、可配置 API 地址、Keystore 加密 token）
- [x] 考试/学习业务接入（M12-02 已随 PR #45 合并 main——学习试卷列表、考场状态机、审阅报告，PR CI 与 merge 后 main CI 全绿实证；仍未跑模拟器/真机）
- [x] 语音能力接入（M12-03 第一切片已随 PR #47 合并 main，merge commit `2bbeb072`——服务端权威语音陪练：创建/恢复会话、读题播报链、命令/意图作答、打断/暂停/跳过/结束、录音转写代理、全卷报告、trace 上报；PR CI run `34049713374` 与 merge 后 main CI run `34049946924` 四项全绿实证；仍未跑模拟器/真机、未接真实 provider）
- [x] 搜索接入（M12-04 第一切片已随 PR #49 合并 main，merge commit `64e26caeb83645e49a8f2db87dbcbfd97ea524e0`，PR CI run `34073874737` 与 merge 后 main CI run `34074112566` 四项全绿——服务端权威搜索 providers/plan/search/record、318 JVM 单测、lint 0 error；Android 16 emulator-5554 loopback mock 冒烟已通过；仍未验真机、真实 provider、非 https 禁用态模拟器交互与生产可用边界）
- [x] 治理与发布链路接入（只读第一切片）（M12-05 已随 PR #51 合并 main，merge commit `8c6fe6b6354c504e8e61beb078bfe2675b7f558a`——Android 只读消费 /version、/ops-snapshot、/audit 三端点、347 JVM 单测、lint 0 error；PR CI run `34085123408` 与 merge 后 main CI run `34085414607` 四项全绿（API/Android/Docker/Web）；Android 16 emulator-5554 loopback mock 冒烟通过（证据 `docs/evidence/m12-05-android-governance/`）；仍未验真机、真实 provider、生产后端/生产 DB、治理写操作与生产可用）
- [x] loopback 冒烟 harness（M12-06 已随 PR #53 合并 main，merge commit `4cbdfd2512891b36443c2caa5d967769567fcfac`——tools/android_smoke + tests/android_smoke（18 files / +4566），Python 单测 215 passed / 1 skipped；真实 emulator-5554 冒烟 15 stage passed、mock expected 18 / unexpected 0 / total 18、logcat 本包 FATAL/ANR/crash=0（AndroidRuntime 标签行 145 为 uiautomator 工具进程生命周期日志的仅诊断计数，非崩溃）；首提交引入的根 tests/__init__.py 由 `e505716` 删除以避免与 services/api 测试包冲突；PR CI run `34132047633` 与 merge 后 main CI run `34132434939` 四项全绿；仍未验真机、真实 provider 与生产链路，证据 .verify/m12-06-android-smoke-r8 为 gitignored 本地目录不入库）
- [ ] 治理与发布链路接入第二切片（待运维授权评估：真机验证、真实 provider 冒烟、治理写链路/生产接入——治理写操作与生产链路不随只读第一切片视为完成）

## M13 HarmonyOS

- [x] M13-01 只读壳 + API/Auth/设置（已随 PR #54 合并 main，merge commit `cedf862e4b940d7c637378eea40be2247ba4249d`——ArkTS 五 Tab 只读壳（com.ailearningos.app）、AiosApi 只读 GET 六端点（METHOD_GET 硬编码）、UrlPolicy base URL 白名单、Settings 仅存 base URL（preferences 唯一键 api_base_url，零 token/password/key）、仅 ohos.permission.INTERNET 权限；本地模拟器（hdc -t 127.0.0.1:5557）clean assembleHap / 未签名 HAP 安装 / 启动 / 五 Tab 真实点击 / 六张 1320x2856 非空截图验收通过（证据 `docs/evidence/m13-01-harmony-shell-api-auth/`，随 PR 入库）；PR CI run `34132471980` 与 merge 后 main CI run `34132893106` 四项全绿（API/Android/Docker/Web——CI 无 HarmonyOS job，HarmonyOS 构建验收为本地口径）；未验真机、未接真实 provider/生产 DB/生产后端/治理写链路，production_ready=false 不变）
- [x] M13-02 Home mock 数据与降级验收（已随 PR #56 合并 main，merge commit `0b125319f8b2ef4f05428d75925e8293c3e8d4a6` 本地 git 可验证，PR CI run 回填待后续状态切片——`tools/harmony_mock` 复用 Android 只读 mock 契约，仅暴露 Home 六个 GET 端点，非 GET/未支持 method（如 HEAD/OPTIONS/FOO）/未知路径/错误 audit query 均 404；契约测试实现时首验 18765 与 28765 均 15/15 passed，审查补齐未支持 method 负例并修复 fail-closed 501 缺口后最终两端口均 18/18 passed；Settings 真实 UI 保存并测试 `http://192.168.8.3:8765/` 成功，Home 六区 mock 渲染通过；停止 mock 后六区错误态保留重试、App 不崩溃且四 Tab 可切换。已知 HomePane 仅 aboutToAppear 读取一次 base URL，Settings 更新后已挂载 Home 不自动刷新，后续独立优化（该缺口已由 M13-03 修复）。证据 README 与 fixture 入库，`.verify/` 原始证据不入库；未验真机、真实 provider、生产后端/生产 DB，CI 无 HarmonyOS job，production_ready=false 不变）
- [x] M13-03 Home 地址变更自动刷新（已随 PR #57 合并 main，merge commit `529b4d4ade7d172cbc3c4d66bcf0720568bbb9a3`——修复 M13-02 记录的 HomePane 生命周期缺口：Settings 保存 base URL 成功后，已挂载 Home 无需重启即自动刷新。使用官方 `@kit.BasicServicesKit` `emitter` 进程内事件；事件 ID 集中由 SettingsStore 导出 `EVENT_ID_URL_CHANGED = 'aios://settings/url_changed'`；SettingsStore 仅在 UrlPolicy 校验 + preferences `put` + `flush` 全部成功后 emit，非法保存不写盘不发事件；HomePane 持单一稳定回调，`aboutToAppear` 先 off 再 on、`aboutToDisappear` 用带回调的 `emitter.off` 精确退订；回调不信任事件 payload，以 `loadBaseUrl(context)` 重读持久化为权威来源；`aboutToAppear` 仍仅一次初始加载与一次初始刷新。clean 构建通过（`hvigorw.bat clean --no-daemon` → `assembleHap --no-daemon` 均 BUILD SUCCESSFUL，HAP 197,338 bytes，SHA256 `C06327CED5973DD5A634E8A974C2DF9C26D6991CBF815F2BE9416BC8C1938960`）；本地模拟器 `127.0.0.1:5557` 正向流（默认地址真实错误态 → Settings 真实 UI 保存 `http://192.168.8.3:8766/` → 不重启 App，Home URL 与六区刷新为 mock 数据）与负向流（`javascript:alert` 被 URL policy 拒绝后 Home 保留旧数据不刷新为错误态，证明无事件发出）均通过；证据 README 入库，`.verify/` 原始证据不入库；PR head `b5a60a1` CI 四项 success，merge 后 main CI run `34157243447` 四项 success；未验真机、真实 provider、生产后端/生产 DB、治理写链路，CI 无 HarmonyOS job，production_ready=false 不变）
- [x] M13-04 Android USB 物理设备冒烟（已随 PR #58 合并 main，merge commit `029281639ba645742d9ea089b96f2493774b8b68`——M12-06 冒烟 harness 扩展到 USB 物理设备：`--device-type physical` 在 App 启动前 `adb reverse --no-rebind` 建立设备回环→宿主回环通道（mock 仍只绑宿主机 `127.0.0.1`，不暴露 LAN），首次启动后经真实 Settings UI 把 base URL 配成 `http://127.0.0.1:<port>/`，收尾 finally 中 `adb reverse --remove`（失败如实记 stage）；滚动坐标按 `wm size` 实测尺寸百分比换算（1080x2400 等价旧坐标、720x1600 落屏内），搜索流程执行后按需收起 IME（`dumpsys input_method` 判断）再滚结果卡进视口；finalize 韧性——dump logcat 失败（如 USB 掉线）时 dump-logcat 记 failed、stats 置零标 `unavailable`、summary 照常写出，不隐性通过；真机加固——uiautomator dump 零尺寸 bounds 节点跳过、ANR 词边界匹配（`fileCanRead` 子串不再误判）。Python 单测 285 passed / 1 skipped（canonical venv）、Gradle `testDebugUnitTest` 347/0 + `lintDebug` 0 error + `assembleDebug`、模拟器回归 15/15 stage；真机 Huawei MGA-AL00（720x1600，serial `EYFBB22923201473`）18/18 stage passed、mock 19 expected / 0 unexpected / 19 total、logcat FATAL/ANR/本包 crash 均 0、`adb reverse --list` 收尾为空；PR CI run `34160683530` 与 merge 后 main CI run `34176554925` 四项 success（证据 `docs/evidence/m13-04-android-physical-smoke/`）；mock only、无真实 provider/生产后端/生产 DB/凭据，production_ready=false 不变）
- [ ] M13 后续切片（待评估：学习/考试/搜索/语音/治理各业务域只读接入、AGC 签名与发布流程、真机验证与真实 provider 冒烟——授权评估前不动工、不虚构进展）
