[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1, 65535)][int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
if ($BackendPort -eq $FrontendPort) { throw 'Backend and frontend ports must be different.' }
function Get-PhysicalDirectory([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) { throw "Expected directory: $Path" }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Local demo path contains a reparse point: $Path"
    }
    return [IO.Path]::GetFullPath($item.FullName).TrimEnd([IO.Path]::DirectorySeparatorChar)
}
$workspace = Get-PhysicalDirectory (Join-Path $PSScriptRoot '..')
$tmpCandidate = Join-Path $workspace '.tmp'
if (-not (Test-Path -LiteralPath $tmpCandidate -PathType Container)) { [void](New-Item -ItemType Directory -Path $tmpCandidate) }
$tmpRoot = Get-PhysicalDirectory $tmpCandidate
$profileCandidate = Join-Path $tmpRoot 'local-demo'
if (Test-Path -LiteralPath $profileCandidate) { $profilePath = Get-PhysicalDirectory $profileCandidate }
else { $profilePath = [IO.Path]::GetFullPath($profileCandidate).TrimEnd([IO.Path]::DirectorySeparatorChar) }
if (-not $profilePath.StartsWith($tmpRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Local demo profile resolves outside workspace .tmp.' }
$venvPython = Join-Path $workspace '.venv/Scripts/python.exe'
$vite = Join-Path $workspace 'frontend/node_modules/vite/bin/vite.js'
foreach ($required in @($venvPython, $vite, (Join-Path $workspace 'backend/app.py'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required existing demo component is missing: $required" }
}

function Assert-FreeLoopbackPort([int]$Port) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try { $listener.Start() } catch { throw "Loopback port $Port is unavailable: $($_.Exception.Message)" }
    finally { $listener.Stop() }
}
New-Item -ItemType Directory -Force -Path $profilePath | Out-Null
$registryPath = Join-Path $profilePath 'processes.json'
if (Test-Path -LiteralPath $registryPath) {
    try { $existing = Get-Content -Raw -LiteralPath $registryPath | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "Local demo registry is unreadable; inspect or remove it after verifying processes: $registryPath" }
    $entries = @($existing.processes)
    $entryNames = (@($entries | ForEach-Object { [string]$_.name } | Sort-Object) -join ',')
    $allOwned = $entries.Count -eq 2 -and $entryNames -ceq 'backend,frontend'
    $ownedByEntry = @($entries | ForEach-Object {
        try {
            $p = Get-Process -Id ([int]$_.pid) -ErrorAction Stop
            $cim = $null
            try { $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($_.pid)" -ErrorAction Stop } catch { }
            $pathMatches = [IO.Path]::GetFullPath($p.Path) -eq [IO.Path]::GetFullPath($_.executable)
            $commandMatches = $cim -and $cim.CommandLine -eq $_.commandLine
            [pscustomobject]@{ name=[string]$_.name; owned=($pathMatches -and $commandMatches -and ([DateTimeOffset]$p.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds() -eq [long]$_.startUtc) }
        } catch { [pscustomobject]@{ name=[string]$_.name; owned=$false } }
    })
    $complete = $allOwned -and [int]$existing.backendPort -eq $BackendPort -and [int]$existing.frontendPort -eq $FrontendPort -and @($ownedByEntry | Where-Object { -not $_.owned }).Count -eq 0
    if ($complete) { Write-Host 'Local demo already started by this profile.'; return }
    if (@($ownedByEntry | Where-Object { $_.owned }).Count -gt 0) {
        throw "Local demo registry describes a partial or different running set. No processes were changed; run scripts/stop-local-demo.ps1, then retry. Registry: $registryPath"
    }
    Remove-Item -LiteralPath $registryPath -Force
}

Assert-FreeLoopbackPort $BackendPort
Assert-FreeLoopbackPort $FrontendPort

$dbPath = Join-Path $profilePath 'demo.sqlite3'
$children = [Collections.Generic.List[Diagnostics.Process]]::new()
function Start-LoggedProcess([string]$File, [string[]]$Arguments, [string]$WorkingDirectory, [hashtable]$Environment, [string]$Name) {
    $stdoutPath = Join-Path $profilePath "$Name.stdout.log"
    $stderrPath = Join-Path $profilePath "$Name.stderr.log"
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $File
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($arg in $Arguments) { [void]$start.ArgumentList.Add($arg) }
    $start.Environment.Clear()
    foreach ($key in @('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATHEXT', 'SystemDrive', 'ComSpec')) {
        $value = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($null -ne $value) { $start.Environment[$key] = $value }
    }
    foreach ($key in $Environment.Keys) { $start.Environment[$key] = [string]$Environment[$key] }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    if (-not $process.Start()) { throw "Could not start $Name." }
    $children.Add($process)
    $outFile = [IO.File]::Open($stdoutPath, 'Create', 'Write', 'Read')
    $errFile = [IO.File]::Open($stderrPath, 'Create', 'Write', 'Read')
    $outTask = $process.StandardOutput.BaseStream.CopyToAsync($outFile)
    $errTask = $process.StandardError.BaseStream.CopyToAsync($errFile)
    $outTask.ContinueWith([Action[Threading.Tasks.Task]]{ param($t) $outFile.Dispose() }) | Out-Null
    $errTask.ContinueWith([Action[Threading.Tasks.Task]]{ param($t) $errFile.Dispose() }) | Out-Null
    $process.Refresh()
    $startUtc = ([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()
    $identity = $null
    try { $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction Stop } catch { }
    $commandLine = if ($identity -and $identity.CommandLine) { $identity.CommandLine } else { $File + ' ' + ($Arguments -join ' ') }
    return [pscustomobject]@{ process=$process; entry=[pscustomobject]@{ name=$Name; pid=$process.Id; startUtc=$startUtc; executable=$File; commandLine=$commandLine; stdout=$stdoutPath; stderr=$stderrPath } }
}

try {
    $backendLaunch = Start-LoggedProcess $venvPython @('-m','uvicorn','app:app','--app-dir',(Join-Path $workspace 'backend'),'--host','127.0.0.1','--port',[string]$BackendPort) $workspace @{ ALPHA_DEMO_MODE='1'; ALPHA_DB_PATH=$dbPath } 'backend'
    $entries = [Collections.Generic.List[object]]::new()
    $entries.Add($backendLaunch.entry)
    $backend = $backendLaunch.process
    Start-Sleep -Milliseconds 500
    if ($backend.HasExited) { throw "Backend exited during startup; inspect $profilePath/backend.stderr.log" }
    $frontendLaunch = Start-LoggedProcess (Get-Command node.exe -ErrorAction Stop).Source @($vite,'--host','127.0.0.1','--port',[string]$FrontendPort,'--strictPort') (Join-Path $workspace 'frontend') @{} 'frontend'
    $entries.Add($frontendLaunch.entry)
    $frontend = $frontendLaunch.process
    Start-Sleep -Milliseconds 500
    if ($frontend.HasExited) { throw "Frontend exited during startup; inspect $profilePath/frontend.stderr.log" }
    $record = [ordered]@{ schema=1; profile=$profilePath; database=$dbPath; backendPort=$BackendPort; frontendPort=$FrontendPort; processes=@($entries.ToArray()) }
    $json = $record | ConvertTo-Json -Depth 5
    $tempRegistry = Join-Path $profilePath 'processes.json.tmp'
    [IO.File]::WriteAllText($tempRegistry, $json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tempRegistry -Destination $registryPath -Force
    Write-Host "Local demo started: http://127.0.0.1:$FrontendPort (backend http://127.0.0.1:$BackendPort)."
    Write-Host "Logs and isolated database: $profilePath"
} catch {
    $cleanupChildren = @($children.ToArray())
    [array]::Reverse($cleanupChildren)
    foreach ($child in $cleanupChildren) {
        try {
            if (-not $child.HasExited) { $child.Kill($true); $child.WaitForExit(5000) | Out-Null }
        } catch { Write-Warning "Could not stop a child started by this run (PID $($child.Id)): $_" }
    }
    throw
}
