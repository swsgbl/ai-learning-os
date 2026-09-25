# M14-140：production drift watch 告警任务桥真实子进程运行时闭环（测试切片）

- 切片：分支 `ops/m14-140-drift-watch-alert-task-runtime`（独立 worktree
  `m14-140-drift-watch-alert-task-runtime`，基于 supervisor 本地 sync-base
  `49b67eaa95913da32a5509830e5096b4670fb40e`——其 tree 与 canonical
  remote main `98feae3006ec94bb8ecab21c90fbc9f03eb3c8fd` 完全一致
  （任务书给定；sync-base 本身为 supervisor 运维基座，本切片在其上
  恰好一笔任务 commit），单次 local commit（不推送、不建 PR）。
- 目标：闭合 M14-137 留下的具体诚实边界——任务桥契约测试全部经注入
  FakeRunner，「真实 M14-135 dispatch 子进程移交未实证」。本切片新增
  黑盒运行时集成测试
  `services/api/tests/test_production_drift_watch_alert_task_runtime.py`
  （模板与断言口径对齐 M14-136），**不改动任何工具源码**
  （M14-137 任务桥 / M14-135 dispatcher / M14-127 watcher / M14-129
  scheduler 语义零触碰；测试/docs-only）。
- **边界性质（置顶）：本切片三条路径的期望结局全部是零出站请求——
  仅在本机 ephemeral 回环接收器（127.0.0.1 动态端口、用毕即关）上
  提供「零请求」断言的可观测面，不存在任何真实 HTTP 交换；全程零
  外部端点、零 DNS 主机名、零生产服务/Docker/调度器/DB/MinIO/语音/
  设备/真实 secret 接触，不安装/修改任何计划任务。
  `production_ready=false` 恒不变。**

## 1. 测试设计（3 项，1.6s 量级，纯标准库，可移植）

- 被测面：真实 CLI 子进程（`sys.executable + tools/ops/
  production_drift_watch_alert_task.py`），黑盒调用、不 import 工具
  模块、零 monkeypatch——被测面就是真实 subprocess/网络构造面；
  `PYTHONIOENCODING=utf-8` 确定化中文 stdout 并由 env 原样继承给
  M14-135 孙进程。
- 输入：合成最小自洽 M14-127 execute 模式 drift-watch 报告（older +
  newest 两份定序；文件名 stamp 与 started 交叉校验 [stamp, stamp+2s]
  自洽；drift=true 报告 fail check detail 携带投毒 marker
  `POISON-m14-140-drift-detail-97c2`，断言其绝不出现在任何 stdout
  面）+ 临时 secret JSON（`http://127.0.0.1:<动态端口>/hook` + 合成
  token `tk-m14-140-task-runtime-4b9e`，仅存在于 pytest 临时目录与
  测试内存），全部输入只进 pytest 临时目录（用毕即删，仓库零残留）；
  execute 案例显式 `--dispatch-artifact-dir` 指向临时目录（M14-135
  仓库默认工件目录零触碰）。
- 接收器：`ThreadingHTTPServer` 绑定 `("127.0.0.1", 0)`（动态端口），
  线程 `serve_forever` + `shutdown/server_close/join` 全清理；无法
  绑定回环（受限沙箱）→ 整套 `pytest.skip`，绝不假通过。记录面只留
  方法/路径/字节数（token 与 URL 绝不落任何文件）。

## 2. 实证的运行时行为（此前只有 FakeRunner 契约锁定）

1. **drift=true execute → 真实移交 + 子进程 fail-closed 透传**：
   任务桥以 M14-137 精确 execute/confirm 门运行 → stdout 出现桥侧
   「移交」行；M14-135 dispatch CLI **真实子进程**已运行的运行时证据
   是 `scheme-not-https`——该词汇只存在于 M14-135 移交目标族（经桥
   逐行回显；FakeRunner 注入打不出来）。因 M14-137 白名单刻意不
   转发 `--allow-loopback-http`，子进程对 http webhook fail-closed
   且**零 HTTP 请求**（接收器零记录）；子进程拒绝发生在 URL 校验、
   先于 M14-135 任何目录/工件写入 → dispatch 工件目录不存在（零
   ledger/dispatch 工件）；任务桥退出码 **2**（子进程拒绝原样透传）。
   真实目录扫描如实：newest stem + 测试独立重算的精确 SHA-256 上
   stdout、「候选 2 份」计数；older 报告零痕迹；token / 完整 URL /
   `127.0.0.1` 字面量 / `/hook` 接收器路径 / 绝对本地路径（pytest
   临时目录正反斜杠双形态）/ 投毒 detail 绝不入 stdout/stderr。
2. **drift=true plan**：exit **3** + `dispatch-would-be-required` +
   newest stem + 独立重算精确 SHA-256；接收器零请求；pytest 临时
   目录树前后快照不变（plan 零写入的运行时面）——plan 零网络零
   子进程经结果观测（零请求 + 零新文件）。
3. **drift=false execute**：exit **0** + `skipped-no-alerts` +
   「零 dispatch 子进程」；drift 判定确实只依据最新报告布尔——
   older drift=true 报告的 stem 与结论在 stdout 零痕迹、绝不触发
   移交；接收器零请求；**dispatch 工件目录不存在**（若子进程曾被
   构造，M14-135 即便 skip 路径也会写 dispatch-*.json/.md 并创建
   工件目录——目录不存在即零子进程副作用的运行时证明）；任务桥
   自身零写入（目录树快照不变）。

## 3. 验证（canonical venv，Python 3.11.15）

- 新测试：`3 passed in 1.58s`（本 README §4 命令 1）。
- 聚焦回归（四套合跑，同 venv）：M14-137 契约
  `test_production_drift_watch_alert_task.py`（29）+ M14-136 运行时
  `test_production_drift_watch_alert_dispatch_runtime.py`（4）+
  M14-135 契约 `test_production_drift_watch_alert_dispatch.py`
  （127）+ 本切片新套件（3）→ **163 passed in 4.98s**。
- ruff（默认 + `--select F,E9`）All checks passed / py_compile 通过 /
  `git diff --check` 干净 / 新增行秘密扫描 0 命中（仅有的
  `tk-m14-140-task-runtime-4b9e` 与 `POISON-m14-140-drift-detail-97c2`
  为测试源码内合成 sentinel 常量，仅存在于测试源码与运行期临时
  目录）/ markdown 结构 sanity（fence/标题层级）通过。

## 4. 验证命令清单（supervisor 可复跑）

```bash
cd <worktree>
<venv>/Scripts/python.exe -m pytest services/api/tests/test_production_drift_watch_alert_task_runtime.py -v
<venv>/Scripts/python.exe -m pytest \
  services/api/tests/test_production_drift_watch_alert_task.py \
  services/api/tests/test_production_drift_watch_alert_dispatch_runtime.py \
  services/api/tests/test_production_drift_watch_alert_dispatch.py \
  services/api/tests/test_production_drift_watch_alert_task_runtime.py -q
<venv>/Scripts/python.exe -m ruff check services/api/tests/test_production_drift_watch_alert_task_runtime.py
<venv>/Scripts/python.exe -m ruff check --select F,E9 services/api/tests/test_production_drift_watch_alert_task_runtime.py
<venv>/Scripts/python.exe -m py_compile services/api/tests/test_production_drift_watch_alert_task_runtime.py
git diff --check
```

## 5. 诚实边界（本切片之后仍然成立）

- **本切片不存在任何成功外发**：三条路径的期望结局全部是 fail-closed
  拒绝或跳过（exit 2 / 3 / 0）——「任务桥 → M14-135 子进程 → 真实
  webhook 2xx 送达」的完整闭环**未在本切片实证**（M14-137 白名单
  刻意不转发 `--allow-loopback-http`，故回环联放行路径在任务桥形态
  上不可达——这正是本切片实证的 fail-closed 面，而非缺口）；真实
  HTTPS 外发仍留待 supervisor 获准窗口（M14-135/M14-136 边界维持）。
- 不证明调度集成（drift=true 自动触发分发仍缺位）、不证明告警端到端
  送达、不解除任何 release blocker；不安装/修改任何计划任务；
  `release_ready=false` / `production_ready=false` 恒不变，
  release-approval 仍是 human-only 门。
- 跨进程并发（同报告双桥并发移交）维持 M14-135 台账既有边界，本
  切片不触碰。
