# Rhapsode Windows launcher -- follows INSTALL.md Section B (uv path).
# Browser mode by default; -Desktop for the pywebview window (WebView2).
#
# The launcher only reports success when the server answers /api/health. Server
# output goes to server.log next to the library, so a crash is readable instead
# of vanishing with the hidden console.
param(
    [switch]$Desktop,
    [int]$TimeoutSeconds = 120
)
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot"
$env:PYTHONIOENCODING = 'utf-8'

$py = '.venv\Scripts\python.exe'
$uv = (Get-Command uv -EA SilentlyContinue).Source
if (-not $uv) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
if (-not (Test-Path $uv)) { "install uv: winget install --id astral-sh.uv -e"; exit 1 }

$port = if ($env:MURMUR_PORT) { $env:MURMUR_PORT } else { 8765 }
$url = "http://127.0.0.1:$port"

function Test-RhapsodeHealth {
    try { Invoke-RestMethod "$url/api/health" -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

if (Test-RhapsodeHealth) {
    "Rhapsode is already running on $url"
    if (-not $Desktop) { Start-Process $url }
    exit 0
}

if (-not (Test-Path $py)) {
    "creating uv venv (3.12, because misaki pins <3.13)"
    & $uv venv --python 3.12 .venv
}
$stamp = ".venv\.requirements.sha"
$hash = (Get-FileHash -Algorithm SHA256 -Path requirements.txt |
         ForEach-Object Hash) -join '+'
if (-not (Test-Path $stamp) -or (Get-Content $stamp) -ne $hash) {
    "installing deps via uv (minutes the first time)"
    & $uv pip install --python $py -q -r requirements.txt
    if ($Desktop) { & $uv pip install --python $py -q "pywebview>=5.3" }
    if ($LASTEXITCODE) { "warning: film deps failed; reader unaffected" }
    Set-Content $stamp $hash
}

$homeDir = if ($env:MURMUR_HOME) { $env:MURMUR_HOME } else { "$env:LOCALAPPDATA\Rhapsode" }
try {
    New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
} catch {
    "cannot create the data directory: $homeDir"
    "  $($_.Exception.Message)"
    exit 1
}
if (-not (Test-Path "$homeDir\models\kokoro-v1.0.onnx")) {
    "downloading Kokoro-82M (~340 MB)"
    & $py -m murmur.bootstrap
    if ($LASTEXITCODE) {
        "the voice model download failed (exit $LASTEXITCODE); the reader cannot start"
        exit 1
    }
}
# Timestamped logs: an older server may still hold yesterday's file open.
$stampName = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $homeDir "server-$stampName.log"
$errLog = Join-Path $homeDir "server-$stampName.err.log"

if ($Desktop) {
    $proc = Start-Process -FilePath $py -ArgumentList '-m', 'murmur.desktop' `
        -WorkingDirectory $PSScriptRoot -PassThru
} else {
    $proc = Start-Process -FilePath $py -ArgumentList '-m', 'murmur' `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError $errLog -PassThru
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if (Test-RhapsodeHealth) {
        "Rhapsode is up on $url"
        if (-not $Desktop) { Start-Process $url }
        exit 0
    }
    if ($proc.HasExited) { break }
    Start-Sleep -Milliseconds 700
}

"Rhapsode did not start on port $port."
if ($proc.HasExited) {
    $proc.Refresh()
    if ($null -ne $proc.ExitCode) { "  python exited with code $($proc.ExitCode)" }
    else { "  python exited" }
}
$holder = $null
try { $holder = (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop).OwningProcess } catch {}
if ($holder) {
    $name = (Get-Process -Id $holder -ErrorAction SilentlyContinue).ProcessName
    "  port $port is held by $name (PID $holder): stop it, or set MURMUR_PORT"
}
foreach ($file in @($errLog, $log)) {
    if ((Test-Path $file) -and (Get-Item $file).Length -gt 0) {
        "--- $(Split-Path $file -Leaf), last 20 lines ---"
        Get-Content $file -Tail 20
    }
}
exit 1
