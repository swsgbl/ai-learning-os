# M14-241: public device HTTP error classification（最小闭环修复）

## 结论与边界

- 切片：worktree
  `ai-learning-os-worktrees/m14-241-public-device-http-error-classification`，
  分支 `ops/m14-241-public-device-http-error-classification`，基于
  current main `7693c082369cb1eb6cf0ec93663369387fbf4726`。单本地
  commit，不 push、不开 PR、不合并。
- 根因：`urllib.error.HTTPError` 继承自 `OSError`；
  `tools/android_release/public_device_smoke.py` 的 `_read_bounded`
  在传输异常处先落入 `OSError` 分类，因此真实 404/503 会被误标为
  blocked 的 `{stage}_network_unavailable`。
- 修复：在请求返回和响应读取两个分类点都优先捕获 `HTTPError`，映射
  既有稳定码 `{stage}_http_error`；普通 `OSError`、URI 错误和
  `ValueError` 仍映射 blocked 的 `{stage}_network_unavailable`。
- 测试：新增两个纯离线注入测试，分别锁定
  `HTTPError -> manifest_http_error` 与
  `OSError -> manifest_network_unavailable`。测试只注入 transport
  异常，不接触网络、设备、生产或其他外部依赖。
- 本切片不声明任何公网移动发布就绪；未运行真实公网探测或设备冒烟。

## 验证

在 canonical checkout 的既有虚拟环境解释器下从本 worktree 根目录运行
（本 worktree 自身无 `.venv`，未安装任何依赖）：

```powershell
python -m pytest tests/android_release/test_public_device_smoke.py -k "http_error_to_stable_http_error or plain_os_error_to_network_unavailable" -q
# 3 passed, 36 deselected in 0.10s

python -m pytest tests/android_release/test_public_device_smoke.py -q
# 39 passed in 0.32s

python -m ruff check tools/android_release/public_device_smoke.py tests/android_release/test_public_device_smoke.py
# All checks passed!

python -m compileall -q tools/android_release/public_device_smoke.py tests/android_release/test_public_device_smoke.py
# exit 0

git diff --check
# exit 0
```

变更面恰 6 个文件：上述工具与测试文件、本 README、
`docs/ROADMAP.md`，以及 `docs/CHANGELOG.md` /
`docs/PROJECT_STATUS.md`。
