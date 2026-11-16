#!/usr/bin/env bash
# Start Murmur (installs dependencies and downloads the voice model on first run).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${MURMUR_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

if ! command -v uv >/dev/null 2>&1; then
  echo "Murmur needs uv to manage its Python environment."
  echo "Install it with:  brew install uv"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating virtual environment…"
  uv venv -p 3.13 .venv
fi

STAMP=".venv/.requirements.sha"
HASH="$(cat requirements.txt requirements-film.txt | shasum -a 256 | awk '{print $1}')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$HASH" ]; then
  echo "Installing dependencies…"
  uv pip install --python .venv/bin/python -q -r requirements.txt
  if [ "${MURMUR_SKIP_FILM:-0}" != "1" ]; then
    uv pip install --python .venv/bin/python -q -r requirements-film.txt \
      || echo "warning: Cinema dependencies failed to install; run without Cinema or install requirements-film.txt manually." >&2
  fi
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

MURMUR_HOME="${MURMUR_HOME:-$HOME/.murmur}"
if [ ! -f "$MURMUR_HOME/models/kokoro-v1.0.onnx" ] || [ ! -f "$MURMUR_HOME/models/voices-v1.0.bin" ]; then
  python -m murmur.bootstrap
fi

if curl -sf "${URL}/api/health" >/dev/null 2>&1; then
  echo "Murmur is already running at ${URL}"
  [ "${MURMUR_NO_OPEN:-0}" = "1" ] || open "${URL}"
  exit 0
fi

echo "Murmur → ${URL}"
if [ "${MURMUR_NO_OPEN:-0}" != "1" ]; then
  ( sleep 1.4; open "${URL}" ) &
fi

exec python -m murmur
