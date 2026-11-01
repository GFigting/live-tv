# 每周执行 live-tv 流水线，并把输出写入 output/run-<时间>.log
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-weekly.ps1
# 可选：-Limit 30 只检测前 30 个频道（调试用）
param([int]$Limit = 0)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "未找到虚拟环境：$python。请先执行 python -m venv .venv 与 pip install -e `".[dev]`""
}

$logDir = Join-Path $root "output"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("run-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".log")

$arguments = @("-m", "livetv", "check", "--config", "config/sources.yaml")
if ($Limit -gt 0) {
    $arguments += @("--limit", "$Limit")
}

& $python @arguments *>&1 | Tee-Object -FilePath $log
exit $LASTEXITCODE
