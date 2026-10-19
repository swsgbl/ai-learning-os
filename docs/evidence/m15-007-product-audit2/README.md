# M15-007 证据：产品审计二批（真机实测反馈）+ 全面对账

- 基点 current main（#387 后）。PR **#388**（四项 UX 整改）+
  **#389**（v0.1.1 版本号）；tag `v0.1.1` 经 M15-006 发布流水线
  出包（用户手机 app 内「检查更新」可见新版本——闭环验证）。
- **M15-010 后续（#393 / tag v0.1.3）**：真机 instrumented test 实锤
  「记住密码」自 M15-007 引入即全失效——`KeystoreCredentialVault` 加密
  时预生成 IV 传入 `init()`，AndroidKeyStore 抛
  `InvalidAlgorithmParameterException: Caller-provided IV not permitted`，
  被上层 `catch (_: Throwable)` 吞掉 → 密文静默不落盘（JVM 单测走
  FakeVault 覆盖不到 Keystore 路径，360 tests 全绿掩盖）。修复对齐
  `KeystoreTokenStore`（加密不传 IV 由 Keystore 生成、解密才传 caller
  IV）；新增真机回归 `CredentialVaultDiagTest`（修复前 FAILED 给出确切
  异常栈，修复后 PASS）。真机全链验证：登录落盘 61B（run-as 实证）→
  冷启动登录态保持 → 退出重登预填 → 预填一键登录成功。v0.1.2 安装
  冒烟中另实证 consumeSuccess 修复（退出重进登录页稳定打开）与
  token 冷启动恢复正常。

## 一、用户实测反馈 → 本批整改（PR #388）

| # | 用户反馈（2026-10-18 真机 v0.1.0） | 交付 |
| --- | --- | --- |
| 1 | API 设置地址等菜单不该在首页 | 首页技术卡（当前用户/API 状态/治理入口）全部收纳进设置页；首页 = 登录引导/上手引导/学习入口 |
| 2 | 注册完应有记住密码 | 登录页「记住密码」勾选（默认开）→ CredentialVault（Keystore AES-GCM 加密）→ 预填；取消即抹除 |
| 3 | 用户使用指引没有 | 移动端「三步上手」引导卡（定制课程→开练→错因报告，可关闭记忆） |
| 4 | 学习菜单不应预设算法复杂度/函数与导数 | 服务端 `PaperOut.mine` + 学习页「我的课程」分组为主（materialize 定制卷）；公共试卷默认收起；生产种子卷随 v0.1.1 部署清理 |
| 5 | API 密钥在哪里填 | **下批 M15-008**（方案已定：治理中心「AI 能力与密钥」页——见下） |

验证：服务端 mine 契约测试（materialize 闭环 + 双用户交叉）+
Android **360 tests / 0 failures** + lintDebug 全过。

## 二、全面对账（开发计划 ↔ 实现 ↔ 历次用户要求）

### M14 语音链（全部已实现并生产部署）

| 计划 | 状态 |
| --- | --- |
| M14-267~272 服务端权威语音管线（订阅/帧管道/流式 ASR/VAD/打断） | ✅ #354~#370，LiveKit+FunASR 真实 E2E |
| M14-273 生产切换 | ✅ 生产镜像 + monitor + recovery |
| M14-274~276 Web live 接入 + pump + Android 真机语音 | ✅ |
| M14-277 鸿蒙语音作答 | ✅ #369（hmharness 实现 + supervisor 验收） |

### M15 产品化（审计驱动的三批）

| 批次 | 要求 | 状态 |
| --- | --- | --- |
| 一批（M15-001~004，#371~#381） | 注册（三端）/首页搜索/定制课程入口+成卷/Web 三步引导/首启连接引导 | ✅ 全部合并 + 真机验证收口 |
| 发布渠道（M15-006，#382~#387） | 可下载 + 应用内更新检查 | ✅ v0.1.0 已发（GitHub Releases pre-release）+ 生产 latest-app 端点透出 |
| **二批（M15-007，#388/#389）** | 首页信息架构/记住密码/我的课程/上手引导 | ✅ 本批 |
| 二批遗留 | **API 密钥配置入口**（用户两次提出） | ⏳ M15-008（见下） |
| 二批遗留 | Harmony 端对本批四项对齐 | ⏳ M15-009 |
| 二批遗留 | 真机 v0.1.1 安装冒烟（发布时手机状态待确认） | ⏳ 发布后补 |

### ROADMAP 已知长期缺口（既有记录，非本批引入）

- M1-04 文档解析 adapter（MinerU/Marker/olmOCR 未实现，Docling 框架在）
- M2 Redis timeout worker（懒翻转替代，语义达标注明）
- M5-03 真实抓取管线（预检门已实现无消费者）
- M4 缺口 3 收窄（Web 契约层已备；vad/llm/first_audio 无 Web 埋点）
- M13 Harmony AGC 签名/发布材料（工具链就绪，材料缺位）
- coding 题判分恒 False（M2 已知缺口）

## 三、API 密钥配置方案（M15-008 计划，回应用户两次提出）

**定位**：AI 密钥（LLM/云 ASR/云 TTS）是**服务端部署配置**，
非学习者功能；入口做在 Web 治理中心（admin-only）：

1. `GET /api/v1/admin/ai-providers`（admin）：三槽位状态
   （已配置/未配置 + env 变量名 + 密钥尾 4 位脱敏）；
2. `PUT /api/v1/admin/ai-providers/{slot}`（admin）：在线填写
   密钥 → **加密落库**（AUTH_SECRET 派生密钥 AES-GCM）→ 审计
   记录 → providers 装配优先读 DB（回退 env，热生效免重启）；
3. Web 治理中心新增「AI 能力」页（状态 + 填写 + 指引）；
4. 安全边界：密钥永不回显全文；写入走 admin 门禁 + audit；
   删除 = 置空回退 env。

工程量：服务端加密存储 + providers 装配改造 + Web 页 ≈ 一个
中型切片。本批（#388）优先交付四项核心 UX，密钥页紧随其后。

## 非声明

- 本批 Android 为主（用户真机在用）；Harmony 对齐列入 M15-009。
- 生产种子卷（函数与导数基础/算法复杂度入门）清理随 v0.1.1
  部署执行（运维 SQL，删除公共种子卷——用户明确不想要预设）。
- 三 ready 标志均 false 不变；v0.1.1 仍 pre-release。
