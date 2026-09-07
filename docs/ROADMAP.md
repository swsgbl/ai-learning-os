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
- [ ] 搜索接入（M12-04 第一切片已本地实现并提交于分支 `feature/m12-04-android-search-flow`（来源 URL 安全行返工与模拟器冒烟证据未提交），未 push、未开 PR——服务端权威搜索：搜索源可用性、计划预览、执行搜索与 skipped 原因、query_id 回查，结果/回查来源 URL 原文可溯源展示且仅合法 https 可经 ACTION_VIEW 打开，本地门禁 318 JVM 单测（首轮 303 + 返工新增 15）/ lint 0 error / 19 warning（基线同构成）；Android 16 emulator-5554 loopback mock 冒烟已通过（2026-09-07，证据 `docs/evidence/m12-04-android-search/`——五位导航、providers/plan/search/record、结果与回查 URL 原文两行断行、合法 https ACTION_VIEW 由 Chrome 接管、无 FATAL/ANR；Chrome 停在首运行页不代表页面加载成功，mock URL 为 localhost 不代表外网 provider）；未验真机、真实 provider、非 https 禁用态的模拟器交互（该禁用逻辑由 ResultUrlPolicyTest JVM 覆盖）与生产可用，待 Codex 验收）
- [ ] 治理与发布链路接入
