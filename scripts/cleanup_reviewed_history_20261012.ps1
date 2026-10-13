param([switch]$Apply)

# Preview by default. Run -Apply manually in your local PowerShell to remove
# only the audited historical intermediates and regenerable caches.
$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$prefix = $workspace.TrimEnd('\') + '\'
$planPath = Join-Path $workspace 'test-output\cleanup-history-plan-20261012.json'
$planHash = 'fbe7049f066793fd026f344e9a33e8aee9957e552136849ec8b7825fd3d9cd10'
if ((Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash -ne $planHash) {
    throw 'The reviewed plan changed. Stop and audit it again.'
}
$plan = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath($plan.root) -ne $workspace) { throw 'Workspace mismatch.' }

function Assert-SafePath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target outside the intended workspace: $full"
    }
    $cursor = $workspace
    foreach ($part in $full.Substring($prefix.Length).Split('\')) {
        $cursor = Join-Path $cursor $part
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a linked path: $cursor"
            }
        }
    }
    return $full
}

function Get-SafeInventory([string]$Path) {
    $base = Assert-SafePath $Path
    $first = Get-Item -LiteralPath $base -Force
    $pending = New-Object 'System.Collections.Generic.Stack[System.IO.FileSystemInfo]'
    $files = New-Object 'System.Collections.Generic.List[object]'
    $pending.Push($first)
    $directoryCount = 0
    [long]$bytes = 0
    while ($pending.Count -gt 0) {
        $item = $pending.Pop()
        [void](Assert-SafePath $item.FullName)
        if ($item.PSIsContainer -or $item -is [IO.DirectoryInfo]) {
            $directoryCount++
            foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) {
                $pending.Push($child)
            }
        } else {
            $relative = ''
            if ($first.PSIsContainer -or $first -is [IO.DirectoryInfo]) {
                $relative = $item.FullName.Substring($base.Length).TrimStart('\').Replace('\','/')
            }
            $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
            $files.Add([pscustomobject]@{relative=$relative; bytes=$item.Length; sha256=$hash})
            $bytes += $item.Length
        }
    }
    return [pscustomobject]@{Files=$files.ToArray(); DirectoryCount=$directoryCount; Bytes=$bytes}
}

function Assert-Unchanged($Target) {
    $path = Assert-SafePath (Join-Path $workspace $Target.relative)
    $actual = Get-SafeInventory $path
    if ($actual.Files.Count -ne @($Target.files).Count -or
        $actual.DirectoryCount -ne $Target.directory_count -or $actual.Bytes -ne $Target.bytes) {
        throw "Target contents changed since review: $path"
    }
    $expected = @{}
    foreach ($file in $Target.files) { $expected[$file.relative] = $file }
    foreach ($file in $actual.Files) {
        if (-not $expected.ContainsKey($file.relative) -or
            $expected[$file.relative].sha256 -ne $file.sha256) {
            throw "Target file changed since review: $path / $($file.relative)"
        }
    }
    return $path
}

function Assert-ProtectedFiles {
    foreach ($guard in $plan.protected_file_guards) {
        $path = Assert-SafePath (Join-Path $workspace $guard.relative)
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $guard.sha256) {
            throw "Protected output changed or missing: $path"
        }
    }
    foreach ($relative in $plan.protected_directories) {
        if (-not (Test-Path -LiteralPath (Join-Path $workspace $relative) -PathType Container)) {
            throw "Protected directory is missing: $relative"
        }
    }
}

Assert-ProtectedFiles
$ready = @()
foreach ($target in $plan.targets) {
    $path = Assert-SafePath (Join-Path $workspace $target.relative)
    if (-not (Test-Path -LiteralPath $path)) { continue }
    [void](Assert-Unchanged $target)
    $ready += $target
    Write-Output ('{0,12:N0} bytes  {1}' -f $target.bytes, $target.relative)
}
[long]$plannedBytes = ($ready | Measure-Object -Property bytes -Sum).Sum
if (-not $Apply) {
    Write-Output ('PREVIEW ONLY: {0} targets, {1:N2} MiB. No files were deleted.' -f $ready.Count, ($plannedBytes/1MB))
    Write-Output 'To execute this reviewed plan locally, run this script with -Apply.'
    return
}

$report = [ordered]@{root=$workspace; status='running'; deleted=@(); removed_bytes=0; error=$null}
try {
    foreach ($target in $ready) {
        $path = Assert-Unchanged $target
        if ($target.kind -eq 'directory') {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
        } else {
            Remove-Item -LiteralPath $path -Force -ErrorAction Stop
        }
        if (Test-Path -LiteralPath $path) { throw "Target still exists after cleanup: $path" }
        $report.deleted += [ordered]@{path=$target.relative; bytes=$target.bytes}
        $report.removed_bytes += $target.bytes
    }
    Assert-ProtectedFiles
    $report.status = 'completed'
} catch {
    $report.status = 'failed'
    $report.error = $_.Exception.Message
    throw
} finally {
    $reportPath = Join-Path $workspace ('test-output\cleanup-history-result-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Output ('Verified removed: {0} targets, {1:N2} MiB. Report: {2}' -f $report.deleted.Count, ($report.removed_bytes/1MB), $reportPath)
}
