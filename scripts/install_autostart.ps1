# Windows Task Scheduler:
# 1) Oturum acilinca baslat
# 2) Her 5 dakikada bir kontrol et (uyku sonrasi olurse yeniden baslat)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $Root "start_tracker.bat"
$taskName = "AmazonPriceTracker"
$watchdogName = "AmazonPriceTrackerWatchdog"

if (-not (Test-Path $bat)) {
    Write-Error "Bulunamadi: $bat"
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$bat`"" `
    -WorkingDirectory $Root

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $logonTrigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

# Watchdog: uyku/uyanmadan sonra process olduysa yeniden baslatir
$watchSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew

$watchTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 9999)

Register-ScheduledTask `
    -TaskName $watchdogName `
    -Action $action `
    -Trigger $watchTrigger `
    -Settings $watchSettings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Gorevler kuruldu:"
Write-Host "  - $taskName (oturum acilinca)"
Write-Host "  - $watchdogName (her 5 dk kontrol)"
Write-Host "Manuel:  schtasks /Run /TN $taskName"
Get-ScheduledTask -TaskName $taskName, $watchdogName | Format-Table TaskName, State
