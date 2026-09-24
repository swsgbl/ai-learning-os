# M14-121：监控告警分发回环运行时闭环（测试切片）

- 切片：分支 `ops/m14-121-alert-dispatch-loopback-runtime`（独立 worktree
  `m14-121-alert-dispatch-loopback-runtime`，基于 main `2b1c2dd4`（PR #206
  merge = M14-119 合入，精确基点）），单次 local commit（不推送、不建 PR）。
- 目标：闭合 M14-119 留下的具体诚实边界——契约测试全部经注入
  FakeTransport，「真实 Transport（http.client 直连）的运行时行为未实证」。
  本切片新增黑盒集成测试
  `services/api/tests/test_monitoring_alert_dispatch_runtime.py`，**不改动
  任何工具源码**（M14-119 fail-closed 语义零触碰）。
- **边界性质（置顶）：仅在本机 ephemeral 回环接收器上完成真实 HTTP 往返
  （127.0.0.1 + 动态端口 + 用毕即关）；全程零外部端点、零 DNS 主机名、
  零生产服务/容器/DB/secret 接触。`production_ready=false` 恒不变。**

## 1. 测试设计（3 项，1.8s 量级，纯标准库，可移植）

- 接收器：`ThreadingHTTPServer` 绑定 `("127.0.0.1", 0)`（动态端口），
  线程 `serve_forever` + `shutdown/server_close/join` 全清理；无法绑定
  回环（受限沙箱）→ 整套 `pytest.skip`，绝不假通过。头契约（Bearer
  Authorization/Content-Type/Accept/User-Agent/路径/方法）**仅在内存中
  断言**；记录面只留布尔/SHA-256/字节数/解析后的固定词汇 payload——
  token 与 URL 绝不落入任何文件。
- 被测面：真实 CLI 子进程（`sys.executable + tools/ops/
  monitoring_alert_dispatch.py --allow-loopback-http --execute --confirm
  "EXECUTE MONITOR ALERT DISPATCH"`），合成最小自洽 monitor 报告
  （warn ×1）+ 临时 secret JSON（`http://127.0.0.1:<动态端口>/hook`），
  全部输入/工件只进 pytest 临时目录（用毕即删，仓库零残留）。

## 2. 实证的运行时行为（此前只有契约锁定）

1. **真实 2xx 交换**：execute 真实走完 `http.client` 直连 → 接收器恰
   收到一次 POST（零重试的运行时面）；全部请求头契约在真线上成立；
   **线上 body 的 SHA-256/字节数与工具落盘 dispatch 报告登记的
   `payload.sha256/bytes` 逐字节对账一致**（真实出站的就是登记的那份）；
   报告 SHA-256 独立重算对账；payload 顶层/报告块键集精确（固定词汇）。
2. **sanitized 报告/台账**：`dispatch-ledger.jsonl` 恰一行、15 键精确、
   值与接收器实测一致；`dispatch-*.json/.md` 恰各一份、
   `dispatch_status=sent`、`url_validation=validated-loopback-http`；
   token / 完整 URL / `127.0.0.1` 字面量 / `/hook` 路径**绝不出现在任何
   落盘工件与子进程 stdout/stderr**。
3. **幂等重复拒绝**：同报告二次 execute → exit 2 + `duplicate-dispatch`
   + 接收器仍恰一次请求（零重发）+ 台账仍一行 + 工件零追加。
4. **fail-closed 豁免门（运行时）**：同一回环 URL 不加
   `--allow-loopback-http` → exit 2 + `scheme-not-https` + 接收器零请求 +
   零工件——http 豁免门在真实 CLI 路径上未被放松。

## 3. 验证

- 新测试：`python -m pytest
  services/api/tests/test_monitoring_alert_dispatch_runtime.py` →
  **3 passed**。
- 既有套件回归：`test_monitoring_alert_dispatch.py`（76）+ 监控家族
  8 套件全绿（见 §4 命令）——零工具源码改动，纯加法切片。
- ruff（默认 + F,E9）/ py_compile / `git diff --check` / 新增行秘密扫描
  （唯一命中为测试内合成 token 常量 `tk-m14-121-loopback-runtime-*`，
  仅存在于测试源码与运行期临时目录）全净。

## 4. 验证命令清单（supervisor 可复跑）

```bash
cd <worktree>
<venv>/Scripts/python.exe -m pytest services/api/tests/test_monitoring_alert_dispatch_runtime.py -v
<venv>/Scripts/python.exe -m pytest services/api/tests/test_monitoring_alert_dispatch.py -q
<venv>/Scripts/python.exe -m pytest services/api/tests/test_production_monitor.py \
  services/api/tests/test_monitoring_history.py \
  services/api/tests/test_monitoring_insights.py \
  services/api/tests/test_monitoring_pipeline.py \
  services/api/tests/test_monitoring_pipeline_task.py \
  services/api/tests/test_monitoring_history_query.py \
  services/api/tests/test_monitoring_threshold_calibration.py \
  services/api/tests/test_monitoring_alert_dispatch.py \
  services/api/tests/test_monitoring_alert_dispatch_runtime.py -q
<venv>/Scripts/python.exe -m ruff check services/api/tests/test_monitoring_alert_dispatch_runtime.py
<venv>/Scripts/python.exe -m ruff check --select F,E9 services/api/tests/test_monitoring_alert_dispatch_runtime.py
<venv>/Scripts/python.exe -m py_compile services/api/tests/test_monitoring_alert_dispatch_runtime.py
git diff --check
```

## 5. 诚实边界（本切片之后仍然成立）

- **真实外部端点外发仍未发生**：回环 ≠ 生产 webhook；TLS 面（证书/代理/
  远端 3xx/限流）运行时行为仍未实证，首次真实外发仍留待 supervisor
  获准窗口。
- DNS 主机名解析 pin、跨进程台账互斥、scheduler/SMTP/Alertmanager 集成、
  重试语义（刻意为零）——均维持 M14-119 的既有边界，本切片不触碰。
- 不解除 provider-smoke/long-soak/任何 release blocker；
  `release_ready=false` / `production_ready=false` 恒不变。
