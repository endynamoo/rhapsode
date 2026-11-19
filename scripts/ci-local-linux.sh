#!/usr/bin/env bash
# The linux.yml jobs, on a Linux kernel you already have: inside WSL.
#
#   ./scripts/ci-local-linux.sh unit    the suite, in a venv from requirements-test.txt only
#   ./scripts/ci-local-linux.sh run     a real server, /api/health, tesseract OCR, remote off
#   ./scripts/ci-local.sh ui            the browser journeys in Chromium
#
# Why this exists: Windows can run the suite and the installer, but the Linux
# backend is different code - tesseract instead of Windows.Media.Ocr, POSIX paths,
# a case-sensitive filesystem, and no winsdk or pywebview. Those were exactly the
# failures that reached CI first (cv2 missing, onnxruntime missing).
#
# Not a runner either: this is your WSL distro, not a clean ubuntu-latest. A pass
# here is evidence, and says nothing about apt's luck on the day.
set -euo pipefail

JOB="${1:-unit}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Keep the venv on the Linux filesystem: /mnt/c is drvfs, where pip's console
# scripts land without an executable bit and the interpreter cannot be trusted.
VENV="${RHAP_LOCAL_VENV:-$HOME/.cache/rhapsode-ci/venv}"
PORT="${RHAP_LOCAL_PORT:-8899}"

say() { printf '   %s\n' "$*"; }
fail() { printf '   %s\n' "$*" >&2; exit 1; }

ensure_venv() {
    local requirements="$1"
    if [ ! -x "$VENV/bin/python" ]; then
        say "creating $VENV"
        python3 -m venv "$VENV"
    fi
    say "pip install -r $requirements"
    "$VENV/bin/python" -m pip install -q --upgrade pip
    "$VENV/bin/python" -m pip install -q -r "$ROOT/$requirements"
}

run_unit() {
    ensure_venv requirements-test.txt
    say "unittest discover -s tests -v"
    cd "$ROOT"
    # No tail here: the summary line is what proves the suite ran.
    "$VENV/bin/python" -m unittest discover -s tests 2>&1
}

# command name -> apt package name (the tesseract binary ships in tesseract-ocr;
# `apt-get install tesseract` answers "Unable to locate package tesseract")
apt_package() {
    case "$1" in
        tesseract) printf 'tesseract-ocr' ;;
        *) printf '%s' "$1" ;;
    esac
}

ensure_apt_packages() {
    local missing=() name
    for name in "$@"; do
        command -v "$name" >/dev/null 2>&1 || missing+=("$(apt_package "$name")")
    done
    [ ${#missing[@]} -eq 0 ] && return 0
    say "apt-get install ${missing[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
    for name in "$@"; do
        command -v "$name" >/dev/null 2>&1 || fail "apt reported success but $name is not on PATH"
    done
}

run_server() {
    ensure_apt_packages ffmpeg tesseract
    ensure_venv requirements.txt
    local home
    home="$(mktemp -d)"
    say "MURMUR_HOME=$home"
    cd "$ROOT"
    MURMUR_HOME="$home" MURMUR_PORT="$PORT" MURMUR_WARM_STILLS=0 \
        "$VENV/bin/python" -m murmur > "$home/server.log" 2>&1 &
    local pid=$!
    local health="" i
    for i in $(seq 1 60); do
        sleep 2
        if health="$(curl -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null)"; then break; fi
        if ! kill -0 "$pid" 2>/dev/null; then break; fi
    done
    if [ -z "$health" ]; then
        kill "$pid" 2>/dev/null || true
        tail -40 "$home/server.log" >&2 || true
        fail "the server did not answer /api/health"
    fi
    local network
    network="$(curl -fsS "http://127.0.0.1:$PORT/api/network")"
    kill "$pid" 2>/dev/null || true
    "$VENV/bin/python" - "$health" "$network" <<'PY'
import json
import sys

health = json.loads(sys.argv[1])
network = json.loads(sys.argv[2])
print(f"   health ok={health.get('ok')} ocr={health.get('ocr')} "
      f"backend={health.get('ocrBackend')} modelReady={health.get('modelReady')}")
print(f"   network enabled={network.get('enabled')} remoteAccess={network.get('remoteAccess')}")
problems = []
if not health.get("ok"):
    problems.append("health.ok is false")
if not health.get("ocr"):
    problems.append(f"OCR unavailable (backend {health.get('ocrBackend')!r})")
if health.get("ocrBackend") != "tesseract":
    problems.append(f"expected the tesseract backend, got {health.get('ocrBackend')!r}")
if network.get("enabled") or network.get("remoteAccess"):
    problems.append("remote access must stay off by default")
if problems:
    raise SystemExit("; ".join(problems))
PY
}

run_ui() {
    ensure_apt_packages ffmpeg
    ensure_venv requirements.txt
    say "installing playwright and chromium"
    "$VENV/bin/python" -m pip install -q "playwright>=1.47"
    "$VENV/bin/python" -m playwright install --with-deps chromium >/dev/null 2>&1 ||
        "$VENV/bin/python" -m playwright install chromium
    cd "$ROOT"
    "$VENV/bin/python" -m unittest tests.ui.test_smoke 2>&1
}

case "$JOB" in
    unit) run_unit ;;
    run) run_server ;;
    ui) run_ui ;;
    *) fail "unknown job '$JOB' (unit, run, ui)" ;;
esac
