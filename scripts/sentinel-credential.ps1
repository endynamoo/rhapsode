<#
.SYNOPSIS
Hand git a credential for one repository without any GUI prompt.

.DESCRIPTION
This machine has no Git Credential Manager in the picture: `.gitconfig` clears
the helper list and installs one of its own that answers only from
`$env:SENTINEL_GIT_TOKEN`. That is deliberate - GCM pops a window that blocks
whatever non-interactive session touched git first, and a dialog cannot be
answered by an agent.

`Enable-SentinelGit` fills that variable from sentinel's token store, which is
the same token `sentinel-gh` uses for its own git operations. It returns
`$true` when a token is loaded and `$false` when none is available, so a
read-only script can warn and carry on while a script that must push can stop
with an explanation instead of hanging.

  . (Join-Path $PSScriptRoot 'sentinel-credential.ps1')
  if (-not (Enable-SentinelGit)) { Write-Host 'no credential' }
#>
function Enable-SentinelGit {
    param([string]$Repo = 'endynamoo/rhapsode')

    if ($env:SENTINEL_GIT_TOKEN) { return $true }

    $sentinel = if ($env:SENTINEL_HOME) { $env:SENTINEL_HOME } else { 'C:\Apps\sentinel' }
    if (-not (Test-Path (Join-Path $sentinel 'sentinel\gh.py'))) { return $false }

    $root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }

    $code = "import sys; sys.path.insert(0, r'$sentinel'); from sentinel import gh; print(gh.token_for('$Repo', False))"
    try {
        $token = (& $python -c $code 2>$null | Select-Object -Last 1)
    } catch {
        return $false
    }
    if (-not $token) { return $false }
    $env:SENTINEL_GIT_TOKEN = $token.Trim()
    return $true
}

<#
.SYNOPSIS
Read one GitHub REST path through sentinel's read token, as parsed JSON.

.DESCRIPTION
`gh` is logged out on this machine on purpose (its write path is sentinel-gh, and
its read path is sentinel's own fine-grained token). Anything in a script that
wanted `gh api` gets the same answer here, and `$null` when it cannot be read -
the caller decides whether that is a warning or a failure.
#>
function Invoke-SentinelApi {
    param([string]$Path)

    $sentinel = if ($env:SENTINEL_HOME) { $env:SENTINEL_HOME } else { 'C:\Apps\sentinel' }
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }

    $code = @"
import json, sys
sys.path.insert(0, r'$sentinel')
from sentinel import read
print(json.dumps(read._get(r'$Path')))
"@
    try {
        $json = (& $python -c $code 2>$null | Select-Object -Last 1)
    } catch {
        return $null
    }
    if (-not $json) { return $null }
    try { return $json | ConvertFrom-Json } catch { return $null }
}
