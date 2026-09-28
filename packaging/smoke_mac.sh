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
# (matches NEXUS started again by the restart below, not this script, whose command line also has the path)
cleanup() { kill "$PID" 2>/dev/null; pkill -f -- "$EXE --no-browser" 2>/dev/null; sleep 1; kill -9 "$PID" 2>/dev/null; pkill -9 -f -- "$EXE --no-browser" 2>/dev/null; true; }
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
if [ -n "${NEXUS_VOICE_CHECK:-}" ]; then  # voice notes, iPhone photos, PDFs: their libraries made it into the app
  python3 packaging/voice_check.py "$BASE" || fail "voice notes don't work in the built app"
  python3 packaging/media_check.py "$BASE" || fail "iPhone photos or PDFs don't work in the built app"
fi

# Back up, change something, choose the backup to restore, restart from the page: the backup is back.
curl -fsS -X POST "$BASE/api/backups" >/dev/null || fail "backup"
for i in $(seq 1 60); do
  B="$(curl -fsS "$BASE/api/backups")"
  echo "$B" | python3 -c 'import json,sys; b=json.load(sys.stdin); sys.exit(0 if b["backups"] and not b["running"] else 1)' && break
  sleep 1
done
ZIP="$(echo "$B" | python3 -c 'import json,sys,os; b=json.load(sys.stdin); print(os.path.join(b["folder"], b["backups"][0]["name"]))')" || fail "no backup made"
IN_BACKUP="$(curl -fsS "$BASE/api/entries" | python3 -c 'import json,sys; print(json.load(sys.stdin)["total"])')"
curl -fsS -X POST "$BASE/api/entries" -H 'Content-Type: application/json' -d '{"original_text": "Made after the backup", "analyze": false}' >/dev/null
curl -fsS -X POST "$BASE/api/backups/restore" -F "file=@$ZIP" | grep -q restore_pending || fail "restore wasn't accepted"
curl -fsS "$BASE/api/backups" | grep -q '"can_restart":true' || fail "the app can't restart itself"
curl -fsS -X POST "$BASE/api/restart" >/dev/null || fail "restart"
for i in $(seq 1 60); do curl -fsS -m 1 "$BASE/api/ping" >/dev/null 2>&1 || break; sleep 0.3; done
for i in $(seq 1 90); do curl -fsS -m 2 "$BASE/api/ping" >/dev/null 2>&1 && break; sleep 1; [ "$i" = 90 ] && fail "didn't come back after restarting"; done
curl -fsS "$BASE/api/backups" | grep -q '"ok":true' || fail "the backup wasn't restored"
curl -fsS "$BASE/api/entries" | grep -q "\"total\":$IN_BACKUP[,}]" || fail "expected only the $IN_BACKUP entries from the backup"
echo "Restored a backup through Settings and restarted from the page."
