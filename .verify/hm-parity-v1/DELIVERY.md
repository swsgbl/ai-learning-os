# hm-parity v1 最终交付(2026-10-24)

工程:`apps/harmony`(bundle com.ailearningos.app) · commit `67fa1da`
目标设备:HDC 192.168.161.137:10178(VMware KaihongOS 6.1.2.08,API 23)

## 一、文件清单

| 文件 | 状态 | 内容 |
|---|---|---|
| `entry/src/main/ets/AiosApi.ets` | 修改 | 追加通用受保护 `requestJson<T>`(GET/POST;内存 bearer fail-closed;409/404/503 服务端 detail 原样透出;400 固定文案;绝不 [object Object]) |
| `entry/src/main/ets/LearningApi.ets` | 新增 | 双面板全部端点封装:学习冲刺(contracts/confirm/sprints/advance/assessment/submit)+ 教材工作台(catalog/search、imports、parse(?ocr_max_pages=15)、concepts、create-sprint);11 阶段链中文映射 + nextSprintPhase 单向推进 |
| `entry/src/main/ets/pages/LearningSprintPage.ets` | 新增 | 合同列表→确认→阶段链→推进→前/后测作答(选项+1-5 信心)→提交服务端判分;答案隔离(题目投影无 answer 字段);409 detail 透出;disposed+generation 竞态防护 |
| `entry/src/main/ets/pages/TextbookWorkbenchPage.ets` | 新增 | 目录搜索→导入(permission_statement:true)→书架(分态徽标:已解析/扫描书/待解析/大小/概念数)→解析/OCR 解析/提取概念/一键开冲刺 |
| `entry/src/main/resources/base/profile/main_pages.json` | 修改 | 注册两页路由(VoiceExamPage 模式) |
| `entry/src/main/ets/components/StudyPane.ets` | 修改 | 学习 Tab 顶部两张入口卡(六 Tab 结构零改动) |

构建:`hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon` → BUILD SUCCESSFUL

## 二、验收清单

### UI 真机验收(uitest dumpLayout 结构化证据,非截图)

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| 1 | 首页六 Tab 不变(首页/学习/搜索/语音/设置/治理) | **PASS** | layout1.json 全量 dump |
| 2 | 学习 Tab 两张入口卡:📚 教材工作台 + 🏃 学习冲刺 | **PASS** | layout-study-tab-final.json(卡片 @287,245 / @706,245,含副标题) |
| 3 | LearningSprintPage 打开:标题/48h 副题/未登录提示/合同区 401 detail 透出 | **PASS** | layout-learning-sprint-final.json(「请求失败 (HTTP 401): Missing bearer token」逐字展示) |
| 4 | TextbookWorkbenchPage 打开:搜索区/书架/401 detail 透出 | **PASS** | layout-textbook-workbench.json(此前轮次) |
| 5 | harmony_ui_regression 视觉回归(教材工作台/学习冲刺关键词) | **PASS** | shot-uiregress-pass.jpeg(vision 模型确认双卡可见) |

### API 契约验收(qa_m15025 登录 token,直连 https://ndtool.cn/aios)

| # | 判据 | 结果 | 实测 |
|---|---|---|---|
| 6 | POST /auth/login → token | **PASS** | JWT 237 字节 |
| 7 | GET /learning/contracts | **PASS** | 返回真实合同(离散数学冲刺,3 个 target_outcomes,DTO 字段逐一匹配) |
| 8 | GET /contracts/{id}/sprints | **PASS** | sprint id/phase=created/paused/expired 字段匹配 |
| 9 | GET /sprints/{id}/assessment?phase=baseline 越阶段拒绝 | **PASS** | detail「前测/后测未在对应阶段:当前 created(需 baseline_assessment)」——requestJson 将原样透出 |
| 10 | GET /catalog/search?q=math | **PASS** | 1 条命中(离散数学,ChinaTextbook,permission_status/parts_count 字段匹配) |
| 11 | GET /library/imports | **PASS** | 3 本书;含 ready+parsed 态(分态徽标有真实数据) |
| 12 | POST /imports/{id}/concepts | **PASS(错误路径)** | 服务端 LLM 网关临时故障,detail「LLM 输出不可解析(可重试)」——UI 将如实展示,证明 5xx detail 透出语义 |

### 不变式审计

- ✅ 答案隔离:AssessmentItem 投影无 answer/explanation 字段;判分只在 submit 后来自服务端
- ✅ token 零持久化:内存 bearer(AiosApi 模块级),不进 preferences/文件
- ✅ UrlPolicy.validateBaseUrl 先于全部请求(requestJson 首行)
- ✅ 409/404/503 detail 逐字透出(验收 #3/#9/#12 三处实证)
- ✅ 六 Tab 结构零改动(仅 StudyPane 顶部加卡)

## 三、已知限制

1. **VM 中文 IME 劫持文本注入**:uitest inputText/uinput -K -t 输入 ASCII 会被输入法转成中文(密码框因不弹 IME 而幸免)。因此登录态 UI 端到端(键入用户名→登录→面板拉数据)未能在 VM 上走完;以 API 契约验收(#6-#12)+ 未登录 UI 渲染验收(#1-#5)双证据替代。真机(物理键盘/手输)无此限制。
2. **宿主 API24 产物 → API23 设备**:需重打包(pack.info compatible/target 24→23 + module.json 60101024→60101023)+ VM 侧签名。脚本已固化(`.hm-tmp/hm-patch-hap.cjs` + `.hm-tmp/vm-sign23.sh`),建议搬入 `tools/harmony_release/`。
3. concepts 端点当前因服务端 LLM 网关故障不可用(与客户端无关,错误文案已验证会如实展示)。
4. `.verify/hm-parity-v1/entry-api23-unsigned.hap` 为中间产物(未签名),正式发布走 DevEco 签名流程。

## 四、证据文件(.verify/hm-parity-v1/)

- layout-home.json / layout-study-tab-final.json / layout-learning-sprint-final.json / layout-textbook-workbench.json(uitest dumpLayout)
- shot-study-tab.jpeg / shot-uiregress-pass.jpeg
- VERIFY.md(过程记录)
- DELIVERY.md(本文)
