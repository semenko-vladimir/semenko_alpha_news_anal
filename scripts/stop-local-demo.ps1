[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
function Get-PhysicalDirectory([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) { throw "Expected directory: $Path" }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Local demo path contains a reparse point: $Path" }
    return [IO.Path]::GetFullPath($item.FullName).TrimEnd([IO.Path]::DirectorySeparatorChar)
}
$workspace = Get-PhysicalDirectory (Join-Path $PSScriptRoot '..')
$tmpRoot = Get-PhysicalDirectory (Join-Path $workspace '.tmp')
$profileCandidate = Join-Path $tmpRoot 'local-demo'
if (-not (Test-Path -LiteralPath $profileCandidate -PathType Container)) { Write-Host 'No local demo registry found.'; return }
$profilePath = Get-PhysicalDirectory $profileCandidate
if (-not $profilePath.StartsWith($tmpRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Local demo profile resolves outside workspace .tmp.' }
$registryPath = Join-Path $profilePath 'processes.json'
if (-not (Test-Path -LiteralPath $registryPath -PathType Leaf)) { Write-Host 'No local demo registry found.'; return }
$registry = Get-Content -Raw -LiteralPath $registryPath | ConvertFrom-Json
$entries = @($registry.processes)
[array]::Reverse($entries)
$unresolved = $false
foreach ($entry in $entries) {
    $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
    if (-not $process) { Write-Host "Already exited: $($entry.name) ($($entry.pid))"; continue }
    $startUtc = ([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()
    $cim = $null
    try { $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.pid)" -ErrorAction Stop } catch { }
    $commandMatches = if ($cim) { $cim.CommandLine -eq $entry.commandLine } else { $false }
    $executableMatches = try { [IO.Path]::GetFullPath($process.Path) -eq [IO.Path]::GetFullPath($entry.executable) } catch { $false }
    if ($startUtc -ne [long]$entry.startUtc -or -not $executableMatches -or -not $commandMatches) {
        Write-Warning "Ownership check failed; leaving PID $($entry.pid) untouched."
        $unresolved = $true
        continue
    }
    [Diagnostics.Process]::GetProcessById([int]$entry.pid).Kill($true)
    Write-Host "Stopped owned $($entry.name) process ($($entry.pid))."
}
if ($unresolved) { Write-Warning 'Registry retained so ownership can be checked again later.' }
else { Remove-Item -LiteralPath $registryPath -Force }
