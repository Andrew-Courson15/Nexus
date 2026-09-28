#!/usr/bin/env bash
# Runs the NEXUS iPhone app in the iOS simulator against a real NEXUS (used by the build workflow):
# 1. computer on: the app loads NEXUS, saves its copy, and sends a typed capture and a file;
# 2. computer off: the app still opens, showing the copy saved on the phone.
# Screenshots of both go to $OUT.
set -euo pipefail
set -x  # show each step in the build log
cd "$(dirname "$0")/.."
APP="ios/build/Debug-iphonesimulator/NEXUS.app"
BUNDLE="local.nexus.app"
OUT="${OUT:-$RUNNER_TEMP/ios-test}"; mkdir -p "$OUT"
export NEXUS_DATA_DIR="$OUT/nexus-data" NEXUS_AI_PROVIDER=local NEXUS_EMBEDDING_PROVIDER=none \
       NEXUS_TRANSCRIPTION_PROVIDER=placeholder NEXUS_PORT=8000 NEXUS_HOST=127.0.0.1
python run.py > "$OUT/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for i in $(seq 1 60); do curl -fsS -m 2 http://127.0.0.1:8000/api/ping >/dev/null 2>&1 && break; sleep 1; done
curl -fsS -X POST http://127.0.0.1:8000/api/entries -H 'Content-Type: application/json' \
  -d '{"original_text": "El Faro keeps the lighthouse lit", "title": "El Faro", "analyze": false}' >/dev/null
python - <<'PY'
import io, httpx
from PIL import Image
b = io.BytesIO(); Image.new("RGB", (400, 300), (40, 110, 160)).save(b, "PNG")
httpx.post("http://127.0.0.1:8000/api/uploads", files={"file": ("tide-spirit.png", b.getvalue(), "image/png")}, data={"analyze": "false"}, trust_env=False).raise_for_status()
PY

DEVICE="$(xcrun simctl list devices available -j | python -c '
import json, sys
devices = [d for rt, ds in json.load(sys.stdin)["devices"].items() if "iOS" in rt for d in ds if d["name"].startswith("iPhone")]
print(devices[-1]["udid"])')"
echo "Simulator: $DEVICE"
xcrun simctl boot "$DEVICE" 2>/dev/null || true
xcrun simctl bootstatus "$DEVICE" -b
xcrun simctl install "$DEVICE" "$APP"
ARGS=(-serverURL http://127.0.0.1:8000 -nexus-selftest YES)

echo "== 1. Computer on"
xcrun simctl launch "$DEVICE" "$BUNDLE" "${ARGS[@]}"
sleep 30
xcrun simctl io "$DEVICE" screenshot "$OUT/1-computer-on.png"
ENTRIES="$(curl -fsS 'http://127.0.0.1:8000/api/entries?limit=50')"
echo "$ENTRIES" | python -c 'import json,sys; [print(" -", e["title"], "|", (e.get("excerpt") or "")[:90]) for e in json.load(sys.stdin)["items"]]'
grep -q "offline/snapshot" "$OUT/server.log" || { echo "FAILED: the app never saved its copy"; tail -40 "$OUT/server.log"; exit 1; }
echo "$ENTRIES" | grep -q "iOS self-test: typed capture" || { echo "FAILED: typed capture didn't arrive"; exit 1; }
echo "$ENTRIES" | grep -q "ios-selftest.txt" || { echo "FAILED: the file upload didn't arrive"; exit 1; }
echo "$ENTRIES" | grep -q '\\"typed\\":201' || echo "(note: self-test report not found)"
echo "Typed capture and file upload went through the app."

echo "== 2. Computer off"
kill $SERVER; sleep 2
xcrun simctl terminate "$DEVICE" "$BUNDLE" || true
xcrun simctl launch "$DEVICE" "$BUNDLE" "${ARGS[@]}"
sleep 20
xcrun simctl io "$DEVICE" screenshot "$OUT/2-computer-off.png"
CONTAINER="$(xcrun simctl get_app_container "$DEVICE" "$BUNDLE" data)"
ls "$CONTAINER/Documents" || true
TEXT="$(cat "$CONTAINER"/Documents/selftest-*.txt 2>/dev/null | tail -c 3000)"
echo "$TEXT" > "$OUT/page-text.txt"
LAST="$(ls -t "$CONTAINER"/Documents/selftest-*.txt | head -1)"
grep -q "El Faro" "$LAST" || { echo "FAILED: with the computer off the app didn't show the saved copy"; cat "$LAST"; exit 1; }
grep -qi "copy saved on this phone" "$LAST" || echo "(note: offline bar text not found)"
echo "With the computer off, the app opened and showed the saved copy."
