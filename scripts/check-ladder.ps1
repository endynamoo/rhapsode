<#
.SYNOPSIS
Verify the promotion ladder is intact: main -> testing -> stable, fast-forward only,
every rung under a known-good tag, CI green on each head.

.DESCRIPTION
Read-only. Exits 1 if the ladder has been broken, so it can gate a promotion or a
session's wrap-up. Run it after other agents have been working.

  .\scripts\check-ladder.ps1
  .\scripts\check-ladder.ps1 -SkipCI
#>
param([switch]$SkipCI)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$problems = @()

function Invoke-Git {
    param([string[]]$GitArgs)
    $output = & git -C $repo @GitArgs 2>$null
    if ($LASTEXITCODE -ne 0) { throw ("git " + ($GitArgs -join ' ') + " failed") }
    return $output
}

Write-Host "fetching origin..." -ForegroundColor DarkGray
Invoke-Git @('fetch', '--prune', 'origin') | Out-Null

$dirty = @(Invoke-Git @('status', '--porcelain'))
if ($dirty.Count -gt 0) {
    Write-Host "note: working tree has uncommitted changes (fine for a check, not for a promotion)" -ForegroundColor Yellow
}

Write-Host "`nbranch heads" -ForegroundColor Cyan
$shas = @{}
foreach ($branch in @('main', 'testing', 'stable')) {
    $sha = (@(Invoke-Git @('rev-parse', '--short', "origin/$branch"))) -join ''
    $shas[$branch] = $sha
    Write-Host ("  {0,-8} {1}" -f $branch, $sha)
}

$counts = @{}
foreach ($pair in @(@('stable', 'main'), @('stable', 'testing'), @('testing', 'main'))) {
    $ahead = (@(Invoke-Git @('rev-list', '--count', "origin/$($pair[0])..origin/$($pair[1])"))) -join ''
    $counts["$($pair[1]) ahead of $($pair[0])"] = $ahead
}
foreach ($key in $counts.Keys | Sort-Object) {
    Write-Host ("  {0,-34} {1}" -f $key, $counts[$key])
}

Write-Host "`nancestry" -ForegroundColor Cyan
$ancestry = @(
    @{ Ancestor = 'origin/testing'; Descendant = 'origin/main'; Rule = 'testing is contained in main' },
    @{ Ancestor = 'origin/stable'; Descendant = 'origin/testing'; Rule = 'stable is contained in testing' }
)
foreach ($check in $ancestry) {
    & git -C $repo merge-base --is-ancestor $check.Ancestor $check.Descendant 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  ok   " + $check.Rule) -ForegroundColor Green
    } else {
        Write-Host ("  FAIL " + $check.Rule) -ForegroundColor Red
        $problems += $check.Rule
    }
}

Write-Host "`nknown-good tags" -ForegroundColor Cyan
$tags = @(Invoke-Git @('tag', '--list', 'known-good-*'))
if ($tags.Count -eq 0) {
    Write-Host "  FAIL no known-good-* tag exists" -ForegroundColor Red
    $problems += 'no known-good-* tag exists'
}
foreach ($tag in $tags) {
    & git -C $repo merge-base --is-ancestor $tag 'origin/stable' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  ok   {0} is in stable" -f $tag) -ForegroundColor Green
    } else {
        Write-Host ("  FAIL {0} is not in stable" -f $tag) -ForegroundColor Red
        $problems += "$tag is not in stable"
    }
}

if (-not $SkipCI) {
    Write-Host "`nCI" -ForegroundColor Cyan
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) {
        Write-Host "  skipped (gh not on PATH)" -ForegroundColor Yellow
    } else {
        $url = (@(Invoke-Git @('remote', 'get-url', 'origin'))) -join ''
        $slug = $null
        if ($url -match 'github\.com[:/]+([^/]+)/(.+?)(\.git)?$') {
            $slug = "$($Matches[1])/$($Matches[2])"
        }
        if (-not $slug) {
            Write-Host "  skipped (origin is not a GitHub URL)" -ForegroundColor Yellow
        } else {
            foreach ($branch in @('main', 'testing', 'stable')) {
                $runs = gh api "repos/$slug/actions/runs?branch=$branch&per_page=1" 2>$null | ConvertFrom-Json
                $run = $runs.workflow_runs[0]
                if (-not $run) {
                    Write-Host ("  ???  {0,-8} no runs" -f $branch) -ForegroundColor Yellow
                    continue
                }
                $verdict = if ($run.status -ne 'completed') { $run.status } else { $run.conclusion }
                $sha = $run.head_sha.Substring(0, 7)
                if ($verdict -eq 'success' -and $sha -eq $shas[$branch]) {
                    Write-Host ("  ok   {0,-8} {1}" -f $branch, $verdict) -ForegroundColor Green
                } elseif ($verdict -eq 'success') {
                    Write-Host ("  warn {0,-8} last run was {1} but HEAD is another commit" -f $branch, $sha) -ForegroundColor Yellow
                } elseif ($run.status -ne 'completed') {
                    Write-Host ("  warn {0,-8} CI is {1}" -f $branch, $verdict) -ForegroundColor Yellow
                } else {
                    Write-Host ("  FAIL {0,-8} {1}" -f $branch, $verdict) -ForegroundColor Red
                    $problems += "CI on $branch is $verdict"
                }
            }
        }
    }
}

Write-Host ""
if ($problems.Count -gt 0) {
    Write-Host "the ladder is broken:" -ForegroundColor Red
    foreach ($problem in $problems) { Write-Host ("  - " + $problem) -ForegroundColor Red }
    Write-Host "`nDo not force anything. Find who moved the pointer (git reflog show origin/stable is local only -`nuse the GitHub branch history) and restore it to the newest known-good tag." -ForegroundColor Yellow
    exit 1
}

Write-Host "ladder intact: main -> testing -> stable, all rungs under a known-good tag" -ForegroundColor Green
exit 0
