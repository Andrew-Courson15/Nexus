#!/usr/bin/env bash
# Connect your iPhone to the NEXUS on this Mac, privately, from anywhere, using Tailscale.
# Safe to run again. Run from the nexus folder:
#   ./scripts/phone_setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
stop() { printf "\n\033[1;33m%s\033[0m\n" "$*"; exit 1; }

[ -x .venv/bin/python ] || stop "NEXUS isn't set up yet. Run ./scripts/setup_mac.sh first."

# 1. Tailscale installed?
TS=""
for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then TS="$c"; break; fi
done
[ -n "$TS" ] || stop "Tailscale isn't installed on this Mac yet.
Install it from the Mac App Store (search 'Tailscale') or https://tailscale.com/download/mac,
open it, sign in (any Google/Microsoft/Apple account), then run this script again."

# 2. Signed in, and what is this Mac called on the private network?
STATUS="$("$TS" status --json 2>/dev/null || true)"
read -r STATE NAME < <(printf '%s' "$STATUS" | .venv/bin/python -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("none -"); sys.exit()
print(d.get("BackendState") or "none", (d.get("Self") or {}).get("DNSName", "").rstrip(".") or "-")')
[ "$STATE" = "Running" ] || stop "Tailscale is installed but not connected.
Click the Tailscale icon in the menu bar, choose 'Log in', then run this script again."
[ "$NAME" != "-" ] || stop "Couldn't find this Mac's Tailscale name. Turn on MagicDNS at
https://login.tailscale.com/admin/dns and run this script again."
say "This Mac's private address: https://$NAME"

# 3. Let NEXUS answer to that name (it only answers to names it knows).
[ -f .env ] || cp .env.example .env
.venv/bin/python - "$NAME" <<'PY'
import sys
from pathlib import Path
name, env = sys.argv[1], Path(".env")
lines = env.read_text().splitlines()
for i, line in enumerate(lines):
    if line.startswith("NEXUS_ALLOWED_HOSTS="):
        hosts = [h.strip() for h in line.split("=", 1)[1].split(",") if h.strip()]
        if name not in hosts:
            lines[i] = "NEXUS_ALLOWED_HOSTS=" + ",".join(hosts + [name])
            print(f"Added {name} to NEXUS_ALLOWED_HOSTS in .env")
        break
else:
    lines.append(f"NEXUS_ALLOWED_HOSTS=localhost,127.0.0.1,{name}")
    print(f"Added {name} to NEXUS_ALLOWED_HOSTS in .env")
env.write_text("\n".join(lines) + "\n")
PY

# 4. Private HTTPS link: https://<name> -> NEXUS on this Mac (only your own devices can reach it).
PORT="$(grep -E '^NEXUS_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"')"
PORT="${PORT:-8000}"
say "Turning on the private HTTPS link..."
"$TS" serve --bg "$PORT" || stop "Tailscale couldn't turn on the link.
- If it mentions HTTPS or certificates: open https://login.tailscale.com/admin/dns,
  turn on MagicDNS and 'HTTPS Certificates', then run this script again."

# 5. Optional: start NEXUS when you log in, so the phone can reach it without opening anything.
printf "\nStart NEXUS automatically when you log in to this Mac? (y/n) "
read -r ANS
PLIST="$HOME/Library/LaunchAgents/local.nexus.plist"
if [[ "$ANS" =~ ^[yY] ]]; then
  mkdir -p "$HOME/Library/LaunchAgents" data
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.nexus</string>
  <key>ProgramArguments</key><array><string>$PWD/.venv/bin/python</string><string>run.py</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$PWD/data/nexus.log</string>
  <key>StandardErrorPath</key><string>$PWD/data/nexus.log</string>
</dict></plist>
PL
  launchctl unload "$PLIST" 2>/dev/null || true
  say "NEXUS will start by itself when you log in."
fi

say "Mac is ready. Now:"
echo "  1. Restart NEXUS so it picks up the new address: click the NEXUS icon -> Stop NEXUS,"
echo "     then click the icon again to start it (or just log out and back in)."
echo "  2. On the iPhone: install 'Tailscale' from the App Store and sign in with the SAME account"
echo "     you used on this Mac."
echo "  3. On the iPhone, open Safari and go to:  https://$NAME"
echo "  4. Tap Share -> Add to Home Screen. NEXUS now opens like an app."
echo
echo "The phone reaches NEXUS while this Mac is awake. When it's asleep or off, the phone app"
echo "still opens: it shows the copy it saved and keeps new captures until the Mac is back."
