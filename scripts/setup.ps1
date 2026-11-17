# One-shot Windows setup for Rhapsode.
#
#   .\scripts\setup.ps1                    # detect the card, pick a profile, install
#   .\scripts\setup.ps1 -Profile lite      # force a profile (lite|balanced|max|cpu)
#   .\scripts\setup.ps1 -Profile max -NoGguf
#
# What it does, in order: checks the prerequisites, builds .venv, installs the
# reader, bootstraps Kokoro, builds image-venv for the stills engine, fetches the
# q4 transformer and the detectors, writes the engine profile, then smoke-tests.
param(
    [ValidateSet('auto', 'lite', 'balanced', 'max', 'cpu')]
    [string]$Profile = 'auto',
    [switch]$NoGguf,
    [switch]$SkipDeps,
    [switch]$SkipSmoke
)
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot\.."

function Step($message) { "== $message" }
function Fail($message) { "!! $message"; exit 1 }

$homeDir = if ($env:MURMUR_HOME) { $env:MURMUR_HOME } else { "$env:LOCALAPPDATA\Rhapsode" }
$python = Join-Path $homeDir 'image-venv\Scripts\python.exe'
$venvPy = '.venv\Scripts\python.exe'

# ---------------------------------------------------------------- prerequisites
Step 'prerequisites'
$uv = (Get-Command uv -EA SilentlyContinue).Source
if (-not $uv) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
if (-not (Test-Path $uv)) { Fail 'uv is missing: winget install --id astral-sh.uv -e' }
$ffmpeg = (Get-Command ffmpeg -EA SilentlyContinue).Source
if (-not $ffmpeg) { "   ffmpeg not found: MP3 export and chapter video will be unavailable" }
"   uv:      $uv"
"   ffmpeg:  $(if ($ffmpeg) { $ffmpeg } else { 'missing' })"
"   data:    $homeDir"

# ---------------------------------------------------------------- reader venv
Step 'reader environment'
if (-not (Test-Path $venvPy)) {
    & $uv venv --python 3.12 .venv
}
if (-not $SkipDeps) {
    "   installing requirements (minutes the first time)"
    & $uv pip install --python $venvPy -q -r requirements.txt
    & $uv pip install --python $venvPy -q -r requirements-film.txt
    if ($LASTEXITCODE) { "   warning: film deps failed; the reader still works" }
}

Step 'voice model'
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
if (-not (Test-Path "$homeDir\models\kokoro-v1.0.onnx")) {
    & $venvPy -m murmur.bootstrap
    if ($LASTEXITCODE) { Fail 'the Kokoro download failed' }
} else {
    "   already present"
}

# ---------------------------------------------------------------- engine
Step 'stills engine'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\setup_engine.ps1" -Profile $Profile $(if ($NoGguf) { '-SkipWeights' })
$engineExit = $LASTEXITCODE
if ($engineExit) { "   the engine setup reported a problem (exit $engineExit); the reader still works" }

# ---------------------------------------------------------------- smoke
if (-not $SkipSmoke) {
    Step 'smoke test'
    & $venvPy -c @"
from murmur import config, detectors, profile
print('   profile :', profile.describe(config.PROFILE))
print('   ocr     :', __import__('murmur.ocr', fromlist=['backend_name']).backend_name())
print('   detectors:', detectors.available())
"@
}

Step 'done'
"   Start it with:  .\run.ps1"
"   Change the engine anytime:  .\scripts\setup.ps1 -Profile lite"
