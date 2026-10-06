# M14-243: CI runner/action 稳定性钉定（ubuntu-24.04 + wrapper-validation v6）

## 结论与边界

- 切片：worktree
  `ai-learning-os-worktrees/m14-243-ci-runner-pin`，分支
  `ops/m14-243-ci-runner-pin`，基于 current main
  `4013807f23af41f3b24b45149b12b7948699fca3`（PR #329 merge）。单本地
  commit，不 push、不开 PR、不合并。
- 动因：GitHub 已公告 ubuntu-latest 于 2026-10-19 起向 Ubuntu 26.04
  迁移（actions/runner-images #14748 与 GitHub Blog 2026-09-17 Ubuntu
  26 GA / latest 迁移公告），且 gradle/actions/wrapper-validation@v4
  为旧 node20 runtime 主版本——两处平台漂移面都可能静默破坏 CI。
- 变更：ci.yml 五个 job（web/api/docker/android/release-tools）与
  release-candidate.yml 单 job 的 `runs-on` 全部由 ubuntu-latest 钉定
  为 ubuntu-24.04（共 6 处）；android job 的 wrapper-validation 由
  @v4 升 @v6。
- 契约测试：test_workflow_actions_runtime.py 新增
  test_all_jobs_pin_ubuntu_24_04（参数化 ci / release-candidate，断言
  job 集合与全部 `runs-on` 恒为 ubuntu-24.04，防新增 job 绕过钉定），
  WRAPPER_VALIDATION_MAJOR 由 4 升 6；既有 checkout/setup-node/
  setup-python v7、setup-java v6、upload-artifact v4、Node 22 /
  Python 3.11 版本策略与 RC 不变量断言全部保持不变。
- 边界：只动 workflow YAML、契约测试与 docs；零应用/运行时代码、
  生产配置、Docker、DB、语音服务、网络隧道、移动设备与凭据触碰。
  本切片不部署、不发布、不声明任何就绪。

## 验证

在 canonical checkout 的既有虚拟环境解释器
（`ai-learning-os/.venv`，Python 3.11.15）下从本 worktree 根目录运行：

```powershell
python -m pytest services/api/tests/test_workflow_actions_runtime.py -q
# 17 passed in 0.19s

python -m pytest services/api/tests/test_workflow_actions_runtime.py -k "ubuntu_24_04 or wrapper" -q
# 3 passed, 14 deselected in 0.04s

python -m ruff check services/api/tests/test_workflow_actions_runtime.py
# All checks passed!

python -m compileall -q services/api/tests/test_workflow_actions_runtime.py
# exit 0

git diff --check
# exit 0
```

附加确认：

```bash
rg "runs-on: ubuntu-latest" .github/workflows
# 零命中（rg exit 1）

rg "wrapper-validation@v4" .github/workflows
# 零命中（rg exit 1）
```

敏感值扫描：对本切片全部新增文本（`git diff` 的 `+` 行）运行
凭据模式正则扫描（password/passwd/secret/api key/token/私钥块/
AKIA/GitHub pat 前缀等），命中数 **0**。

变更面恰 7 个文件：两个 workflow YAML、契约测试、本 README、
`docs/CHANGELOG.md`、`docs/PROJECT_STATUS.md` 与 `docs/ROADMAP.md`。
