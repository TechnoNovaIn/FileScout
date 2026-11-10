# Register FileScout as a permanent Windows Scheduled Task

$taskName = "FileScoutService"
$oldTask = "FileScoutWebServer"

Write-Host "Configuring FileScout Scheduled Task..."

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Filescout\scripts\start-service.ps1"

$triggerStartup = New-ScheduledTaskTrigger -AtStartup
$triggerLogon   = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# Remove old task if present
Unregister-ScheduledTask -TaskName $oldTask -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Register under SYSTEM so it runs on machine boot before any user logs in
try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger @($triggerStartup, $triggerLogon) `
        -Settings $settings `
        -User "SYSTEM" `
        -RunLevel Highest
    Write-Host "Successfully registered $taskName as SYSTEM."
} catch {
    Write-Host "Falling back to current user principal: $_"
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger @($triggerStartup, $triggerLogon) `
        -Settings $settings `
        -RunLevel Highest
}

# Verify task
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State, @{N="Triggers";E={$_.Triggers.CimClass.ClassName}}
