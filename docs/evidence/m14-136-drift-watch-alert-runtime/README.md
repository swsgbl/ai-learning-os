# M14-136：production drift watch 告警分发回环运行时闭环（测试切片）

- 切片：分支 `ops/m14-136-drift-watch-alert-runtime`（独立 worktree
  `m14-136-drift-watch-alert-runtime`，基于 main
  `606d8239ad4ff1bdd48d447cbce700dd54b468db`（PR #223 merge =
  M14-135 合入，精确基点）），单次 local commit（不推送、不建 PR）。
  目标：闭合 M14-135 留下的具体诚实边界——契约测试全部经注入
  FakeTransport/FakeClock，「真实 Transport（http.client 直连）的
  运行时行为未实证」。本切片新增黑盒集成测试
  `services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py`
  （模板与断言口径对齐 M14-121 回环运行时闭环），**不改动任何工具
  源码**（M14-135 fail-closed 语义零触碰，测试/docs-only）。
- **边界性质（置顶）：仅在本机 ephemeral 回环接收器上完成真实 HTTP
  往返（127.0.0.1 + 动态端口 + 用毕即关）；全程零外部端点、零 DNS
  主机名、零生产服务/Docker/调度器/DB/MinIO/语音/设备/真实 secret
  接触。`production_ready=false` 恒不变。**

## 1. 测试设计（4 项，2.3s 量级，纯标准库，可移植）

- 接收器：`ThreadingHTTPServer` 绑定 `("127.0.0.1", 0)`（动态端口），
  线程 `serve_forever` + `shutdown/server_close/join` 全清理；无法绑定
  回环（受限沙箱）→ 整套 `pytest.skip`，绝不假通过。头契约（Bearer
  Authorization/Content-Type/Accept/User-Agent/路径/方法）**仅在内存中
  断言**；记录面只留布尔/SHA-256/字节数/解析后的固定词汇 payload——
  token 与 URL 绝不落入任何文件。
- 被测面：真实 CLI 子进程（`sys.executable + tools/ops/
  production_drift_watch_alert_dispatch.py --allow-loopback-http
  --execute --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"`），
  合成最小自洽 M14-127 execute 模式 drift-watch 报告（drift=true 与
  drift=false 各一份；文件名 stamp 与 started 交叉校验 [stamp,
  stamp+2s] 自洽）+ 临时 secret JSON
  （`http://127.0.0.1:<动态端口>/hook`），全部输入/工件只进 pytest
  临时目录（用毕即删，仓库零残留）。
- 投毒面：drift=true 合成报告的 fail check `detail` 携带投毒 marker
  （`POISON-m14-136-drift-detail-c31f`）——接收器断言其绝不上线
  （`poison_in_body=False`），且任何落盘工件/子进程 stdout/stderr 均
  不含该文本（报告校验只看 check_id/subject/status，detail 文本必须
  被固定词汇 payload 边界挡在门外）。

## 2. 实证的运行时行为（此前只有契约锁定）

1. **真实 2xx 交换**：execute 真实走完 `http.client` 直连 → 接收器恰
   收到一次 POST（**零重试**的运行时面）；全部请求头契约在真线上成立
   （POST/`/hook`/`Bearer <token>`/`application/json` 双头/
   `aios-m14-135-drift-watch-alert-dispatch/1.0`）；**线上 body 的
   SHA-256/字节数与工具落盘 dispatch 报告登记的
   `payload.sha256/bytes` 逐字节对账一致**（真实出站的就是登记的
   那份）；源报告 SHA-256 独立重算对账（报告内 `report.sha256`、
   payload `report.sha256`、台账 `report_sha256` 三处一致）；payload
   顶层/报告块键集精确（固定词汇：`schema_version/tool/milestone/
   event=drift-watch-alert/dispatched_at/report` + 报告身份
   stem/sha256/起止 UTC/project/drift/counts/drift_reasons/截断
   计数）。
2. **sanitized 报告/台账**：`drift-dispatch-ledger.jsonl` 恰一行、
   16 键精确、值与接收器实测一致（含 `dispatch_id` 闭式形态
   `<YYYYMMDD-HHMMSS>-<8 位小写 hex>`）；`dispatch-*.json/.md` 恰各
   一份（零 plan 工件）、`dispatch_status=sent`、
   `url_validation=validated-loopback-http`、
   `ledger={"status":"appended","entries":1}`；**token / 完整 URL /
   `127.0.0.1` 字面量 / `/hook` 接收器路径 / 绝对本地路径（pytest
   临时目录正反斜杠双形态）/ 投毒 report detail 绝不出现在任何落盘
   工件与子进程 stdout/stderr**。
3. **幂等重复拒绝**：同报告二次 execute → exit 2 + `duplicate-dispatch`
   + 接收器仍恰一次请求（零重发）+ 台账仍一行 + dispatch 工件零追加
   （拒绝路径零新增 sent-success 工件）。
4. **fail-closed 豁免门（运行时）**：同一回环 URL 不加
   `--allow-loopback-http` → exit 2 + `scheme-not-https` + 接收器零
   请求 + 零工件落盘——http 豁免门在真实 CLI 路径上未被放松。
5. **drift=false 无告警路径**：合法 execute 报告 drift=false →
   exit 0 + stdout 含 `skipped-no-alerts` + 接收器零请求 + 台账文件
   不存在（零触碰）+ dispatch 报告恰一份
   （`dispatch_decision=skip`、`dispatch_status=skipped-no-alerts`、
   `ledger={"status":"not-applicable","entries":null}`）——分发判定
   在真实运行时确实只依据报告自身 drift 布尔。

## 3. 验证（canonical venv，Python 3.11.15）

- 新测试：`4 passed in 2.29s`（本 README §4 命令 1）。
- 既有套件回归（零工具源码改动，纯加法切片）：
  - M14-135 契约 `test_production_drift_watch_alert_dispatch.py`：
    **127 passed**；
  - M14-119/M14-121 邻居（`test_monitoring_alert_dispatch.py` /
    `test_monitoring_alert_dispatch_runtime.py`）：**76 + 3 passed**；
  - M14-127/M14-129/M14-133 邻居（`test_production_drift_watch.py` /
    `test_production_drift_watch_task.py` /
    `test_production_drift_watch_history.py`）：**96 + 82 + 47
    passed**；
  - 七套件合跑：**435 passed in 6.77s**。
- ruff（默认 + `--select F,E9`）All checks passed / py_compile 通过 /
  `git diff --check` 干净 / 新增行秘密扫描 0 命中（仅有的
  `tk-m14-136-loopback-runtime-8e2d` 与 `POISON-m14-136-drift-detail-
  c31f` 为测试源码内合成 sentinel 常量，仅存在于测试源码与运行期
  临时目录）/ markdown 结构 sanity（fence/标题层级）通过。

## 4. 验证命令清单（supervisor 可复跑）

```bash
cd <worktree>
<venv>/Scripts/python.exe -m pytest services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py -v
<venv>/Scripts/python.exe -m pytest services/api/tests/test_production_drift_watch_alert_dispatch.py -q
<venv>/Scripts/python.exe -m pytest services/api/tests/test_monitoring_alert_dispatch.py \
  services/api/tests/test_monitoring_alert_dispatch_runtime.py \
  services/api/tests/test_production_drift_watch.py \
  services/api/tests/test_production_drift_watch_task.py \
  services/api/tests/test_production_drift_watch_history.py \
  services/api/tests/test_production_drift_watch_alert_dispatch.py \
  services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py -q
<venv>/Scripts/python.exe -m ruff check services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py
<venv>/Scripts/python.exe -m ruff check --select F,E9 services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py
<venv>/Scripts/python.exe -m py_compile services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py
git diff --check
```

## 5. 诚实边界（本切片之后仍然成立）

- **真实外部端点外发仍未发生**：回环接收器 ≠ 生产 webhook；TLS 面
  （证书/代理/远端 3xx/限流）运行时行为仍未实证，首次真实外发仍留待
  supervisor 获准窗口（M14-135 边界原样维持）。
- 本切片只实证「真实 HTTP 交换在 M14-135 工具上成立」：不证明调度
  集成（drift=true 自动触发分发仍缺位）、不证明告警端到端闭环、不
  解除任何 release blocker；`release_ready=false` /
  `production_ready=false` 恒不变，release-approval 仍是 human-only 门。
- 接收器断言面在内存中完成；跨进程台账互斥、重试语义（刻意为零）
  维持 M14-135 既有边界，本切片不触碰。
