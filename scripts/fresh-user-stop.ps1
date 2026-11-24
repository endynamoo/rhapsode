# Tear down a fresh-install sandbox started with `fresh-user.ps1 -Serve`.
#
#   .\scripts\fresh-user-stop.ps1 -Root "$env:TEMP\opencode\fresh-20261123-120000"
#
# Stops the app, uninstalls it from that profile, puts the machine's own uninstall entry back
# (the installer writes to HKCU regardless of LOCALAPPDATA), and removes the sandbox. Safe to
# run twice: every step is conditional.

param(
    [Parameter(Mandatory = $true)][string]$Root
)

$ErrorActionPreference = 'Stop'

function Say([string]$text) { Write-Host $text }

if (-not (Test-Path $Root)) {
    Say "no such sandbox: $Root"
    exit 0
}
$report = Join-Path $Root 'fresh-report.json'
$port = 8899
if (Test-Path $report) {
    try { $port = (Get-Content $report -Raw | ConvertFrom-Json).port } catch { }
}

Say "== stopping the fresh install"
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/quit" -Method Post -TimeoutSec 15 | Out-Null } catch { }
    Start-Sleep -Seconds 3
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        Say "   asked the app to quit, then ended pid $($listener.OwningProcess)"
    }
}
Get-Process -Name fluxgen -ErrorAction SilentlyContinue | ForEach-Object { try { $_.Kill() } catch { } }
$left = @(Get-Process -Name 'Rhapsode', 'fluxgen' -ErrorAction SilentlyContinue)
Say "   processes left: $($left.Count)"

Say "== uninstalling from the sandbox"
$uninstaller = Get-ChildItem -Path $Root -Recurse -File -Filter 'unins000.exe' -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
if ($uninstaller) {
    $code = (Start-Process -FilePath $uninstaller -Wait -PassThru -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')).ExitCode
    Say "   uninstaller exit $code"
} else {
    Say "   no uninstaller found (already removed?)"
}

# The installer's HKCU entry is what a machine uses to uninstall Rhapsode. Remove the
# sandbox's, then put back whatever existed before the test - but only when there really
# was something to put back. fresh-user.ps1 writes its decision to uninstall-key.json,
# because the backup path is fixed and shared, so the file alone cannot say whether it
# describes this machine or a sandbox from a previous run. Before 2026-11-23 it could not
# say either, and a sandbox entry made during an earlier run was restored as if it were the
# machine's own.
$keyPath = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8D3A1C1E-6F2B-4C6D-9E4A-1F0B0A000001}_is1'
& cmd /c "reg delete `"HKCU\$keyPath`" /f >nul 2>&1"
# The backup lives outside the sandbox, because this script deletes the sandbox.
$backup = Join-Path $env:TEMP 'opencode\rh-uninstall-backup\uninstall-key.reg'
$meta = Join-Path $env:TEMP 'opencode\rh-uninstall-backup\uninstall-key.json'
$restore = $false
$why = 'no backup was recorded'
if (Test-Path $meta) {
    try {
        $state = Get-Content $meta -Raw | ConvertFrom-Json
        $restore = [bool]$state.present -and (Test-Path $backup)
        $why = [string]$state.note
        if ([bool]$state.present -and -not (Test-Path $backup)) { $why = 'the record says there was an entry, but its file is missing' }
    } catch {
        $why = 'the backup record could not be read'
    }
} elseif (Test-Path $backup) {
    # A run from before the record existed left only the .reg. Trust it only when it names
    # a directory that still exists and is not a sandbox.
    $text = Get-Content $backup -Raw
    $match = [regex]::Match($text, '"InstallLocation"="([^"]*)"')
    $location = if ($match.Success) { ($match.Groups[1].Value -replace '\\\\', '\').TrimEnd('\') } else { '' }
    $restore = [bool]$location -and (Test-Path -LiteralPath $location)
    $why = if ($restore) { "restored from an older backup at $location" } else { "an older backup pointed at '$location', which does not exist - not restored" }
}
if ($restore) { & cmd /c "reg import `"$backup`" >nul 2>&1" }
$present = Test-Path ("HKCU:\" + $keyPath)
$outcome = if ($present) { 'restored' } else { 'none' }
Say "   machine's uninstall entry: $outcome - $why"
if (-not $restore -and $present) { Say "   WARNING: an entry is present but no valid backup was recorded - check where it points" }

Say "== removing the sandbox"
Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
Say "   gone: $(-not (Test-Path $Root))"
Say "== done"
