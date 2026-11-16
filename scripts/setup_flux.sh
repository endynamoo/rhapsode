#!/usr/bin/env bash
# Install the FLUX.2 klein 4B image engine for Murmur (Apple Silicon).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: brew install uv" >&2
  exit 1
fi

export HF_HOME="${MURMUR_HOME:-$HOME/.murmur}/models/hf"
VENV="${MURMUR_HOME:-$HOME/.murmur}/image-venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating $VENV…"
  uv venv --python 3.12 "$VENV"
fi

echo "Installing mflux…"
uv pip install --python "$VENV/bin/python" -q --upgrade mflux

MODEL="mflux-community/flux2-klein-4b-mflux-q4"
OUT="${TMPDIR:-/tmp}/murmur-flux-smoke.png"
echo "Downloading and testing $MODEL (about 4.6 GB)…"
"$VENV/bin/mflux-generate-flux2" \
  --model "$MODEL" \
  --base-model flux2-klein-4b \
  --prompt "Monochrome manga panel, a single ink line on white paper, no text" \
  --steps 4 --width 512 --height 512 --seed 1 \
  --output "$OUT"
rm -f "$OUT"
echo
echo "FLUX.2 klein 4B is ready."
echo "Murmur will use it for Settings → Cinema quality → High."
