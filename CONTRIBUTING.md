# 贡献指南（Contributing Guide)

感谢你考虑为 AI Learning OS 贡献！先花三分钟了解本项目的节奏。

## 找到合适的事做

- 带 `good first issue` 标签的 issue 是入门好起点
- 带 `bug` / `feature` 标签的按自己能力认领（评论认领，两周未动释出）
- 文档与测试改进永远欢迎，无需事先认领

## 开发环境

```powershell
git clone https://github.com/swsgbl/ai-learning-os.git
cd ai-learning-os

# 服务端（Python 3.11+，uv 或 venv 均可）
cd services/api && pip install -r requirements.txt && pytest

# Web（Node 20+，仓库根）
npm install && npm run typecheck && npm run lint && npm run build

# Android：Android Studio 打开 apps/android
# HarmonyOS：DevEco Studio 打开 apps/harmony
```

## 提交规范

- 分支从 `origin/main` 切出：`feat/<topic>` / `fix/<topic>` / `docs/<topic>`
- Commit 格式 `<type>(<scope>): <subject>`，type 取
  `feat / fix / refactor / docs / test / chore`
- **全部通过 CI 门禁才可合并**：
  - API：ruff + pytest + migration
  - Web：typecheck + lint + build + test
  - Android：unit test + lint + assemble
  - Docker：compose build + healthy + smoke
- 提交前在仓库根自查：
  ```powershell
  npm run typecheck; npm run lint; npm run build
  pytest
  .venv\Scripts\python.exe -m ruff check services/api/app services/api/tests
  ```

## 本项目的硬性架构红线（PR 评审必查）

1. **服务端权威**：Web/客户端只渲染状态与提交意图——考试时间、判分、
   正确答案、掌握度真相绝不落客户端（见 `AGENTS.md`）。
2. **答案隔离**：任何作答类接口的题目投影**不得包含 answer 字段**；
   判分只发生在提交之后。
3. **密钥零泄漏**：token / API 密钥零持久化、零日志；回显只允许后 4 位。
4. **数据库边界**：集成测试只允许连 `ai_learning_os_test` 隔离库，
   绝不写共享主库 `ai_learning_os`。
5. **新脚本带 shebang 必须** `git update-index --chmod=+x`（Windows 下
   极易漏，CI 会拦）。

## 验收证据文化

行为类变更（新功能 / 修复）请在 PR 中附**可复现证据**，惯例是
`.verify/<topic>/` 目录（该目录被 gitignore，用 `git add -f` 白名单
提交）：探针输出、layout dump、测试记录。服务端契约变更加一条
**用生产形状 payload 做的契约测试**——历史教训：按记忆写契约曾造成
整轮返工，只有真实 payload 可信。

## AI 协作开发的署名

本项目大量采用「AI 执行 + 人类监督验收」的开发流（含 hmharness
棒式分工）。AI 参与产出的 PR 按 GitHub 默认署名规范标注
`Co-Authored-By`；验收结论与证据由人类或监督方复核后合并。

## 报告问题

- 缺陷：[issue](https://github.com/swsgbl/ai-learning-os/issues)，
  附版本号、部署形态、复现步骤
- 安全问题：**不要开公开 issue**，走
  [SECURITY.md](SECURITY.md) 的私密通道

## 许可证

提交即表示你同意贡献内容按 [LICENSE](LICENSE) 与 [NOTICE](NOTICE.md)
（含 `services/api` 的 AGPL-3.0 组合义务）授权。
