# Setup Task Scheduler for Claude Token Meter
# Usage: powershell -ExecutionPolicy Bypass -File setup-task.ps1
# No need to open an elevated shell yourself — the script requests Administrator
# rights via UAC on its own (see below). Registering a scheduled task with
# -RunLevel Highest requires elevation; without it Register-ScheduledTask fails
# with a *non-terminating* "Access is denied" that the old version printed right
# past, so it claimed success while having created nothing.

# Self-elevate to Administrator if not already running as admin
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Requesting Administrator privileges..." -ForegroundColor Yellow
    # -NoExit keeps the elevated window open so the verify result below is
    # actually readable — it appears in the *new* window, which would otherwise
    # close the instant the script ends.
    $arguments = "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Start-Process powershell -Verb RunAs -ArgumentList $arguments
    exit
}

# Configuration
$TaskName = "Claude-Token-Meter"
$ScriptPath = "D:\Project\giftv-claude-meter\meter.py"
$WorkingDir = "D:\Project\giftv-claude-meter"
$PythonExe = "C:\Users\PC\AppData\Local\Programs\Python\Python313\pythonw.exe"  # Windowless build — python.exe flashes a console window every run; pythonw.exe suppresses it. Full path since Task Scheduler doesn't reliably see user-shell PATH (the WindowsApps python.exe alias stub can shadow it and fail silently)

# Check if task already exists
$TaskExists = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($TaskExists) {
    Write-Host "Task '$TaskName' already exists. Removing..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create trigger (every 1 minute)
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 365)

# Create action
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $ScriptPath `
    -WorkingDirectory $WorkingDir

# Register task (run under current user, run whether logged in or not)
Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -RunLevel Highest `
    -User (whoami) `
    -Force | Out-Null

# Verify the task actually exists — do not trust the absence of a red error,
# Register-ScheduledTask's failures are non-terminating
$verify = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($verify) {
    $info = $verify | Get-ScheduledTaskInfo
    Write-Host "✅ Verified: NextRunTime = $($info.NextRunTime)" -ForegroundColor Green
} else {
    Write-Host "❌ Task creation failed verification — check permissions" -ForegroundColor Red
    exit 1
}

Write-Host "Task '$TaskName' created successfully!" -ForegroundColor Green
Write-Host "Task runs every 1 minute"
Write-Host "Script path: $ScriptPath"
Write-Host ""
Write-Host "To view the task:"
Write-Host "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "To manually run once:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To view logs:"
Write-Host "  Get-Content '$WorkingDir\logs\meter.log' -Tail 20"
