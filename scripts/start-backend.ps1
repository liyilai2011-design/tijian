param(
    [ValidateRange(1, 65535)] [int] $Port = 8000,
    [switch] $Reload,
    [switch] $Lan
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Python was not found. Install server-side Python and follow README.md to create .venv.'
    }
    $pythonExe = $pythonCommand.Source
}

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env') -PathType Leaf)) {
    throw 'Missing .env. Copy .env.example to .env first; database and account initialization are separate later stages.'
}

& $pythonExe -c 'import fastapi, uvicorn, sqlalchemy, alembic, pydantic_settings'
if ($LASTEXITCODE -ne 0) {
    throw 'Server dependencies are missing. Install backend/requirements.txt in .venv as explained in README.md.'
}

$listenAddress = '127.0.0.1'
if ($Lan) { $listenAddress = '0.0.0.0' }
$serverArguments = @('-m', 'uvicorn', 'app.main:app', '--host', $listenAddress, '--port', "$Port", '--no-access-log')
if ($Reload) {
    $serverArguments += '--reload'
}

Write-Host "Backend: http://127.0.0.1:$Port/api/health"
Write-Host "Database readiness (after initialization): http://127.0.0.1:$Port/api/ready"
Write-Host "Webpage (after frontend build): http://127.0.0.1:$Port"
if ($Lan) { Write-Host 'LAN test enabled: clients may use http://SERVER_IP:PORT. Use demonstration data for this HTTP test.' }
Write-Host 'Frontend debugging can use a second terminal; see README.md.'
Write-Host 'Press Ctrl+C to stop. This script does not install dependencies or Windows services.'

Push-Location (Join-Path $projectRoot 'backend')
try {
    & $pythonExe @serverArguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
