#!/usr/bin/env bash
# Run the standalone Murmur engine sidecar.
set -euo pipefail
cd "$(dirname "$0")/.."
export MURMUR_ENGINE_PORT="${MURMUR_ENGINE_PORT:-8931}"
exec .venv/bin/python -m murmur.engine_api
