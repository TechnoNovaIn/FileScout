# Elevated reconfiguration of FileScoutWebServer task
$taskName = "FileScoutWebServer"
$vbsPath = "C:\Filescout\scripts\FileScoutAutoStart.vbs"

try {
    # 1. New Action: run wscript.exe silently (GUI subsystem, NO console window)
    $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbsPath`""

    # 2. Triggers: At system startup AND at user logon
    $triggerStartup = New-ScheduledTaskTrigger -AtStartup
    $triggerLogon = New-ScheduledTaskTrigger -AtLogOn

    # 3. Settings: Hidden, no time limit, battery-friendly
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                                           -DontStopIfGoingOnBatteries `
                                           -DontStopOnIdleEnd `
                                           -ExecutionTimeLimit (New-TimeSpan -Days 365) `
                                           -MultipleInstances IgnoreNew `
                                           -Priority 6 `
                                           -RestartCount 3 `
                                           -RestartInterval (New-TimeSpan -Minutes 1) `
                                           -Hidden

    # 4. Principal: Current user, highest privileges
    $principal = New-ScheduledTaskPrincipal -UserId "vasudava-balaji\admin" -LogonType Interactive -RunLevel Highest

    # 5. Register/Update task
    Register-ScheduledTask -TaskName $taskName `
                           -Action $action `
                           -Trigger @($triggerStartup, $triggerLogon) `
                           -Settings $settings `
                           -Principal $principal `
                           -Description "FileScout 24/7 background watchdog on port 3001" `
                           -Force | Out-Null

    Add-Content -Path "C:\Filescout\startup.log" -Value "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] Scheduled task '$taskName' successfully reconfigured to run detached at startup and logon."
} catch {
    Add-Content -Path "C:\Filescout\startup.log" -Value "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] Error reconfiguring task: $_"
}

# 6. Ensure detached background watchdog is running
try {
    Start-Process -FilePath "wscript.exe" -ArgumentList "`"$vbsPath`"" -WindowStyle Hidden
} catch {}
