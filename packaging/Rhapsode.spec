# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: Rhapsode.app (native window + bundled Murmur engine)."""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.getcwd())

datas = [(os.path.join(ROOT, "web"), "web")]
binaries = []
for helper in ("murmur-translate", "murmur-prompts"):
    path = os.path.join(ROOT, "bin", helper)
    if os.path.exists(path):
        binaries.append((path, "bin"))
hiddenimports = [
    "webview.platforms.cocoa",
    "Foundation",
    "AppKit",
    "Vision",
    "objc",
    "pypdfium2",
    "pypdfium2_raw",
    "PIL",
    "torch",
    "torch.backends.mps",
    "diffusers",
    "transformers",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

for package in (
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
    "pyopenjtalk",
    "jieba",
    "pypinyin",
    "diffusers",
    "transformers",
    "accelerate",
    "safetensors",
    "huggingface_hub",
    "tokenizers",
):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception as exc:  # pragma: no cover - build-time diagnostics
        print(f"warning: could not collect {package}: {exc}")

models = os.path.join(ROOT, "build_assets", "models")
if os.path.isdir(models):
    for name in sorted(os.listdir(models)):
        path = os.path.join(models, name)
        if os.path.isfile(path):
            datas.append((path, "models"))

a = Analysis(
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "PyQt5", "PySide6", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Rhapsode",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Rhapsode")

app = BUNDLE(
    coll,
    name="Rhapsode.app",
    icon=os.path.join(ROOT, "packaging", "Rhapsode.icns"),
    bundle_identifier="local.rhapsode",
    info_plist={
        "CFBundleName": "Rhapsode",
        "CFBundleDisplayName": "Rhapsode",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "MIT Licence. Third-party notices in THIRD_PARTY.md",
        "LSApplicationCategoryType": "public.app-category.productivity",
    },
)
