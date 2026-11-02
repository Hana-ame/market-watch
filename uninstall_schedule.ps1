# Market-watch: remove Windows scheduled task
# Usage: powershell.exe -ExecutionPolicy Bypass -File E:\market-watch\uninstall_schedule.ps1

$ErrorActionPreference = "Stop"
$taskName = "MarketWatch-Refresh"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
  Write-Host "[ok] task removed: $taskName"
} else {
  Write-Host "[skip] task not found: $taskName"
}