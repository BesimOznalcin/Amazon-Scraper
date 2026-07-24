# Amazon Price Tracker otomatik baslatma gorevlerini kaldir
foreach ($taskName in @("AmazonPriceTracker", "AmazonPriceTrackerWatchdog")) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Gorev kaldirildi (varsa): $taskName"
}
