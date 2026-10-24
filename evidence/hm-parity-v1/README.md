# hm-parity v1 验收记录（学习冲刺 + 教材工作台,2026-10-24）

commit: 67fa1da `feat(harmony): learning sprint + textbook workbench panels (hm-parity v1)`

## 交付物
- `entry/src/main/ets/LearningApi.ets`(267行):学习冲刺 6 端点 + 教材工作台 6 端点 + 11 阶段链中文映射
- `entry/src/main/ets/pages/LearningSprintPage.ets`(592行):合同列表→确认→阶段链→推进→前测/后测作答(选项+1-5信心)→服务端判分
- `entry/src/main/ets/pages/TextbookWorkbenchPage.ets`(534行):目录搜索→导入→书架→解析/OCR/概念提取/一键开冲刺
- `AiosApi.ets` +86行:通用受保护 `requestJson<T>`(GET/POST,内存 bearer,UrlPolicy 白名单,4xx detail 透出)
- `StudyPane.ets` +41行:学习 Tab 顶部两张入口卡(router.pushUrl,六 Tab 结构零改动)
- `main_pages.json`:注册 2 个新页面

## 不变式守护
- 答案隔离:AssessmentItem 无 answer 字段;判分只在提交后来自服务端
- token 零持久化:内存 bearer(AiosApi 模块级),登录提示只读判定
- 错误如实:401/409/404/503 的服务端 detail 字符串原文展示,400 固定文案(注入防回显)

## 构建链(KaihongOS VM @ 192.168.161.137:10178,API 23)
宿主 DevEco SDK=26 产出的 hap 装机被拒(older sdk version),走 VM 原生链:
1. 宿主 tar 源码(排除 build/oh_modules)→ hdc file send → /data/local/tmp/hmproj
2. VM: build-profile 覆写为 API23 OpenHarmony(数字 23)+ deviceTypes:["default"]
3. VM `pack_hap -m debug`:hvigor CompileArkTS 通过(~15s),PackageHap 因无 java 失败属预期
4. 手动 C++ pack:注入 pkgContextInfo.json(entry+@ohos/hypium)后 ohos_packing_tool pack
5. VM 设备侧签名:UnsgnedDebugProfileTemplate.json 改 bundle-name → sign-profile(keyAlias "openharmony application profile debug")→ sign-app
6. bm install 成功,aa start 启动成功

## 真机验收(截图证据,见本目录 before/)
- 六 Tab 结构完好(首页/学习/搜索/语音/设置/治理)
- 学习 Tab 顶部显示两张入口卡:📘教材工作台 / 🏃学习冲刺
- 入口卡点击 → router.pushUrl 跳转正常
- 学习冲刺页:标题+返回+未登录提示+401 detail 原文(`请求失败 (HTTP 401): Missing bearer token`)+重试按钮,全部如实渲染
- (教材工作台页渲染未截图留证——窗口层叠导致点击漂移,但同一 router 机制已被学习冲刺页验证)

## 已知限制
- 前后测作答全流程与登录后合同操作需有效 token + 服务端数据,本次以未登录 fail-closed 路径验证
- VM 上多窗口层叠(dsh/BrowserOS/AIOS),坐标点击需先 `aa start` 唤前台
