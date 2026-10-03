param(
    [ValidateSet('Install', 'Build', 'Dev', 'Test')] [string] $Action = 'Build',
    [ValidateRange(1, 65535)] [int] $Port = 5173
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw 'Node.js was not found. Install server-side Node.js with npm; see README.md.'
}

$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
$npmCli = Join-Path $projectRoot '.deps\npm\bin\npm-cli.js'
if ($npmCommand) {
    $npmExecutable = $npmCommand.Source
    $npmPrefix = @()
}
elseif (Test-Path -LiteralPath $npmCli -PathType Leaf) {
    $npmExecutable = $nodeCommand.Source
    $npmPrefix = @($npmCli)
}
else {
    throw 'npm was not found. Install Node.js with npm on the server; no client installation is needed.'
}

$frontendRoot = Join-Path $projectRoot 'frontend'
if ($Action -ne 'Install' -and -not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules\vite\bin\vite.js') -PathType Leaf)) {
    throw 'Frontend dependencies are missing. Run scripts/frontend.ps1 -Action Install on the server first.'
}

switch ($Action) {
    'Install' { $npmArguments = @('ci', '--no-audit', '--no-fund') }
    'Build' { $npmArguments = @('run', 'build') }
    'Test' { $npmArguments = @('run', 'test') }
    'Dev' { $npmArguments = @('run', 'dev', '--', '--host', '127.0.0.1', '--port', "$Port", '--strictPort') }
}

Write-Host "Frontend action: $Action"
if ($Action -eq 'Dev') {
    Write-Host "Webpage: http://127.0.0.1:$Port"
    Write-Host 'API requests are forwarded to the backend at http://127.0.0.1:8000. Press Ctrl+C to stop.'
}

Push-Location $frontendRoot
try {
    & $npmExecutable @npmPrefix @npmArguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
