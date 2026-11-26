#!/usr/bin/env bash
# Start Rhapsode (installs dependencies and downloads the voice model on first run).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${MURMUR_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

# Open the page with whatever desktop this is: `open` is macOS-only, Linux has xdg-open.
open_url() {
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1
  fi
}

if ! command -v uv >/dev/null 2>&1; then
  echo "Rhapsode needs uv to manage its Python environment."
  echo "Install it with:  brew install uv"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating virtual environment…"
  uv venv -p 3.13 .venv
fi

STAMP=".venv/.requirements.sha"
HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$HASH" ]; then
  echo "Installing dependencies…"
  uv pip install --python .venv/bin/python -q -r requirements.txt
  echo "$HASH" > "$STAMP"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ── native helpers (optional) ──────────────────────────────────────────
mkdir -p bin
if command -v swiftc >/dev/null 2>&1; then
  build_helper() {
    local src="$1" out="$2"
    shift 2
    if [ ! -x "$out" ] || [ "$src" -nt "$out" ]; then
      swiftc -O -parse-as-library "$@" "$src" -o "$out"
    fi
  }
  build_helper native/translate.swift bin/murmur-translate \
    -framework Translation -framework NaturalLanguage \
    || echo "warning: could not build the translation helper; translation will be unavailable." >&2
  build_helper native/prompts.swift bin/murmur-prompts \
    -framework FoundationModels \
    || echo "warning: could not build the prompt helper; Cinema will use its built-in prompts." >&2
else
  echo "note: swiftc not found — translation and on-device prompts stay disabled." >&2
  echo "      Install Xcode Command Line Tools to enable them: xcode-select --install" >&2
fi

# macOS keeps the historical ~/.murmur path. Everywhere else config.py picks the platform
# convention (XDG on Linux); pinning it here sent a Linux reader to a directory the docs do
# not mention, and left $XDG_DATA_HOME unused.
if [ "$(uname -s)" = "Darwin" ]; then
  export MURMUR_HOME="${MURMUR_HOME:-$HOME/.murmur}"
fi
# `set -u` made this read fail on Linux, where MURMUR_HOME is deliberately unset:
# ask the app where its data lives instead of guessing at the platform convention.
DATA_HOME="${MURMUR_HOME:-$(python -c 'from murmur import config; print(config.HOME)')}"
if [ ! -f "$DATA_HOME/models/kokoro-v1.0.onnx" ] || [ ! -f "$DATA_HOME/models/voices-v1.0.bin" ]; then
  python -m murmur.bootstrap
fi

if curl -sf "${URL}/api/health" >/dev/null 2>&1; then
  echo "Rhapsode is already running at ${URL}"
  [ "${MURMUR_NO_OPEN:-0}" = "1" ] || open_url "${URL}"
  exit 0
fi

echo "Rhapsode → ${URL}"
if [ "${MURMUR_NO_OPEN:-0}" != "1" ]; then
  ( sleep 1.4; open_url "${URL}" ) &
fi

exec python -m murmur
