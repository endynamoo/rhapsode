# Build image-venv: the FLUX.2 [klein] 4B stills engine for Windows and Linux.
#
# The app itself never imports torch. This creates a separate interpreter that
# owns the GPU stack, so the reader keeps working (and keeps its memory) while a
# chapter is being painted.
#
# Usage:
#   .\scripts\setup_image.ps1                      # reuse an existing ROCm/CUDA torch
#   .\scripts\setup_image.ps1 -TorchPython <exe>   # point at a specific interpreter
#   .\scripts\setup_image.ps1 -InstallTorch        # pip install torch into the venv
#
# Without -TorchPython the script looks for an interpreter that already has a
# working GPU torch (this is how the shared ROCm lane on this machine is reused,
# with no multi-gigabyte download); otherwise it falls back to -InstallTorch.
param(
    [string]$TorchPython = "",
    [switch]$InstallTorch,
    [switch]$SkipSmoke
)
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot\.."

$homeDir = if ($env:MURMUR_HOME) { $env:MURMUR_HOME } else { "$env:LOCALAPPDATA\Rhapsode" }
$venv = Join-Path $homeDir 'image-venv'
$python = Join-Path $venv 'Scripts\python.exe'

function Find-GpuPython {
    $candidates = @()
    if ($TorchPython) { $candidates += $TorchPython }
    $candidates += @(
        "$env:USERPROFILE\.unsloth\studio\unsloth_studio\Scripts\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (-not (Test-Path $candidate)) { continue }
        $probe = & $candidate -c "import torch;print('gpu' if torch.cuda.is_available() else 'cpu')" 2>$null
        if ($probe -eq 'gpu') { return $candidate }
    }
    return ""
}

if (Test-Path $python) {
    "image-venv already exists: $venv"
} else {
    $base = Find-GpuPython
    if ($base -and -not $InstallTorch) {
        "reusing the torch that is already installed: $base"
        New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
        & $base -m venv --system-site-packages $venv
    } else {
        "creating a plain venv (torch will be installed into it)"
        New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
        $pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
        if (-not $pyLauncher) { throw "python 3.12 is required: py -3.12 -m venv" }
        & py -3.12 -m venv $venv
    }
}

"installing diffusers and friends into image-venv"
& $python -m pip install --upgrade pip --quiet
& $python -m pip install --quiet "diffusers>=0.42" "transformers>=4.49" "accelerate>=1.3" `
    "safetensors>=0.5" "huggingface_hub>=0.28" "pillow>=10.4"

if ($InstallTorch) {
    "installing torch (this is the big download)"
    & $python -m pip install --quiet "torch>=2.6" diffusers
}

# A venv built from a trampoline interpreter inherits the *base* Python, not the
# ROCm torch that lives in the layer above it. If the venv cannot see the GPU,
# point it at the site-packages that own a working torch instead.
$gpuVisible = & $python -c "import torch;print(torch.cuda.is_available())" 2>$null
if ($gpuVisible -ne 'True' -and $base) {
    $baseSite = & $base -c "import torch, os; print(os.path.dirname(os.path.dirname(torch.__file__)))" 2>$null
    if ($baseSite) {
        "layering the GPU torch from $baseSite"
        & $python -m pip uninstall -y torch --quiet
        Set-Content -Path (Join-Path $venv 'Lib\site-packages\_torch_layer.pth') -Value $baseSite -Encoding ASCII
        $gpuVisible = & $python -c "import torch;print(torch.cuda.is_available())" 2>$null
    }
}

$env:HF_HOME = Join-Path $homeDir 'models\hf'
"fetching the q4 transformer (2.4 GB; this is what makes an 8 GB card work)"
& $python -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('unsloth/FLUX.2-klein-4B-GGUF', 'flux-2-klein-4b-Q4_K_M.gguf'))"
"checking the GPU is visible"
& $python -c "import torch; print('torch', torch.__version__, 'gpu', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

if (-not $SkipSmoke) {
    $smoke = Join-Path $env:TEMP 'rhapsode-flux-smoke.png'
    Remove-Item $smoke -Force -ErrorAction SilentlyContinue
    "generating one 512x512 still (downloads the weights on first run)"
    $env:PYTHONPATH = (Get-Location).Path
    $start = Get-Date
    & $python -m murmur.fluxgen --prompt "monochrome manga panel, a single ink line on white paper, no text" `
        --width 512 --height 512 --steps 2 --seed 1 --out $smoke
    if ($LASTEXITCODE -or -not (Test-Path $smoke)) { throw "the smoke test did not produce an image" }
    "smoke test wrote $smoke in $([math]::Round(((Get-Date) - $start).TotalSeconds,1))s"
    Remove-Item $smoke -Force -ErrorAction SilentlyContinue
}

"Done. Rhapsode will use $python for stills."
