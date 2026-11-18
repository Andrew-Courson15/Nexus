#!/usr/bin/env bash
# App Store screenshots of the NEXUS iPhone app, on the largest iPhone simulator, with a made-up
# demo archive (ios/demo_archive.py). Used by the build workflow; the pictures go to $OUT.
set -euo pipefail
cd "$(dirname "$0")/.."
APP="ios/build/Debug-iphonesimulator/NEXUS.app"
BUNDLE="local.nexus.app"
OUT="${OUT:-$RUNNER_TEMP/app-store-screenshots}"; mkdir -p "$OUT"
export NEXUS_DATA_DIR="$RUNNER_TEMP/demo-data" NEXUS_AI_PROVIDER=local NEXUS_EMBEDDING_PROVIDER=none \
       NEXUS_TRANSCRIPTION_PROVIDER=placeholder NEXUS_PORT=8000 NEXUS_HOST=127.0.0.1 NEXUS_PROCESSING_MODE=sync
python run.py > "$OUT/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for i in $(seq 1 60); do curl -fsS -m 2 http://127.0.0.1:8000/api/ping >/dev/null 2>&1 && break; sleep 1; done
python ios/demo_archive.py http://127.0.0.1:8000

# The biggest iPhone available (App Store Connect asks for 6.9" or 6.7" screenshots).
DEVICE="$(xcrun simctl list devices available -j | python -c '
import json, sys
ds = [d for rt, ds in json.load(sys.stdin)["devices"].items() if "iOS" in rt for d in ds if "Pro Max" in d["name"] or "Plus" in d["name"]]
ds.sort(key=lambda d: ("Pro Max" in d["name"], d["name"]))
print(ds[-1]["udid"])')"
echo "Simulator: $DEVICE"
xcrun simctl boot "$DEVICE" 2>/dev/null || true
xcrun simctl bootstatus "$DEVICE" -b
xcrun simctl status_bar "$DEVICE" override --time "9:41" --batteryState charged --batteryLevel 100 \
  --cellularMode active --cellularBars 4 --wifiBars 3 || true
xcrun simctl install "$DEVICE" "$APP"
n=0
for screen in "#/" "#/archive" "#/art" "#/projects/1" "#/entry/1"; do
  n=$((n+1))
  xcrun simctl terminate "$DEVICE" "$BUNDLE" 2>/dev/null || true
  xcrun simctl launch "$DEVICE" "$BUNDLE" -serverURL http://127.0.0.1:8000 -nexus-open "$screen" -nexus-screenshots YES
  sleep 12
  xcrun simctl io "$DEVICE" screenshot "$OUT/$n.png"
done
python - "$OUT" <<'PY'
import sys, pathlib
from PIL import Image
for p in sorted(pathlib.Path(sys.argv[1]).glob("*.png")):
    print(p.name, Image.open(p).size)
PY
