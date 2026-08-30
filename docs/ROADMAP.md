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
