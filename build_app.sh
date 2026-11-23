#!/usr/bin/env bash
# Build Rhapsode.app (native window, Murmur engine, bundled weights).
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: brew install uv"
  exit 1
fi

if [ ! -d .venv ]; then
  uv venv -p 3.13 .venv
fi
uv pip install --python .venv/bin/python -q -r requirements.txt -r requirements-lang.txt -r requirements-build.txt
if [ "${MURMUR_SKIP_FILM:-0}" != "1" ] && [ -f requirements-film.txt ]; then
  uv pip install --python .venv/bin/python -q -r requirements-film.txt \
    || echo "warning: Cinema dependencies failed to install; the app will build without stills generation." >&2
fi

# ── native helpers ─────────────────────────────────────────────────────
# build_assets is where the helpers' compiler output goes; it used to be created later,
# so on a fresh clone each redirect failed with "No such file or directory" and the
# warning that explains *why* a helper did not build had nothing to print.
mkdir -p bin build_assets
if command -v swiftc >/dev/null 2>&1; then
  if swiftc -O -parse-as-library -framework Translation -framework NaturalLanguage \
      native/translate.swift -o bin/murmur-translate 2>"build_assets/translate-build.log"; then
    echo "Built translation helper"
  else
    # The direct TranslationSession initializer is macOS 26 SDK only. An older
    # toolchain still builds a working app; Apple translation is unavailable.
    echo "warning: translation helper not built (needs the macOS 26 SDK); Apple translation unavailable." >&2
    sed -n '1,5p' "build_assets/translate-build.log" >&2 || true
  fi
  if [ -f native/prompts.swift ]; then
    if swiftc -O -parse-as-library -framework FoundationModels \
        native/prompts.swift -o bin/murmur-prompts 2>"build_assets/prompts-build.log"; then
      echo "Built prompt helper"
    else
      # Foundation Models needs the macOS 26 SDK. Older toolchains still build a
      # working app; prompt writing falls back to the built-in writer.
      echo "warning: prompt helper not built (needs the macOS 26 SDK); using the fallback." >&2
      sed -n '1,5p' "build_assets/prompts-build.log" >&2 || true
    fi
  fi
else
  echo "swiftc is required to build the native helpers (install Xcode Command Line Tools)." >&2
  exit 1
fi

# ── icon ────────────────────────────────────────────────────────────────
.venv/bin/python scripts/make_icon.py
ICON="build_assets/icon_1024.png"
mkdir -p build_assets/Rhapsode.iconset
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON" --out "build_assets/Rhapsode.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$ICON" --out "build_assets/Rhapsode.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build_assets/Rhapsode.iconset -o packaging/Rhapsode.icns

# ── weights ─────────────────────────────────────────────────────────────
mkdir -p build_assets/models
if [ "${MURMUR_BUNDLE_MODELS:-1}" = "1" ]; then
  MODEL_DIR="${MURMUR_HOME:-$HOME/.murmur}/models"
  if [ -f "$MODEL_DIR/kokoro-v1.0.onnx" ] && [ -f "$MODEL_DIR/voices-v1.0.bin" ]; then
    cp -f "$MODEL_DIR/kokoro-v1.0.onnx" "$MODEL_DIR/voices-v1.0.bin" build_assets/models/
    echo "Bundling Kokoro-82M weights"
  else
    echo "No weights in $MODEL_DIR — the app will use ~/.murmur/models at runtime."
  fi
fi

# ── bundle ──────────────────────────────────────────────────────────────
.venv/bin/pyinstaller --noconfirm --clean packaging/Rhapsode.spec

# OpenCV's videoio FFmpeg library is 39 MB that nothing calls (FaceDetectorYN, resize and a
# log-level call are the only cv2 uses) and it is LGPL. Drop it from the bundle.
find dist/Rhapsode.app -name 'opencv_videoio_ffmpeg*' -delete 2>/dev/null || true
chmod +x dist/Rhapsode.app/Contents/Frameworks/bin/murmur-translate 2>/dev/null || true
chmod +x dist/Rhapsode.app/Contents/Frameworks/bin/murmur-prompts 2>/dev/null || true

# Ship the license texts with the bundle.
cp -f LICENSE THIRD_PARTY.md dist/Rhapsode.app/Contents/Resources/ 2>/dev/null || true
cp -Rf licenses dist/Rhapsode.app/Contents/Resources/ 2>/dev/null || true

# Ad-hoc signature so macOS will launch it locally.
codesign --force --deep --sign - dist/Rhapsode.app 2>/dev/null || true

echo
echo "Built: $(pwd)/dist/Rhapsode.app"
echo "Open:  open dist/Rhapsode.app"
