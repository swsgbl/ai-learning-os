# M14-01 修正轮：API 测试证据脚本——记录「哪台解释器、哪个 commit、跑了什么命令」。
# 目的：让 docs/PROJECT_STATUS.md 里「N passed / M skipped」可被任何人一步复现，
# 不依赖口头描述的「canonical venv」。
#
# 用法（仓库根或任意目录均可，脚本自定位仓库根）：
#   powershell -ExecutionPolicy Bypass -File tools\voice\run_api_tests.ps1
#   # 指定解释器（默认自动解析，见下方顺序）：
#   powershell -ExecutionPolicy Bypass -File tools\voice\run_api_tests.ps1 -Python <python.exe full path>
#
# 解释器解析顺序（找到即用，并在输出中打印实际路径——这就是证据；不硬编码任何
# 盘符/机器特定绝对路径）：
#   1. -Python 参数
#   2. $env:AIOS_TEST_PYTHON
#   3. 本工作树 .venv\Scripts\python.exe（存在即用——推荐：工作树自包含）
#   4. 相对同级主检出 .venv：..\..\ai-learning-os\.venv\Scripts\python.exe
#      （相对仓库根探测，无盘符假设——标准 worktree 布局
#       <root>\ai-learning-os-worktrees\<name> 下即主检出的 canonical venv；
#       非该布局的机器请用 -Python / AIOS_TEST_PYTHON 显式指定）
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
    # 相对同级主检出（标准 worktree 布局；无盘符假设）
    $siblingVenv = Join-Path $RepoRoot "..\..\ai-learning-os\.venv\Scripts\python.exe"
    if (Test-Path $siblingVenv) { return (Resolve-Path $siblingVenv).Path }
    Write-Error "未找到可用解释器：用 -Python / `$env:AIOS_TEST_PYTHON 指定，或创建工作树 venv：`n  py -3.11 -m venv .venv`n  .venv\Scripts\pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt"
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
