param(
    [ValidateRange(1, 65535)] [int] $Port = 8000,
    [switch] $Lan
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html') -PathType Leaf)) {
    throw 'The webpage has not been built. Run scripts/frontend.ps1 -Action Build first; see README.md.'
}

& (Join-Path $PSScriptRoot 'start-backend.ps1') -Port $Port -Lan:$Lan
exit $LASTEXITCODE
