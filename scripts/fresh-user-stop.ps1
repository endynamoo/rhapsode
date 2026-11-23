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
# sandbox's, then put back whatever existed before the test.
$keyPath = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8D3A1C1E-6F2B-4C6D-9E4A-1F0B0A000001}_is1'
& cmd /c "reg delete `"HKCU\$keyPath`" /f >nul 2>&1"
# The backup lives outside the sandbox, because this script deletes the sandbox.
$backup = Join-Path $env:TEMP 'opencode\rh-uninstall-backup\uninstall-key.reg'
$hadBackup = Test-Path $backup
if ($hadBackup) {
    & cmd /c "reg import `"$backup`" >nul 2>&1"
}
$restored = Test-Path ("HKCU:\" + $keyPath)
$note = ''
if (-not $hadBackup) { $note = ' (none existed before the test)' }
Say "   machine's uninstall entry present after teardown: $restored$note"
if (-not $hadBackup -and $restored) { Say "   WARNING: an entry exists but no backup was found - check where it points" }

Say "== removing the sandbox"
Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
Say "   gone: $(-not (Test-Path $Root))"
Say "== done"
