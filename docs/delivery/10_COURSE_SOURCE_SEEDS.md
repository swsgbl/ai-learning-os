# 课程与真题来源种子 v2.2

日期：2026-08-30  
用途：Search Service / Source Registry 的初始种子，不等于内容再分发白名单。实际导入仍必须逐项执行 robots、访问控制、许可条款和人工复核。

## 1. 来源分层

| Tier | 定义 | 默认动作 |
|---|---|---|
| S | 学校、院系、图书馆、出版社或项目官方入口。 | 优先收录目录、链接、公开元数据和官方说明。 |
| A | OpenStax、OER Commons、OpenLearn 等明确 OER 平台，或官方开放教材。 | 读取具体许可后进入私有学习库。 |
| B | 课程平台、MOOC、edX/Coursera 等官方聚合页。 | 收录课程入口；正文、视频和习题按平台条款处理。 |
| C | 社区仓库、攻略、笔记、转载站。 | 仅作发现线索，不默认导入或再分发。 |
| U | 未识别来源。 | 只保留 URL 和摘要线索，进入人工审核。 |

## 2. 中国大陆与香港官方入口

| 来源 | URL | 初始状态 | 用途 |
|---|---|---|---|
| 国家高等教育智慧教育平台 | https://higher.smartedu.cn/ | S / platform terms | 中国高校课程目录和官方入口发现。 |
| 清华 MOOC Platform | https://www.tsinghua.edu.cn/en/Admissions/Online_Education/MOOC_Platform.htm | S / platform terms | 清华课程与 XuetangX 入口。 |
| XuetangX | https://www.xuetangx.com/ | B / platform terms | 清华发起的 MOOC 聚合平台。 |
| 北大 Global Open Courses | https://www.oir.pku.edu.cn/goc/Application1/Global_Open_Courses1.htm | S / application terms | 北大全球开放课程目录。 |
| 北大英文官网在线课程导航 | https://english.pku.edu.cn/admissions.html | S / navigation | edX、Coursera、MOOC 与官方课程入口发现。 |
| HKU Online Learning | https://tl.hku.hk/hkuonline/ | S / platform terms | HKU 官方 MOOC 与证书课程入口。 |
| HKU ExamBase FAQ | https://lib.hku.hk/FAQ/Research/index.html | S / access controlled | 只提供授权入口与使用说明，不绕权抓取。 |
| HKU ExamBase LibGuide | https://libguides.lib.hku.hk/c.php?g=766114&p=5501362 | S / access controlled | 部分历年试卷的官方图书馆入口。 |
| HKUST CEI MOOC | https://cei.hkust.edu.hk/en-hk/education-innovation/mooc | S / platform terms | HKUST 官方 MOOC 入口。 |
| 中国大学 MOOC | https://www.icourse163.org/ | B / platform terms | 国内课程发现入口，正文按平台条款处理。 |

## 3. 西方大学与 OER 官方入口

| 来源 | URL | 初始状态 | 用途 |
|---|---|---|---|
| MIT OpenCourseWare | https://ocw.mit.edu/ | S / CC BY-NC-SA + AI terms | 讲义、作业、考试、视频和课程结构。 |
| CMU OLI | https://oli.cmu.edu/ | S / page license | Learning-by-doing、即时反馈和学习目标设计。 |
| Stanford Online free courses | https://online.stanford.edu/free-courses | S / course terms | 免费课程目录。 |
| Stanford CS224N | https://web.stanford.edu/class/cs224n/ | S / course terms | AI 课程 schedule 与 assignments。 |
| Harvard CS50x | https://cs50.harvard.edu/x/ | S / course terms | 编程课程和 problem sets。 |
| Berkeley CS61A | https://cs61a.org/ | S / course terms | CS 基础课公开材料。 |
| Open Yale Courses | https://oyc.yale.edu/ | S / page terms | 公开讲座与课程资源。 |
| OpenStax | https://openstax.org/ | A / CC BY-NC-SA 4.0 | 开放教材与章节结构。 |
| OER Commons | https://www.oercommons.org/ | A / per-item license | OER 资源检索与逐项许可识别。 |
| The Open University OpenLearn | https://www.open.edu/openlearn/free-courses | A / per-course terms | 免费短课程与学习单元。 |
| TU Delft OCW | https://ocw.tudelft.nl/ | A / CC BY-NC-SA | 欧洲大学课程材料。 |
| University of Oxford Podcasts | https://podcasts.ox.ac.uk/open | A / CC per-item | 公开讲座和 OER 音频。 |
| Cambridge PACE short online courses | https://www.pace.cam.ac.uk/course-type/short-online-courses | S / enrollment terms | 继续教育课程入口。 |

## 4. 开源课程与技术课程

| 来源 | URL | 初始状态 | 用途 |
|---|---|---|---|
| Microsoft Generative AI for Beginners | https://microsoft.github.io/generative-ai-for-beginners/ | S / repository license | GenAI 基础课程结构和练习。 |
| Microsoft AI for Beginners | https://microsoft.github.io/AI-For-Beginners/ | S / repository license | AI 基础课程和测验样例。 |
| Open edX | https://openedx.org/ | S / open source | LMS/CMS 结构、课程对象和学习活动参照。 |
| LearnHouse | https://github.com/learnhouse/learnhouse | S / repository license | 开源课程平台与 AI 交互元素参照。 |
| Microsoft AI course repository | https://github.com/microsoft/AI-For-Beginners | S / repository license | 课程源码与课件结构。 |

## 5. 平台与搜索扩展

| 来源 | URL | 用途 | 边界 |
|---|---|---|---|
| edX | https://www.edx.org/ | 大学课程聚合发现。 | 不缓存受限课程正文。 |
| Coursera | https://www.coursera.org/ | 大学课程聚合发现。 | 审计权限与平台条款由用户持有。 |
| Class Central | https://www.classcentral.com/ | 课程元数据线索。 | C 级线索，需回链官方来源。 |
| GitHub | https://github.com/ | 课程仓库、讲义、实验和开源项目发现。 | 仓库 license 不覆盖内嵌教材和第三方材料。 |
| arXiv | https://arxiv.org/ | 论文、教程和方法线索。 | 引用与再分发按 license 和页面条款。 |

## 6. 种子源解析规则

1. 每个来源先落 `Source`，记录 `tier`、`access_state`、`license_state`、`robots_policy`、`rate_limit` 和 `last_verified_at`。
2. 课程目录和公开元数据可先索引；正文、视频字幕、习题、答案和试卷必须单独判定。
3. `ACCESS_CONTROLLED` 只返回入口、图书馆说明或用户已授权内容，不保存正文。
4. `UNKNOWN` 只允许出现在人工审核队列，不得进入公共复用池。
5. 搜索结果排序必须优先官方域名，其次 OER，再平台，最后社区线索。
6. 每个导入对象保留原 URL、retrieved_at、license text URL、content hash 和派生关系。
