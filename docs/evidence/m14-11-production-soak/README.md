# M14-11 生产 soak/并发彩排 harness（开发回合）— 验收证据归档

- 日期：2026-09-11
- 分支：`feat/m14-11-production-soak`（基于 `main@3634d90`（PR #82 merge
  commit，本地 git 可验证））；流程：Claude 只 commit + push 本分支；
  supervisor 审查 + 独立验证后经 GitHub REST API 创建并合并 PR
  （本地 gh/git 桥损坏）——本 README 不宣称任何 PR 已创建/已合并
- 状态：harness + 聚焦契约测试交付完毕；**有界只读生产 soak 执行明确延后**，
  由 supervisor 在获准窗口运行——本开发回合**未发起任何生产流量**
  （spec 约定：开发回合不得运行负载测试）
- 入库变更：`tools/ops/soak_rehearsal.py`（单文件、纯标准库、零第三方依赖）、
  `services/api/tests/test_soak_rehearsal.py`（60 项契约测试）、本 README，
  以及 PROJECT_STATUS / ROADMAP / CHANGELOG / `tools/ops/README.md` 同步
- 结论：GET-only、unauthenticated、loopback-literal-only、零代理面、
  fail-closed 五要素门禁 + 保守硬顶全部由测试锁定；原始生产结果落
  gitignored `.verify/artifacts/m14-11-production-soak/` 绝不入库；
  **单机生产栈口径，`production_ready=false` 不变**

## 产品形态

```
# plan（默认，零网络请求——打印计划并落 plan 报告）
python tools/ops/soak_rehearsal.py

# execute（五要素齐备才放行，缺一即 EXIT_USAGE 零请求）
python tools/ops/soak_rehearsal.py --execute \
    --confirm "EXECUTE READ-ONLY LOOPBACK SOAK" \
    --duration-seconds 60 --concurrency 4 --max-requests 300
```

- **execute 五要素**（全部必须同时满足）：`--execute` 显式旗标 +
  精确确认短语 `EXECUTE READ-ONLY LOOPBACK SOAK`（一字不差）+ 有界
  duration + 有界 concurrency + 有界总请求上限。
- **保守硬顶（fail-closed，超顶即 EXIT_USAGE 零请求）**：duration
  1–120s；concurrency 1–8；总请求 1–2000；单请求超时 0.5–10s；worker
  节拍 0.05–5s（聚合速率上限 = concurrency/pace）；语音目标最小间隔
  1–60s（默认 2s → 每语音目标 ≤0.5 rps）。
- **固定目标画像**（不可经 CLI 注入任意 URL）：Web `http://127.0.0.1:3011/`
  与 `/login`、API `http://127.0.0.1:8000/health`；`--include-voice` 才加入
  FunASR/CosyVoice 低频 GET `/health`（8010/8011）——绝无音频/模型推理请求；
  `--only` 仅能在已启用集合内筛选。
- **loopback 纪律**：仅接受字面 loopback IP（127.0.0.0/8、`::1`）；
  主机名（含 `localhost`）一律拒绝（零 DNS 解析）；带 query/fragment/
  userinfo/非显式端口的 URL 一律拒绝。HTTP 客户端为 `http.client` 直连
  ——该路径从不读取 proxy 环境变量/系统代理（结构性 loopback 旁路，
  测试以「恶意假代理零连接」实证）；proxy 环境变量仅探测**键名存在性**
  写入报告注记，值绝不读取/记录。
- **报告**：schema 版本化 JSON（`schema_version=1`）+ Markdown 双写至
  gitignored `.verify/artifacts/m14-11-production-soak/`（`plan-*.json/md`
  与 `soak-*.json/md`）。内容含 start/end UTC、限制、每目标
  requests/success/failure/status 计数、latency min/p50/p95/p99/max
  （nearest-rank）、吞吐、安全归类错误（仅异常类别 + 类名，绝无异常
  文本/header/body/query/凭据/env 值）、停止原因（max-requests /
  duration-deadline）、`completed_after_deadline`（deadline 后零新发、
  在途请求限于单请求超时内完成并如实入档）与部分结果。
- **退出码**：0 plan 成功 / execute 完成且有成功样本；1 execute 完成但
  零成功样本（栈疑似未起，可见失败）；2 用法/门禁拒绝（零请求）。

## 本回合验证（2026-09-11，canonical venv Python 3.11.15 / pytest 9.1.1 / ruff 0.16.5）

1. **聚焦契约测试**：`python -m pytest services/api/tests/test_soak_rehearsal.py -q`
   → **60 passed**（3.4s；连跑三遍全绿，无并发抖动）。覆盖：plan 零网络
   （socket 阻断下照常出计划）、execute 门禁 fail-closed（缺旗标/短语不
   精确/任一限制超硬顶——含「五要素齐备但超顶」同样拒绝）、loopback/
   代理行为（校验矩阵 + 恶意假代理零连接实证 + 假服务器 GET-only/仅
   UA+Accept 头）、引擎语义（max-requests 停止、duration deadline 确定性
   轨迹 + 在途越界计数 + 部分结果、语音最小间隔门控、barrier 实证并发
   在途重叠）、统计纯函数（nearest-rank 分位/计数/吞吐）、报告 schema
   （plan/execute 双模式必检键 + UTC 时间戳）、脱敏层（凭据形态标记值
   绝不落入 JSON/Markdown、proxy env 仅键名存在性入档）。
2. **源码契约**：源文件零 `urllib.request`/`urlopen`/`getproxies`/
   `ProxyHandler`/第三方 HTTP 库字面量（代理旁路的结构性保证）；零
   `subprocess`/容器面/调度器命令字面量（本工具零子进程）；仅一处发
   请求点且恒为 GET、仅 `User-Agent`/`Accept` 两个固定头（无
   Cookie/Authorization 面）。
3. **邻居套件**（共享模块零改动，纯干扰排查）：`test_production_recovery.py`
   + `test_windows_startup_task.py` → **111 passed**。
4. **静态**：`ruff check tools/ops/soak_rehearsal.py
   services/api/tests/test_soak_rehearsal.py`、`py_compile`、
   `git diff --check` 全过。

测试中唯一真实 socket 流量：测试自起的 127.0.0.1 ephemeral 假 HTTP
服务器与计数假代理——**零生产端口流量**（3011/8000/8010/8011 全程未触碰）。

## 安全边界（本回合零违背）

- 不启动/停止/重建/构建/拉取/修改任何 Docker 容器；不触碰恢复 env；
  不执行任何计划任务；不触碰 FunASR/CosyVoice 进程状态；不停止代理、
  Docker Desktop、CC Switch、模拟器或任何无关进程。
- 全程零生产网络请求；不入库任何原始生产结果；零密钥/零 env 原文/
  零 token/零 SID 入档（含本 README）；不触碰 untracked `.claude/`。

## 延后项（supervisor 获准窗口）

- 用本 harness 对生产栈执行**有界只读 soak**（建议从保守档起步：
  `--duration-seconds 60 --concurrency 2 --max-requests 120`，观察后再升
  `--concurrency 4 --max-requests 300`）；`--include-voice` 仅在确认语音面
  低频 /health 无影响后加入。执行结果（gitignored `.verify/artifacts/
  m14-11-production-soak/soak-*.json/md`）由 supervisor 裁决后再回填台账。
- 「长稳/并发 soak」生产阻塞项在真实执行 + 结果裁决完成前保持未决。
