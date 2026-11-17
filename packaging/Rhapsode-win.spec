# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: Rhapsode (Windows) — one-folder build.

Windows branch of the macOS Rhapsode.spec. Differences:
  - webview.platforms.winforms/edgechromium instead of cocoa
  - no Foundation/AppKit/Vision/objc, no swift helpers
  - torch/diffusers/transformers family EXCLUDED: on Windows the film engine
    is CPU-only preview per AGENTS.md B4, and pulling torch here would add
    ~2.5 GB to the folder. Reader (Kokoro ONNX) needs none of it.
    Set RHAPSODE_WITH_FILM=1 at build time to include them.
"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.getcwd())
WITH_FILM = os.environ.get("RHAPSODE_WITH_FILM") == "1"

datas = [(os.path.join(ROOT, "web"), "web"),
         (os.path.join(ROOT, "branding", "rhapsode.ico"), "branding"),
         # Handed to image-venv's interpreter to paint a still (FLUX.2 runs there,
         # never inside this bundle).
         (os.path.join(ROOT, "murmur", "fluxgen.py"), ".")]
binaries = []
hiddenimports = [
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
    "pythonnet",
    "pypdfium2",
    "pypdfium2_raw",
    "PIL",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

packages = [
    "onnxruntime",
    "kokoro_onnx",
    "espeakng_loader",
    "phonemizer",
    "pywebview",
    "pypdfium2",
    "pypdfium2_raw",
    "bs4",
    "docx",
    "trafilatura",
    "soundfile",
    "uvicorn",
    "misaki",
    "jieba",
    "pypinyin",
]
if WITH_FILM:
    packages += ["torch", "diffusers", "transformers", "accelerate",
                 "safetensors", "huggingface_hub", "tokenizers", "pyopenjtalk"]
else:
    hiddenimports.append("pyopenjtalk")  # optional language path; harmless if missing

# Windows.Media.Ocr ships as compiled namespaces; PyInstaller cannot infer them.
if os.name == "nt":
    packages.append("winsdk")

for package in packages:
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception as exc:  # pragma: no cover - build-time diagnostics
        print(f"warning: could not collect {package}: {exc}")

# voice weights are NOT bundled (340 MB); first launch bootstraps them into
# %LOCALAPPDATA%\Rhapsode, same as the source install. Drop files into
# build_assets/models to ship them inside the folder instead:
models = os.path.join(ROOT, "build_assets", "models")
if os.path.isdir(models):
    for name in sorted(os.listdir(models)):
        path = os.path.join(models, name)
        if os.path.isfile(path):
            datas.append((path, "models"))

excludes = ["tkinter", "matplotlib", "pandas", "PyQt5", "PySide6",
            "IPython", "pytest"]
if not WITH_FILM:
    excludes += ["torch", "diffusers", "transformers", "accelerate"]

a = Analysis(
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Rhapsode.exe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    icon=os.path.join(SPECPATH, "..", "branding", "rhapsode.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Rhapsode")
