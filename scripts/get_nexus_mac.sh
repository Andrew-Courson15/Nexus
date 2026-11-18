#!/bin/bash
# Install or update the NEXUS app on a Mac from the latest release, in one Terminal command:
#
#   curl -fsSL https://raw.githubusercontent.com/Andrew-Courson15/Nexus/refs/heads/claude/nexus-ai-os-rhh5si/scripts/get_nexus_mac.sh | bash
#
# It downloads the newest NEXUS-<version>.dmg, quits a running NEXUS app, puts the new NEXUS.app in
# Applications and opens it. Because Terminal downloads it (not a browser), the Mac doesn't block it
# as "not from an identified developer"; the quarantine mark is cleared too, just in case.
# Your ideas are never touched: they live in ~/Library/Application Support/NEXUS, not in the app.
set -euo pipefail

REPO="Andrew-Courson15/Nexus"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This is for a Mac. On Windows, download NEXUS-Setup from https://github.com/$REPO/releases/latest"
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "The NEXUS Mac app needs a Mac with an Apple chip (M1 or newer); this one has an Intel chip."
  echo "Ask Andrew for an Intel version."
  exit 1
fi

say "Finding the newest NEXUS…"
RELEASE="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" || true)"
URL="$(printf '%s' "$RELEASE" | grep -m1 -o '"browser_download_url": *"[^"]*\.dmg"' \
  | sed 's/.*"\(https:[^"]*\)"$/\1/' || true)"
if [ -z "$URL" ]; then
  echo "Couldn't find the NEXUS download. Is the internet connection working?"
  exit 1
fi
echo "${URL##*/}"

TMP="$(mktemp -d)"
MNT="$TMP/mount"
cleanup() { hdiutil detach "$MNT" -quiet >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT

say "Downloading…"
curl -fL --progress-bar -o "$TMP/NEXUS.dmg" "$URL"

if pgrep -f "NEXUS.app/Contents/MacOS/NEXUS" >/dev/null; then
  say "Closing NEXUS…"
  osascript -e 'quit app "NEXUS"' >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do pgrep -f "NEXUS.app/Contents/MacOS/NEXUS" >/dev/null || break; sleep 0.5; done
  pkill -f "NEXUS.app/Contents/MacOS/NEXUS" 2>/dev/null || true
  sleep 1
fi

say "Installing…"
DEST="/Applications"
[ -w "$DEST" ] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
mkdir -p "$MNT"
hdiutil attach -nobrowse -readonly -quiet -mountpoint "$MNT" "$TMP/NEXUS.dmg"
rm -rf "$DEST/NEXUS.app.new"
ditto "$MNT/NEXUS.app" "$DEST/NEXUS.app.new"
rm -rf "$DEST/NEXUS.app"
mv "$DEST/NEXUS.app.new" "$DEST/NEXUS.app"
xattr -dr com.apple.quarantine "$DEST/NEXUS.app" 2>/dev/null || true

if curl -fsS -m 2 http://127.0.0.1:8000/api/ping >/dev/null 2>&1; then
  say "Another NEXUS is still running (probably one you start from a folder)."
  echo "Close it (its Terminal window), then open NEXUS from $DEST."
  exit 0
fi

say "Opening NEXUS…"
open "$DEST/NEXUS.app"
echo "Done: NEXUS is in $DEST and opens in your browser in a moment."
echo "If you used NEXUS from a folder before, click \"Bring my archive here\" when it opens."
