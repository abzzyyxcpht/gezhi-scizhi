param([string]$Repository = "scizhi-agent")
$ErrorActionPreference = "Stop"
if ($Repository -notmatch '^[A-Za-z0-9_.-]+$') { throw "Invalid repository name" }
$releaseRoot = Join-Path (Split-Path $PSScriptRoot -Parent) "deliverables\scizhi-agent"
if (-not (Test-Path -LiteralPath (Join-Path $releaseRoot "SOURCE_MANIFEST.json"))) {
    $releaseRoot = Split-Path $PSScriptRoot -Parent
}
if (-not (Test-Path -LiteralPath (Join-Path $releaseRoot "SOURCE_MANIFEST.json"))) { throw "Run prepare_opensource.py first" }
$account = & gh api user --jq .login
if ($LASTEXITCODE -ne 0) { throw "Run gh auth login -h github.com -p https -w, then retry" }
Push-Location -LiteralPath $releaseRoot
try {
    if (-not (Test-Path -LiteralPath .git)) { throw "Prepared Git repository is missing" }
    & python scripts/verify_public_source.py
    if ($LASTEXITCODE -ne 0) { throw "Public source verification failed" }
    $target = "$account/$Repository"
    if (& git status --porcelain) { throw "Prepared repository has uncommitted changes; review and commit them before publishing" }
    $knownRemotes = @(& git remote)
    if ($knownRemotes -contains "origin") {
        $remote = & git remote get-url origin
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect origin" }
        if ($remote -notmatch ('^(https://github\.com/|git@github\.com:)' + [regex]::Escape($target) + '(\.git)?$')) { throw "Existing origin does not match requested repository" }
        & git push -u origin main
        if ($LASTEXITCODE -ne 0) { throw "Push failed" }
    } else {
        & gh repo create $target --public --source . --remote origin --push --description "SciZhi: research agents with Plan, Tower and Swarm"
        if ($LASTEXITCODE -ne 0) { throw "Repository creation or push failed" }
    }
    & gh repo view $target --json url --jq .url
} finally { Pop-Location }
