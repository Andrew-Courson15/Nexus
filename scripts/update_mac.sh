#!/usr/bin/env bash
# Update NEXUS in one step: stop it, get the latest version, set it up, start it again.
# Your ideas and settings (the data folder and .env) are never touched by an update.
#   ./scripts/update_mac.sh
# (Settings > Updates in NEXUS runs this with --no-browser: the open page reloads by itself.)
set -uo pipefail
NO_BROWSER=""; [ "${1:-}" = "--no-browser" ] && NO_BROWSER=1
cd "$(dirname "$0")/.."

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
stop() { printf "\n\033[1;33m%s\033[0m\n" "$*"; exit 1; }

[ -d .git ] || stop "This copy of NEXUS wasn't installed with git, so it can't update itself.
Download the new version, replace everything except the 'data' folder and the '.env' file,
then run ./scripts/setup_mac.sh"

PORT="$(grep -E '^NEXUS_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]"')"
PORT="${PORT:-8000}"

# 1. Stop the running NEXUS (icon, Terminal or login item), so the new version is what runs.
say "Stopping NEXUS..."
PIDS="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$PIDS" ]; then kill $PIDS 2>/dev/null; sleep 2; echo "Stopped."; else echo "NEXUS wasn't running."; fi

# 2. Get the latest version.
say "Downloading the latest version..."
git pull || stop "Couldn't download the update (git pull failed). Check the internet connection and run this again.
If it mentions 'local changes', send a screenshot before doing anything else."

# 3. Install anything new (safe to run every time).
say "Setting up..."
./scripts/setup_mac.sh || stop "Setup stopped early (see the message above). Fix that, then run this again."

# 4. Start the new version.
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)"
say "Starting NEXUS $VERSION..."
if [ -d "$HOME/Applications/NEXUS.app" ] && [ -z "$NO_BROWSER" ]; then
  open "$HOME/Applications/NEXUS.app"
else
  mkdir -p data
  nohup .venv/bin/python run.py >> data/nexus.log 2>&1 &
  sleep 3
  [ -n "$NO_BROWSER" ] || open "http://localhost:$PORT"
fi
echo "On your phone: open NEXUS while this Mac is on. It loads the new version by itself"
echo "(or close it fully and open it again), then saves its copy for when the Mac is off."
echo "Check it in the phone's Settings > This device."
