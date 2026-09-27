#!/usr/bin/env bash
# Build the downloadable NEXUS app for Mac: dist/NEXUS.app, runs without Python, zipped as
# dist/NEXUS-<version>-mac.zip. Run it on a Mac from the nexus folder:
#   ./scripts/build_app_mac.sh
# People who download it: unzip, drag NEXUS to Applications, open it (the first time: right-click >
# Open, because it isn't signed yet). Their ideas live in ~/Library/Application Support/NEXUS.
# (They still need Ollama from https://ollama.com for the AI; NEXUS's first-run check says so.)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Set NEXUS up first: ./scripts/setup_mac.sh"; exit 1; }
echo "==> Installing the build tool (PyInstaller)..."
.venv/bin/python -m pip install --quiet pyinstaller pystray
# The app icon, from the 1024px NEXUS icon.
if [ ! -f packaging/NEXUS.icns ]; then
  ICONSET="$(mktemp -d)/NEXUS.iconset"; mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z $size $size static/icons/mac-icon-1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size * 2)) $((size * 2)) static/icons/mac-icon-1024.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o packaging/NEXUS.icns
fi
echo "==> Building (a few minutes)..."
.venv/bin/python -m PyInstaller packaging/nexus.spec --noconfirm --clean
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)"
ZIP="dist/NEXUS-$VERSION-mac.zip"
rm -f "$ZIP"
ditto -c -k --keepParent dist/NEXUS.app "$ZIP"
echo "==> Done: $ZIP"
echo "    Try it: open dist/NEXUS.app (quit your usual NEXUS first, or it just opens that one)."
