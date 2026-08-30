# ADR-001: 从 Grok 原型迁移到生产 monorepo

## 决策

保留 `grok-workspace` 的界面风格和交互资产，但不在其目录内继续构建生产能力。新建 `ai-learning-os` monorepo，Web 使用 Next.js，API 使用 FastAPI。

## 原因

1. Grok 原型已经验证语音陪练、限时考试和错题复盘的用户流程。
2. 原型把考试时间、成绩、题库答案和掌握度放在浏览器，不能满足服务端权威与审计要求。
3. Grok 平台的 auth、PWA、preview bridge 和 app-data scaffolding 与本机生产架构无关。
4. 独立 monorepo 可以先固定 API 契约，再替换内存仓储为 PostgreSQL。

## 迁移清单

### 保留

- 视觉 token：米色纸质背景、深绿 accent、Newsreader/中文衬线标题、低阴影卡片。
- 导航结构：首页、语音、考场、学习库、掌握。
- 交互组件：Button、Card、Badge、Input、Progress、PaperCard、AppShell。
- 学习流程：语音读题、选项点选、考场题号矩阵、交卷确认、四角度复盘。
- 中文语音答案解析：A/B/C/D、甲乙丙丁、第一个到第四个、正确/错误。

### 不迁移

- 浏览器 `Date.now()` 作为考试开始时间。
- `sessionStorage` 和 zustand persist 作为成绩事实源。
- 前端静态题库、正确答案和解析。
- Grok preview host bridge、平台 auth、PWA manifest。
- 业务层直连 xAI `grok-4.5`。
- 前端 mastery 数值更新作为最终 Student Model。

## 后果

短期需要重写考试状态和 API 客户端；长期获得服务端权威、可替换模型层、可替换语音层和可审计数据事件。
