param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$upgradeRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$upgradePreview = Join-Path $upgradeRoot 'design-previews\2026-10-03-comprehensive-upgrade'
$upgradeSources = Join-Path $upgradeRoot 'docs\research\upstream-2026-10-03'
$upgradeScratch = Join-Path $upgradeRoot '_testtmp'
$upgradeTargets = [Collections.Generic.List[string]]::new()

function Assert-UpgradeTarget([string]$Target, [string]$Boundary) {
    $absolute = [IO.Path]::GetFullPath($Target)
    $resolved = (Resolve-Path -LiteralPath $absolute).ProviderPath
    $boundaryAbsolute = [IO.Path]::GetFullPath($Boundary).TrimEnd('\','/')
    if (-not $resolved.StartsWith($boundaryAbsolute + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target escaped its explicit boundary: $resolved"
    }
    $checkPath = $resolved
    while ($checkPath.Length -gt $boundaryAbsolute.Length) {
        $item = Get-Item -LiteralPath $checkPath -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Cleanup target includes a reparse point: $checkPath" }
        $checkPath = Split-Path -Parent $checkPath
    }
    return $resolved
}

# Fixed source provenance and license files remain; sampled source text is temporary.
$upgradeKeepNames = @('commit.json','source-manifest.json','manifest.json','README.md','LICENSE','LICENSE.md','LICENSE.txt','NOTICE.md','THIRD-PARTY-NOTICES.md','THIRD_PARTY_NOTICES.md')
Get-ChildItem -LiteralPath $upgradeSources -File -Recurse | Where-Object {
    $_.Name -notin $upgradeKeepNames
} | ForEach-Object { $upgradeTargets.Add((Assert-UpgradeTarget $_.FullName $upgradeSources)) }

# Keep the latest complete screenshot set, remove earlier partial/redundant runs.
$upgradeCaptureDirs = @(Get-ChildItem -LiteralPath (Join-Path $upgradePreview 'native') -Directory | Sort-Object Name)
$upgradeLastCapture = $upgradeCaptureDirs[-1].FullName
foreach ($capture in $upgradeCaptureDirs) {
    if ($capture.FullName -ne $upgradeLastCapture) { $upgradeTargets.Add((Assert-UpgradeTarget $capture.FullName $upgradePreview)) }
}

# Capture helpers created these isolated fixture directories in this chat's workday.
Get-ChildItem -LiteralPath $upgradeScratch -Directory | Where-Object {
    $_.Name -like 'scizhi-native-qa-*' -and $_.CreationTime.Date -eq [datetime]'2026-10-03'
} | ForEach-Object { $upgradeTargets.Add((Assert-UpgradeTarget $_.FullName $upgradeScratch)) }

$upgradeVerifyReport = Get-Content -Raw -LiteralPath (Join-Path $upgradeRoot 'docs\comprehensive-upgrade-python-2026-10-03.json') | ConvertFrom-Json
$upgradeSystemScratch = [IO.Path]::GetFullPath($upgradeVerifyReport.isolated_scratch)
if (Test-Path -LiteralPath $upgradeSystemScratch) {
    if ((Split-Path -Leaf $upgradeSystemScratch) -notlike 'scizhi-comprehensive-*') { throw 'Unexpected system fixture name' }
    $upgradeTargets.Add((Assert-UpgradeTarget $upgradeSystemScratch ([IO.Path]::GetTempPath())))
}

# Interpreter and lint caches are reproducible; installed runtime dependencies remain.
foreach ($area in @('src','tests','scripts')) {
    $areaRoot = Join-Path $upgradeRoot $area
    Get-ChildItem -LiteralPath $areaRoot -Directory -Recurse -Filter '__pycache__' | ForEach-Object {
        $upgradeTargets.Add((Assert-UpgradeTarget $_.FullName $areaRoot))
    }
}
foreach ($cache in @('.pytest_cache','.ruff_cache')) {
    $cachePath = Join-Path $upgradeRoot $cache
    if (Test-Path -LiteralPath $cachePath) { $upgradeTargets.Add((Assert-UpgradeTarget $cachePath $upgradeRoot)) }
}
foreach ($emptyFixture in @('composer-ui','input-review','reasoning-app-ui')) {
    $fixturePath = Join-Path $upgradeScratch $emptyFixture
    if ((Test-Path -LiteralPath $fixturePath) -and -not (Get-ChildItem -LiteralPath $fixturePath -Force | Select-Object -First 1)) {
        $upgradeTargets.Add((Assert-UpgradeTarget $fixturePath $upgradeScratch))
    }
}

$upgradeReportRows = @()
foreach ($target in ($upgradeTargets | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $target)) { continue }
    $entry = Get-Item -LiteralPath $target -Force
    $files = if ($entry.PSIsContainer) { @(Get-ChildItem -LiteralPath $target -File -Recurse -Force) } else { @($entry) }
    $bytes = ($files | Measure-Object -Property Length -Sum).Sum
    $upgradeReportRows += [pscustomobject]@{ path=$target; files=$files.Count; bytes=[long]$bytes; removed=[bool]$Execute }
    if ($Execute) { Remove-Item -LiteralPath $target -Force -Recurse }
}
if ($Execute) {
    # Empty directories under the sampled-source boundary carry no retained artifact.
    Get-ChildItem -LiteralPath $upgradeSources -Directory -Recurse | Sort-Object { $_.FullName.Length } -Descending | ForEach-Object {
        if (-not (Get-ChildItem -LiteralPath $_.FullName -Force | Select-Object -First 1)) {
            $checked = Assert-UpgradeTarget $_.FullName $upgradeSources
            Remove-Item -LiteralPath $checked -Force
        }
    }
}
$upgradeCleanupReport = [pscustomobject]@{
    date='2026-10-03'; executed=[bool]$Execute; targets=$upgradeReportRows;
    files=($upgradeReportRows | Measure-Object -Property files -Sum).Sum;
    bytes=($upgradeReportRows | Measure-Object -Property bytes -Sum).Sum;
    preserved=@('data','dist','deliverables','tui/node_modules','source provenance and licenses','final native screenshots and Ink frames')
}
$upgradeReportPath = Join-Path $upgradeRoot ('docs\comprehensive-upgrade-cleanup-' + $(if ($Execute) {'2026-10-03'} else {'preview-2026-10-03'}) + '.json')
$upgradeCleanupReport | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $upgradeReportPath -Encoding utf8
$upgradeCleanupReport | Select-Object executed,files,bytes
