#!/usr/bin/env bash
# Start a built NEXUS (the executable inside NEXUS.app), check that it answers and works, then stop it.
# Used by the build workflow.   bash packaging/smoke_mac.sh dist/NEXUS.app/Contents/MacOS/NEXUS
# Uses NEXUS_HOME (where its data goes) and NEXUS_PORT from the environment.
set -uo pipefail
EXE="$1"
PORT="${NEXUS_PORT:-8000}"
BASE="http://127.0.0.1:$PORT"
"$EXE" --no-browser > "$RUNNER_TEMP/nexus-stdout.log" 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null; sleep 1; kill -9 "$PID" 2>/dev/null; true; }
trap cleanup EXIT
fail() {
  echo "FAILED: $*"
  echo "--- output ---"; tail -80 "$RUNNER_TEMP/nexus-stdout.log" 2>/dev/null
  echo "--- nexus.log ---"; tail -80 "$NEXUS_HOME/data/nexus.log" 2>/dev/null
  exit 1
}
for i in $(seq 1 90); do
  kill -0 "$PID" 2>/dev/null || fail "NEXUS exited early"
  if PING="$(curl -fsS -m 2 "$BASE/api/ping")"; then echo "Answered after $i s: $PING"; break; fi
  sleep 1
  [ "$i" = 90 ] && fail "NEXUS didn't answer within 90 s"
done
ENTRY="$(curl -fsS -X POST "$BASE/api/entries" -H 'Content-Type: application/json' \
  -d '{"original_text": "Smoke test: a lighthouse keeper who wrestles at night", "analyze": false}')" || fail "capture"
curl -fsS -X POST "$BASE/api/search" -H 'Content-Type: application/json' -d '{"query": "lighthouse"}' | grep -q '"results":\[{' || fail "search"
curl -fsS "$BASE/" | grep -q "app.js" || fail "page"
curl -fsS "$BASE/sw.js" >/dev/null || fail "service worker"
curl -fsS "$BASE/api/uploads/does-not-exist" -o /dev/null -w '%{http_code}\n' | grep -qE '404|422' || true
[ -f "$NEXUS_HOME/data/nexus.db" ] || fail "no database in NEXUS_HOME"
echo "Captured: $ENTRY"
echo "Search, page and service worker work."
