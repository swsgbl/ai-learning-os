# M14-01 修正轮：API 测试证据脚本——记录「哪台解释器、哪个 commit、跑了什么命令」。
# 目的：让 docs/PROJECT_STATUS.md 里「N passed / M skipped」可被任何人一步复现，
# 不依赖口头描述的「canonical venv」。
#
# 用法（仓库根或任意目录均可，脚本自定位仓库根）：
#   powershell -ExecutionPolicy Bypass -File tools\voice\run_api_tests.ps1
#   # 指定解释器（默认自动解析，见下方顺序）：
#   powershell -ExecutionPolicy Bypass -File tools\voice\run_api_tests.ps1 -Python C:\path\to\python.exe
#
# 解释器解析顺序（找到即用，并在输出中打印实际路径——这就是证据）：
#   1. -Python 参数
#   2. $env:AIOS_TEST_PYTHON
#   3. 本工作树 .venv\Scripts\python.exe（存在即用——推荐：工作树自包含）
#   4. D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe
#      （canonical venv——2026-09-09/10 记录的 1729/1754 passed 数字所用解释器；
#       Python 3.11.15 + fastapi/httpx/pytest 等 services/api/requirements*.txt 全集）
# 依赖缺失时不自动安装（无副作用）：给出安装命令并退出 2。
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $RepoRoot

function Resolve-TestPython {
    if ($Python) { return $Python }
    if ($env:AIOS_TEST_PYTHON) { return $env:AIOS_TEST_PYTHON }
    $worktreeVenv = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $worktreeVenv) { return $worktreeVenv }
    $canonical = "D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe"
    if (Test-Path $canonical) { return $canonical }
    Write-Error "未找到可用解释器：用 -Python 指定，或创建工作树 venv：`n  py -3.11 -m venv .venv`n  .venv\Scripts\pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt"
}

$Py = Resolve-TestPython
$Head = git rev-parse HEAD
Write-Host "[api-test-evidence] repo      : $RepoRoot"
Write-Host "[api-test-evidence] git head  : $Head"
Write-Host "[api-test-evidence] python    : $Py"
Write-Host "[api-test-evidence] py version: $(& $Py --version)"
& $Py -m pytest --version
if ($LASTEXITCODE -ne 0) {
    Write-Error "pytest 不可用——安装依赖后重试：`n  & '$Py' -m pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt"
    exit 2
}

# 与 PROJECT_STATUS 记录一致的命令形态（pytest.ini 提供 pythonpath/testpaths）
Write-Host "[api-test-evidence] command   : & python -m pytest services/api/tests -q   (cwd = repo root)"
& $Py -m pytest services/api/tests -q
exit $LASTEXITCODE
