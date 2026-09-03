# Registers scripts/daily_cycle.py to run once a day, so research and
# outreach drafting continue without any session being open.
#
# It never sends anything and never collects contact details - it writes
# a digest to .\digests\<date>.md for a human to read and act on.
#
# Run once:  powershell -File scripts\register_daily_task.ps1
# Stop with: powershell -File scripts\unregister_daily_task.ps1

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "scripts\daily_cycle.py"

$action = New-ScheduledTaskAction -Execute $venvPython -Argument "`"$script`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask -TaskName "Ledgerlift_DailyCycle" -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Ledgerlift daily research + outreach drafting. Never sends anything; writes digests for human review."

Write-Host "Registered. Runs daily at 9:00 AM."
Write-Host "Digests land in .\digests\<date>.md - review and send from there yourself."
