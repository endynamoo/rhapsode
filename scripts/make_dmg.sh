#!/usr/bin/env bash
# Wrap dist/Rhapsode.app in a drag-install DMG. macOS only.
#
#   ./scripts/make_dmg.sh                       # version from murmur.__version__
#   RHAPSODE_VERSION=0.2.0-alpha ./scripts/make_dmg.sh
#
# Output: dist/Rhapsode-<version>-<arch>.dmg with an Applications symlink, so
# the mounted volume is the usual "drag the app next to Applications" window.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != "Darwin" ]; then
  echo "make_dmg.sh needs macOS (ditto and hdiutil)." >&2
  exit 1
fi

APP="dist/Rhapsode.app"
if [ ! -d "$APP" ]; then
  echo "Build the app first: ./build_app.sh" >&2
  exit 1
fi

VERSION="${RHAPSODE_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(.venv/bin/python -c 'import murmur; print(murmur.__version__)' 2>/dev/null || echo 0.0.0)"
fi

ARCH="$(uname -m)"
STAGE="build_assets/dmg"
DMG="dist/Rhapsode-${VERSION}-${ARCH}.dmg"

rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Rhapsode.app"
ln -s /Applications "$STAGE/Applications"

rm -f "$DMG"
hdiutil create -volname "Rhapsode ${VERSION}" -srcfolder "$STAGE" \
  -fs HFS+ -format UDZO -ov "$DMG" >/dev/null

echo "Built: $(pwd)/$DMG"
