#!/usr/bin/env bash
# Put a NEXUS icon on this Mac: builds ~/Applications/NEXUS.app and adds it to the Desktop and Dock.
#   ./scripts/make_mac_app.sh            (safe to run again, e.g. after moving the nexus folder)
#   ./scripts/make_mac_app.sh --no-dock  (skip the Dock)
#
# Clicking the icon starts NEXUS in the background (if it isn't running) and opens it in the browser.
# Clicking it while NEXUS is running asks whether to open it or stop it.
set -euo pipefail
cd "$(dirname "$0")/.."
NEXUS_DIR="$(pwd)"

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
stop() { printf "\n\033[1;33m%s\033[0m\n" "$*"; exit 1; }

[ "$(uname)" = "Darwin" ] || stop "This script is for macOS."
[ -x .venv/bin/python ] || stop "NEXUS isn't set up yet. Run ./scripts/setup_mac.sh first."

APP="$HOME/Applications/NEXUS.app"
say "Building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Icon: static/icons/mac-icon-1024.png -> NEXUS.icns (sips and iconutil come with macOS).
ICONSET="$(mktemp -d)/NEXUS.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" static/icons/mac-icon-1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) static/icons/mac-icon-1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/NEXUS.icns"
rm -rf "$(dirname "$ICONSET")"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>NEXUS</string>
  <key>CFBundleDisplayName</key><string>NEXUS</string>
  <key>CFBundleIdentifier</key><string>local.nexus.launcher</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>NEXUS</string>
  <key>CFBundleIconFile</key><string>NEXUS</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

# The launcher. NEXUS_DIR is baked in; everything else is read when the icon is clicked.
{
  printf '#!/bin/bash\nNEXUS_DIR=%q\n' "$NEXUS_DIR"
  cat <<'LAUNCHER'
alert() { osascript -e 'on run argv' -e 'display alert (item 1 of argv) message (item 2 of argv) as critical' -e 'end run' "$1" "$2" >/dev/null 2>&1; }
cd "$NEXUS_DIR" 2>/dev/null || { alert "NEXUS folder not found" "It used to be at $NEXUS_DIR. Run scripts/make_mac_app.sh again from the nexus folder."; exit 1; }
[ -x .venv/bin/python ] || { alert "NEXUS isn't set up" "In Terminal, run: cd \"$NEXUS_DIR\" && ./scripts/setup_mac.sh"; exit 1; }

PORT="$(grep -E '^NEXUS_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'")"
PORT="${PORT:-8000}"
URL="http://localhost:$PORT"
LOG="$NEXUS_DIR/data/nexus.log"
running() { curl -fsS -m 2 "$URL/api/ping" >/dev/null 2>&1; }

if running; then
  choice="$(osascript -e 'button returned of (display dialog "NEXUS is running." buttons {"Stop NEXUS", "Open"} default button "Open" with title "NEXUS" with icon note)' 2>/dev/null)"
  if [ "$choice" = "Stop NEXUS" ]; then
    kill $(lsof -ti "tcp:$PORT" -sTCP:LISTEN) 2>/dev/null
    osascript -e 'display notification "NEXUS stopped." with title "NEXUS"'
  else
    open "$URL"
  fi
  exit 0
fi

# Ollama (the local AI) usually starts with the Mac; make sure it's up. Captures still work without it.
open -ga Ollama 2>/dev/null || true

mkdir -p "$NEXUS_DIR/data"
printf '\n--- NEXUS started %s ---\n' "$(date)" >> "$LOG"
nohup "$NEXUS_DIR/.venv/bin/python" run.py >> "$LOG" 2>&1 &
disown
osascript -e 'display notification "Starting…" with title "NEXUS"'

for _ in $(seq 1 60); do
  if running; then open "$URL"; exit 0; fi
  sleep 1
done
alert "NEXUS didn't start" "Details are in $LOG"
exit 1
LAUNCHER
} > "$APP/Contents/MacOS/NEXUS"
chmod +x "$APP/Contents/MacOS/NEXUS"
touch "$APP"  # make Finder pick up the icon

# Desktop shortcut.
ln -sfn "$APP" "$HOME/Desktop/NEXUS"
say "Added NEXUS to the Desktop"

# Dock (skip with --no-dock).
if [ "${1:-}" != "--no-dock" ]; then
  if defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "Applications/NEXUS.app"; then
    say "NEXUS is already in the Dock"
  else
    defaults write com.apple.dock persistent-apps -array-add \
      "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>file://${APP// /%20}/</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
    killall Dock 2>/dev/null || true
    say "Added NEXUS to the Dock"
  fi
fi

say "Done. Click the NEXUS icon to start it (the first start takes a few seconds)."
echo "    Click it again while it's running to open it or stop it."
echo "    It's also in Launchpad / Spotlight as \"NEXUS\"."
