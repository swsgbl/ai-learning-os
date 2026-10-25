# 支持与求助（Support）

按问题类型选择正确渠道，能得到最快响应：

| 你遇到的问题 | 渠道 |
|---|---|
| 疑似缺陷（有复现步骤） | [提交 issue](https://github.com/swsgbl/ai-learning-os/issues)（用 Bug 模板） |
| 使用/部署疑问 | [GitHub Discussions](https://github.com/swsgbl/ai-learning-os/discussions)（Q&A 分类） |
| 功能建议 | issue（用 Feature 模板）或 Discussions（Ideas 分类）先讨论再立项 |
| 安全漏洞 | **私密通道**，见 [SECURITY.md](SECURITY.md)——勿公开 issue |
| 行为准则问题 | 见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 举报节 |

## 自助排查顺序

1. **部署问题**：先读 [README](README.md) 的「生产本地版（一条命令）」
   与「版本与回滚」节——多数环境问题（端口占用/代理/数据卷）有现成配方；
2. **版本问题**：确认你在最新 Release 上（Releases 页）；
3. **考试/判分/掌握度行为疑问**：这些是**服务端权威**设计——先看
   `docs/CHANGELOG.md` 对应条目确认是否为已知变更；
4. **语音引擎**：本地 funasr/CosyVoice 冷启动首次请求可能超时（约
   50s），重试一次再报告。

## 响应预期

- issue：7 个工作日内首响应
- 安全报告：7 个工作日内（按 SECURITY.md 承诺）
- Discussions：尽力而为，社区互助优先

## 商业支持

暂无官方商业支持；自托管部署咨询可在 Discussions 标注
`consulting`，社区可见范围内互助。
