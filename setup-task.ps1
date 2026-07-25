# Setup Task Scheduler for Claude Token Meter
# Run as Administrator
# Usage: powershell -ExecutionPolicy Bypass -File setup-task.ps1

# Configuration
$TaskName = "Claude-Token-Meter"
$ScriptPath = "C:\Users\Kiata\OneDrive\Desktop\giftv-claude-meter\meter.py"
$WorkingDir = "C:\Users\Kiata\OneDrive\Desktop\giftv-claude-meter"
$PythonExe = "C:\Users\Kiata\AppData\Local\Programs\Python\Python313\pythonw.exe"  # Windowless build — python.exe flashes a console window every run; pythonw.exe suppresses it. Full path since Task Scheduler doesn't reliably see user-shell PATH (the WindowsApps python.exe alias stub can shadow it and fail silently)

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

Write-Host "✅ Task '$TaskName' created successfully!" -ForegroundColor Green
Write-Host "Task runs every 1 minute"
Write-Host "Script path: $ScriptPath"
Write-Host ""
Write-Host "To view the task:"
Write-Host "  tasklist | findstr Token"
Write-Host ""
Write-Host "To manually run once:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To view logs:"
Write-Host "  Get-Content '$WorkingDir\logs\meter.log' -Tail 20"
