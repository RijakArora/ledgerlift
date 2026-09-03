# Stops the daily automation registered by register_daily_task.ps1.
Unregister-ScheduledTask -TaskName "Ledgerlift_DailyCycle" -Confirm:$false
Write-Host "Ledgerlift daily automation stopped."
