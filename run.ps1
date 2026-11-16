# Murmur/Rhapsode Windows launcher — follows AGENTS.md Section B (uv path).
# Browser mode by default; -Desktop for the pywebview window (WebView2).
param([switch]$Desktop)
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot"
$env:PYTHONIOENCODING = 'utf-8'
$py = '.venv\Scripts\python.exe'
$uv = (Get-Command uv -EA SilentlyContinue).Source
if (-not $uv) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
if (-not (Test-Path $uv)) { "install uv: winget install --id astral-sh.uv -e"; exit 1 }

$port = if ($env:MURMUR_PORT) { $env:MURMUR_PORT } else { 8765 }
$url = "http://127.0.0.1:$port"
try { Invoke-RestMethod "$url/api/health" -TimeoutSec 2 | Out-Null
      "already running"; Start-Process $url; exit 0 } catch {}

if (-not (Test-Path $py)) {
    "creating uv venv (3.12 — misaki pins <3.13)"
    & $uv venv --python 3.12 .venv
}
$stamp = ".venv\.requirements.sha"
$hash = (Get-FileHash -Algorithm SHA256 -Path requirements.txt, requirements-film.txt |
         ForEach-Object Hash) -join '+'
if (-not (Test-Path $stamp) -or (Get-Content $stamp) -ne $hash) {
    "installing deps via uv (minutes the first time)"
    & $uv pip install --python $py -q -r requirements.txt
    & $uv pip install --python $py -q -r requirements-film.txt
    if ($Desktop) { & $uv pip install --python $py -q "pywebview>=5.3" }
    if ($LASTEXITCODE) { "warning: film deps failed; reader unaffected" }
    Set-Content $stamp $hash
}

$homeDir = if ($env:MURMUR_HOME) { $env:MURMUR_HOME } else { "$env:LOCALAPPDATA\Rhapsode" }
if (-not (Test-Path "$homeDir\models\kokoro-v1.0.onnx")) {
    "downloading Kokoro-82M (~340 MB)"
    & $py -m murmur.bootstrap
}

if ($Desktop) {
    Start-Process -FilePath $py -ArgumentList '-m', 'murmur.desktop' -WorkingDirectory $PSScriptRoot
} else {
    Start-Process -FilePath $py -ArgumentList '-m', 'murmur' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    Start-Sleep 3
    Start-Process $url
}
"Rhapsode launched ($($(if ($Desktop) {'window'} else {'browser'})) mode) on port $port"
