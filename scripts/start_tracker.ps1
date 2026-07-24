# Amazon Price Tracker - baslatma scripti
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtualenv bulunamadi: $python"
    exit 1
}

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*price_tracker*schedule*" }
if ($existing) {
    Write-Host "Price tracker zaten calisiyor (PID $($existing.ProcessId -join ', '))."
    exit 0
}

$dataDir = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

Write-Host "Amazon Price Tracker baslatiliyor..."
# Log Python icinden data\tracker.log'a yazilir
Start-Process -FilePath $python `
    -ArgumentList "-m", "price_tracker", "schedule" `
    -WorkingDirectory $Root `
    -WindowStyle Hidden
Write-Host "Baslatildi. Log: $dataDir\tracker.log"
