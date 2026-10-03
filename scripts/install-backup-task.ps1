<#
    Template installer. This script changes Windows Task Scheduler only when explicitly run.
    Run as administrator after a manual backup and recovery rehearsal have passed.
    The scheduled task uses SYSTEM; do not use network shares requiring an interactive login.
#>
param(
    [string] $TaskName = 'HealthcheckDailyBackup',
    [string] $At = '02:00',
    [string] $OutputDirectory,
    [string] $ExternalDirectory,
    [string] $PgBin
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run PowerShell as administrator to register the daily backup task.'
}
if ($At -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw 'At must use HH:mm, for example 02:00.' }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'A task with that name already exists. Review it in Task Scheduler instead of silently overwriting it.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { throw 'Create the project .venv first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env') -PathType Leaf)) { throw 'Configure .env first.' }

# This is argument quoting, not shell evaluation. Forbid quotes/newlines in operator-entered paths.
function Quote-Argument([string] $Value) {
    if ($Value -match '["\r\n]') { throw 'Paths must not contain double quotes or newline characters.' }
    # Appending . preserves a drive root and avoids a backslash before the closing quote.
    if ($Value.EndsWith('\')) { $Value += '.' }
    return '"' + $Value + '"'
}
$runner = Join-Path $PSScriptRoot 'run-backup.ps1'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File ' + (Quote-Argument $runner)
if ($OutputDirectory) { $arguments += ' -OutputDirectory ' + (Quote-Argument $OutputDirectory) }
if ($ExternalDirectory) { $arguments += ' -ExternalDirectory ' + (Quote-Argument $ExternalDirectory) }
if ($PgBin) { $arguments += ' -PgBin ' + (Quote-Argument $PgBin) }
$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings | Out-Null
Write-Host "Registered $TaskName for daily $At (Windows server local clock)."
Write-Host 'Confirm the server time zone is Asia/Shanghai, then run the task manually and review its exit code and logs.'
