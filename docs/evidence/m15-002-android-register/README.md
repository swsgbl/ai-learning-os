# M15-002 证据：Android 注册功能（登录页双模式）

- 分支 `feat/m15-002-android-register-onboarding`，基于 current
  main `ae6673c5`（M15-001 后）。PR #374 CI 5/5 合并
  （merge `842692a`）。

## 交付面

- **`AiosApi.kt`**：`@POST("api/v1/auth/register")` +
  `RegisterRequest`（kotlinx.serialization）。
- **`AuthRepository.kt`**：`register(username, password)`——注册
  201 后立即 `login` 建会话（服务端 `/register` 只建账号返回
  UserOut，会话令牌仍走 `/login`——与 Web 端行为一致）。
- **`LoginViewModel.kt`**：`LoginMode.LOGIN/REGISTER` 双模式——
  `onModeChange` 切换（清错误保留表单）；提交分流；注册 409
  `CONFLICT` → 「用户名已被注册，换一个或切换到登录」；登录 401
  统一文案语义不变；预校验 `validateCredentials` 两模式共用
  （服务端 M9-01 同口径）。
- **`LoginScreen.kt`**：登录/注册 FilterChip + 模式化标题/说明
  （注册模式显示「第一次使用？创建账号后即可定制课程并开始练习」）
  + 动态提交按钮（登录 / 注册并登录）。

## 验证

- 3 项新单测（`LoginViewModelRegisterTest`）：注册成功调
  register 且清表单 / 409 显示 USERNAME_TAKEN 文案 / 默认模式
  仍走 login 不误触 register。
- **全量 350 tests / 0 failures**（Gradle testDebugUnitTest）。
- 过程修复的三处测试基建（记录供同类切片参考）：插入 DTO 时
  `LoginRequest` 的 `@Serializable` 被挤掉（SerializationException）；
  `GatedAuthGateway` 测试替身缺新接口方法；新测试类缺
  `MainDispatcherRule`（Main dispatcher 未初始化）。

## 附：Harmony 注册（M15-003，PR #376）模拟器验证结果

（supervisor 于手机模拟器 127.0.0.1:5555 实测，2026-10-17；本
批原「Harmony 注册待做」项已由 PR #376 交付并完成 UI 级实证）

- 构建 hap 安装 → 启动正常；baseUrl 保存（「已保存」toast）且
  持久化生效（重启后仍指向验收 API）；**设备请求真实到达验收
  API（服务端日志 auth/status 来自模拟器源）**；auth_enabled=true
  → 登录表单渲染。
- **注册 UI 全要素在模拟器实证**：登录/注册双 Toggle 渲染且可
  切换；注册模式引导文案「第一次使用？创建账号后即可定制课程
  并开始练习。」显示；提交按钮动态变「注册并登录」。
- 最终提交回环未完成（诚实记录）：模拟器 IME（拼音键盘）拦截
  uitest inputText 注入、keyEvent Backspace 不生效——文本无法
  可靠填入；且注册端点需 PG（Docker 宕机中，由另一智能体处理，
  按边界不干预）。**端点权威验证由 CI 全量 pytest（含 auth 注册
  测试，PR #376 API job 5/5）覆盖**；postRegister 为 postLogin
  模式复刻（登录路径已在 M14-89 设备验证）。

## 非声明（更新）

- 首启连接引导（代替裸报「无法连接 API」）与设置分区属下一批
  剩余项（Harmony 注册已由 M15-003 交付）。
