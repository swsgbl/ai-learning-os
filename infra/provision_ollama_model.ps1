#Requires -Version 5.1
<#
M14-71 本地 Ollama 固定模型别名供给脚本（幂等、fail-closed、可复现）。

背景（诚实边界）：Ollama /v1/chat/completions 不可靠地消费请求级顶层
options.num_ctx（OpenAI 规范外字段，兼容性不保证——M14-71 实证不依赖）。
本地冒烟的可靠路径是仓库所有的固定模型别名：以 qwen3.5:9b 为 base，
在模型层固化 PARAMETER num_ctx 4096——上下文窗口随模型定义而非请求 Hint。

契约：
- 别名 aios-qwen3.5-9b-4096 = FROM qwen3.5:9b + PARAMETER num_ctx 4096；
  定义只存在于本脚本（仓库可复现），不依赖人工手工 ollama create；
- 幂等：目标别名已存在且参数/父模型与期望一致 => 直接判成功（不重复
  create）；已存在但不一致 => fail-closed 退出（绝不静默覆盖运维改过
  的本地模型——由人工决策后 ollama rm 再重跑）；
- fail-closed：Ollama 不可达 / base 模型缺失 / create 非 success /
  create 后校验失败，一律非零退出并明确报因；
- qwen3:4b 已知不可用，禁止作为 base（显式拒绝，防误修复）；
- 脚本只调用本机 Ollama HTTP API（默认 127.0.0.1:11434），不触碰任何
  生产容器/服务，不读取任何密钥。

用法（仓库根）：
  powershell -NoProfile -ExecutionPolicy Bypass -File infra/provision_ollama_model.ps1
可选参数：-OllamaHost / -BaseModel / -Alias / -NumCtx（默认值即契约值）。
#>
[CmdletBinding()]
param(
    [string]$OllamaHost = "http://127.0.0.1:11434",
    [string]$BaseModel = "qwen3.5:9b",
    [string]$Alias = "aios-qwen3.5-9b-4096",
    [int]$NumCtx = 4096
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Host "[provision-ollama] FAIL: $Message"
    exit 1
}

function Say([string]$Message) {
    Write-Host "[provision-ollama] $Message"
}

if ($NumCtx -lt 1) {
    Fail "NumCtx 必须是 >=1 的整数（当前: $NumCtx）"
}
if ($BaseModel -eq "qwen3:4b") {
    Fail "base 模型 qwen3:4b 已知不可用（M14-71 契约禁止修复/下载该模型）"
}

# 1) Ollama 可达性（fail-closed：编排环境问题不是供给结论）
try {
    $version = Invoke-RestMethod -Method Get -Uri "$OllamaHost/api/version" -TimeoutSec 10
} catch {
    Fail "Ollama 不可达（$OllamaHost/api/version）: $($_.Exception.Message)"
}
Say "Ollama 可达: version=$($version.version)"

# 2) base 模型必须已存在（脚本不隐式拉取数 GB 模型；缺失即失败）
try {
    $baseShow = Invoke-RestMethod -Method Post -Uri "$OllamaHost/api/show" `
        -ContentType "application/json" -Body (@{ name = $BaseModel } | ConvertTo-Json) `
        -TimeoutSec 30
} catch {
    Fail "base 模型 $BaseModel 不存在或 /api/show 失败（请先 ollama pull $BaseModel；脚本不隐式拉取）: $($_.Exception.Message)"
}
Say "base 模型存在: $BaseModel"

function Get-TargetShow {
    try {
        return Invoke-RestMethod -Method Post -Uri "$OllamaHost/api/show" `
            -ContentType "application/json" -Body (@{ name = $Alias } | ConvertTo-Json) `
            -TimeoutSec 30
    } catch {
        return $null
    }
}

function Test-TargetMatches([object]$TargetShow) {
    if ($null -eq $TargetShow) { return $false }
    $params = "$($TargetShow.parameters)"
    if ($params -notmatch "(?m)^\s*num_ctx\s+$NumCtx\s*$") { return $false }
    # 父模型锚定（可复现性）：details.parent_model 必须非空且恰等于 base
    # （缺失/空白同样不匹配——fail-closed，不把无父模型当可接受）
    $parent = $TargetShow.details.parent_model
    if ([string]::IsNullOrWhiteSpace($parent)) { return $false }
    if ($parent -ne $BaseModel) { return $false }
    return $true
}

# 3) 幂等：目标已存在且与期望一致 => 成功返回，不重复 create
$existing = Get-TargetShow
if ($null -ne $existing) {
    if (Test-TargetMatches $existing) {
        Say "别名 $Alias 已存在且参数一致（num_ctx=$NumCtx, base=$BaseModel）——幂等跳过 create"
        Say "RESULT: PASS"
        exit 0
    }
    Fail "别名 $Alias 已存在但与期望不一致（num_ctx/base 漂移）——fail-closed 拒绝覆盖；人工确认后 ollama rm $Alias 再重跑"
}

# 4) create：模型层固化 PARAMETER num_ctx（FROM base，不复制权重，秒级）
Say "创建别名 $Alias = FROM $BaseModel + PARAMETER num_ctx $NumCtx ..."
$createBody = @{
    model      = $Alias
    from       = $BaseModel
    parameters = @{ num_ctx = $NumCtx }
} | ConvertTo-Json -Depth 5
try {
    $created = Invoke-RestMethod -Method Post -Uri "$OllamaHost/api/create" `
        -ContentType "application/json" -Body $createBody -TimeoutSec 300
} catch {
    Fail "create 失败: $($_.Exception.Message)"
}
if ($created.status -and $created.status -ne "success") {
    Fail "create 返回非 success 状态: $($created.status)"
}
Say "create 完成"

# 5) create 后校验（不信任 create 返回，独立 show 复核）
$verify = Get-TargetShow
if (-not (Test-TargetMatches $verify)) {
    Fail "create 后校验失败：$Alias 的 num_ctx/base 与期望不符（show parameters: $($verify.parameters); parent: $($verify.details.parent_model)）"
}
Say "校验通过: $Alias num_ctx=$NumCtx base=$BaseModel"
Say "RESULT: PASS"
exit 0
