# M14-05 Web 生产依赖安全漏洞修复 — 验收证据归档

- 日期：2026-09-10
- 分支：`fix/m14-05-web-next-security`（基于 `origin/main@984539e2d2481f84695c6dd4e1020bec93abd245`，本地 git 可验证）
- 状态：本地实现与验证完成；本 README 记录提交前本地验收快照，远端 PR/CI/合并状态以后续 PROJECT_STATUS 回填为准
- 原始证据路径：`.verify/m14-05-web-security/`（gitignored，不入库）
- 入库证据：本 README（唯一入库文件）
- 结论：`npm audit --omit=dev --registry=https://registry.npmjs.org` 由 1 moderate + 1 high 修复为 **0 vulnerabilities**；lint/typecheck/build 全部 exit 0；业务组件代码零改动

## 漏洞与根因

| 项 | 内容 |
|---|---|
| 基线 | `npm audit --omit=dev --json --registry=https://registry.npmjs.org` exit 1：`{"moderate":1,"high":1,"total":2}` |
| moderate | `next@15.5.24`（范围 9.3.4-canary.0 – 16.3.0-preview.10；fixAvailable `next@16.3.4`，isSemVerMajor） |
| high | next 内嵌生产依赖 `node_modules/next/node_modules/postcss@8.4.31`（≤8.5.22）：GHSA-qx2v-qp2m-jg93（XSS）、GHSA-6g55-p6wh-862q（sourceMappingURL 任意文件读取）、GHSA-fxqj-rqcc-2cmp（修复不完全）、GHSA-r28c-9q8g-f849（source map 路径穿越） |
| spec 与 lock 差异 | `apps/web/package.json` 声明 `next: "^15.5.8"`（范围），lock 解析 `15.5.24`（上次 install 时满足范围的最新 15.x）——npm 设计行为；next@15.5.24 **精确 pin** `postcss: "8.4.31"`，15.x 内无法重解析升级嵌套 postcss，正确修复即升级 next；顶层 `postcss@8.5.26` 为 dev-only（@tailwindcss/postcss），非告警来源 |
| 禁止项核实 | 无 overrides/resolutions、无 ignore-scripts、无手工篡改 lock 版本字符串；升级经 npm 官方 registry 完成 |

## 生产变更摘要

| 文件 | 变更 |
|---|---|
| `apps/web/package.json` | `next ^15.5.8 → ^16.3.4`；`eslint-config-next ^15.5.8 → ^16.3.4`；移除仅被 FlatCompat 使用的 `@eslint/eslintrc ^3.3.3` |
| `package-lock.json` | npm 重解析生成：next/@next/* 15.5.24→16.3.4、嵌套 postcss 8.4.31→8.5.23、eslint-config-next 16 树（typescript-eslint 8.70、react-hooks 7.1.1、+38 传递依赖、−@rushstack/eslint-patch）；**react/tailwind/radix/lucide 等无关包零变动**（对升级前 lock 逐包 diff 验证） |
| `apps/web/eslint.config.mjs` | eslint-config-next 16 导出原生 flat config 数组，legacy FlatCompat 加载报循环引用配置错误；改为直接展开 `eslint-config-next/core-web-vitals` + `eslint-config-next/typescript`；react-hooks v7 新诊断（`set-state-in-effect` ×9 / `refs` ×1）降为 warn 而非重构 9 个存量组件（注释标注后续技术债），核心 hooks 规则维持原 severity |
| `apps/web/tsconfig.json` | Next 16 构建期强制迁移 `jsx: preserve → react-jsx` + include `.next/dev/types/**`（Next 自动重写） |
| `apps/web/next-env.d.ts` | Next 16 自动改为 import 形式引用 routes/root-params 类型 |
| `docs/DEVELOPMENT.md` | 本地验证清单加入生产依赖 audit 门禁命令；release-check 章节补充依赖安全门禁定位说明 |
| `docs/CHANGELOG.md` | [Unreleased] 记录 M14-05 |

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| 生产 audit | 升级后 `npm audit --omit=dev --json --registry=https://registry.npmjs.org` exit 0，`{"info":0,"low":0,"moderate":0,"high":0,"critical":0,"total":0}`；含 dev 的全量 audit 同为 0 |
| 安装 | Node 22 自带 npm 10.8.2（与 CI runner 同构）+ `--registry=https://registry.npmjs.org`：374 packages，`found 0 vulnerabilities`。本机 PATH npm 12.0.2 对镜像 resolved URL + 官方 registry 组合触发 EALLOWREMOTE（npm/cli#9548），故绕过 mise shim 直用 npm 10，未使用 `--allow-remote`、未改 .npmrc/CI |
| lint | `npm run lint` exit 0：0 errors / 11 warnings（react-hooks v7 新诊断 ×10 + `@next/next/no-location-assign-relative-destination` ×1，均为存量模式告警） |
| typecheck | `npm run typecheck` exit 0（tsc --noEmit） |
| build | `npm run build` exit 0（Turbopack）：9 静态 + 3 动态路由全部产出；`output: "standalone"` 产物 `.next/standalone/`（gitignored 不入库） |
| 业务代码 | `src/` 零改动（兼容性调整仅限配置文件：eslint.config.mjs / tsconfig.json / next-env.d.ts） |

## 剩余风险与后续事项

1. **react-hooks v7 新诊断技术债**：9 个组件的 effect 内同步 setState 与 GSAP contextSafe 闭包模式降级为 warn 通过门禁，后续应迁移至 v7 指引（渲染期派生 / 事件处理器内 setState）。
2. `src/lib/api.ts` 的 `window.location.href` 内部导航 warning：涉及 401 跳登录语义，本次未改动。
3. lock 内 363 个存量 `resolved` URL 仍指向 registry.npmmirror.com（本机 .npmrc 默认镜像历史所致，sha512 完整性校验不受影响）；后续可择机以官方 registry 全量重生成统一。CI `npm ci`（npm 10）不受影响。
4. 本机 npm 12.0.2 的 EALLOWREMOTE 限制仍在（环境问题，非仓库问题）：本仓库 mixed-registry lock 在 npm 12 + 显式官方 registry 组合下需 npm 10 或 `--allow-remote`。

## 原始证据清单

以下文件位于 `.verify/m14-05-web-security/`，均不入库：`README.md`（完整记录）、
`audit-baseline.json`、`audit-baseline.stderr`、`audit-after.json`、`audit-final.json`、
`npm-install.log`、`npm-lint.log`、`npm-typecheck.log`、`npm-build.log`。
均不含 token/key/secret/password。

## 基于 main@cae7aa0 的复验（2026-09-11，PR #76 rebase 后）

- **Rebase**：原单提交 `5e47fd2`（基于 984539e）rebase 到 `origin/main@cae7aa0`
  （PR #80 merge，含 M14-08 台账回填）→ 新 head 单提交，parents=`cae7aa0`，
  **零冲突**（8 文件中仅 `docs/DEVELOPMENT.md` 与 main 后续改动同文件，不同
  区域自动合并）；`git diff origin/main...HEAD` 仍恰为原 8 文件 +816/−290，
  `docs/PROJECT_STATUS.md`/`docs/ROADMAP.md` 相对 main **零差异**（M14-08
  回填内容完整保留，本地 git 可验证）。
- **门禁复验（真实命令与退出码，2026-09-11，本 worktree，Node v22.23.2 /
  npm 12.0.2，官方 registry 显式指定，代理仅经环境变量 HTTPS_PROXY/HTTP_PROXY）**：

| 命令 | 结果 |
|---|---|
| `npm audit --omit=dev --registry=https://registry.npmjs.org` | `found 0 vulnerabilities`，**exit 0** |
| `npm install --registry=https://registry.npmjs.org` | **exit 0**；`git status package-lock.json package.json` 零漂移（lock 未动）；npm 提示 `unrs-resolver@1.12.2` postinstall 被 allowScripts 策略阻止（信息性，非失败） |
| `npm run lint` | **exit 0**：0 errors / 11 warnings（与首轮相同的存量告警口径：react-hooks v7 新诊断 + 1 条相对跳转 warning） |
| `npm run typecheck` | **exit 0**（tsc --noEmit） |
| `npm run build` | **exit 0**：Next.js **16.3.4（Turbopack）** 编译 20.2s、TypeScript 通过、9/9 静态页生成；路由清单 11 条（静态 8：`/`、`/_not-found`、`/exam`、`/governance`、`/library`、`/login`、`/progress`、`/voice`；动态 3：`/exam/[paperId]`、`/review/[examId]`、`/voice/[paperId]`） |
| Web 测试脚本 | `apps/web/package.json` **无 test 脚本**（仅 dev/build/lint/typecheck）；根 `npm test`=`pytest services/api` 属 API 门禁，不在本轮 Web 门禁范围 |

- **lock 关键版本复核（node 读 package-lock.json 实测）**：
  `node_modules/next@16.3.4`、`node_modules/eslint-config-next@16.3.4`、
  嵌套 `node_modules/next/node_modules/postcss@8.5.23`（升级目标位）；顶层
  `postcss@8.5.26` 为 dev-only（@tailwindcss/postcss）非告警来源，与首轮口径一致。
- **standalone/Docker 兼容**：`apps/web/next.config.ts` `output:"standalone"` 不变；
  构建实际产出 `apps/web/.next/standalone/apps/web/server.js`（约 21MB），
  与 `apps/web/Dockerfile` 的 `COPY .next/standalone ./` + `CMD ["node",
  "apps/web/server.js"]` 路径形态吻合（`npm ci` + workspace 构建链路未改）。
- **改动面复核**：`apps/web/app|components|lib` 业务代码相对 main **零改动**；
  `apps/web/package.json` 差异仅 next/eslint-config-next 升级与移除
  `@eslint/eslintrc`（flat config 迁移），无无关依赖。
- **CI 口径**：PR #76 的 GitHub Actions 为外部基础设施/billing 形态失败
  （约 4–5 秒、5 job、0 step——与 PR #78/#79/#80 同期同形态，run id 见
  PROJECT_STATUS M14-07/M14-08 条目口径），**非代码失败**；本 README 不以
  本地通过掩盖 CI 未绿，也不虚构 CI 结果。
