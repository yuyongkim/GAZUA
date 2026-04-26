# Windows Task Scheduler 등록 — KST 17:00 (장 마감 후 30분) 매일 실행.
# Run as user (no admin):
#   powershell -File scripts\eod_register_task.ps1
#
# 등록 후 Task Scheduler GUI에서 "ky-platform-EOD" 확인 / 수정 가능.

$RootPath = (Resolve-Path "$PSScriptRoot\..").Path
$BashPath = "C:\Program Files\Git\bin\bash.exe"
$ScriptPath = "$RootPath\scripts\eod_collect.sh"
$LogDir = "$RootPath\runtime_logs"

if (-not (Test-Path $BashPath)) {
    Write-Error "Git Bash not found at $BashPath. Edit \$BashPath in this script."
    exit 1
}
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$TaskName = "ky-platform-EOD"
$Trigger = New-ScheduledTaskTrigger -Daily -At "17:00"
$Action = New-ScheduledTaskAction `
    -Execute $BashPath `
    -Argument "`"$ScriptPath`" full" `
    -WorkingDirectory $RootPath
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# Remove if exists
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[!] removed existing task: $TaskName"
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Settings $Settings `
    -Description "ky-platform 매트릭스 EOD 자동 수집 (every weekday 17:00)" | Out-Null

Write-Host "[OK] registered: $TaskName"
Write-Host "  trigger: daily 17:00"
Write-Host "  command: $BashPath `"$ScriptPath`" full"
Write-Host "  unregister:  Unregister-ScheduledTask -TaskName $TaskName"
Write-Host "  run now:     Start-ScheduledTask -TaskName $TaskName"
Write-Host "  view logs:   $LogDir\eod_YYYYMMDD.log"
