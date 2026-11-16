# Rhapsode agent install guide

This file is written for an automated agent (or a careful user) installing and
building Rhapsode. Read it fully before running commands. Prefer non-interactive
commands and verify each stage.

Repository root = the directory containing this file.

---

## 0. Support status (read first)

| Platform | Read / TTS / highlight | Chapter films | Native app bundle | Path to use |
| --- | --- | --- | --- | --- |
| macOS 13+, Apple Silicon | supported | supported (MPS + MLX) | supported (`.app`) | Section A |
| macOS Intel | supported | CPU only, opt-in | supported (`.app`) | Section A |
| Windows 10/11 x64 | **preview** (source install) | NVIDIA CUDA (Sana); CPU opt-in | **preview** (`Rhapsode-win.spec`, community build) | Section B |
| Linux x64 | **preview** (source install) | NVIDIA CUDA / CPU opt-in | **not yet** | Section C |

Rules:

- Do **not** claim a Windows or Linux native installer exists. Only macOS has a
  packaged `.app` today.
- `./build_app.sh` is **macOS only** (it uses `sips`, `.icns`, and macOS
  PyInstaller settings). Do not run it on Windows or Linux.
- The Windows/Linux source install is the supported preview path. Reading,
  highlighting, audio export, and the UI must work; films may use CPU or the
  placeholder until a GPU backend is configured.
- `packaging/Rhapsode-win.spec` (community Windows build, 2026-11-16): from the repo
  root with `.venv` on PATH run `.venv\Scripts\pyinstaller.exe --noconfirm packaging\Rhapsode-win.spec`
  → `dist\Rhapsode\Rhapsode.exe` (one-folder). Then robocopy `web` into
  `dist\Rhapsode\_internal\web` (spec embeds web/ at Analysis time). Linux remains source-install only.
- Remote access must stay **off** unless the user explicitly asks to enable it
  (Settings → Remote access). Never bind `0.0.0.0` by default.

---

## 1. Detect the machine

Run the block for the host OS, then choose the matching section.

### macOS

```bash
sw_vers
uname -m
sysctl -n machdep.cpu.brand_string 2>/dev/null
system_profiler SPDisplaysDataType 2>/dev/null | head -20
python3 --version
command -v uv; command -v ffmpeg; xcode-select -p
```

### Windows (PowerShell)

```powershell
$PSVersionTable.PSVersion
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture
Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM
py -0p
Get-Command uv, ffmpeg -ErrorAction SilentlyContinue
```

### Linux

```bash
cat /etc/os-release
uname -m
lspci | grep -Ei 'vga|3d|display'
command -v nvidia-smi && nvidia-smi
command -v rocm-smi && rocm-smi
python3 --version
command -v uv; command -v ffmpeg; command -v fc-list
```

Classify the GPU vendor from the name: `NVIDIA`, `AMD/Radeon`, `Intel`,
`Apple`. If nothing is found, treat it as CPU-only.

---

## 2. Common facts

- **Python**
  - macOS: 3.13 is used by `run.sh` and the app build.
  - Windows/Linux: use **3.11 or 3.12** until DirectML/ROCm wheels support 3.13.
- **uv** manages the virtual environment and installs dependencies.
- **ffmpeg** is required for MP3 export and chapter films.
- **TTS model**: ~340 MB, downloaded once by `python -m murmur.bootstrap`.
- **Disk**: ~1–2 GB base; +2.5 GB for film dependencies; image models download
  separately on first film build.
- **Data directory** (database, models, cache, films):
  - macOS: `~/.murmur` (kept for compatibility)
  - Windows: `%LOCALAPPDATA%\Rhapsode`
  - Linux: `$XDG_DATA_HOME/rhapsode` or `~/.local/share/rhapsode`
  - Override with `MURMUR_HOME=/path`.
- **Default URL**: `http://127.0.0.1:8765`.
- **Remote access**: off by default; the server binds loopback only.

---

## 3. Section A — macOS

### A1. Development install (fastest)

```bash
cd "/path/to/murmur"

# Prerequisites
brew install uv ffmpeg
xcode-select -p || xcode-select --install   # only for translation/prompt helpers

./run.sh
```

`run.sh` creates `.venv`, installs `requirements.txt` (and film requirements
unless `MURMUR_SKIP_FILM=1`), downloads Kokoro, and serves the UI.

Open `http://127.0.0.1:8765`.

### A2. Build the native app

```bash
cd "/path/to/murmur"
./build_app.sh
./dist/Rhapsode.app/Contents/MacOS/Rhapsode --selftest
open dist/Rhapsode.app
```

Expected selftest line:

```
ok http://127.0.0.1:<port> (bind 127.0.0.1)
```

The bundle is ad-hoc signed, not notarized. On another Mac, right-click → Open
the first time.

### A3. Verify

```bash
curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/network
```

- `health.ok` must be `true`.
- `network.enabled` and `network.remoteAccess` must be `false` unless the user
  explicitly enabled remote access.

### A4. GPU notes

- Apple Silicon: MPS for Sana, MLX/mflux for FLUX.2. Nothing extra to install.
- Intel Mac: CPU only. CPU Sana requires an explicit opt-in (see 8.3).

---

## 4. Section B — Windows (preview, source install)

### B1. Install prerequisites (PowerShell)

```powershell
winget install --id Python.Python.3.12 -e
winget install --id astral-sh.uv -e
winget install --id Gyan.FFmpeg -e
winget install --id Microsoft.EdgeWebView2Runtime -e
```

Close and reopen PowerShell so `uv`, `ffmpeg`, and `py` are on `PATH`. Verify:

```powershell
py -3.12 --version
uv --version
ffmpeg -version
```

### B2. Create the environment

```powershell
cd "C:\path\to\murmur"

uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

Optional film dependencies (large, ~2.5 GB):

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-film.txt
```

Download the voice model:

```powershell
.venv\Scripts\python.exe -m murmur.bootstrap
```

It prints the data directory. Expected:
`%LOCALAPPDATA%\Rhapsode` (or `MURMUR_HOME` if set).

### B3. Run

Browser/server mode:

```powershell
$env:MURMUR_PORT = "8765"
.venv\Scripts\python.exe -m murmur
Start-Process "http://127.0.0.1:8765"
```

Desktop window (optional):

```powershell
uv pip install --python .venv\Scripts\python.exe "pywebview>=5.3"
.venv\Scripts\python.exe -m murmur.desktop
```

`murmur.desktop` uses Edge WebView2. If it cannot start, the browser mode above
is sufficient.

### B4. GPU / vendor notes

Current preview status:

- **NVIDIA**
  - Install a CUDA `onnxruntime-gpu` build and a matching CUDA `torch` wheel
    from `https://download.pytorch.org/whl/cu###`.
  - Set `MURMUR_TTS_PROVIDER=CUDAExecutionProvider` before starting.
  - Films use Sana through diffusers on CUDA when `torch.cuda.is_available()`.
    FLUX.2/mflux is Apple-only and is not used on Windows.
- **AMD**
  - DirectML (`torch-directml`, `onnxruntime-directml`) is **planned**, not
    wired into the image pipeline yet. Treat as CPU-only preview.
- **Intel**
  - OpenVINO/DirectML is **planned**. Treat as CPU-only preview.
- CPU film generation is intentionally disabled unless the user opts in:

```powershell
$env:MURMUR_ALLOW_CPU_FILM = "1"
```

Expect very slow generation; confirm with the user before enabling.

### B5. Verify

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
Invoke-RestMethod http://127.0.0.1:8765/api/network
```

Then open the UI, add a text document, press play, and confirm the word
highlight moves with the voice.

---

## 5. Section C — Linux (preview, source install)

### C1. Packages (Ubuntu/Debian; adapt for Fedora/Arch)

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv ffmpeg \
  libgtk-3-0 libwebkit2gtk-4.1-0 tesseract-ocr
```

`libwebkit2gtk` is only needed for the desktop window; the browser mode works
without it. Tesseract is the OCR fallback on Linux (Apple Vision is macOS-only).

### C2. Install uv and the environment

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd "/path/to/murmur"

uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Optional films:

```bash
uv pip install --python .venv/bin/python -r requirements-film.txt
```

NVIDIA only (match the driver):

```bash
uv pip install --python .venv/bin/python onnxruntime-gpu
uv pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cu124 torch
export MURMUR_TTS_PROVIDER=CUDAExecutionProvider
```

AMD ROCm is **planned** for the image pipeline; use CPU preview for now.

Download the model:

```bash
.venv/bin/python -m murmur.bootstrap
```

### C3. Run

```bash
export MURMUR_PORT=8765
.venv/bin/python -m murmur
```

Open `http://127.0.0.1:8765`.

Desktop window (optional, needs WebKitGTK):

```bash
uv pip install --python .venv/bin/python "pywebview>=5.3"
.venv/bin/python -m murmur.desktop
```

### C4. Verify

```bash
curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/network
```

Expect `health.ok: true` and remote access off. If `health.ocr` is false,
install `tesseract-ocr`.

---

## 6. End-to-end verification (all platforms)

1. Open the UI. Book is the default view.
2. Add a text document (Add document → Paste text) and press play.
3. Confirm the current word highlights and advances.
4. Open Settings:
   - 20 themes are listed and changing one recolors the app.
   - The reading-font list contains fonts installed on this machine.
5. `GET /api/network` shows `enabled: false` and `remoteAccess: false`.
6. Optional TTS smoke test:

```bash
curl -s -X POST http://127.0.0.1:8765/api/tts \
  -H 'content-type: application/json' \
  -d '{"text":"Rhapsode is installed.","voice":"af_heart"}'
```

Expect JSON with `url` and `duration`. A `503` means the model is missing or
still loading.

7. Optional film smoke test: open a document → Manga → Review scenes. If the
   image backend is unavailable, frames stay as placeholders; that is expected
   on unsupported GPUs.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Port 8765 in use | another Rhapsode/dev server | stop it, or accept the random port the desktop app picks |
| `modelReady: false` forever | Kokoro weights missing | run `python -m murmur.bootstrap` |
| `ocr: false` on Linux | Tesseract missing | install `tesseract-ocr` |
| No desktop window on Linux | WebKitGTK missing | install `libwebkit2gtk-4.1-0` or use browser mode |
| No desktop window on Windows | WebView2 missing | install the Evergreen runtime |
| `Could not render that page` | PDF source missing | re-upload the document |
| Films stuck on placeholder | no supported image backend | install film requirements; on CPU set `MURMUR_ALLOW_CPU_FILM=1` |
| `ONNX_PROVIDER ... not available` | provider mismatch | unset `MURMUR_TTS_PROVIDER` or install the matching onnxruntime build |
| CUDA out of memory | model too large for VRAM | use Fast/Sana quality, close other GPU apps |
| `Permission denied` writing data | sandboxed install | set `MURMUR_HOME` to a writable directory |
| Remote URL asks for token | remote access enabled | use the link with `?token=…` from Settings → Remote access |

Logs go to the terminal that started the server. The desktop app writes to
stdout; run it from a terminal to see errors.

---

## 8. Agent rules

1. **Do not enable remote access** or bind `0.0.0.0` unless the user explicitly
   requests it. If requested, confirm and restart, then use the tokenized link.
2. **Do not commit** `.venv/`, `dist/`, `build/`, models, caches, or
   `~/.murmur` / `%LOCALAPPDATA%\Rhapsode`.
3. **Do not use the system Python**; always use the project `.venv`.
4. **Do not run as root** on Linux. Use a normal user and `sudo` only for
   system packages.
5. **Do not delete** the data directory to “fix” an install; back it up first.
   The library and settings live there.
6. **Do not run `build_app.sh`** outside macOS.
7. **Do not claim GPU support** that the preview does not have: NVIDIA CUDA and
   Apple Silicon are live; AMD DirectML/ROCm and Intel OpenVINO are roadmap
   items in `docs/PLATFORMS.md`.
8. **Verify before reporting success**: run the checks in Section 6.

---

## 9. Where to read more

- `docs/ARCHITECTURE.md` — modules, UML diagrams, API, security model.
- `docs/PLATFORMS.md` — Windows/Linux/GPU plan, backend interfaces, packaging.
- `README.md` — feature overview and developer quick start.
