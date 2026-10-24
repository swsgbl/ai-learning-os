# hm-parity v3 断点（2026-10-24 勘察棒，上下文耗尽退场）

**状态：勘察 100% 完成，代码零写入。** 下棒直接按本文件开工，勿重读大文件。

## 分支与工作区
- 分支 `feat/hm-parity-v3`（基点 69a9928 = origin/main + docs prompt，已 push）
- 工作区脏改动：`LearningApi.ets` +7 行（上棒加的 `presentReview` 端点，无害保留）
- `.hm-tmp/`（未跟踪）= v1/v2 可复用脚本：`hm-patch-hap-v2.cjs`(API24→23 重打包)、
  `make-profile-v2.cjs`(profile UDID patch)、`vm-sign-install*.cjs`、`find-inputs.cjs`、
  `list-texts.cjs`、`verify-patched-zip.cjs`

## 任务（docs/agent-prompts/hm-hmos-parity-v3.md，已读毕）
- **A** 设置 Tab 第三面板 `AiKeysPane.ets`（SettingsPane/AuthPane/DownloadPane 之后）：
  7 端点（GET/PUT/DELETE ai-keys、POST llm/models 探测、POST llm/profiles、
  POST profiles/{id}/activate、DELETE profiles/{id}）。密钥只显后 4 位、零持久化、删除需确认。
- **B** 新页 `CreateCoursePage.ets` + main_pages.json 注册（第 6 路由）+ 首页 HomePane
  置顶入口卡：POST generation-drafts {topic} → 3s 轮询（timeout 120s 如实报错，
  disposed+generation 竞态防护）→ materialize → 成功回学习 Tab。
- **C** 语音 Tab：登录态加「选择试卷开语音考试」入口（拉 papers → pushUrl
  VoiceExamPage 传参 paperId/paperTitle，对齐 StudyPane「直接考试」按钮的现有传参写法）。
  VoicePane 本体已有 /voice/providers 展示（缺口 C 第 2 点已天然满足）。

## 关键代码事实（已核实）
- 网络层：`AiosApi.ets` 的 `requestJson<T>(baseUrl, method, path, bodyObj)` —
  bearer fail-closed（inMemoryBearer 模块级）+ 409/404 detail 透出 + 400 固定文案。
  **新端点全部走 requestJson，勿另写 http 调用。**
- 登录态判定：`hasBearerToken()`（AiosApi 导出）；AuthPane 管理会话。
- Index.ets：设置 Tab 在 `TabContent→Column→Scroll→Column{SettingsPane/AuthPane/DownloadPane}`
  追加 AiKeysPane 即可；语音 Tab 是 `Column{ Button('🎙 开始语音作答')+VoicePane }`——
  在 Button 后插入登录态选卷入口。六 Tab 结构零改动。
- HomePane：WelcomeCard 之后加「🎯 定制课程」入口卡（参照 v1 StudyPane 入口卡样式：
  #f5f5f5 圆角 8 + Text + Button）。
- main_pages.json 现有 5 页：Index/VoiceExamPage/LearningSprintPage/TextbookWorkbenchPage/ExamPage。
- 路由传参参照：ExamPage 的「直接考试」在 StudyPane，`pushUrl({url, params:{paperId,paperTitle}})`。

## API 探针（下棒先跑，宿主 node）
生产 `https://ndtool.cn/aios/api/v1`，qa_m15025 / Qa-M15-025!x9。
先 login 拿 token，再实测：GET /me/ai-keys、PUT /me/ai-keys/{slot}（用假 key 后删）、
POST /me/ai-keys/llm/models、POST /me/ai-keys/llm/profiles、activate、DELETE、
POST /courses/generation-drafts、materialize、GET /voice/providers。
**注意 PUT ai-keys 返回脱敏 {slot,key_set}；slot 合法值以 GET 返回为准。**

## 构建装机链（全固化，照抄）
1. `cd apps\harmony && hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon`（勿改 build-profile.json5 SDK 版本）
2. `node .hm-tmp\hm-patch-hap-v2.cjs`（API 24→23 重打包）
3. hdc tconn 192.168.161.137:10178 → 推 VM → VM 侧 `/data/local/home/.ohos/signature`
   hap-sign-tool：sign-profile（长名参数，keyAlias 全小写 "openharmony application profile release"）
   + sign-app（"openharmony application release"，compatibleVersion 23）
4. **先 bm uninstall 再 bm install**（provision type 冲突坑）→ aa start com.ailearningos.app
5. hdc 路径 `C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc`

## 验收清单（任务书）
1. BUILD SUCCESSFUL + VM 装机 + 六 Tab 不回归（layout dump）
2. 设置 Tab AiKeysPane（未登录态提示）
3. 首页定制课程入口 → CreateCoursePage（未登录态）
4. 语音 Tab 升级入口结构
5. API 探针全绿（qa token 实测）
6. `.verify/hm-parity-v3/{VERIFY.md,DELIVERY.md}` + layout dump 证据
（VM IME 无法注入登录——设备端一律用未登录态验证，API 用宿主探针）

## 已知坑（历史）
- 写大 ArkTS 文件偶发截断注入 → 写后 findstr 关键行抽查
- VM 截图会被 BrowserOS 窗口遮挡 → layout dump 为权威证据
- requestJson 的 bodyObj 参数 null 时无 extraData——GET/DELETE 传 null，POST 传 {}

## v2 收尾棒增补(2026-10-25 01:50,commit 7bccc93 已入库)

### 1. v2 验收时发现并已修复的 bug(presentReview)
- 真机复现:startReviewItem 直接 attempt → 409「非法复习状态转换: 
  'scheduled' → 'attempted'」。服务端状态机要求 scheduled→presented→attempted。
- 已修:LearningApi.presentReview + startReviewItem 先 present(scheduled 时)再拉题。
- **注意:装机链须用本次修复后的构建**(7bccc93),VM 上的旧包无此修复。

### 2. VM 登录配方(v2 实测打通,可用但繁琐——设备端验证优先未登录态)
1. `bm uninstall && bm install && aa start`(重装清 Asset token,也清 IME 污染缓存)
2. 设置 Tab:点用户名框(约 687,552)→ 点屏幕右下「中」字(1207,705)切英文 →
   `uitest uiInput inputText <x> <y> qa_m15025`(处女输入才干净,重复注入会追加)
3. 密码框:Back 收输入法 → 点框 → 切英文 → **分段注入** `Qa-M15`/`-025`/`!x9`
   (整串注入丢 `!`,12 字符变 11 → 401)
4. 内容区上滑(swipe 640→500)露出提交按钮「登录」(约 [275,453])→ 点击
5. 验证:「登录成功: qa_m15025」「当前登录用户」
- Asset Store 持久化 token 存在:force-stop 重启后会显示「已从安全存储恢复登录」,
   但各页面 aboutToAppear 的首次请求可能早于恢复完成 → 点刷新/重试即可。

### 3. ExamPage 已知缺陷(低优先,可在 v3 顺带修)
- 报告页(REPORT phase)标题栏不渲染「退出」按钮(条件 `phase !== REPORT` 写反了意图),
   报告页只能 force-stop 退出。修复:报告页也显示退出(或加「完成」按钮 router.back)。

### 4. dump 驱动配方(v2 验证过的可靠模式,.hm-tmp/ 有成品可抄)
- 全量 dump → 找 text="砚席" 节点的 hostWindowId → 只收集该窗口节点(抗 BrowserOS 遮挡)
- `uitest uiInput click/swipe/keyEvent Back` 可用;text/inputText 中文框必污染
- 窗口漂移(点击后砚席窗口消失):`aa start` 重新拉起,最多 3 轮

### 5. 装机链一键脚本
- `.hm-tmp/redeploy.cjs`:patch→推VM→签名→install 全链(注意 profile-v2.json 要
   先 file send 到 /data/local/tmp/v2sign/,脚本内没推它,单跑一次即可)
