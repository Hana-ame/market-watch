# Market-watch: install Windows scheduled task
# Runs a WSL refresh on weekdays at 09:20 and 15:10 (pre-open + post-close).
# Intraday refresh is handled by WSL cron; this task also covers the case WSL was not running.
# Usage: powershell.exe -ExecutionPolicy Bypass -File E:\market-watch\install_schedule.ps1

$ErrorActionPreference = "Stop"
$taskName = "MarketWatch-Refresh"
$distro = "Ubuntu"
$cmd = "/mnt/e/market-watch/daily_guard.sh"

$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $distro -e bash $cmd"
$triggerOpen = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:20"
$triggerClose = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "15:10"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($triggerOpen, $triggerClose) `
  -Description "Local refresh + ensure GitHub Pages data is fresh for /mnt/e/market-watch (09:20 / 15:10)" `
  -Settings $settings -Force | Out-Null

Write-Host "[ok] task registered: $taskName"
$t = Get-ScheduledTask -TaskName $taskName
Write-Host ("[state] " + $t.State)
$t.Triggers | ForEach-Object { Write-Host ("[trigger] " + $_.StartBoundary) }