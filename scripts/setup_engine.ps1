# Engine-only setup: what the stills need, nothing else.
#
# Called by the installer (optionally) and by scripts\setup.ps1; also safe to re-run
# by hand from Settings or a terminal. It is idempotent: every step checks first.
#
#   .\scripts\setup_engine.ps1                 # detect the card, install, pick a profile
#   .\scripts\setup_engine.ps1 -Profile lite   # force a profile
#   .\scripts\setup_engine.ps1 -DryRun         # say what it would do
#
# GPU support comes from the torch wheel: NVIDIA wants a cu### build, AMD a ROCm
# build. -TorchIndex overrides the default for either.
param(
    [ValidateSet('auto', 'lite', 'balanced', 'max', 'cpu')]
    [string]$Profile = 'auto',
    [string]$TorchIndex = '',
    [switch]$SkipWeights,
    [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
Set-Location "$PSScriptRoot\.."

function Say($m) { "   $m" }
function Fail($m) { "!! $m"; exit 1 }

$homeDir = if ($env:MURMUR_HOME) { $env:MURMUR_HOME } else { "$env:LOCALAPPDATA\Rhapsode" }
$venv = Join-Path $homeDir 'image-venv'
$python = Join-Path $venv 'Scripts\python.exe'
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
$env:HF_HOME = Join-Path $homeDir 'models\hf'

# ---- what is in this machine --------------------------------------------------
# Sunshine and remote sessions add virtual adapters; they are never the renderer.
$adapters = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -and $_.Name -notmatch 'Microsoft Basic|Remote Display|Virtual|Mirror|Indirect|Idd' }
$gpu = $adapters | Where-Object { $_.Name -match 'NVIDIA|GeForce|Quadro|RTX|Tesla|AMD|Radeon|ATI|Intel|Arc ' } | Select-Object -First 1
if (-not $gpu) { $gpu = $adapters | Select-Object -First 1 }
$gpuName = if ($gpu) { $gpu.Name } else { 'none' }
$vendor = 'cpu'
if ($gpuName -match 'NVIDIA|GeForce|Quadro|RTX|Tesla') { $vendor = 'nvidia' }
elseif ($gpuName -match 'AMD|Radeon|ATI') { $vendor = 'amd' }
elseif ($gpuName -match 'Intel|Arc ') { $vendor = 'intel' }
Say "gpu: $gpuName ($vendor)"

# ---- an existing GPU torch is worth more than any download --------------------
function Find-GpuTorch {
    $candidates = @(
        $env:MURMUR_TORCH_PYTHON,
        "$env:USERPROFILE\.unsloth\studio\unsloth_studio\Scripts\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (-not $candidate -or -not (Test-Path $candidate)) { continue }
        $ok = & $candidate -c "import torch;print(torch.cuda.is_available())" 2>$null
        if ($ok -eq 'True') { return $candidate }
    }
    return ''
}

# ---- a base interpreter that actually runs -----------------------------------
# `py -3.12` is not enough on its own: this machine lists 3.12 only as the Microsoft
# Store alias, which the launcher cannot start ("No suitable Python runtime found").
# Try the names a real install answers to, and only accept one that reports 3.11/3.12.
function Test-PythonVersion {
    param([string[]]$Command)
    if (-not $Command -or -not $Command[0]) { return $false }
    $exe = $Command[0]
    $rest = @()
    if ($Command.Count -gt 1) { $rest = $Command[1..($Command.Count - 1)] }
    $version = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    return ($version -eq '3.12' -or $version -eq '3.11')
}

function Find-BasePython {
    $candidates = @()
    if ($env:MURMUR_BASE_PYTHON) { $candidates += ,@($env:MURMUR_BASE_PYTHON) }
    $candidates += ,@('python3.12')
    $candidates += ,@('python')
    $candidates += ,@('py', '-3.12')
    foreach ($candidate in $candidates) {
        if (Test-PythonVersion $candidate) { return ,$candidate }
    }
    return $null
}

# ---- image venv ---------------------------------------------------------------
if (Test-Path $python) {
    Say "image-venv already exists"
} elseif ($DryRun) {
    Say "would create $venv"
} else {
    $base = Find-GpuTorch
    if ($base) {
        Say "reusing the GPU torch at $base"
        & $base -m venv --system-site-packages $venv
    } else {
        $interpreter = Find-BasePython
        if (-not $interpreter) {
            Say "no Python 3.11 or 3.12 could be started on this machine"
            Say "install one (winget install --id Python.Python.3.12 -e) or set MURMUR_BASE_PYTHON"
            Fail "stills engine not set up"
        }
        $exe = $interpreter[0]
        $rest = @()
        if ($interpreter.Count -gt 1) { $rest = $interpreter[1..($interpreter.Count - 1)] }
        Say ("creating an image-venv with " + $exe + " " + ($rest -join " ")).Trim()
        & $exe @rest -m venv $venv
    }
}

if (-not $DryRun -and (Test-Path $python)) {
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet "diffusers>=0.42" "transformers>=4.49" "accelerate>=1.3" `
        "safetensors>=0.5" "huggingface_hub>=0.28" "pillow>=10.4" "gguf>=0.10"

    # torch: only worth downloading when the venv cannot see the card
    $visible = & $python -c "import torch;print(torch.cuda.is_available())" 2>$null
    if ($visible -ne 'True') {
        $index = $TorchIndex
        if (-not $index) {
            if ($vendor -eq 'nvidia') { $index = 'https://download.pytorch.org/whl/cu126' }
            elseif ($vendor -eq 'amd') { $index = 'https://download.pytorch.org/whl/rocm6.4' }
        }
        if ($index) {
            Say "installing torch for $vendor from $index (several GB)"
            & $python -m pip install --quiet torch --index-url $index
            if ($LASTEXITCODE) {
                Say "torch install failed; stills will fall back to the placeholder"
                Say "pass -TorchIndex with the wheel index your driver needs"
            }
        } else {
            Say "no GPU torch index known for '$vendor'; stills fall back to the placeholder"
        }
    }

    # a venv built from a trampoline interpreter sees the base Python, not the
    # ROCm torch layered above it: point it at the real site-packages
    $visible = & $python -c "import torch;print(torch.cuda.is_available())" 2>$null
    if ($visible -ne 'True') {
        $base = Find-GpuTorch
        if ($base) {
            $site = & $base -c "import torch, os; print(os.path.dirname(os.path.dirname(torch.__file__)))" 2>$null
            if ($site) {
                Say "layering the GPU torch from $site"
                & $python -m pip uninstall -y torch --quiet
                Set-Content -Path (Join-Path $venv 'Lib\site-packages\_torch_layer.pth') -Value $site -Encoding ASCII
            }
        }
    }
}

# ---- proof it can actually paint -----------------------------------------------
# A venv, a CPU torch and 2.4 GB of weights look like success and paint nothing. Check the
# card before spending the download, and leave a marker the app reads as "ready".
if (Test-Path $python) {
    $visible = & $python -c "import torch;print(torch.cuda.is_available())" 2>$null
    if ($visible -ne 'True') {
        Say "torch cannot see the graphics card, so Cinema would only show placeholders"
        if ($vendor -eq 'amd') {
            Say "AMD on Windows needs a ROCm build of torch; the public ROCm index has no Windows wheel"
            Say "set MURMUR_TORCH_PYTHON to an interpreter that has one, or see docs\STILLS.md"
        } else {
            Say "install the torch build this card needs, or see docs\STILLS.md"
        }
        Fail "stills engine prepared, but no GPU torch is visible"
    }
    $torchVersion = & $python -c "import torch;print(torch.__version__)" 2>$null
    Say "torch sees the card: $torchVersion"
    $marker = "{ ""ready"": true, ""torch"": ""$torchVersion"", ""when"": ""$(Get-Date -Format s)"" }"
    Set-Content -Path (Join-Path $venv 'rhapsode-ready.json') -Value $marker -Encoding ASCII
}

# ---- weights ------------------------------------------------------------------
if ($SkipWeights) {
    Say "skipping weights (-SkipWeights)"
} elseif ($DryRun) {
    Say "would fetch the q4 transformer and the detectors"
} else {
    $gguf = Get-ChildItem (Join-Path $env:HF_HOME 'hub') -Recurse -Filter '*klein*Q4_K_M*.gguf' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($gguf) {
        Say "q4 transformer already present"
    } elseif (Test-Path $python) {
        Say "fetching the q4 transformer (2.4 GB)"
        & $python -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('unsloth/FLUX.2-klein-4B-GGUF', 'flux-2-klein-4b-Q4_K_M.gguf'))"
    }
    if (Test-Path '.venv\Scripts\python.exe') {
        & '.venv\Scripts\python.exe' -c @"
from murmur import detectors
for name in (detectors.FACES_MODEL, detectors.PERSON_MODEL, detectors.POSE_MODEL):
    try:
        detectors._fetch(name)
    except Exception as exc:
        print(f'   {name}: {exc}')
"@
    }
}

# ---- profile ------------------------------------------------------------------
$venvPy = if (Test-Path $python) { $python } else { '.venv\Scripts\python.exe' }
if ($DryRun) {
    Say "would write the profile"
    exit 0
}
if (-not (Test-Path $venvPy)) { Fail "no interpreter to write the profile with" }

# A packaged install has no `murmur` for the image interpreter (it lives inside the frozen
# app), so asking it to save the profile only produces a traceback. The app writes and
# activates the profile itself when the setup reports success.
$hasMurmur = & $venvPy -c "import murmur; print('yes')" 2>$null
if ($hasMurmur -eq 'yes') {
    if ($Profile -eq 'auto') {
        $vram = & $venvPy -c "from murmur import profile; print(profile._detect_vram() or 0)" 2>$null
        $Profile = & $venvPy -c "from murmur import profile; print(profile.recommend(float('$vram')))" 2>$null
        $Profile = ($Profile | Select-Object -Last 1).Trim()
        Say "detected VRAM: $vram GB -> profile '$Profile'"
        if ($vram -ge 10 -and $vram -lt 22) {
            Say "if this card is shared (remote desktop, another GPU job), use -Profile lite: ~5 GiB instead of ~13"
        }
    }
    & $venvPy -c "from pathlib import Path; from murmur import profile; print(profile.describe(profile.save(Path(r'$homeDir'), '$Profile')))"
} else {
    Say "the app writes the profile when it starts (no murmur module for the image interpreter)"
}

Say 'engine setup finished'
