# hm-parity v3 验收记录(2026-10-25,未登录态)

## 构建链
- hvigorw assembleHap release → BUILD SUCCESSFUL(7.6s)
- hm-patch-hap-v2.cjs API24→23(534888B→534892B,module.json 60101023)
- VM 签名:sign-profile(debug alias)+sign-app(release alias)成功
- bm uninstall → install successfully → aa start 成功

## 验收矩阵(layout dump 权威,视觉模型不可用)
| # | 验收点 | 证据文件 | 结果 |
|---|--------|----------|------|
| 1 | 首页 Tab「🎯 定制课程」卡+开始定制按钮 | v3-02-tab-home.json | ✅ |
| 2 | CreateCoursePage 路由+标题+未登录提示+输入区+生成按钮 | v3-03-createcourse.json | ✅ |
| 3 | 语音 Tab:🎙开始语音作答+登录态选卷入口(未登录如实提示) | v3-04-voice.json | ✅ |
| 4 | 设置 Tab AiKeysPane(BYOK):标题+刷新+未登录提示 | v3-01-home.json(实抓设置Tab) | ✅ |

## 契约修正(相对 RESUME-STATE-V3 勘察记录)
读服务端 me_ai_keys.py ground truth 后修正,与勘察记录差异:
- 槽位白名单收敛为单槽 "llm"(非 openai/anthropic 多槽)
- GET /me/ai-keys 响应 = {keys:[{slot,configured,last4,updated_at,effect,endpoint,model}], profiles:[LlmProfileOut]}
- LlmProfile 字段 = id/label/last4/endpoint/model/active(无 provider/display_name/base_url/created_at)
- POST llm/profiles 请求 = {label,api_key,endpoint?,model?,activate}(响应裸 LlmProfileOut 201)
- POST llm/models 请求 = {api_key?,endpoint?}(均可空=用已存配置;无 model 参数)
- 密钥展示=last4 服务端字段(非客户端截断)

## 交付文件
- ets/V3Api.ets(新,10 端点)
- ets/components/AiKeysPane.ets(新,BYOK 面板)
- ets/pages/CreateCoursePage.ets(新,第 6 路由)
- ets/pages/Index.ets(+import ×2,设置Tab挂AiKeysPane,语音Tab登录态选卷入口)
- ets/components/HomePane.ets(+定制课程入口卡)
- ets/pages/ExamPage.ets(报告页退出按钮修复:条件写反)
- main_pages.json(+CreateCoursePage)

## UI 工具链(可复用)
- .hm-tmp/lay2txt.cjs:layout dump → 可读文本树
- .hm-tmp/ui-nav.cjs:layout dump 找文本 bounds → uinput 点击
- .hm-tmp/v3-install.cjs + 手动 hdc 三步(见构建链;cmd 嵌套引号陷阱:file send 目标路径勿带引号)
