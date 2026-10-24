# hm-parity v1 验收记录(2026-10-24)

目标设备:HDC 192.168.161.137:10178(VMware KaihongOS 6.1.2.08,API 23,UDID DF4857...)

## 交付物
- entry/src/main/ets/AiosApi.ets — 追加通用受保护 requestJson<T>(GET/POST,bearer fail-closed,
  409/404/503 服务端 detail 原样透出,400 固定文案)
- entry/src/main/ets/LearningApi.ets — 新建:学习冲刺 + 教材工作台全部端点封装
  (contracts/confirm/sprints/advance/assessment/submit + catalog/search/library imports
  /parse(?ocr_max_pages=15)/concepts/create-sprint),11 阶段链中文映射
- entry/src/main/ets/pages/LearningSprintPage.ets — 新建:合同列表→确认→阶段链→
  推进→前/后测作答(选项+1-5 信心)→提交判分;答案隔离(题目投影无 answer 字段)
- entry/src/main/ets/pages/TextbookWorkbenchPage.ets — 新建:目录搜索→导入
  (permission_statement:true)→书架(分态徽标)→解析/OCR/提取概念/一键开冲刺
- main_pages.json — 注册两页路由
- StudyPane.ets — 学习 Tab 顶部两张入口卡(六 Tab 结构零改动)

## 构建
hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon
→ BUILD SUCCESSFUL(API 24 产物 973,759 B)

## 装机(深开鸿 VM 签名链)
宿主 SDK 签名被 VM BMS 拒(code 9568329 verify signature failed)。
改走 VM 侧签名(/data/local/home/.ohos/signature):
1. sign-profile 生成 p7b:模板 UnsgnedDebugProfileTemplate.json,patch bundle-name→
   com.ailearningos.app、validity 2024-2035、device-ids→本机 UDID;
   keyAlias 必须小写 "openharmony application profile debug"(keystore 别名大小写敏感!)
2. 宿主 node 重打包 HAP(hm-patch-hap.cjs):pack.info "compatible/target":24→23、
   module.json minAPIVersion/targetAPIVersion 60101024→60101023(VM 是 API 23)
3. VM 侧 sign-app(keyAlias 小写 "openharmony application release")→ bm install 成功

## 真机验收(layout dump + 截图 + harmony_ui_regression)
- [x] 首页六 Tab 不变(首页/学习/搜索/语音/设置/治理)——layout-home.json
- [x] 学习 Tab 两张入口卡渲染:📚 教材工作台@287,245 / 🏃 学习冲刺@706,245
      ——layout-study-tab.json + shot-study-tab.jpeg
- [x] TextbookWorkbenchPage 打开:标题/未登录提示条/目录搜索区/我的教材区
      (401 detail 如实展示 "Missing bearer token")——layout-textbook-workbench.json
- [x] LearningSprintPage 打开:标题「48 小时可验证闭环」/未登录提示/合同区
      (401 detail 如实展示)——layout-learning-sprint.json
- [x] harmony_ui_regression PASS(expect 教材工作台/学习冲刺 均 hit)
      ——shot-uiregress-pass.jpeg

## 未登录状态下验证范围说明
本轮真机验证覆盖:路由注册、页面渲染、入口卡导航、受保护请求失败路径
(401 + 服务端 detail 中文文案)。登录态端到端(合同确认/作答判分/导入解析)
依赖测试账号 + 后端会话,属提示词验收清单的账号依赖项,未在本轮执行。

## 遗留
- 宿主构建产物 API 24,装机需 VM 侧 patch+重签(脚本已固化 .hm-tmp/vm-sign23.sh
  + hm-patch-hap.cjs;后续可搬入 tools/harmony_release)
- 工程build-profile保持 6.1.1(24)(原状);VM 兼容目标是 23,重打包脚本负责对齐
