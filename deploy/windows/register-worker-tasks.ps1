<#
Registers the Skills Gap Analyzer scheduled tasks on a Windows home PC.

  * "SkillsGap Worker"   - every hour: python -m src.worker run
  * "SkillsGap Cleanup"  - daily 03:15: python -m src.worker cleanup
  * "SkillsGap Report"   - daily 08:00: python -m src.worker report --days 1 --email

Settings match the production plan, section 8:
  * runs whether the user is logged on or not (S4U logon: no password is stored)
  * runs as soon as possible after a missed start (StartWhenAvailable)
  * never starts a second copy while one is running (MultipleInstances IgnoreNew;
    the worker also holds its own lock file)
  * runs with standard (not administrator) rights

Run once from an elevated PowerShell in the project folder:
    powershell -ExecutionPolicy Bypass -File deploy\windows\register-worker-tasks.ps1
Remove with:
    powershell -ExecutionPolicy Bypass -File deploy\windows\register-worker-tasks.ps1 -Unregister
#>
param(
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$names = @("SkillsGap Worker", "SkillsGap Cleanup", "SkillsGap Report")

if ($Unregister) {
    foreach ($name in $names) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
    Write-Host "Removed the SkillsGap scheduled tasks."
    return
}

$python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "No virtual environment at $python. Create it first: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
}

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 50) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)

function Register-SkillsGapTask($name, $arguments, $trigger) {
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $ProjectDir
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "Registered '$name'"
}

$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
Register-SkillsGapTask "SkillsGap Worker" "-m src.worker run" $hourly
Register-SkillsGapTask "SkillsGap Cleanup" "-m src.worker cleanup" (New-ScheduledTaskTrigger -Daily -At 3:15am)
Register-SkillsGapTask "SkillsGap Report" "-m src.worker report --days 1 --email" (New-ScheduledTaskTrigger -Daily -At 8:00am)

Write-Host ""
Write-Host "Done. Check runs with:  .venv\Scripts\python.exe -m src.worker status"
Write-Host "Also: turn off sleep (Settings > System > Power) so the PC stays awake for the hourly run."
