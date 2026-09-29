# M14-188 Web CI warning hygiene — 交付证据归档

- 日期：2026-09-30（开发切片交付，待 supervisor 合并收口）
- 分支：`ops/m14-188-web-warning-hygiene`（独立 worktree，单个本地
  commit，未 push、未开 PR）。
- 基线：supervisor 核实远端 main merge SHA
  `d3d66d1720c2a5cac63d184546bef077a8a96a31`（tree
  `7941b38a84cbb687814afd1620685f6200011b48`）。本 worktree 因直连
  git fetch 超时，基于 content-equivalent commit
  `a1adb0381ee1afd6c8dfd4dd0a5486e6acea6bee`；开工复核
  `git rev-parse HEAD^{tree}` 输出与上述远端 tree **逐字符一致**，
  即内容基线与合并后 main 精确等同（本地 HEAD 不等于远端 main SHA，
  仅 tree 等同）。
- 背景：合并后 main CI run `36620781095` 5/5 success，但 lint 步骤
  仍输出项目自有 warning。本切片清零其中三处，不动其他范围。

## 三处 warning 与修法

| 位置 | 修改前 | 规则 | 修法 |
| --- | --- | --- | --- |
| `apps/web/src/lib/api.ts:54` | `window.location.href = LOGIN_PATH;` | `@next/next/no-location-assign-relative-destination` | `window.location.href = new URL(LOGIN_PATH, window.location.origin).href;` |
| `apps/web/public/sw.js:81` | `} catch (err) {`（err 未使用） | `@typescript-eslint/no-unused-vars` | `} catch {`（optional catch binding） |
| `apps/web/public/sw.js:129` | `} catch (err) {`（err 未使用） | 同上 | 同上 |

### api.ts 修法依据（规则源码实证，非猜测）

读取 `node_modules/@next/eslint-plugin-next/dist/rules/no-location-assign-relative-destination.js`
确认规则拦截范围：

1. 同时拦 `location.href = <相对>` 与 `location.assign(<相对>)`
   （CallExpression 与 AssignmentExpression 两个 visitor）——「换成
   assign」不是出路。
2. `isRelativeUrl` 仅以 `^(?:[a-z][\d+.a-z-]*:|\/\/)` 判绝对；对
   `LOGIN_PATH` 这类模板字面量派生 const，`getStaticStringPrefix` 取
   其第一个静态 quasi（`${BASE_PATH}/login` 的头 quasi 为空串），
   空串被判相对 → 原写法必被拦，且与 BASE_PATH 实际取值无关。
3. `getStaticStringPrefix` 对 `NewExpression`（如 `new URL(...)`）
   返回 null → 不报告。

因此最小实现是显式绝对化：`new URL(LOGIN_PATH, window.location.origin).href`。
语义逐项保持不变：

- **整页跳转清空客户端状态**：仍是 `window.location.href` 赋值导航
  （未改 `router.push` / `location.replace`，后者的 replace 会改历史
  栈行为，非最小变更）。
- **显式携带 BASE_PATH**：`LOGIN_PATH = ${BASE_PATH}/login` 常量不动，
  绝对化仅解析到当前 origin；浏览器对 root-relative 与绝对 URL 的
  解析结果相同，运行时目标与修改前一致。
- **防登录页自身循环**：`!window.location.pathname.startsWith(LOGIN_PATH)`
  守卫原样保留。
- **无 eslint-disable**：全仓未新增任何 disable 注释（sw-contract
  测试已加 `not.toContain("eslint-disable")` 断言钉住 sw.js）。

### sw.js 修法

仅移除两处未使用的 `err` 绑定（ES2019 optional catch binding，
PWA 目标浏览器均支持）。两处 catch 分支体、install 失败不阻塞、
导航离线回退链（请求缓存 → 壳缓存 → 内嵌兜底页）零改动；catch 内
原有注释保留并标注 M14-188。

## 聚焦测试（先红后绿）

- `apps/web/src/lib/base-path.test.ts` 新增 describe
  「api 401 整页跳转实现（目标 URL 显式携带 BASE_PATH）」，4 用例：
  1. 默认构建 401 → `href` 写为绝对 URL `http://testhost/login`，
     且不经 `location.assign`（锁调用方式）；
  2. `/aios` 构建 401 → `http://testhost/aios/login`（前缀显式、
     无 `/aios/aios` 双重前缀）；
  3. 登录页自身 401 不重定向（防循环守卫回归防护）；
  4. 非 401（500）失败不触发跳转。
  测试以 node 环境 `vi.stubGlobal` 构造 `window`/`fetch` 驱动
  `request()` 的 401 分支。
- `apps/web/src/lib/sw-contract.test.ts` 新增 describe
  「sw.js warning 卫生（M14-188）」，3 用例：源码不再出现带绑定的
  `catch (` 且无 eslint-disable；两处 catch 守卫以 optional binding
  形态存在（数量恰 2）；离线回退链关键源码契约仍在。
- TDD 证据：修复前运行两文件 → 4 个新用例失败（其余 27 通过）；
  修复后 31/31 通过。

## 四项验证（worktree 内实测，2026-09-30）

| 命令 | 结果 |
| --- | --- |
| `npm run test --workspace apps/web` | ✅ 10 files / 130 tests 全通过 |
| `npm run typecheck` | ✅ exit 0，无错误 |
| `npm run lint --workspace apps/web -- --max-warnings=0` | ⚠️ exit 1 —— 三条目标 warning 已清零（见下），但剩余 11 条**范围外既有债务**导致计数无法归零；CI 实际命令 `npm run lint`（无该旗标）exit 0，与 main CI 5/5 行为一致 |
| `npm run build --workspace apps/web` | ✅ exit 0：Compiled successfully、TypeScript 通过、11/11 静态页生成，构建输出零 warning |

### warning 消失证据（lint 前后对比）

- 修改前基线（同 worktree、依赖同一 lockfile 安装）：
  `npx eslint .` → `✖ 14 problems (0 errors, 14 warnings)`，含
  - `src/lib/api.ts 54:7 … no-location-assign-relative-destination`
  - `public/sw.js 81:16 / 129:12 'err' is defined but never used`
- 修改后：`✖ 11 problems (0 errors, 11 warnings)`；
  `npx eslint . | grep -cE "src.lib.api\.ts|public.sw\.js"` → **0**
  （两个目标文件在 lint 输出中出现次数为零）。
- 剩余 11 条全部为 react-hooks 家族（set-state-in-effect / refs /
  exhaustive-deps），即 `apps/web/eslint.config.mjs` 中 M14-05 为
  Next 16 升级**显式降级为 warn** 的技术债（配置注释自述
  "follow-up debt: migrate those call sites to the v7 guidance"，
  涉及约 10 个业务组件文件），在本切片「tracked 改动仅限三处修复 +
  聚焦测试 + 本 evidence」的边界内不动；如强行清零需大规模业务
  重构，风险与收益不匹配，留待专项切片。本切片未新增任何 warning、
  未弱化任何 lint/测试配置。

### 依赖与 lockfile

`npm ci`（registry 走用户级 npmmirror 镜像）严格按 tracked
`package-lock.json` 安装，安装前后 `git status --porcelain` 均无
lockfile 差异 —— **零 lockfile 变更**。

## 入库变更清单

- `apps/web/src/lib/api.ts`（+6/−1）
- `apps/web/public/sw.js`（+3/−3）
- `apps/web/src/lib/base-path.test.ts`（+85/−0）
- `apps/web/src/lib/sw-contract.test.ts`（+24/−1）
- `docs/evidence/m14-188-web-warning-hygiene/README.md`（本文件，+130）

合计 5 文件，+248/−5（`git diff --numstat HEAD^ HEAD`；修正记录：初稿 +118/−5 漏计本 README 的 130 行，经 supervisor 复审纠正）。未触碰 `docs/PROJECT_STATUS.md` /
`docs/ROADMAP.md` / `docs/CHANGELOG.md`（避免与并行 M14-187H 冲突，
台账由 supervisor 合并时统一收口）。

## 边界声明

- 零生产触碰：无部署、无 Docker、无 API/DB/MinIO/语音/VPS/Nginx/frp
  操作；无网络请求（依赖安装走包镜像除外）。
- 未输出任何 key/token/secret。
- 单本地 commit，未 push、未开 PR。
