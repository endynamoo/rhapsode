#!/usr/bin/env bash
# Read whatever is selected in the frontmost app — or OCR the image on the
# clipboard (⌃⌘⇧4 screenshots land there).
#
# Bind this script to a global hotkey with Shortcuts.app:
#   1. Shortcuts.app → + → "Run Shell Script"
#   2. paste:  ~/Documents/Default\ Project/murmur/scripts/quick-read.sh
#   3. Give the shortcut a keyboard shortcut (e.g. ⌃⌥R)
#   4. Grant Accessibility permission when macOS asks.
#
# If Murmur is already open in a tab, it plays immediately; otherwise a new
# tab opens and you may need to press play once (browser autoplay rules).
set -uo pipefail

PORT="${MURMUR_PORT:-8765}"
BASE="http://127.0.0.1:${PORT}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

notify() {
  osascript -e "display notification \"$1\" with title \"Murmur\"" >/dev/null 2>&1 || true
}

open_result() {
  local response="$1" id delivered
  id="$(printf '%s' "$response" | sed -E 's/.*"id"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
  delivered="$(printf '%s' "$response" | sed -E 's/.*"delivered"[[:space:]]*:[[:space:]]*(true|false).*/\1/')"
  if [ -z "$id" ] || [ "$id" = "$response" ]; then
    return 1
  fi
  if [ "$delivered" != "true" ]; then
    open "${BASE}/#${id}?play=1"
  fi
  return 0
}

# Make sure the app is running.
if ! curl -sf "${BASE}/api/health" >/dev/null 2>&1; then
  notify "Murmur isn't running — starting it…"
  MURMUR_NO_OPEN=1 nohup "${HERE}/run.sh" >/tmp/murmur.log 2>&1 &
  for _ in $(seq 1 60); do
    curl -sf "${BASE}/api/health" >/dev/null 2>&1 && break
    sleep 0.25
  done
  if ! curl -sf "${BASE}/api/health" >/dev/null 2>&1; then
    notify "Could not start Murmur — see /tmp/murmur.log"
    exit 1
  fi
fi

# 1) Preferred: the accessibility selection (no clipboard involved).
TEXT="$(osascript <<'APPLESCRIPT' 2>/dev/null
tell application "System Events"
  set frontProc to first application process whose frontmost is true
  try
    tell frontProc
      set sel to value of attribute "AXSelectedText" of focused UI element
    end tell
    if sel is missing value then return ""
    return sel
  on error
    return ""
  end try
end tell
APPLESCRIPT
)"

# 2) No selection? If the clipboard holds an image, OCR it.
if [ -z "${TEXT//[[:space:]]/}" ]; then
  CLIP_INFO="$(osascript -e 'clipboard info' 2>/dev/null || true)"
  if printf '%s' "$CLIP_INFO" | grep -qE 'PNG|TIFF|picture|JPEG'; then
    RESPONSE="$(curl -sS -X POST "${BASE}/api/ocr-clipboard")"
    if open_result "$RESPONSE"; then
      notify "Reading text from the clipboard image"
      exit 0
    fi
    notify "No readable text was found in that image."
    exit 1
  fi
fi

# 3) Fall back to copying the selection, then put the old clipboard back.
if [ -z "${TEXT//[[:space:]]/}" ]; then
  SAVED="$(pbpaste 2>/dev/null || true)"
  osascript -e 'tell application "System Events" to keystroke "c" using {command down}' >/dev/null 2>&1
  sleep 0.25
  TEXT="$(pbpaste 2>/dev/null || true)"
  if [ -n "$SAVED" ]; then
    printf '%s' "$SAVED" | pbcopy 2>/dev/null || true
  fi
  if [ -z "${TEXT//[[:space:]]/}" ] || [ "$TEXT" = "$SAVED" ]; then
    notify "Select some text or copy an image first."
    exit 1
  fi
fi

if [ "${#TEXT}" -lt 12 ]; then
  notify "Not enough text selected."
  exit 1
fi

RESPONSE="$(printf '%s' "$TEXT" | curl -sS -X POST "${BASE}/api/quick-read" \
  -H 'Content-Type: text/plain; charset=utf-8' --data-binary @-)"

if ! open_result "$RESPONSE"; then
  notify "Murmur could not read that text."
  exit 1
fi
