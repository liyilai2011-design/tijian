param(
    [string] $OutputDirectory,
    [string] $ExternalDirectory,
    [string] $PgBin
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw 'Project .venv\Scripts\python.exe was not found. Create the server environment first.'
}

$backupArguments = @((Join-Path $PSScriptRoot 'backup.py'), '--project-root', $projectRoot)
if ($OutputDirectory) { $backupArguments += @('--output-dir', $OutputDirectory) }
if ($ExternalDirectory) { $backupArguments += @('--external-dir', $ExternalDirectory) }
if ($PgBin) { $backupArguments += @('--pg-bin', $PgBin) }

$logDirectory = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logFile = Join-Path $logDirectory ('backup-' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
('Backup started at ' + (Get-Date -Format 's')) | Out-File -LiteralPath $logFile -Encoding utf8 -Append
$previousPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 treats redirected native stderr as ErrorRecord objects.
    # Capture those lines, then propagate the actual Python exit code to Task Scheduler.
    $ErrorActionPreference = 'Continue'
    & $pythonExe @backupArguments 2>&1 | Out-File -LiteralPath $logFile -Encoding utf8 -Append
    $result = $LASTEXITCODE
}
finally { $ErrorActionPreference = $previousPreference }
('Backup exit code: ' + $result) | Out-File -LiteralPath $logFile -Encoding utf8 -Append
if ($result -ne 0) { Write-Error "Backup failed. See $logFile" -ErrorAction Continue }
exit $result
