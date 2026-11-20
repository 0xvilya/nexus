param([switch]$Stop, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$BundleRoot = $PSScriptRoot
$WorkerExe = Join-Path $BundleRoot 'rainforge-worker.exe'
$DataPath = Join-Path $BundleRoot '.data'
$StatePath = Join-Path $DataPath 'launcher.json'
New-Item -ItemType Directory -Force -Path $DataPath | Out-Null
$Guard = $null

function Get-OwnedWorker($Saved) {
    if ($null -eq $Saved) { return $null }
    $Candidate = Get-Process -Id $Saved.pid -ErrorAction SilentlyContinue
    if ($null -ne $Candidate -and $Candidate.Path -eq $WorkerExe -and
        $Candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $Saved.started_ticks) {
        return $Candidate
    }
    return $null
}

function Test-WorkerReady([string]$Address) {
    try {
        $Session = Invoke-RestMethod -Uri ($Address + '/api/bootstrap') -TimeoutSec 2
        $Headers = @{ Origin = $Address; 'X-RainForge-Token' = $Session.token }
        $Body = @{ jsonrpc = '2.0'; id = 'launcher-health'; method = 'system.snapshot'; params = @{} } | ConvertTo-Json -Compress
        $Reply = Invoke-RestMethod -Uri ($Address + '/api/rpc') -Method Post -Headers $Headers -ContentType 'application/json' -Body $Body -TimeoutSec 2
        return $Reply.result.version -eq '0.1.0'
    } catch { return $false }
}

function Stop-OwnedWorker($OwnedProcess) {
    # PyInstaller one-file executables use a parent bootloader and a child worker.
    # Kill the verified process tree so the child cannot retain the data lock.
    & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $OwnedProcess.Id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the owned worker process tree.' }
    $OwnedProcess.WaitForExit(5000) | Out-Null
}

try {
    $Guard = [System.IO.File]::Open((Join-Path $DataPath 'launcher.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $Saved = $null
    if (Test-Path -LiteralPath $StatePath) {
        try { $Saved = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json } catch { $Saved = $null }
    }
    $Owned = Get-OwnedWorker $Saved
    if ($Stop) {
        if ($null -ne $Owned) { Stop-OwnedWorker $Owned }
        if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath }
        Write-Output 'RainForge stopped.'
        exit 0
    }
    if (-not (Test-Path -LiteralPath $WorkerExe) -or -not (Test-Path -LiteralPath (Join-Path $BundleRoot 'ui\index.html'))) {
        throw 'Portable build is incomplete. Keep the launcher in the RainForge NEXUS folder.'
    }
    if ($null -ne $Owned) {
        $Address = [string]$Saved.url
        if (-not (Test-WorkerReady $Address)) { throw 'RainForge is already running but not responding. Use Stop RainForge before retrying.' }
    } else {
        $Listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
        $Listener.Start()
        $Port = $Listener.LocalEndpoint.Port
        $Listener.Stop()
        $Address = 'http://127.0.0.1:' + $Port
        $Arguments = '--data-dir "' + $DataPath + '" --runtime-dir "' + (Join-Path $BundleRoot 'runtime') + '" --http --assets "' + (Join-Path $BundleRoot 'ui') + '" --port ' + $Port
        $Started = Start-Process -FilePath $WorkerExe -ArgumentList $Arguments -WorkingDirectory $BundleRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $DataPath 'worker.stdout.log') -RedirectStandardError (Join-Path $DataPath 'worker.stderr.log')
        $Ready = $false
        for ($Attempt = 0; $Attempt -lt 50; $Attempt++) {
            $Started.Refresh()
            if ($Started.HasExited) { throw 'Worker failed to start. See .data\worker.stderr.log.' }
            if (Test-WorkerReady $Address) { $Ready = $true; break }
            Start-Sleep -Milliseconds 200
        }
        if (-not $Ready) { Stop-OwnedWorker $Started; throw 'Worker startup timed out.' }
        $Metadata = @{ pid = $Started.Id; started_ticks = $Started.StartTime.ToUniversalTime().Ticks.ToString(); url = $Address }
        $TemporaryState = Join-Path $DataPath ('launcher-' + [guid]::NewGuid().ToString('N') + '.tmp')
        $Metadata | ConvertTo-Json | Set-Content -LiteralPath $TemporaryState -Encoding UTF8
        if (Test-Path -LiteralPath $StatePath) { [System.IO.File]::Replace($TemporaryState, $StatePath, ($StatePath + '.previous')) }
        else { [System.IO.File]::Move($TemporaryState, $StatePath) }
    }
    if (-not $NoBrowser) {
        $EdgeExe = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
        if (Test-Path -LiteralPath $EdgeExe) {
            Start-Process -FilePath $EdgeExe -ArgumentList ('--app=' + $Address) -WindowStyle Normal | Out-Null
        } else { Start-Process $Address | Out-Null }
    }
    Write-Output $Address
} catch {
    Write-Error $_
    exit 1
} finally {
    if ($null -ne $Guard) { $Guard.Dispose() }
}
