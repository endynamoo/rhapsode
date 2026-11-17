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

# ---------------------------------------------------------------- image venv
Step 'stills engine (image-venv)'
if (-not (Test-Path $python)) {
    $base = ''
    foreach ($candidate in @(
        $env:MURMUR_TORCH_PYTHON,
        "$env:USERPROFILE\.unsloth\studio\unsloth_studio\Scripts\python.exe"
    )) {
        if (-not $candidate -or -not (Test-Path $candidate)) { continue }
        $gpu = & $candidate -c "import torch;print(torch.cuda.is_available())" 2>$null
        if ($gpu -eq 'True') { $base = $candidate; break }
    }
    if ($base) {
        "   reusing the GPU torch already installed at $base"
        & $base -m venv --system-site-packages (Join-Path $homeDir 'image-venv')
    } else {
        "   creating a plain image-venv (torch will be installed into it)"
        & py -3.12 -m venv (Join-Path $homeDir 'image-venv')
    }
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet "diffusers>=0.42" "transformers>=4.49" "accelerate>=1.3" `
        "safetensors>=0.5" "huggingface_hub>=0.28" "pillow>=10.4" "gguf>=0.10"
    if (-not $base) {
        "   installing torch (the big download)"
        & $python -m pip install --quiet "torch>=2.6"
    }
    # A venv built from a trampoline interpreter sees the base Python, not the
    # ROCm torch in the layer above it; point it at the real site-packages.
    $visible = & $python -c "import torch;print(torch.cuda.is_available())" 2>$null
    if ($visible -ne 'True' -and $base) {
        $baseSite = & $base -c "import torch, os; print(os.path.dirname(os.path.dirname(torch.__file__)))" 2>$null
        if ($baseSite) {
            & $python -m pip uninstall -y torch --quiet
            Set-Content -Path (Join-Path $homeDir 'image-venv\Lib\site-packages\_torch_layer.pth') -Value $baseSite -Encoding ASCII
        }
    }
} else {
    "   already present"
}
$env:HF_HOME = Join-Path $homeDir 'models\hf'

# ---------------------------------------------------------------- weights
Step 'weights'
if ($NoGguf) {
    "   skipping the q4 transformer (-NoGguf)"
} else {
    $gguf = Get-ChildItem (Join-Path $env:HF_HOME 'hub') -Recurse -Filter '*klein*Q4_K_M*.gguf' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($gguf) {
        "   q4 transformer already present"
    } else {
        "   fetching the q4 transformer (2.4 GB)"
        & $python -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('unsloth/FLUX.2-klein-4B-GGUF', 'flux-2-klein-4b-Q4_K_M.gguf'))"
    }
}

Step 'detectors'
& $venvPy -c @"
from murmur import detectors
for name in (detectors.FACES_MODEL, detectors.PERSON_MODEL, detectors.POSE_MODEL):
    try:
        path = detectors._fetch(name)
        print(f'   {name}: {path.name}')
    except Exception as exc:
        print(f'   {name}: FAILED {exc}')
"@

# ---------------------------------------------------------------- profile
Step 'engine profile'
$profilePath = Join-Path $homeDir 'engine.json'
$chosen = $Profile
if ($chosen -eq 'auto') {
    $vram = & $venvPy -c "from murmur import profile; print(profile._detect_vram() or 0)" 2>$null
    $chosen = & $venvPy -c "from murmur import profile; print(profile.recommend(float('$vram')))" 2>$null
    $chosen = ($chosen | Select-Object -Last 1).Trim()
    "   detected VRAM: $vram GB -> profile '$chosen'"
    "   if this card is shared (remote desktop, another agent), use -Profile lite: ~5 GiB instead of ~13"

}
& $venvPy -c "from pathlib import Path; from murmur import profile; print(profile.save(Path(r'$homeDir'), '$chosen'))"
Get-Content $profilePath

# ---------------------------------------------------------------- smoke
if (-not $SkipSmoke) {
    Step 'smoke test'
    & $venvPy -c @"
from murmur import config, detectors, profile
print('   profile :', profile.describe(config.PROFILE))
print('   ocr     :', __import__('murmur.ocr', fromlist=['backend_name']).backend_name())
print('   detectors:', detectors.available())
print('   git tag :', 'n/a')
"@
}

Step 'done'
"   Start it with:  .\run.ps1"
"   Change the engine anytime:  .\scripts\setup.ps1 -Profile lite   (or edit $profilePath)"
