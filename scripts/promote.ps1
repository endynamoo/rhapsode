# Cut or promote a release along main -> testing -> stable.
#
#   .\scripts\promote.ps1 -To testing  -DryRun     # show what would happen
#   .\scripts\promote.ps1 -To testing              # cut a candidate from main
#   .\scripts\promote.ps1 -To stable -Tag "what changed"   # promote and anchor it
#
# Fast-forward only, clean tree only: the ladder stays linear, so any promotion can
# be undone by moving the branch back to a known-good-<date> tag.
param(
    [Parameter(Mandatory = $true)][ValidateSet('testing', 'stable')][string]$To,
    [string]$Note = '',
    [switch]$Tag,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# The push at the end needs a credential, and this machine answers git only from
# $env:SENTINEL_GIT_TOKEN - the credential manager is out of the picture so it
# cannot pop a dialog at whoever is not watching. Load the token first, and stop
# with an explanation instead of hanging if there is none.
$env:GIT_TERMINAL_PROMPT = '0'
. (Join-Path $PSScriptRoot 'sentinel-credential.ps1')
if (-not (Enable-SentinelGit) -and -not $DryRun) {
    throw "No git credential: run this where sentinel's token store is available, or set SENTINEL_GIT_TOKEN. Use -DryRun to inspect without pushing."
}

if ($Tag -and $To -ne 'stable') {
    throw "Only 'stable' gets a known-good tag; promote through testing first."
}

function Invoke-Git {
    param([string[]]$GitArgs)
    if ($DryRun) {
        Write-Host ("+ git " + ($GitArgs -join ' ')) -ForegroundColor DarkGray
        return
    }
    & git -C $repo @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw ("git " + ($GitArgs -join ' ') + " failed with exit code " + $LASTEXITCODE)
    }
}

function Read-Git {
    param([string[]]$GitArgs)
    $output = & git -C $repo @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw ("git " + ($GitArgs -join ' ') + " failed with exit code " + $LASTEXITCODE)
    }
    return $output
}

# --- guards ---------------------------------------------------------------
$dirty = Read-Git @('status', '--porcelain')
if ($dirty) {
    throw "Working tree is not clean. Commit or stash first:`n$($dirty -join "`n")"
}

$startedOn = (Read-Git @('rev-parse', '--abbrev-ref', 'HEAD')) -join ''
$source = if ($To -eq 'testing') { 'main' } else { 'testing' }

Invoke-Git @('fetch', 'origin', '--prune')

$localExists = (Read-Git @('branch', '--list', $To)) -join ''
if (-not $localExists) {
    Invoke-Git @('switch', '--track', "origin/$To")
} else {
    Invoke-Git @('switch', $To)
}

# --- promote --------------------------------------------------------------
Invoke-Git @('merge', '--ff-only', "origin/$source")
Invoke-Git @('push', 'origin', $To)

# --- anchor ---------------------------------------------------------------
if ($Tag) {
    $date = Get-Date -Format 'yyyy.MM.dd'
    $name = "known-good-$date"
    $taken = @(Read-Git @('tag', '--list', "$name*"))
    if ($taken.Count -gt 0) {
        $name = "known-good-$date-$($taken.Count + 1)"
    }
    $message = if ($Note) { $Note } else { "stable promoted from testing" }
    Invoke-Git @('tag', '-a', $name, '-m', $message)
    Invoke-Git @('push', 'origin', $name)
    Write-Host ("tagged " + $name) -ForegroundColor Green
}

$head = (Read-Git @('log', '-1', '--oneline')) -join ''
Write-Host ("{0} is now {1}" -f $To, $head) -ForegroundColor Green
if ($startedOn -and $startedOn -ne $To) {
    Write-Host ("you came from {0}; switch back with: git switch {0}" -f $startedOn)
}
