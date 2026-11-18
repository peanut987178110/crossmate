# CrossMate 启动脚本（Windows PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1 [-Port 8000] [-Test]
param(
    [int]$Port = 8000,
    [switch]$Test
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$e2e = Join-Path $root "e2e"

Push-Location $backend
try {
    if ($Test) {
        Write-Host "== 接口测试 ==" -ForegroundColor Cyan
        python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "接口测试失败" }
        Write-Host "== E2E 测试 ==" -ForegroundColor Cyan
        $env:PYTHON_FOR_APP = (Get-Command python).Source
        Push-Location $e2e
        try { python -m pytest -q } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "E2E 测试失败" }
        Write-Host "全部通过。" -ForegroundColor Green
        exit 0
    }
    $env:PORT = "$Port"
    Write-Host "CrossMate 启动中: http://127.0.0.1:$Port/app/index.html" -ForegroundColor Green
    Write-Host "API 文档:        http://127.0.0.1:$Port/docs"
    python -m app.main
} finally {
    Pop-Location
}