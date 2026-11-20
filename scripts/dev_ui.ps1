param([int]$Port = 8844)
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$AssetsPath = Join-Path $RepoRoot 'apps\desktop\dist'
if (-not (Test-Path -LiteralPath (Join-Path $AssetsPath 'index.html'))) {
    throw 'Build the React frontend first: cd apps/desktop; npm ci; npm run build'
}
& python (Join-Path $PSScriptRoot 'worker.py') --data-dir (Join-Path $RepoRoot '.data') --runtime-dir (Join-Path $RepoRoot 'runtime') --http --assets $AssetsPath --port $Port
exit $LASTEXITCODE
