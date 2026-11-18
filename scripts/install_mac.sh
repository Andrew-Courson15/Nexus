#!/usr/bin/env bash
# One-step NEXUS install for a Mac: installs what's missing (Homebrew, Python, Ollama), sets NEXUS
# up, puts the NEXUS icon on the Desktop and in the Dock, and opens it. Safe to run again.
# Easiest: double-click "Install NEXUS (Mac).command" in the nexus folder.
set -uo pipefail
cd "$(dirname "$0")/.."
chmod +x scripts/*.sh 2>/dev/null || true  # a downloaded ZIP can lose these

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
stop() { printf "\n\033[1;33m%s\033[0m\n" "$*"; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

echo "NEXUS installer: this sets up everything NEXUS needs on this Mac."

brew_env() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}
need_brew() {
  brew_env && return 0
  say "Installing Homebrew (the Mac asks for your password once)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || stop "Homebrew couldn't be installed. See https://brew.sh, then double-click the installer again."
  brew_env || stop "Homebrew was installed but can't be found. Close this window and double-click the installer again."
  # so new Terminal windows find it too
  grep -q "brew shellenv" "$HOME/.zprofile" 2>/dev/null || echo "eval \"\$($(command -v brew) shellenv)\"" >> "$HOME/.zprofile"
}
brew_env || true

# 1. Python 3.10 or newer (macOS's own python3 is too old)
PY_OK=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if has "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY_OK=1; break; fi
done
if [ -z "$PY_OK" ]; then
  need_brew
  say "Installing Python 3.12..."
  brew install python@3.12 || stop "Python couldn't be installed. Run 'brew install python@3.12' in Terminal to see why."
fi

# 2. Ollama (runs the AI on this Mac) - installed and running
if ! has ollama && [ ! -d /Applications/Ollama.app ]; then
  need_brew
  say "Installing Ollama (the local AI)..."
  brew install --cask ollama || stop "Ollama couldn't be installed. Download it from https://ollama.com/download, then run the installer again."
fi
if ! curl -fsS -m 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  say "Starting Ollama..."
  open -ga Ollama 2>/dev/null || true
  for _ in $(seq 1 30); do
    curl -fsS -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi
curl -fsS -m 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
  || stop "Ollama is installed but isn't running yet. Open the Ollama app (menu bar), then run the installer again."
has ollama || export PATH="$PATH:/Applications/Ollama.app/Contents/Resources"

# 3. First time only: choose the AI model that suits this Mac's memory.
if [ ! -f .env ]; then
  cp .env.example .env
  MEM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 17179869184) / 1073741824 ))
  if [ "$MEM_GB" -lt 16 ]; then
    sed -i '' 's/^NEXUS_OLLAMA_MODEL=.*/NEXUS_OLLAMA_MODEL=gemma3:4b/' .env
    echo "This Mac has ${MEM_GB} GB of memory, so NEXUS will use the lighter gemma3:4b model."
  fi
fi

# 4. NEXUS itself (Python packages, models, speech model, Desktop/Dock icon)
./scripts/setup_mac.sh || stop "Setup stopped early (see the message above). Fix that, then run the installer again."

# 5. Phone (optional, needs Tailscale)
if has tailscale || [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
  printf "\nSet up NEXUS for your phone now? (y/n) "
  read -r ANS
  [[ "$ANS" =~ ^[yY] ]] && ./scripts/phone_setup_mac.sh
else
  echo
  echo "For your phone later: install Tailscale (Mac App Store), then open NEXUS > Settings > Your phone."
fi

# 6. Open NEXUS
say "NEXUS is installed. Opening it..."
if [ -d "$HOME/Applications/NEXUS.app" ]; then open "$HOME/Applications/NEXUS.app"; fi
echo "Next time, click the NEXUS icon on your Desktop or in the Dock."
