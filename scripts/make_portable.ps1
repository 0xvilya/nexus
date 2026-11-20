param([Parameter(Mandatory = $true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Destination = [System.IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $Destination) { throw 'Destination already exists. Existing user files will not be overwritten.' }
$WorkerSource = Join-Path $RepoRoot 'apps\desktop\src-tauri\binaries\rainforge-worker-x86_64-pc-windows-msvc.exe'
foreach ($Required in @($WorkerSource, (Join-Path $RepoRoot 'apps\desktop\dist\index.html'), (Join-Path $RepoRoot 'runtime\bin\manifest.json'))) {
    if (-not (Test-Path -LiteralPath $Required)) { throw ('Build prerequisite missing: ' + $Required) }
}
New-Item -ItemType Directory -Path $Destination | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Destination 'runtime') | Out-Null
Copy-Item -LiteralPath $WorkerSource -Destination (Join-Path $Destination 'rainforge-worker.exe')
Copy-Item -LiteralPath (Join-Path $RepoRoot 'apps\desktop\dist') -Destination (Join-Path $Destination 'ui') -Recurse
Copy-Item -LiteralPath (Join-Path $RepoRoot 'runtime\bin') -Destination (Join-Path $Destination 'runtime\bin') -Recurse
Copy-Item -LiteralPath (Join-Path $RepoRoot 'runtime\catalog.json') -Destination (Join-Path $Destination 'runtime\catalog.json')
if (Test-Path -LiteralPath (Join-Path $RepoRoot 'runtime\models')) {
    New-Item -ItemType Directory -Path (Join-Path $Destination 'runtime\models') | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $RepoRoot 'runtime\models') -File -Filter '*.gguf' | Copy-Item -Destination (Join-Path $Destination 'runtime\models')
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portable_launcher.ps1') -Destination (Join-Path $Destination 'Launch RainForge.ps1')
Copy-Item -LiteralPath (Join-Path $RepoRoot 'LICENSE') -Destination (Join-Path $Destination 'LICENSE')
@'
RainForge NEXUS - portable development build

Open the RainForge NEXUS desktop shortcut to start.
The interface opens in a browser app window. Native Tauri/installer work is pending.
Python, Node.js and Ollama are not required to launch this packaged build.
Use Stop RainForge.lnk in this folder to stop its local worker and loaded model.
Closing the browser window alone leaves the local worker running until stopped.
No automatic startup or scheduled service is installed.
Your notes are stored in .data\workspace. Do not delete .data to update the program.
The included SmolLM2 135M model is a runtime smoke model, not a finished coding agent.
Model source, pinned revision, hashes and license link are in runtime\catalog.json.
The MIT llama.cpp notice is in runtime\bin\LICENSE-llama.cpp.txt.
'@ | Set-Content -LiteralPath (Join-Path $Destination 'READ ME.txt') -Encoding UTF8
$SourceCommit = & git -C $RepoRoot rev-parse HEAD
@{ build = 'portable-development'; version = '0.1.0'; source_repository = $RepoRoot; source_commit = $SourceCommit } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'BUILD_INFO.json') -Encoding UTF8
Write-Output $Destination

if (Test-Path -LiteralPath (Join-Path $RepoRoot 'runtime\vulkan')) { Copy-Item -LiteralPath (Join-Path $RepoRoot 'runtime\vulkan') -Destination (Join-Path $Destination 'runtime\vulkan') -Recurse }
